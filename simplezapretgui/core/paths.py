"""Пути и константы приложения.

Все файлы программы — в папке data рядом с SimpleZapretGUI.exe, то есть там, куда
пользователь установил программу (по умолчанию C:\\Program Files\\SimpleZapretGUI\\data).
Установщик не даёт выбрать путь с кириллицей: с ним не работают winws/WinDivert.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "SimpleZapretGUI"
APP_VERSION = "1.0.1"
APP_REPO_URL = "https://github.com/gaboter/SimpleZapretGUI"

REPO_OWNER = "Flowseal"
REPO_NAME = "zapret-discord-youtube"
REPO = f"{REPO_OWNER}/{REPO_NAME}"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/main"
VERSION_URL = f"{RAW_BASE}/.service/version.txt"
IPSET_URL = f"{RAW_BASE}/.service/ipset-service.txt"
HOSTS_URL = f"{RAW_BASE}/.service/hosts"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
API_TAG = f"https://api.github.com/repos/{REPO}/releases/tags/{{tag}}"
RELEASE_ZIP = f"https://github.com/{REPO}/releases/download/{{tag}}/zapret-discord-youtube-{{tag}}.zip"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"

SERVICE_NAME = "zapret"
SERVICE_REG_KEY = r"System\CurrentControlSet\Services\zapret"
SERVICE_REG_VALUE = "zapret-discord-youtube"
TASK_NAME = "SimpleZapretGUI"
MUTEX_NAME = "SimpleZapretGUI_Mutex"
INSTANCE_KEY = "SimpleZapretGUI-instance"

IS_WINDOWS = sys.platform == "win32"


def _data_root() -> Path:
    override = os.environ.get("SZG_DATA_DIR")
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):                     # установленная программа
        return Path(sys.executable).resolve().parent / "data"
    return Path(__file__).resolve().parent.parent.parent / "data"   # запуск из исходников


DATA_ROOT = _data_root()
# где хранили данные версии 1.0.x — оттуда они один раз переносятся в DATA_ROOT
LEGACY_ROOT = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / APP_NAME if IS_WINDOWS else None
ZAPRET_DIR = DATA_ROOT / "zapret"            # управляемая копия zapret
USER_DIR = DATA_ROOT / "user"                # всё пользовательское (переживает обновления)
USER_STRATEGIES_DIR = USER_DIR / "strategies"
BACKUP_DIR = DATA_ROOT / "backups"
TEMP_DIR = DATA_ROOT / "temp"
LOG_DIR = DATA_ROOT / "logs"

SETTINGS_FILE = USER_DIR / "settings.json"
RESULTS_FILE = USER_DIR / "test_results.json"
OVERRIDES_FILE = USER_DIR / "list_overrides.json"
TARGETS_FILE = USER_DIR / "targets.txt"
HOSTS_BACKUP = USER_DIR / "hosts.backup"


def ensure_dirs() -> None:
    for d in (DATA_ROOT, USER_DIR, USER_STRATEGIES_DIR, BACKUP_DIR, TEMP_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)


def resource_path(rel: str) -> Path:
    """Путь к ресурсам, работает и из исходников, и из сборки PyInstaller."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent.parent))
    return base / rel


def app_executable() -> str:
    if getattr(sys, "frozen", False):
        return sys.executable
    return f'"{sys.executable}" "{Path(sys.argv[0]).resolve()}"'
