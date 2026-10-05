"""Главное окно: боковая навигация, страницы, строка состояния, трей."""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QHBoxLayout, QMainWindow, QMenu,
                               QMessageBox, QProgressBar, QStackedWidget, QSystemTrayIcon, QToolButton,
                               QVBoxLayout, QWidget, QLabel)

from ..core.paths import APP_NAME, APP_VERSION
from . import icons, theme
from .controller import Controller
from .search import FindBar
from .taskbar import TaskbarProgress
from .widgets import ScrollPage
from .pages.builder import BuilderPage
from .pages.home import HomePage
from .pages.lists import ListsPage
from .pages.service import ServicePage
from .pages.settings import LogPage, SettingsPage
from .pages.strategies import StrategiesPage
from .pages.tests import TestsPage


LEVEL_COLOR = {"info": theme.MUTED, "ok": theme.OK, "warn": theme.WARN, "error": theme.ERR,
               "busy": theme.ACCENT}


class Spinner(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(14, 14)
        self._a = 0
        self._busy = False
        self._color = QColor(theme.FAINT)
        self._t = QTimer(self)
        self._t.setInterval(30)
        self._t.timeout.connect(self._tick)

    def _tick(self):
        self._a = (self._a + 12) % 360
        self.update()

    def set_state(self, busy: bool, color: str):
        self._busy = busy
        self._color = QColor(color)
        self._t.start() if busy else self._t.stop()
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        if self._busy:
            p.setPen(QPen(self._color, 2, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(r, -self._a * 16, 270 * 16)
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(self._color)
            p.drawEllipse(r.adjusted(2, 2, -2, -2))


class StatusBar(QWidget):
    def __init__(self, ctl: Controller, open_log):
        super().__init__()
        self.setObjectName("statusbar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(32)
        self.setCursor(Qt.PointingHandCursor)
        self.open_log = open_log
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 14, 0)
        lay.setSpacing(8)
        self.spin = Spinner()
        self.text = QLabel("Готово")
        self.text.setStyleSheet(f"color:{theme.MUTED}; font-size:12px;")
        self.bar = QProgressBar()
        self.bar.setFixedWidth(140)
        self.bar.setFixedHeight(6)
        self.bar.hide()
        self.ver = QLabel("")
        self.ver.setStyleSheet(f"color:{theme.FAINT}; font-size:12px;")
        lay.addWidget(self.spin)
        lay.addWidget(self.text, 1)
        lay.addWidget(self.bar)
        lay.addWidget(self.ver)
        self.ctl = ctl
        ctl.log_entry.connect(self._entry)
        ctl.busy_changed.connect(self._busy)
        ctl.update_progress.connect(self._progress)
        ctl.status_changed.connect(self._status)
        self._is_busy = False
        # через 5 с после последней записи журнала показываем, включён ли обход
        self.idle = QTimer(self)
        self.idle.setSingleShot(True)
        self.idle.setInterval(5000)
        self.idle.timeout.connect(self._show_state)
        self.idle.start()

    def _status(self, st):
        self.ver.setText(f"zapret {st.version or '—'}   ·   {APP_NAME} {APP_VERSION}")
        if not self.idle.isActive() and not self._is_busy:
            self._show_state()

    def _show_state(self):
        if self._is_busy:
            self.text.setText(self.ctl.busy)
            return
        st = self.ctl.status
        if not st.installed:
            txt, col = "zapret не установлен", theme.WARN
        elif st.running:
            src = {"app": "сеанс", "service": "служба", "external": "запущен вне приложения"}.get(st.source, "")
            txt = "Обход включён" + (f" · {st.strategy}" if st.strategy else "") + (f" · {src}" if src else "")
            col = theme.OK if st.source != "external" else theme.WARN
        else:
            txt, col = "Обход выключен", theme.WARN
        self.text.setText(txt)
        self.text.setStyleSheet(f"color:{col}; font-size:12px;")
        self.spin.set_state(False, col)

    def _entry(self, e):
        self.text.setText(e.text.splitlines()[0][:180])
        self.text.setStyleSheet(f"color:{LEVEL_COLOR.get(e.level, theme.MUTED)}; font-size:12px;")
        if not self._is_busy:
            self.spin.set_state(e.level == "busy", LEVEL_COLOR.get(e.level, theme.MUTED))
        self.idle.start()

    def _busy(self, b: bool, text: str):
        self._is_busy = b
        self.spin.set_state(b, theme.ACCENT if b else theme.FAINT)
        if not b:
            self.idle.start()

    def _progress(self, done: int, total: int):
        if total <= 0 and done <= 0:
            self.bar.hide()
            return
        self.bar.show()
        if total > 0:
            self.bar.setRange(0, total)
            self.bar.setValue(done)
        else:
            self.bar.setRange(0, 0)

    def mousePressEvent(self, e):
        self.open_log()


class MainWindow(QMainWindow):
    PAGES = [("home", "power", "Главная"), ("strategies", "sliders", "Стратегии"),
             ("builder", "wand", "Подбор стратегии"),
             ("lists", "globe", "Списки ресурсов"), ("tests", "gauge", "Автотест"),
             ("service", "tools", "Сервис"), ("settings", "cog", "Настройки"), ("log", "log", "Журнал")]

    def __init__(self, ctl: Controller):
        super().__init__()
        self.ctl = ctl
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icons.app_icon(icons.LOGO_OFF))
        self.resize(1000, 700)
        self.setMinimumSize(600, 560)
        self._quitting = False

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        h = QHBoxLayout()
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        v.addLayout(h, 1)

        # --- боковая панель
        side = QWidget()
        side.setObjectName("sidebar")
        side.setAttribute(Qt.WA_StyledBackground, True)
        side.setFixedWidth(64)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(10, 14, 10, 14)
        sl.setSpacing(6)
        self.stack = QStackedWidget()
        self.nav = QButtonGroup(self)
        self.nav_buttons = {}
        self.pages = {}
        self.holders = {}

        tests = TestsPage(ctl)
        service = ServicePage(ctl)
        run_test = lambda names: (self.go_to("tests"), tests.run(names))  # noqa: E731
        run_diag = lambda: (self.go_to("service"), service.run_diagnostics())  # noqa: E731
        strategies = StrategiesPage(ctl, self.go_to, run_test)

        def open_strategy(name):
            self.go_to("strategies")
            strategies._select_name(name)

        pages = {
            "home": HomePage(ctl, self.go_to, run_test, run_diag),
            "strategies": strategies,
            "builder": BuilderPage(ctl, open_strategy, run_test),
            "lists": ListsPage(ctl),
            "tests": tests,
            "service": service,
            "settings": SettingsPage(ctl),
            "log": LogPage(ctl),
        }
        for key, ic, tip in self.PAGES:
            b = QToolButton()
            b.setObjectName("nav")
            b.setCheckable(True)
            b.setIcon(icons.icon(ic, theme.MUTED, theme.ACCENT, 22))
            b.setIconSize(QSize(22, 22))
            b.setFixedSize(44, 44)
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.go_to(k))
            self.nav.addButton(b)
            self.nav_buttons[key] = b
            if key == "settings":
                sl.addStretch(1)
                sb = QToolButton()
                sb.setObjectName("nav")
                sb.setIcon(icons.icon("search", theme.MUTED, theme.ACCENT, 22))
                sb.setIconSize(QSize(22, 22))
                sb.setFixedSize(44, 44)
                sb.setToolTip("Поиск по настройкам и сервису (Ctrl+F)")
                sb.setCursor(Qt.PointingHandCursor)
                sb.clicked.connect(lambda: self.find.open_bar())
                sl.addWidget(sb, 0, Qt.AlignHCenter)
            sl.addWidget(b, 0, Qt.AlignHCenter)
            self.pages[key] = pages[key]
            holder = ScrollPage(pages[key]) if key in ("home", "strategies", "builder", "lists", "tests") \
                else pages[key]
            self.holders[key] = holder
            self.stack.addWidget(holder)
        # --- поиск (Ctrl+F) над страницами
        self.find = FindBar([("settings", pages["settings"]), ("service", pages["service"])],
                            self.go_to, self.current_key)
        content = QWidget()
        cv = QVBoxLayout(content)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        cv.addWidget(self.find)
        cv.addWidget(self.stack, 1)
        h.addWidget(side)
        h.addWidget(content, 1)
        QShortcut(QKeySequence.Find, self, activated=self.find.open_bar)
        QShortcut(QKeySequence("F3"), self, activated=self.find.next)
        QShortcut(QKeySequence("Shift+F3"), self, activated=self.find.prev)
        self.status = StatusBar(ctl, lambda: self.go_to("log"))
        v.addWidget(self.status)
        self.go_to("home")

        # --- трей
        self.taskbar = TaskbarProgress()
        self._arrow = icons.LOGO_OFF
        self._test_frac = None
        self.tray = QSystemTrayIcon(icons.app_icon(icons.LOGO_OFF), self)
        self.tray.setToolTip(APP_NAME)
        m = QMenu()
        self.act_show = QAction("Открыть", self, triggered=self.show_normal)
        self.act_toggle = QAction("Подключить", self, triggered=self._tray_toggle)
        self.act_quit = QAction("Выход", self, triggered=self.quit)
        m.addAction(self.act_show)
        m.addAction(self.act_toggle)
        m.addSeparator()
        m.addAction(self.act_quit)
        self.tray.setContextMenu(m)
        self.tray.activated.connect(lambda r: self.show_normal() if r in (
            QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        self.tray.show()
        ctl.status_changed.connect(self._tray_status)
        ctl.error_raised.connect(self._tray_error)
        ctl.update_available.connect(self._tray_update)
        ctl.test_progress.connect(self._test_progress)
        ctl.test_finished.connect(self._test_finished)
        ctl.builder_finished.connect(lambda: self._test_finished("Подбор стратегии завершён"))
        ctl.app_update_available.connect(self._tray_app_update)
        ctl.quit_for_update.connect(lambda: self.quit(force=True))

    def current_key(self) -> str:
        w = self.stack.currentWidget()
        return next((k for k, h in self.holders.items() if h is w), "")

    def go_to(self, key: str):
        w = self.holders.get(key)
        if w:
            self.stack.setCurrentWidget(w)
            self.nav_buttons[key].setChecked(True)

    # ------------------------------------------------------------ трей
    def _tray_status(self, st):
        if st.running:
            color = icons.LOGO_ON if st.source != "external" else theme.WARN
            tip = f"{APP_NAME}: обход включён — {st.strategy or 'стратегия?'}"
        else:
            color, tip = icons.LOGO_OFF, f"{APP_NAME}: обход выключен"
        if self._test_frac is not None:
            tip = f"{APP_NAME}: автотест — {round(self._test_frac * 100)}%"
        if color != self._arrow or self._test_frac is not None:
            self._arrow = color
            self._apply_icons()
        self.tray.setToolTip(tip)
        self.act_toggle.setText("Отключить" if st.running else "Подключить")

    def _apply_icons(self):
        """Цвет стрелки логотипа = состояние обхода; во время автотеста — полоска прогресса."""
        ic = icons.app_icon(self._arrow, self._test_frac)
        self.tray.setIcon(ic)
        self.setWindowIcon(icons.app_icon(self._arrow))

    def _test_progress(self, done: int, total: int, name: str):
        self._test_frac = done / total if total else 0.0
        self._apply_icons()
        self.tray.setToolTip(f"{APP_NAME}: автотест {done} из {total}" + (f" — {name}" if name else ""))
        if self.isVisible():
            self.taskbar.set_progress(int(self.winId()), done, total)

    def _test_finished(self, message: str = "Автотест стратегий завершён"):
        self._test_frac = None
        self._apply_icons()
        self.taskbar.clear(int(self.winId()))
        self.tray.showMessage(APP_NAME, message, icons.app_icon(self._arrow), 5000)

    def _tray_error(self, msg: str):
        if msg and not self.isVisible():
            self.tray.showMessage(APP_NAME, msg[:200], QSystemTrayIcon.Warning, 5000)

    def _tray_update(self, remote: str, local: str):
        if remote and local and not self.isVisible():
            self.tray.showMessage(APP_NAME, f"Доступна новая версия zapret {remote}",
                                  QSystemTrayIcon.Information, 5000)

    def _tray_app_update(self, rel):
        if not self.isVisible():
            self.tray.showMessage(APP_NAME, f"Доступна новая версия SimpleZapretGUI {rel.version}",
                                  icons.app_icon(self._arrow), 5000)

    def _tray_toggle(self):
        if self.ctl.status.running:
            self.ctl.disconnect()
        else:
            name = self.ctl.settings.get("last_strategy") or self.ctl.best_strategy() or "general"
            self.ctl.connect(name)

    def showEvent(self, e):
        super().showEvent(e)
        if self._test_frac is not None:
            QTimer.singleShot(200, lambda: self.taskbar.set_progress(int(self.winId()),
                                                                     int(self._test_frac * 1000), 1000))

    def show_normal(self):
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------ выход
    def closeEvent(self, e):
        if self._quitting:
            e.accept()
            return
        if self.ctl.settings.get("close_to_tray") and QSystemTrayIcon.isSystemTrayAvailable():
            e.ignore()
            self.hide()
            if not self.ctl.settings.get("_tray_hint_shown"):
                self.tray.showMessage(APP_NAME, "Приложение продолжает работать в трее.",
                                      icons.app_icon(self._arrow), 3000)
                self.ctl.settings.set("_tray_hint_shown", True)
            return
        e.ignore()
        self.quit()

    def quit(self, force: bool = False):
        st = self.ctl.status
        if not force and self.ctl.busy.startswith("Тест"):
            if QMessageBox.question(self, APP_NAME, "Идёт автотест. Прервать и выйти?") != QMessageBox.Yes:
                return
        if st.source == "app" and self.ctl.settings.get("stop_on_exit"):
            try:
                self.ctl.zap.stop_process(unload_driver=True)
            except Exception:
                pass
        self._quitting = True
        self.tray.hide()
        QApplication.quit()
