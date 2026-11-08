"""Слежение за завершением работы Windows.

Windows сообщает приложению о выключении так: сначала WM_QUERYENDSESSION («можно завершать?»), и если все
согласны — WM_ENDSESSION с wParam=TRUE («завершаем»). После ответа на WM_ENDSESSION процесс могут убить в любой
момент, поэтому отключить обход нужно прямо в обработчике. Если выключение отменили (например, другая программа
попросила подождать), приходит WM_ENDSESSION с wParam=FALSE — тогда обход трогать нельзя.

Сигнал ловится двумя независимыми путями:
  1. Нативный фильтр сообщений: WM_ENDSESSION(TRUE) → отключаем обход сразу.
  2. aboutToQuit: Qt испускает его при WM_ENDSESSION; если при этом Windows действительно завершает сеанс
     (GetSystemMetrics(SM_SHUTTINGDOWN)) — отключаем обход, если ещё не успели. Обычный выход из программы
     этим путём не затрагивается: там своя логика («Останавливать обход при выходе»).

Отключение выполняется один раз, повторные вызовы игнорируются.
"""
from __future__ import annotations

import ctypes
from typing import Callable

from PySide6.QtCore import QAbstractNativeEventFilter
from PySide6.QtGui import QGuiApplication

from ..core.session import windows_is_shutting_down

WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016

_state = {"ending": False}


class _MSG(ctypes.Structure):
    """Структура MSG из Win32 (описана вручную, чтобы не зависеть от платформы)."""
    _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint), ("wParam", ctypes.c_size_t),
                ("lParam", ctypes.c_ssize_t), ("time", ctypes.c_uint), ("pt_x", ctypes.c_int32),
                ("pt_y", ctypes.c_int32)]          # LONG в Windows всегда 32-битный


def is_session_ending() -> bool:
    """Идёт ли завершение сеанса Windows. Окну в этот момент нельзя «отказываться» закрываться,
    иначе Windows сочтёт программу помехой выключению."""
    app = QGuiApplication.instance()
    return bool(_state["ending"] or (app is not None and app.isSavingSession()))


class SessionGuard(QAbstractNativeEventFilter):
    def __init__(self, app: QGuiApplication, on_end: Callable[[], None]):
        super().__init__()
        self._on_end = on_end
        self._done = False
        app.installNativeEventFilter(self)
        app.commitDataRequest.connect(lambda _m: _state.update(ending=True))    # Qt: сеанс сохраняется
        app.aboutToQuit.connect(self._about_to_quit)

    def _run(self) -> None:
        if self._done:
            return
        self._done = True
        _state["ending"] = True
        try:
            self._on_end()
        except Exception:
            pass                                  # при выключении падать нельзя

    def _about_to_quit(self) -> None:
        if windows_is_shutting_down():
            self._run()

    def nativeEventFilter(self, event_type, message):
        try:
            if bytes(event_type) == b"windows_generic_MSG":
                msg = _MSG.from_address(int(message))
                if msg.message == WM_QUERYENDSESSION:
                    _state["ending"] = True       # пока только отметка: выключение ещё могут отменить
                elif msg.message == WM_ENDSESSION:
                    if msg.wParam:
                        self._run()               # выключение подтверждено — отключаем обход сейчас
                    else:
                        _state["ending"] = False  # отменено — ничего не делаем
        except Exception:
            pass
        return False, 0
