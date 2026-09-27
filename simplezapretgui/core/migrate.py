"""Перенос данных версий 1.0.x из %ProgramData%\\SimpleZapretGUI в папку программы.

Выполняется один раз при запуске, если на новом месте ещё нет ни zapret, ни настроек.
Если служба zapret работала из старой папки, она останавливается и после переноса
переустанавливается уже с новым путём — с той же стратегией.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from . import log, winutil
from .paths import (DATA_ROOT, LEGACY_ROOT, SERVICE_NAME, SERVICE_REG_KEY, SERVICE_REG_VALUE,
                    SETTINGS_FILE, ZAPRET_DIR)


def _move_tree(src: Path, dst: Path) -> None:
    """Переместить содержимое src в dst, сливая папки, которые уже есть (например, пустые logs)."""
    dst.mkdir(parents=True, exist_ok=True)
    for item in list(src.iterdir()):
        target = dst / item.name
        if item.is_dir() and target.is_dir():
            _move_tree(item, target)
            try:
                item.rmdir()
            except OSError:
                pass
        elif not target.exists():
            shutil.move(str(item), str(target))


def migrate_legacy() -> bool:
    """True — данные были перенесены."""
    old = LEGACY_ROOT
    if old is None or not old.exists():
        return False
    try:
        if old.resolve() == DATA_ROOT.resolve():
            return False
    except OSError:
        return False
    if ZAPRET_DIR.exists() or SETTINGS_FILE.exists():
        return False                     # на новом месте уже есть данные — старые не трогаем

    old_s = str(old).lower()
    svc = winutil.service_query(SERVICE_NAME)
    service_strategy = ""
    if svc.exists and old_s in svc.bin_path.lower():
        service_strategy = winutil.reg_read("HKLM", SERVICE_REG_KEY, SERVICE_REG_VALUE) or "general"
        winutil.service_stop(SERVICE_NAME)
        winutil.service_delete(SERVICE_NAME)
    # winws, запущенный из старой копии, держит её файлы — завершаем
    for p in winutil.find_processes("winws.exe"):
        if old_s in (p.exe or "").lower() and winutil.psutil:
            try:
                winutil.psutil.Process(p.pid).kill()
            except Exception:
                pass
    time.sleep(0.5)

    try:
        _move_tree(old, DATA_ROOT)
    except Exception as e:  # noqa: BLE001
        log.error(f"Не удалось перенести данные из {old}: {e}")
        return False
    try:
        shutil.rmtree(old)
    except OSError:
        log.warn(f"Старая папка {old} удалена не полностью — её можно удалить вручную")
    log.ok(f"Данные перенесены из {old} в {DATA_ROOT}")

    if service_strategy:
        from .zapret import Zapret
        zap = Zapret(ZAPRET_DIR)
        s = zap.strategy(service_strategy) or zap.strategy("general")
        if s:
            try:
                zap.service_install(s)
                log.ok(f"Служба zapret переустановлена из новой папки ({s.name})")
            except Exception as e:  # noqa: BLE001
                log.error(f"Службу zapret не удалось переустановить: {e}. Включите её заново в «Сервисе».")
    return True
