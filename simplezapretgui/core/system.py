"""Автозапуск приложения и очистка системы при удалении."""
from __future__ import annotations

from . import winutil
from .diagnostics import hosts_remove
from .paths import IS_WINDOWS, SERVICE_NAME, TASK_NAME, app_executable
from .zapret import Zapret


def set_autostart(enabled: bool) -> bool:
    """Задача планировщика с наивысшими правами: приложение стартует свёрнутым без запроса UAC."""
    if not IS_WINDOWS:
        return False
    if not enabled:
        winutil.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], timeout=15)
        return True
    exe = app_executable()
    tr = f'{exe} --tray' if exe.startswith('"') else f'"{exe}" --tray'
    rc, out = winutil.run(["schtasks", "/Create", "/TN", TASK_NAME, "/TR", tr, "/SC", "ONLOGON",
                           "/RL", "HIGHEST", "/F", "/DELAY", "0000:10"], timeout=15)
    return rc == 0


def autostart_enabled() -> bool:
    if not IS_WINDOWS:
        return False
    rc, _ = winutil.run(["schtasks", "/Query", "/TN", TASK_NAME], timeout=10)
    return rc == 0


def uninstall_cleanup(remove_hosts: bool = True) -> None:
    """Вызывается деинсталлятором: служба, winws, драйвер, задача, блок в hosts.

    Трогаем только то, что относится к копии zapret приложения: служба zapret,
    установленная из другой папки, остаётся на месте.
    """
    z = Zapret()
    root = str(z.root).lower()
    svc = winutil.service_query(SERVICE_NAME)
    ours = svc.exists and root in svc.bin_path.lower()
    try:
        if ours:
            z.service_remove()
        else:
            for p in winutil.find_processes("winws.exe"):
                if root in (p.exe or "").lower() and winutil.psutil:
                    try:
                        winutil.psutil.Process(p.pid).kill()
                    except Exception:
                        pass
            if not winutil.process_running("winws.exe"):
                for drv in ("WinDivert", "WinDivert14"):
                    winutil.service_stop(drv)
                    winutil.service_delete(drv)
    except Exception:
        pass
    set_autostart(False)
    if remove_hosts:
        try:
            hosts_remove()
        except Exception:
            pass
