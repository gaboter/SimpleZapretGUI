"""Низкоуровневые помощники для Windows.

Все внешние команды запускаются без окон консоли (CREATE_NO_WINDOW), чтобы пользователь
ничего не видел. На не-Windows системах функции возвращают безопасные значения —
это позволяет разрабатывать и тестировать интерфейс где угодно.
"""
from __future__ import annotations

import ctypes
import locale
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Optional

from .paths import IS_WINDOWS

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008

try:  # psutil обязателен в сборке, но не ломаемся без него при разработке
    import psutil  # type: ignore
except Exception:  # pragma: no cover
    psutil = None


def _decode(data: bytes) -> str:
    if not data:
        return ""
    for enc in ("utf-8", "cp866", locale.getpreferredencoding(False) or "cp1251"):
        try:
            return data.decode(enc)
        except Exception:
            continue
    return data.decode("utf-8", errors="replace")


def startupinfo():
    if not IS_WINDOWS:
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    return si


def run(cmd, timeout: float = 30, input_text: Optional[str] = None) -> tuple[int, str]:
    """Запустить команду скрыто. Возвращает (код, объединённый вывод)."""
    if not IS_WINDOWS:
        return 1, "not windows"
    try:
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
            input=input_text.encode() if input_text is not None else None,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
            startupinfo=startupinfo(),
            shell=isinstance(cmd, str),
        )
        return p.returncode, _decode(p.stdout)
    except subprocess.TimeoutExpired:
        return -1, "timeout"
    except Exception as e:  # noqa: BLE001
        return -2, str(e)


def powershell(script: str, timeout: float = 30) -> tuple[int, str]:
    return run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-Command", script], timeout=timeout)


# ---------------------------------------------------------------- права администратора
def is_admin() -> bool:
    if not IS_WINDOWS:
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin(exe: str, params: str, cwd: Optional[str] = None) -> bool:
    if not IS_WINDOWS:
        return False
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, cwd, 1)
    return rc > 32


_mutex_handle = None


def create_mutex(name: str) -> None:
    """Именованный мьютекс — по нему установщик понимает, что приложение запущено."""
    global _mutex_handle
    if IS_WINDOWS:
        _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, False, name)


# ---------------------------------------------------------------- службы
@dataclass
class ServiceInfo:
    exists: bool
    state: str = ""          # RUNNING / STOPPED / STOP_PENDING / START_PENDING
    bin_path: str = ""

    @property
    def running(self) -> bool:
        return self.state == "RUNNING"


def service_query(name: str) -> ServiceInfo:
    if not IS_WINDOWS:
        return ServiceInfo(False)
    rc, out = run(["sc", "query", name], timeout=10)
    if rc != 0 and "1060" in out:
        return ServiceInfo(False)
    m = re.search(r"STATE\s*:\s*\d+\s+(\w+)", out)
    if not m:
        # локализованный вывод без ключевых слов — пробуем через psutil
        if psutil:
            try:
                s = psutil.win_service_get(name).as_dict()
                return ServiceInfo(True, s.get("status", "").upper(), s.get("binpath", ""))
            except Exception:
                return ServiceInfo(False)
        return ServiceInfo(rc == 0)
    info = ServiceInfo(True, m.group(1).upper())
    rc2, out2 = run(["sc", "qc", name], timeout=10)
    m2 = re.search(r"BINARY_PATH_NAME\s*:\s*(.+)", out2)
    if m2:
        info.bin_path = m2.group(1).strip()
    return info


def service_stop(name: str) -> None:
    run(["net", "stop", name], timeout=30)


def service_delete(name: str) -> None:
    run(["sc", "delete", name], timeout=15)


def list_services() -> list[tuple[str, str]]:
    """[(имя, отображаемое имя)] всех служб Win32."""
    if not IS_WINDOWS or not psutil:
        return []
    res = []
    try:
        for s in psutil.win_service_iter():
            try:
                res.append((s.name(), s.display_name()))
            except Exception:
                pass
    except Exception:
        pass
    return res


# ---------------------------------------------------------------- реестр
def reg_read(root: str, key: str, value: str) -> Optional[str]:
    if not IS_WINDOWS:
        return None
    import winreg
    hive = {"HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCU": winreg.HKEY_CURRENT_USER}[root]
    try:
        with winreg.OpenKey(hive, key) as k:
            v, _ = winreg.QueryValueEx(k, value)
            return str(v)
    except OSError:
        return None


