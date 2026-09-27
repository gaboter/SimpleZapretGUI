"""Журнал приложения: пишет в файл и рассылает записи подписчикам (UI)."""
from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .paths import LOG_DIR


@dataclass
class LogEntry:
    ts: float
    level: str        # info | ok | warn | error | busy
    text: str


_listeners: list[Callable[[LogEntry], None]] = []
_history: list[LogEntry] = []
_lock = threading.Lock()
_console = bool(os.environ.get("SZG_DEBUG"))   # при отладке журнал дублируется в консоль


def subscribe(fn: Callable[[LogEntry], None]) -> None:
    _listeners.append(fn)


def history() -> list[LogEntry]:
    return list(_history)


def _emit(level: str, text: str) -> None:
    e = LogEntry(time.time(), level, text)
    with _lock:
        _history.append(e)
        del _history[:-2000]
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            with open(LOG_DIR / "app.log", "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{level.upper()}] {text}\n")
        except Exception:
            pass
        if _console and sys.stdout is not None:
            try:
                print(f"{time.strftime('%H:%M:%S')} [{level.upper():5}] {text}", flush=True)
            except Exception:
                pass
    for fn in list(_listeners):
        try:
            fn(e)
        except Exception:
            pass


def info(t: str) -> None: _emit("info", t)
def ok(t: str) -> None: _emit("ok", t)
def warn(t: str) -> None: _emit("warn", t)
def error(t: str) -> None: _emit("error", t)
def busy(t: str) -> None: _emit("busy", t)


def trace(text: str) -> None:
    """Трассировка ошибки фоновой задачи: только в файл (и в консоль при отладке), без показа в UI."""
    with _lock:
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            with open(LOG_DIR / "app.log", "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [TRACE]\n{text.rstrip()}\n")
        except Exception:
            pass


def rotate() -> None:
    p = LOG_DIR / "app.log"
    try:
        if p.exists() and p.stat().st_size > 2_000_000:
            p.replace(LOG_DIR / "app.old.log")
    except Exception:
        pass
