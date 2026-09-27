"""Обновление самого SimpleZapretGUI из релизов на GitHub.

Последний релиз берётся из GitHub API. Если его версия новее текущей, приложение скачивает
установщик SimpleZapretGUI-Setup-X.Y.Z.exe, проверяет контрольную сумму (если GitHub её отдаёт),
запускает установку в тихом режиме и закрывается. Установщик обновляет файлы программы,
не трогая папку data, и сам снова запускает приложение.
"""
from __future__ import annotations

import hashlib
import json
import re
import ssl
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .paths import APP_REPO_URL, APP_VERSION, IS_WINDOWS, LOG_DIR, TEMP_DIR
from .updater import download, http_get
from .zapret import version_tuple


APP_API_LATEST = APP_REPO_URL.replace("https://github.com/", "https://api.github.com/repos/") + "/releases/latest"


@dataclass
class AppRelease:
    version: str          # 1.0.2
    page: str             # страница релиза на GitHub
    notes: str            # описание релиза
    asset_url: str        # ссылка на установщик
    asset_name: str
    size: int
    sha256: str           # может быть пустым — тогда проверяется только размер


def _latest_without_api() -> Optional[AppRelease]:
    """Запасной путь, если API недоступен (лимит 60 запросов/час на IP — у провайдеров с общим
    IP он кончается быстро): страница releases/latest перенаправляет на тег последнего релиза."""
    req = urllib.request.Request(f"{APP_REPO_URL}/releases/latest", method="HEAD",
                                 headers={"User-Agent": "SimpleZapretGUI"})
    with urllib.request.urlopen(req, timeout=10, context=ssl.create_default_context()) as r:
        final = r.geturl()
    m = re.search(r"/releases/tag/v?([0-9][0-9.]*)$", final)
    if not m:
        return None                            # релизов нет — страница без тега
    ver = m.group(1)
    name = f"SimpleZapretGUI-Setup-{ver}.exe"
    return AppRelease(ver, final, "", f"{APP_REPO_URL}/releases/download/v{ver}/{name}", name, 0, "")


def latest_release() -> Optional[AppRelease]:
    """Последний релиз с установщиком или None (релизов нет / установщик не приложен)."""
    try:
        data = json.loads(http_get(APP_API_LATEST, timeout=8, accept="application/vnd.github+json"))
    except Exception as e:  # noqa: BLE001
        if "404" in str(e):
            return None                        # релизов ещё нет
        return _latest_without_api()
    ver = str(data.get("tag_name", "")).strip().lstrip("vV")
    if not ver:
        return None
    for a in data.get("assets", []):
        name = a.get("name", "")
        if name.lower().endswith(".exe") and "setup" in name.lower():
            digest = str(a.get("digest") or "")
            return AppRelease(ver, data.get("html_url", ""), (data.get("body") or "").strip(),
                              a.get("browser_download_url", ""), name, int(a.get("size") or 0),
                              digest.split(":", 1)[1] if digest.startswith("sha256:") else "")
    return None


def is_newer(remote: str, local: str = APP_VERSION) -> bool:
    try:
        return version_tuple(remote) > version_tuple(local)
    except Exception:
        return False


def download_installer(rel: AppRelease, progress: Optional[Callable[[int, int], None]] = None) -> Path:
    dest = TEMP_DIR / rel.asset_name
    download(rel.asset_url, dest, progress, timeout=60)
    size = dest.stat().st_size
    if size < 1_000_000 or (rel.size and size != rel.size):
        dest.unlink(missing_ok=True)
        raise RuntimeError("установщик скачался не полностью — попробуйте ещё раз")
    if rel.sha256:
        h = hashlib.sha256()
        with open(dest, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest().lower() != rel.sha256.lower():
            dest.unlink(missing_ok=True)
            raise RuntimeError("контрольная сумма установщика не совпала — файл повреждён")
    return dest


def run_installer(path: Path) -> None:
    """Тихая установка поверх текущей версии; установщик сам перезапустит программу."""
    if not IS_WINDOWS:
        raise RuntimeError("обновление возможно только в Windows")
    args = [str(path), "/SILENT", "/SP-", "/NOCANCEL", "/NORESTART", "/CLOSEAPPLICATIONS",
            f"/LOG={LOG_DIR / 'update.log'}"]
    subprocess.Popen(args, close_fds=True,
                     creationflags=0x00000008 | 0x00000200)   # DETACHED_PROCESS | NEW_PROCESS_GROUP