def reg_write(root: str, key: str, value: str, data: str) -> bool:
    if not IS_WINDOWS:
        return False
    import winreg
    hive = {"HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCU": winreg.HKEY_CURRENT_USER}[root]
    try:
        with winreg.CreateKeyEx(hive, key, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, value, 0, winreg.REG_SZ, data)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- процессы
@dataclass
class ProcInfo:
    pid: int
    name: str
    cmdline: str
    exe: str
    parent: str
    create_time: float


def find_processes(image: str) -> list[ProcInfo]:
    res: list[ProcInfo] = []
    if not psutil:
        return res
    image = image.lower()
    for p in psutil.process_iter(["pid", "name", "exe", "create_time"]):
        try:
            if (p.info["name"] or "").lower() != image:
                continue
            try:
                cmd = subprocess.list2cmdline(p.cmdline())
            except Exception:
                cmd = ""
            try:
                parent = (p.parent().name() if p.parent() else "") or ""
            except Exception:
                parent = ""
            res.append(ProcInfo(p.info["pid"], p.info["name"], cmd, p.info.get("exe") or "",
                                parent, p.info.get("create_time") or 0))
        except Exception:
            continue
    return res


def process_running(image: str) -> bool:
    return bool(find_processes(image))


def kill_image(image: str) -> None:
    if psutil:
        for pi in find_processes(image):
            try:
                psutil.Process(pi.pid).kill()
            except Exception:
                pass
        if not find_processes(image):
            return
    if IS_WINDOWS:
        run(["taskkill", "/IM", image, "/F"], timeout=10)


def open_path(path: str) -> None:
    if IS_WINDOWS:
        os.startfile(path)  # type: ignore[attr-defined]


# ---------------------------------------------------------------- TCP timestamps (RFC 1323)
_TS_ON = ("enabled", "включ", "allowed")
_TS_OFF = ("disabled", "отключ", "выключ")


def _parse_ts(out: str) -> Optional[bool]:
    """Разбор `netsh interface tcp show global` на любом языке Windows.

    Строка с параметром всегда содержит «RFC 1323» («RFC 1323 Timestamps» в английской
    Windows, «Отметки времени RFC 1323» в русской), значение идёт после двоеточия.
    """
    for ln in out.splitlines():
        if "1323" not in ln or ":" not in ln:
            continue
        v = ln.split(":", 1)[1].strip().lower()
        if any(w in v for w in _TS_OFF):
            return False
        if any(w in v for w in _TS_ON):
            return True
    return None


def tcp_timestamps() -> Optional[bool]:
    """True/False — включены ли TCP timestamps; None — определить не удалось."""
    if not IS_WINDOWS:
        return None
    rc, netsh_out = run(["netsh", "interface", "tcp", "show", "global"], timeout=10)
    state = _parse_ts(netsh_out)
    if state is not None:
        return state
    # запасной способ, не зависящий от языка системы
    rc, out = powershell("(Get-NetTCPSetting -SettingName Internet -ErrorAction SilentlyContinue).Timestamps",
                         timeout=20)
    v = out.strip().lower()
    if v.startswith("enabled"):
        return True
    if v.startswith("disabled"):
        return False
    try:
        from . import log
        log.trace("TCP timestamps: состояние не распознано. netsh:\n" + netsh_out[:1500]
                  + "\nPowerShell: " + out[:300])
    except Exception:
        pass
    return None


def set_tcp_timestamps(on: bool) -> Optional[bool]:
    """Включить/выключить TCP timestamps. Возвращает состояние, прочитанное заново после изменения."""
    if not IS_WINDOWS:
        return None
    rc, out = run(["netsh", "interface", "tcp", "set", "global",
                   f"timestamps={'enabled' if on else 'disabled'}"], timeout=15)
    state = tcp_timestamps()
    if state is not on:
        # на некоторых сборках netsh не меняет шаблон Internet — пробуем через PowerShell
        powershell(f"Set-NetTCPSetting -SettingName Internet -Timestamps {'Enabled' if on else 'Disabled'} "
                   "-ErrorAction SilentlyContinue", timeout=20)
        state = tcp_timestamps()
    return state
