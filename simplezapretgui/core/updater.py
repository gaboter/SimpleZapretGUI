"""Проверка обновлений zapret, загрузка релиза и установка с переносом настроек.

Что переносится при обновлении:
  * пользовательские списки lists/*-user.txt и любые нестандартные файлы в lists/;
  * правки встроенных списков (overrides) — применяются заново;
  * Game Filter (utils/game_filter.enabled);
  * режим IPSet (none / any / loaded) и загруженный список IP;
  * выбранные активные фейки (ACTIVE_DISCORD_UDP.bin / ACTIVE_GAME_UDP.bin);
  * пользовательские стратегии из редактора;
  * состояние обхода: если работала служба — она переустанавливается с той же стратегией,
    если работал сеанс — перезапускается.
Старая версия сохраняется в backups/ и восстанавливается при любой ошибке.
"""
from __future__ import annotations

import json
import shutil
import ssl
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

from . import log
from .lists import ListManager
from .paths import (API_LATEST, API_TAG, APP_NAME, APP_VERSION, BACKUP_DIR, RELEASE_ZIP, TEMP_DIR,
                    USER_STRATEGIES_DIR, VERSION_URL, ZAPRET_DIR)
from .zapret import Zapret, ZapretError, read_version, version_tuple

UA = f"{APP_NAME}/{APP_VERSION}"
Progress = Callable[[int, int], None]


def http_get(url: str, timeout: float = 10, accept: str = "*/*") -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept,
                                               "Cache-Control": "no-cache"})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read()


def download(url: str, dest: Path, progress: Optional[Progress] = None, timeout: float = 30) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r, \
            open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(64 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    tmp.replace(dest)


def latest_version() -> str:
    """Версия из .service/version.txt репозитория, запасной путь — GitHub API."""
    errors = []
    try:
        v = http_get(f"{VERSION_URL}?t={int(time.time())}", timeout=6).decode().strip()
        if v and len(v) < 32:
            return v
    except Exception as e:  # noqa: BLE001
        errors.append(str(e))
    try:
        data = json.loads(http_get(API_LATEST, timeout=8, accept="application/vnd.github+json"))
        return str(data.get("tag_name", "")).strip()
    except Exception as e:  # noqa: BLE001
        errors.append(str(e))
    raise ZapretError("не удалось получить версию с GitHub: " + "; ".join(errors))


def release_zip_url(tag: str) -> str:
    try:
        data = json.loads(http_get(API_TAG.format(tag=tag), timeout=8, accept="application/vnd.github+json"))
        for a in data.get("assets", []):
            if a.get("name", "").lower().endswith(".zip"):
                return a["browser_download_url"]
    except Exception:
        pass
    return RELEASE_ZIP.format(tag=tag)


def is_newer(remote: str, local: str) -> bool:
    if not local:
        return True
    try:
        return version_tuple(remote) > version_tuple(local)
    except Exception:
        return remote.strip() != local.strip()


# ---------------------------------------------------------------- установка
def _find_root(extracted: Path) -> Path:
    for p in [extracted, *extracted.rglob("*")]:
        if p.is_dir() and (p / "service.bat").exists() and (p / "bin" / "winws.exe").exists():
            return p
    raise ZapretError("в архиве не найден zapret (нет service.bat или bin/winws.exe)")


def extract_zip(zip_path: Path) -> Path:
    dest = TEMP_DIR / f"extract-{int(time.time())}"
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest)
    return _find_root(dest)


class Snapshot:
    """Пользовательское состояние старой установки."""

    def __init__(self, zap: Zapret):
        self.version = zap.version()
        self.game_filter = zap.game_filter() if zap.root.exists() else None
        self.ipset_mode = zap.ipset_mode() if zap.lists_dir.exists() else "none"
        self.fakes = zap.fakes()["active"] if zap.bin_dir.exists() else {}
        st = zap.status()
        self.service_installed = st.service_exists and not st.service_foreign_path
        self.service_strategy = st.service_strategy
        self.service_running = st.service_state == "RUNNING"
        self.session_strategy = st.strategy if st.source in ("app", "external") else ""
        self.user_files: dict[str, bytes] = {}
        self.ipset_list: Optional[bytes] = None
        if zap.lists_dir.exists():
            for f in zap.lists_dir.iterdir():
                if f.is_file() and f.name.endswith("-user.txt"):
                    self.user_files[f.name] = f.read_bytes()
            for name in ("ipset-all.txt.backup", "ipset-all.txt"):
                f = zap.lists_dir / name
                if f.exists() and f.stat().st_size > 1000:
                    self.ipset_list = f.read_bytes()
                    break


