"""Хранилище настроек и результатов тестов (JSON в %ProgramData%\\SimpleZapretGUI\\user)."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from .paths import RESULTS_FILE, SETTINGS_FILE

DEFAULTS: dict[str, Any] = {
    "last_strategy": "",
    "mode": "session",            # session | service
    "check_updates_on_start": True,
    "auto_connect_on_start": False,
    "start_with_windows": False,
    "close_to_tray": True,
    "stop_on_exit": True,
    "auto_restart_on_change": True,
    "tcp_ts_auto": True,             # включать TCP timestamps при запуске обхода (как service.bat)
    "test_timeout": 5,
    "test_parallel": 16,
    "test_targets_mode": "standard",     # standard | all | selected
    "test_targets_selected": [],
    "health_interval": 60,
    "skipped_version": "",
}


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


class Settings:
    def __init__(self, path: Path = SETTINGS_FILE):
        self.path = path
        self._lock = threading.Lock()
        self.data = {**DEFAULTS, **_read(path, {})}

    def get(self, key: str, default=None):
        return self.data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value) -> None:
        with self._lock:
            self.data[key] = value
            _write(self.path, self.data)


class Results:
    """Результаты автотеста: {strategy: {score, ok, total, time, zapret_version, details}}."""

    def __init__(self, path: Path = RESULTS_FILE):
        self.path = path
        self._lock = threading.Lock()
        self.data: dict[str, dict] = _read(path, {})

    def put(self, name: str, score: float, ok: int, total: int, version: str, details: list) -> None:
        with self._lock:
            self.data[name] = {"score": round(score, 3), "ok": ok, "total": total,
                               "time": time.time(), "zapret_version": version,
                               "details": details}
            _write(self.path, self.data)

    def get(self, name: str) -> dict | None:
        return self.data.get(name)

    def remove(self, name: str) -> None:
        with self._lock:
            self.data.pop(name, None)
            _write(self.path, self.data)

    def rename(self, old: str, new: str) -> None:
        with self._lock:
            if old in self.data:
                self.data[new] = self.data.pop(old)
                _write(self.path, self.data)

    def clear(self) -> None:
        with self._lock:
            self.data = {}
            _write(self.path, self.data)

    def has_any(self, names) -> bool:
        return any(n in self.data for n in names)
