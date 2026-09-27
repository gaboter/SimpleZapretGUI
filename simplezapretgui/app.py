"""Точка входа SimpleZapretGUI."""
from __future__ import annotations

import os
import sys
import traceback

from .core import log
from .core.paths import APP_NAME, INSTANCE_KEY, IS_WINDOWS, MUTEX_NAME, ensure_dirs


def _cli_uninstall() -> int:
    """Вызывается деинсталлятором: остановить и убрать всё, что создало приложение."""
    from .core import system
    try:
        system.uninstall_cleanup(remove_hosts=True)
    except Exception:
        traceback.print_exc()
    return 0


def _ensure_admin() -> bool:
    """winws/WinDivert и службы требуют прав администратора."""
    from .core import winutil
    if not IS_WINDOWS or winutil.is_admin() or os.environ.get("SZG_NO_ADMIN"):
        return True
    from pathlib import Path
    if getattr(sys, "frozen", False):
        exe, params = sys.executable, " ".join(f'"{a}"' for a in sys.argv[1:])
        cwd = str(Path(sys.executable).parent)
    else:
        exe = sys.executable
        params = " ".join(f'"{a}"' for a in ["-m", "simplezapretgui", *sys.argv[1:]])
        cwd = str(Path(__file__).resolve().parent.parent)  # корень проекта
    return not winutil.relaunch_as_admin(exe, params, cwd)


_fault_file = None


def _setup_crash_logging() -> None:
    """Любая ошибка — в logs/crash.log, аварийные падения Qt/C — в logs/fault.log."""
    global _fault_file
    import faulthandler
    import threading
    from .core.paths import LOG_DIR
    try:
        _fault_file = open(LOG_DIR / "fault.log", "a", encoding="utf-8")
        faulthandler.enable(_fault_file)
    except Exception:
        pass
    # без консоли (pythonw / exe) stderr отсутствует — пишем его в файл
    if sys.stderr is None:
        try:
            sys.stderr = open(LOG_DIR / "stderr.log", "a", encoding="utf-8", buffering=1)
        except Exception:
            pass

    def thread_hook(a):
        _write_crash("".join(traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)),
                     show=False)
    threading.excepthook = thread_hook


def _write_crash(text: str, show: bool = True) -> None:
    try:
        from .core.paths import LOG_DIR
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        import time
        with open(LOG_DIR / "crash.log", "a", encoding="utf-8") as fh:
            fh.write(f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n{text}\n")
    except Exception:
        pass
    try:
        sys.stderr.write(text + "\n")
    except Exception:
        pass
    if show and IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, "Приложение не смогло запуститься:\n\n" + text[-1800:],
                                             APP_NAME, 0x10)
        except Exception:
            pass


def main() -> int:
    try:
        return _main()
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception:
        _write_crash(traceback.format_exc())
        return 1


def _main() -> int:
    args = sys.argv[1:]
    ensure_dirs()
    _setup_crash_logging()
    if "--uninstall-cleanup" in args:
        return _cli_uninstall()

    if not _ensure_admin():
        return 0  # перезапущены с правами администратора

    log.rotate()
    import platform
    from .core import winutil as _wu
    from .core.paths import APP_VERSION, DATA_ROOT
    log.info(f"Запуск {APP_NAME} {APP_VERSION} · Python {platform.python_version()} · "
             f"{platform.platform()} · админ: {'да' if _wu.is_admin() else 'нет'} · данные: {DATA_ROOT}")
    try:                                   # данные версий 1.0.x — из ProgramData в папку программы
        from .core.migrate import migrate_legacy
        migrate_legacy()
    except Exception:
        log.trace(traceback.format_exc())

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import QApplication, QMessageBox

    if IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SimpleZapretGUI.App")
        except Exception:
            pass

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)

    # один экземпляр: второй запуск просто показывает окно первого
    sock = QLocalSocket()
    sock.connectToServer(INSTANCE_KEY)
    if sock.waitForConnected(400):
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(400)
        sock.disconnectFromServer()
        return 0

    from .core import winutil
    winutil.create_mutex(MUTEX_NAME)

    from .ui import theme
    from .ui.behavior import AppBehavior
    from .ui.controller import Controller
    from .ui.mainwindow import MainWindow

    f = QFont("Segoe UI Variable Text")
    f.setPointSizeF(9.5)
    app.setFont(f)
    theme.apply(app)                  # единая тёмная тема, включая диалоги
    app._szg_behavior = AppBehavior(app)   # плавный скролл, тёмные заголовки окон

    ctl = Controller()
    win = MainWindow(ctl)

    server = QLocalServer()
    QLocalServer.removeServer(INSTANCE_KEY)
    server.listen(INSTANCE_KEY)

    def on_conn():
        c = server.nextPendingConnection()
        if c:
            c.readyRead.connect(lambda: (c.readAll(), win.show_normal()))
    server.newConnection.connect(on_conn)

    def excepthook(t, v, tb):
        text = "".join(traceback.format_exception(t, v, tb))
        log.error(f"Внутренняя ошибка: {v}")
        _write_crash(text, show=False)
        try:
            ctl.error_raised.emit(f"Внутренняя ошибка приложения: {v}")
        except Exception:
            QMessageBox.critical(None, APP_NAME, text[-1500:])
    sys.excepthook = excepthook

    if "--tray" in args:
        win.hide()
    else:
        win.show()
    ctl.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
