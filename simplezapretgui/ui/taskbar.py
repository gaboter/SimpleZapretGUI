"""Прогресс на кнопке приложения в панели задач Windows (ITaskbarList3 через ctypes)."""
from __future__ import annotations

import ctypes
import uuid

from ..core.paths import IS_WINDOWS

TBPF_NOPROGRESS = 0
TBPF_INDETERMINATE = 1
TBPF_NORMAL = 2
TBPF_ERROR = 4
TBPF_PAUSED = 8

_CLSID = "56FDF344-FD6D-11D0-958A-006097C9A090"   # TaskbarList
_IID = "EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF"     # ITaskbarList3


class _GUID(ctypes.Structure):
    _fields_ = [("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort), ("d3", ctypes.c_ushort),
                ("d4", ctypes.c_ubyte * 8)]


def _guid(s: str) -> _GUID:
    return _GUID.from_buffer_copy(uuid.UUID(s).bytes_le)


class TaskbarProgress:
    def __init__(self):
        self._ptr = None
        self._vt = None
        if not IS_WINDOWS:
            return
        try:
            ole = ctypes.windll.ole32
            ole.CoInitialize(None)
            ptr = ctypes.c_void_p()
            hr = ole.CoCreateInstance(ctypes.byref(_guid(_CLSID)), None, 1,
                                      ctypes.byref(_guid(_IID)), ctypes.byref(ptr))
            if hr != 0 or not ptr.value:
                return
            self._ptr = ptr
            self._vt = ctypes.cast(ptr.value, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
            self._call(3, [])  # HrInit
        except Exception:
            self._ptr = None

    def _call(self, index: int, args: list, argtypes: tuple = ()):
        proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
        fn = proto(self._vt[index])
        return fn(self._ptr, *args)

    def set_progress(self, hwnd: int, done: int, total: int) -> None:
        if not self._ptr:
            return
        try:
            self._call(10, [ctypes.c_void_p(hwnd), TBPF_NORMAL], (ctypes.c_void_p, ctypes.c_int))
            self._call(9, [ctypes.c_void_p(hwnd), ctypes.c_ulonglong(max(0, done)),
                           ctypes.c_ulonglong(max(1, total))],
                       (ctypes.c_void_p, ctypes.c_ulonglong, ctypes.c_ulonglong))
        except Exception:
            pass

    def set_state(self, hwnd: int, state: int) -> None:
        if not self._ptr:
            return
        try:
            self._call(10, [ctypes.c_void_p(hwnd), state], (ctypes.c_void_p, ctypes.c_int))
        except Exception:
            pass

    def clear(self, hwnd: int) -> None:
        self.set_state(hwnd, TBPF_NOPROGRESS)
