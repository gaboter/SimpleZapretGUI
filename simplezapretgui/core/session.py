"""Завершение работы Windows и «хвосты» после неё.

Когда Windows выключается, а обход запущен приложением (режим «Сеанс»), система убивает winws.exe,
но драйвер WinDivert остаётся загруженным — особенно при «быстром запуске» (гибридное выключение:
ядро засыпает вместе с загруженными драйверами). После следующего включения драйвер есть, а winws нет —
отсюда предупреждение «Драйвер WinDivert активен без winws».

Поэтому:
  • при завершении работы Windows приложение само останавливает свой winws и выгружает драйвер;
  • при запуске приложение выгружает драйвер, оставшийся от прошлого сеанса (если он точно ничей).

Служба zapret (режим «Служба») устроена так, чтобы переживать перезагрузку, — её не трогаем.
Чужие обходы (GoodbyeDPI и т. п.) тоже: их драйвер выгружать нельзя.
"""
from __future__ import annotations

import ctypes
import time

from . import winutil
from .paths import IS_WINDOWS

SM_SHUTTINGDOWN = 0x2000
DRIVERS = ("WinDivert", "WinDivert14")
# процессы других обходов, которые пользуются тем же драйвером
OTHER_BYPASS_PROCESSES = ("goodbyedpi.exe",)


def windows_is_shutting_down() -> bool:
    """True, если текущий сеанс Windows завершается (выключение, перезагрузка или выход из учётной записи)."""
    if not IS_WINDOWS:
        return False
    try:
        return bool(ctypes.windll.user32.GetSystemMetrics(SM_SHUTTINGDOWN))
    except Exception:
        return False


def _driver_running() -> bool:
    return any(winutil.service_query(d).state in ("RUNNING", "STOP_PENDING") for d in DRIVERS)


def _someone_else_uses_driver(zap) -> bool:
    """Работает ли что-то, чему драйвер ещё нужен: winws, другие обходы, служба zapret."""
    if winutil.process_running("winws.exe"):
        return True
    if any(winutil.process_running(p) for p in OTHER_BYPASS_PROCESSES):
        return True
    from .zapret import CONFLICT_SERVICES, SERVICE_NAME
    if winutil.service_query(SERVICE_NAME).running:
        return True
    return any(winutil.service_query(s).running for s in CONFLICT_SERVICES)


def leftover_driver_cleanup(zap) -> bool:
    """Выгрузить драйвер WinDivert, оставшийся без winws. True — драйвер выгружен.

    Осторожно: если драйвер хоть кому-то нужен — не трогаем. Службу драйвера удаляем, только убедившись,
    что она действительно остановилась (занятый чужими дескрипторами драйвер остановить не удастся).
    Под замком zapret, чтобы не столкнуться с одновременным запуском обхода.
    """
    with zap._lock:
        if not _driver_running() or _someone_else_uses_driver(zap):
            return False
        done = False
        for drv in DRIVERS:
            if not winutil.service_query(drv).exists:
                continue
            winutil.service_stop(drv)
            time.sleep(0.2)
            if winutil.service_query(drv).state in ("", "STOPPED"):
                winutil.service_delete(drv)
                done = True
        return done


def session_end_cleanup(zap) -> str:
    """Что сделать, когда Windows завершает работу. Возвращает описание для журнала.

    Настройка «Останавливать обход при выходе» здесь не учитывается: при выключении компьютера
    обход всё равно умрёт вместе с сеансом, а вот драйвер надо выгрузить обязательно.
    """
    svc = winutil.service_query(_service_name())
    if svc.exists and svc.running:
        return "служба zapret продолжит работать после перезагрузки — не трогаю"
    if zap.own_process_alive():
        zap.stop_process(unload_driver=True)
        return "обход остановлен, драйвер WinDivert выгружен"
    if winutil.process_running("winws.exe"):
        return "обход запущен вне приложения — не трогаю"
    if leftover_driver_cleanup(zap):
        return "выгружен драйвер WinDivert, оставшийся без обхода"
    return "обход не запущен — делать нечего"


def _service_name() -> str:
    from .zapret import SERVICE_NAME
    return SERVICE_NAME