def install_release(src_root: Path, zap: Zapret, lists_overrides: Optional[Path] = None,
                    progress_text: Callable[[str], None] = lambda s: None) -> dict:
    """Ставит zapret из распакованной папки src_root в zap.root c переносом настроек."""
    target = zap.root
    had_old = zap.installed()
    snap = Snapshot(zap) if had_old else None
    extra_lists: dict[str, bytes] = {}
    if had_old:
        new_names = {p.name for p in (src_root / "lists").glob("*")}
        for f in zap.lists_dir.iterdir():
            if f.is_file() and f.name not in new_names and not f.name.endswith("-user.txt") \
                    and not f.name.startswith("ipset-all"):
                extra_lists[f.name] = f.read_bytes()

    progress_text("Остановка обхода...")
    zap.stop_all()

    backup = None
    if target.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        backup = BACKUP_DIR / f"zapret-{(snap.version if snap else 'old') or 'old'}-{time.strftime('%Y%m%d-%H%M%S')}"
        progress_text("Резервная копия текущей версии...")
        for attempt in range(5):
            try:
                shutil.move(str(target), str(backup))
                break
            except OSError:
                time.sleep(1)
                zap.stop_process()
        else:
            raise ZapretError("не удалось переместить старую папку zapret (файлы заняты?)")
    try:
        progress_text("Копирование файлов...")
        shutil.copytree(src_root, target)
        zap.root = target
        zap.disable_bat_update_check()
        zap.ensure_user_lists()
        _copy_user_strategies(target)
        if snap:
            progress_text("Перенос пользовательских настроек...")
            _migrate(snap, zap, extra_lists)
        if lists_overrides is not None:
            ListManager(zap.lists_dir, lists_overrides).reapply_overrides()
        else:
            ListManager(zap.lists_dir).reapply_overrides()
        restored = _restore_state(snap, zap) if snap else ""
    except Exception as e:
        log.error(f"Ошибка установки, откат: {e}")
        shutil.rmtree(target, ignore_errors=True)
        if backup and backup.exists():
            shutil.move(str(backup), str(target))
            zap.root = target
            if snap:
                try:
                    _restore_state(snap, zap)
                except Exception:
                    pass
        raise
    _cleanup_backups()
    shutil.rmtree(src_root.parent if src_root.parent.name.startswith("extract-") else src_root,
                  ignore_errors=True)
    return {"version": zap.version(), "restored": restored}


def _copy_user_strategies(target: Path) -> None:
    USER_STRATEGIES_DIR.mkdir(parents=True, exist_ok=True)
    for f in USER_STRATEGIES_DIR.glob("*.bat"):
        shutil.copyfile(f, target / f.name)


def _migrate(snap: Snapshot, zap: Zapret, extra_lists: dict[str, bytes]) -> None:
    for name, data in snap.user_files.items():
        (zap.lists_dir / name).write_bytes(data)
    for name, data in extra_lists.items():
        (zap.lists_dir / name).write_bytes(data)
    if snap.game_filter and snap.game_filter["mode"] != "disabled":
        gf = snap.game_filter
        zap.set_game_filter(gf["mode"], gf["tcp"], gf["udp"])
    # IPSet: в новой версии список может быть другим — сохраняем режим пользователя
    try:
        cur = zap.ipset_mode()
        if snap.ipset_list and cur != "loaded" and not (zap.lists_dir / "ipset-all.txt.backup").exists():
            (zap.lists_dir / "ipset-all.txt.backup").write_bytes(snap.ipset_list)
        if cur != snap.ipset_mode:
            zap.set_ipset_mode(snap.ipset_mode)
    except ZapretError as e:
        log.warn(f"IPSet: {e}")
    for kind, name in snap.fakes.items():
        if name and (zap.bin_dir / f"{name}.bin").exists():
            try:
                zap.set_fake(kind, name)
            except ZapretError:
                pass


def _restore_state(snap: Snapshot, zap: Zapret) -> str:
    def pick(name: str):
        return zap.strategy(name) or zap.strategy("general")

    if snap.service_installed and snap.service_strategy:
        s = pick(snap.service_strategy)
        if s:
            zap.service_install(s)
            return f"служба ({s.name})"
    if snap.session_strategy:
        s = pick(snap.session_strategy)
        if s:
            zap.start(s)
            return f"сеанс ({s.name})"
    return ""


def _cleanup_backups(keep: int = 2) -> None:
    items = sorted((p for p in BACKUP_DIR.glob("zapret-*") if p.is_dir()), key=lambda p: p.stat().st_mtime)
    for p in items[:-keep]:
        shutil.rmtree(p, ignore_errors=True)


def download_and_install(tag: str, zap: Zapret, progress: Optional[Progress] = None,
                         progress_text: Callable[[str], None] = lambda s: None) -> dict:
    url = release_zip_url(tag)
    zpath = TEMP_DIR / f"zapret-{tag}.zip"
    progress_text(f"Загрузка zapret {tag}...")
    download(url, zpath, progress)
    progress_text("Распаковка...")
    root = extract_zip(zpath)
    try:
        return install_release(root, zap, progress_text=progress_text)
    finally:
        zpath.unlink(missing_ok=True)


def install_from_zip(zip_path: Path, zap: Zapret, progress_text=lambda s: None) -> dict:
    root = extract_zip(zip_path)
    return install_release(root, zap, progress_text=progress_text)


def install_from_folder(folder: Path, zap: Zapret, progress_text=lambda s: None) -> dict:
    src = _find_root(folder)
    tmp = TEMP_DIR / f"extract-{int(time.time())}" / "zapret"
    shutil.copytree(src, tmp, ignore=shutil.ignore_patterns(".git"))
    # пользовательские файлы из импортируемой папки тоже сохраняем
    return install_release(tmp, zap, progress_text=progress_text)


def current_version(root: Path = ZAPRET_DIR) -> str:
    return read_version(root)
