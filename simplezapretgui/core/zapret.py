"""Работа с установленной копией zapret-discord-youtube.

Повторяет логику service.bat (установка/удаление службы, Game Filter, IPSet, фейки,
пользовательские списки), но без консольных окон и без открытия браузера.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import log, winutil
from .paths import (IS_WINDOWS, SERVICE_NAME, SERVICE_REG_KEY, SERVICE_REG_VALUE,
                    USER_STRATEGIES_DIR, ZAPRET_DIR)
from .strategy import (ResolveContext, Strategy, load_strategy, natural_key, normalize_cmd,
                       resolve)

IPSET_NONE_MARK = "203.0.113.113/32"
CONFLICT_SERVICES = ("GoodbyeDPI", "discordfix_zapret", "winws1", "winws2")


class ZapretError(Exception):
    pass


@dataclass
class Status:
    installed: bool = False
    version: str = ""
    winws_pids: list = field(default_factory=list)
    running: bool = False
    source: str = ""                  # app | service | external
    strategy: str = ""
    started_at: float = 0.0
    service_exists: bool = False
    service_state: str = ""
    service_strategy: str = ""
    service_foreign_path: str = ""    # служба zapret установлена из другой папки
    windivert_state: str = ""
    conflicts: list = field(default_factory=list)
    issues: list = field(default_factory=list)     # [(код, текст)] — предупреждения для главной


class Zapret:
    def __init__(self, root: Path = ZAPRET_DIR):
        self.root = root
        self._proc: Optional[subprocess.Popen] = None
        self._proc_strategy = ""
        self._proc_output: list[str] = []
        self._lock = threading.RLock()
        self.auto_tcp_ts = True        # включать TCP timestamps перед запуском (как service.bat)

    # ------------------------------------------------------------ базовое
    @property
    def bin_dir(self) -> Path:
        return self.root / "bin"

    @property
    def lists_dir(self) -> Path:
        return self.root / "lists"

    @property
    def utils_dir(self) -> Path:
        return self.root / "utils"

    @property
    def winws(self) -> Path:
        return self.bin_dir / "winws.exe"

    def installed(self) -> bool:
        return (self.root / "service.bat").exists() and self.winws.exists()

    def version(self) -> str:
        return read_version(self.root)

    # ------------------------------------------------------------ стратегии
    def strategies(self) -> list[Strategy]:
        if not self.root.exists():
            return []
        user_names = {p.stem.lower() for p in USER_STRATEGIES_DIR.glob("*.bat")}
        res = []
        for p in self.root.glob("*.bat"):
            if p.name.lower().startswith("service"):
                continue
            res.append(load_strategy(p, user=p.stem.lower() in user_names))
        res.sort(key=lambda s: natural_key(s.name))
        return res

    def strategy(self, name: str) -> Optional[Strategy]:
        p = self.root / f"{name}.bat"
        if not p.exists():
            return None
        user = (USER_STRATEGIES_DIR / p.name).exists()
        return load_strategy(p, user)

    def context(self) -> ResolveContext:
        gf = self.game_filter()
        return ResolveContext(self.root, gf["tcp_value"], gf["udp_value"])

    def command_args(self, s: Strategy) -> str:
        if s.error:
            raise ZapretError(f"стратегия «{s.name}» повреждена: {s.error}")
        return resolve(s.template, self.context())

    # ------------------------------------------------------------ подготовка
    def ensure_user_lists(self) -> None:
        """Аналог :load_user_lists — пользовательские списки не должны быть пустыми."""
        L = self.lists_dir
        L.mkdir(parents=True, exist_ok=True)
        defaults = {
            "ipset-exclude-user.txt": "203.0.113.113/32\r\n",
            "list-general-user.txt": "# Never leave this file empty\r\ndomain.example.abc\r\n",
            "list-exclude-user.txt": "domain.example.abc\r\n",
        }
        for name, content in defaults.items():
            f = L / name
            if not f.exists() or not f.read_text(encoding="utf-8", errors="ignore").strip():
                f.write_text(content, encoding="utf-8", newline="")

    def disable_bat_update_check(self) -> None:
        """Убирает флаг check_updates.enabled: батники больше не откроют браузер."""
        f = self.utils_dir / "check_updates.enabled"
        if f.exists():
            try:
                f.unlink()
                log.info("Проверка обновлений в батниках отключена — её выполняет приложение")
            except OSError:
                pass

    def bat_update_check_enabled(self) -> bool:
        return (self.utils_dir / "check_updates.enabled").exists()

    def tcp_enable(self) -> None:
        """Как service.bat: перед запуском включить TCP timestamps (нужны стратегиям с fooling=ts).

        Отключается настройкой «Включать TCP timestamps при запуске обхода».
        """
        if not IS_WINDOWS or not self.auto_tcp_ts:
            return
        if winutil.tcp_timestamps() is not True:
            state = winutil.set_tcp_timestamps(True)
            if state is True:
                log.info("TCP timestamps включены")
            else:
                log.warn("Не удалось включить TCP timestamps — Windows не применила изменение")

    # ------------------------------------------------------------ запуск / остановка
    def start(self, s: Strategy, wait: float = 2.0) -> None:
        if not self.installed():
            raise ZapretError("zapret не установлен")
        args = self.command_args(s)
        with self._lock:
            svc = winutil.service_query(SERVICE_NAME)
            if svc.running:
                raise ZapretError("служба zapret уже работает — остановите её или используйте режим «Служба»")
            self.stop_process()
            self.ensure_user_lists()
            self.tcp_enable()
            if not IS_WINDOWS:
                raise ZapretError("запуск winws возможен только в Windows")
            cmdline = f'"{self.winws}" {args}'
            self._proc_output = []
            try:
                self._proc = subprocess.Popen(
                    cmdline, cwd=str(self.bin_dir),
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                    creationflags=winutil.CREATE_NO_WINDOW, startupinfo=winutil.startupinfo())
            except OSError as e:
                raise ZapretError(f"не удалось запустить winws.exe: {e}") from e
            self._proc_strategy = s.name
            threading.Thread(target=self._reader, args=(self._proc,), daemon=True).start()
        t0 = time.time()
        while time.time() - t0 < wait:
            if self._proc.poll() is not None:
                out = "\n".join(self._proc_output[-15:]).strip()
                self._proc = None
                raise ZapretError("winws.exe завершился сразу после запуска"
                                  + (f":\n{out}" if out else " (возможен конфликт с другим обходом или антивирусом)"))
            time.sleep(0.1)

    def start_raw(self, args: list[str], wait: float = 0.6, name: str = "подбор") -> None:
        """Запустить winws с готовым списком параметров (для подбора стратегии)."""
        if not IS_WINDOWS:
            raise ZapretError("запуск winws возможен только в Windows")
        with self._lock:
            self.stop_process()
            self._proc_output = []
            try:
                self._proc = subprocess.Popen(
                    [str(self.winws), *args], cwd=str(self.bin_dir),
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                    creationflags=winutil.CREATE_NO_WINDOW, startupinfo=winutil.startupinfo())
            except OSError as e:
                raise ZapretError(f"не удалось запустить winws.exe: {e}") from e
            self._proc_strategy = name
            threading.Thread(target=self._reader, args=(self._proc,), kwargs={"quiet": True},
                             daemon=True).start()
        t0 = time.time()
        while time.time() - t0 < wait:
            if self._proc is None or self._proc.poll() is not None:
                out = " ".join(self._proc_output[-3:]).strip()
                self._proc = None
                raise ZapretError(out or "winws не принял параметры")
            time.sleep(0.05)

    def wait_ready(self, timeout: float = 4.0) -> bool:
        """Дождаться, пока winws начнёт перехват («windivert initialized. capture is started.»).

        Старые сборки могут не писать эту строку — тогда просто ждём timeout, пока процесс жив.
        """
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self._proc is None or self._proc.poll() is not None:
                return False
            if any("capture is started" in ln or "windivert initialized" in ln for ln in self._proc_output):
                return True
            time.sleep(0.05)
        return self._proc is not None and self._proc.poll() is None

    def _reader(self, proc: subprocess.Popen, quiet: bool = False) -> None:
        try:
            for raw in iter(proc.stdout.readline, b""):
                line = winutil._decode(raw).rstrip()
                if line:
                    self._proc_output.append(line)
                    del self._proc_output[:-300]
                    low = line.lower()
                    if not quiet and ("error" in low or "could not" in low or "failed" in low):
                        log.error(f"winws: {line}")
        except Exception:
            pass
        code = proc.poll()
        if proc is self._proc and code is not None and not quiet:
            log.error(f"winws.exe неожиданно завершился (код {code})")

    def process_output(self) -> list[str]:
        return list(self._proc_output)

    def own_process_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def stop_process(self, unload_driver: bool = False) -> None:
        """Остановить winws.exe. unload_driver — ещё и выгрузить WinDivert, как это делает service.bat."""
        with self._lock:
            if self._proc is not None:
                p, self._proc = self._proc, None
                try:
                    p.kill()
                    p.wait(3)
                except Exception:
                    pass
            winutil.kill_image("winws.exe")
            if unload_driver:
                self.unload_driver()

    def unload_driver(self) -> bool:
        """Выгрузить драйвер WinDivert, если им больше никто не пользуется."""
        if winutil.process_running("winws.exe"):
            return False
        done = False
        for drv in ("WinDivert", "WinDivert14"):
            if winutil.service_query(drv).exists:
                winutil.service_stop(drv)
                winutil.service_delete(drv)
                done = True
        return done

    def stop_all(self, unload_driver: bool = True) -> None:
        """Полная остановка: процесс, служба zapret (остаётся установленной), WinDivert."""
        svc = winutil.service_query(SERVICE_NAME)
        if svc.running:
            winutil.service_stop(SERVICE_NAME)
        self.stop_process(unload_driver)

    def shutdown_everything(self) -> str:
        """«Выключить всё»: любая служба zapret, все winws.exe, конфликтующие обходы, драйвер WinDivert.

        Службы не удаляются (кроме драйвера WinDivert, который winws создаёт заново при запуске),
        только останавливаются.
        """
        done = []
        svc = winutil.service_query(SERVICE_NAME)
        if svc.running or svc.state == "STOP_PENDING":
            winutil.service_stop(SERVICE_NAME)
            done.append("служба zapret остановлена")
        if winutil.process_running("winws.exe"):
            done.append("winws.exe завершён")
        with self._lock:
            self._proc = None
        winutil.kill_image("winws.exe")
        existing = {n.lower(): n for n, _ in winutil.list_services()}
        for c in CONFLICT_SERVICES:
            if c.lower() in existing:
                winutil.service_stop(existing[c.lower()])
                done.append(f"служба {existing[c.lower()]} остановлена")
        if self.unload_driver():
            done.append("драйвер WinDivert выгружен")
        return ", ".join(done) or "нечего выключать"

    # ------------------------------------------------------------ служба
    def service_install(self, s: Strategy) -> None:
        args = self.command_args(s)
        self.ensure_user_lists()
        self.tcp_enable()
        self.stop_process()
        winutil.service_stop(SERVICE_NAME)
        winutil.service_delete(SERVICE_NAME)
        time.sleep(0.5)
        bin_path = f'"{self.winws}" {args}'
        rc, out = winutil.run(["sc", "create", SERVICE_NAME, "binPath=", bin_path,
                               "DisplayName=", "zapret", "start=", "auto"], timeout=20)
        if rc != 0:
            raise ZapretError(f"не удалось создать службу: {out.strip()}")
        winutil.run(["sc", "description", SERVICE_NAME, "Zapret DPI bypass software"], timeout=10)
        winutil.reg_write("HKLM", SERVICE_REG_KEY, SERVICE_REG_VALUE, s.name)
        rc, out = winutil.run(["sc", "start", SERVICE_NAME], timeout=20)
        if rc != 0:
            raise ZapretError(f"служба создана, но не запустилась: {out.strip()}")

    def service_start(self) -> None:
        self.stop_process()
        rc, out = winutil.run(["sc", "start", SERVICE_NAME], timeout=20)
        if rc != 0 and "1056" not in out:
            raise ZapretError(f"служба не запустилась: {out.strip()}")

    def service_remove(self) -> None:
        """Аналог «Remove Services»: служба zapret, процесс winws, драйвер WinDivert."""
        svc = winutil.service_query(SERVICE_NAME)
        if svc.exists:
            winutil.service_stop(SERVICE_NAME)
            winutil.service_delete(SERVICE_NAME)
        self.stop_process()
        for drv in ("WinDivert", "WinDivert14"):
            if winutil.service_query(drv).exists:
                winutil.service_stop(drv)
                winutil.service_delete(drv)

    def service_strategy(self) -> str:
        return winutil.reg_read("HKLM", SERVICE_REG_KEY, SERVICE_REG_VALUE) or ""

    # ------------------------------------------------------------ Game Filter
    def game_filter(self) -> dict:
        f = self.utils_dir / "game_filter.enabled"
        mode, tcp_r, udp_r = "disabled", "1024-65535", "1024-65535"
        if f.exists():
            for ln in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                k, _, v = ln.strip().partition("=")
                k = k.strip().lower()
                v = v.strip()
                if k == "mode" and v:
                    mode = v.lower()
                elif k == "all":
                    mode = "all"
                elif k == "tcp":
                    if not v:
                        mode = "tcp"
                    elif valid_ports(v):
                        tcp_r = v
                elif k == "udp":
                    if not v:
                        mode = "udp"
                    elif valid_ports(v):
                        udp_r = v
        if mode not in ("all", "tcp", "udp"):
            mode = "disabled"
        tcp_v = tcp_r if mode in ("all", "tcp") else "12"
        udp_v = udp_r if mode in ("all", "udp") else "12"
        return {"mode": mode, "tcp": tcp_r, "udp": udp_r, "tcp_value": tcp_v, "udp_value": udp_v}

    def set_game_filter(self, mode: str, tcp: str, udp: str) -> None:
        tcp = tcp.replace(" ", "") or "1024-65535"
        udp = udp.replace(" ", "") or "1024-65535"
        if not valid_ports(tcp) or not valid_ports(udp):
            raise ZapretError("неверный формат портов (пример: 1024-1934,1936-65535)")
        self.utils_dir.mkdir(parents=True, exist_ok=True)
        (self.utils_dir / "game_filter.enabled").write_text(
            f"mode={mode}\r\ntcp={tcp}\r\nudp={udp}\r\n", encoding="utf-8", newline="")

    # ------------------------------------------------------------ IPSet
    def ipset_mode(self) -> str:
        f = self.lists_dir / "ipset-all.txt"
        if not f.exists():
            return "none"
        raw = f.read_text(encoding="utf-8", errors="ignore")
        if not raw.strip():
            return "any"
        if IPSET_NONE_MARK in raw:
            return "none"
        return "loaded"

    def set_ipset_mode(self, target: str) -> None:
        f = self.lists_dir / "ipset-all.txt"
        backup = self.lists_dir / "ipset-all.txt.backup"
        cur = self.ipset_mode()
        if cur == target:
            return
        if cur == "loaded":
            shutil.copyfile(f, backup)
        if target == "none":
            f.write_text(IPSET_NONE_MARK + "\r\n", encoding="utf-8", newline="")
        elif target == "any":
            f.write_text("", encoding="utf-8")
        elif target == "loaded":
            if not backup.exists():
                raise ZapretError("нет сохранённого списка IP — сначала обновите IPSet")
            shutil.copyfile(backup, f)

    def write_ipset_list(self, content: str) -> None:
        """Новый список IP: в файл, если режим loaded, иначе в резервную копию."""
        target = "ipset-all.txt" if self.ipset_mode() == "loaded" else "ipset-all.txt.backup"
        (self.lists_dir / target).write_text(content, encoding="utf-8")

    # ------------------------------------------------------------ фейки
    def fakes(self) -> dict:
        files = {}
        active = {"discord": "", "game": ""}
        if not self.bin_dir.exists():
            return {"files": files, "active": active}
        hashes = {}
        for p in sorted(self.bin_dir.glob("*.bin")):
            h = hashlib.sha256(p.read_bytes()).hexdigest()
            if p.stem.upper().startswith("ACTIVE_"):
                hashes[p.stem.upper()] = h
            else:
                files[p.stem] = h
        for key, fname in (("discord", "ACTIVE_DISCORD_UDP"), ("game", "ACTIVE_GAME_UDP")):
            for n, h in files.items():
                if h == hashes.get(fname):
                    active[key] = n
                    break
        return {"files": files, "active": active}

    def set_fake(self, kind: str, source: str) -> None:
        dst = self.bin_dir / ("ACTIVE_DISCORD_UDP.bin" if kind == "discord" else "ACTIVE_GAME_UDP.bin")
        src = self.bin_dir / f"{source}.bin"
        if not src.exists():
            raise ZapretError(f"файл {src.name} не найден")
        shutil.copyfile(src, dst)

    # ------------------------------------------------------------ статус
    def status(self) -> Status:
        st = Status(installed=self.installed(), version=self.version() if self.root.exists() else "")
        procs = winutil.find_processes("winws.exe")
        st.winws_pids = [p.pid for p in procs]
        st.running = bool(procs)
        svc = winutil.service_query(SERVICE_NAME)
        st.service_exists, st.service_state = svc.exists, svc.state
        if svc.exists:
            st.service_strategy = self.service_strategy()
            if svc.bin_path and str(self.root).lower() not in svc.bin_path.lower():
                st.service_foreign_path = svc.bin_path
        wd = winutil.service_query("WinDivert")
        st.windivert_state = wd.state if wd.exists else ""
        if procs:
            p = procs[0]
            st.started_at = p.create_time
            if self.own_process_alive():
                st.source, st.strategy = "app", self._proc_strategy
            elif svc.running:
                st.source, st.strategy = "service", st.service_strategy
            else:
                st.source = "external"
                st.strategy = self.match_cmdline(p.cmdline)
        existing = {n.lower() for n, _ in winutil.list_services()}
        st.conflicts = [c for c in CONFLICT_SERVICES if c.lower() in existing]
        if st.source == "external":
            st.issues.append(("external", "zapret запущен вне приложения"
                              + (f" (стратегия «{st.strategy}»)" if st.strategy else " (вручную через .bat)")
                              + ". Его можно выключить отсюда."))
        if st.service_foreign_path:
            st.issues.append(("foreign", "Служба zapret установлена из другой папки:\n"
                              + st.service_foreign_path[:160]))
        if svc.state == "STOP_PENDING":
            st.issues.append(("stop_pending", "Служба zapret зависла при остановке — вероятен конфликт "
                                              "с другим обходом."))
        if not procs and wd.exists and wd.state in ("RUNNING", "STOP_PENDING"):
            st.issues.append(("windivert", "Драйвер WinDivert активен без winws — возможно, работает "
                                           "другой обход или драйвер остался после прошлого запуска."))
        if st.conflicts:
            st.issues.append(("conflicts", "Найдены конфликтующие обходы: " + ", ".join(st.conflicts)))
        if st.installed and not (self.bin_dir / "WinDivert64.sys").exists():
            st.issues.append(("no_sys", "Файл WinDivert64.sys не найден — вероятно, его удалил антивирус."))
        return st

    def match_cmdline(self, cmdline: str) -> str:
        """Угадывает стратегию по командной строке уже работающего winws.exe."""
        target = normalize_cmd(cmdline)
        if not target:
            return ""
        best, best_score = "", 0.0
        ctx = self.context()
        for s in self.strategies():
            if s.error:
                continue
            cand = normalize_cmd(resolve(s.template, ctx))
            if cand == target:
                return s.name
            a, b = set(cand), set(target)
            score = len(a & b) / max(1, len(a | b))
            if score > best_score:
                best, best_score = s.name, score
        return best if best_score > 0.9 else ""


def valid_ports(spec: str) -> bool:
    spec = spec.replace(" ", "")
    if not spec:
        return False
    for item in spec.split(","):
        m = re.fullmatch(r"([1-9]\d{0,4})(?:-([1-9]\d{0,4}))?", item)
        if not m:
            return False
        a = int(m.group(1))
        b = int(m.group(2) or a)
        if a > 65535 or b > 65535 or a > b:
            return False
    return True


def read_version(root: Path) -> str:
    """Версия берётся из service.bat (LOCAL_VERSION) — релизные архивы не содержат .service."""
    sb = root / "service.bat"
    if sb.exists():
        m = re.search(r'set\s+"LOCAL_VERSION=([^"]+)"', sb.read_text(encoding="utf-8", errors="ignore"))
        if m:
            return m.group(1).strip()
    v = root / ".service" / "version.txt"
    if v.exists():
        return v.read_text(encoding="utf-8", errors="ignore").strip()
    return ""


def version_tuple(v: str) -> tuple:
    return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.\-]", v.strip().lstrip("vV")) if x)
