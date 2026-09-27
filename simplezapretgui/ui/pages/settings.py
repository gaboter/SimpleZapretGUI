"""Настройки приложения, управление копией zapret и журнал."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QFileDialog, QMessageBox, QPlainTextEdit, QScrollArea, QVBoxLayout,
                               QWidget)

from ...core import log, system, winutil
from ...core.paths import APP_REPO_URL, APP_VERSION, DATA_ROOT, LOG_DIR, RELEASES_PAGE, ZAPRET_DIR
from .. import icons, theme
from ..widgets import Dot, Switch, button, card, flow, hbox, label, vbox

LEVEL_COLOR = {"info": theme.MUTED, "ok": theme.OK, "warn": theme.WARN, "error": theme.ERR,
               "busy": theme.ACCENT}


class SettingsPage(QWidget):
    def __init__(self, ctl):
        super().__init__()
        self.setObjectName("page")
        self.ctl = ctl
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("page")
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        self.search_root, self.search_scroll = inner, scroll      # для поиска (Ctrl+F)
        root = QVBoxLayout(inner)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)
        root.addWidget(label("Настройки", "h1"))

        c = card()
        l = vbox(margins=(16, 12, 16, 12), spacing=12)
        c.setLayout(l)
        s = ctl.settings

        def row(title: str, hint: str, key: str | None, on_change=None, value=None):
            sw = Switch(bool(s.get(key)) if key else bool(value))

            def changed(v):
                if key:
                    s.set(key, v)
                if on_change:
                    on_change(v)

            sw.toggled.connect(changed)
            row_l = hbox(spacing=16)
            row_l.addLayout(vbox(label(title), label(hint, "faint", wrap=True), spacing=2), 1)
            row_l.addWidget(sw, 0, Qt.AlignVCenter)
            l.addLayout(row_l)
            return sw

        self.autostart = row("Запускать вместе с Windows", "Приложение стартует свёрнутым в трей "
                             "(через Планировщик заданий, без запроса UAC).", "start_with_windows",
                             self._autostart)
        self.svc_mode = row("Запускать обход как службу Windows",
                            "Обход включается вместе с Windows и работает, даже если приложение закрыто. "
                            "Выключено — обход работает, пока открыто приложение.", None,
                            lambda v: ctl.set_mode("service" if v else "session"),
                            s.get("mode") == "service")
        row("Подключаться при запуске", "Сразу включать последнюю выбранную стратегию.",
            "auto_connect_on_start")
        row("Проверять обновления при запуске", "И SimpleZapretGUI, и zapret — в фоне, без браузера. "
            "Новая версия предлагается баннером на главной.", "check_updates_on_start")
        row("Закрывать в трей", "Крестик сворачивает окно в область уведомлений.", "close_to_tray")
        row("Останавливать обход при выходе", "Если обход запущен не как служба Windows. Служба работает всегда.",
            "stop_on_exit")
        row("Перезапускать обход после изменений", "При правке списков, Game Filter, IPSet и фейков.",
            "auto_restart_on_change")
        root.addWidget(c)

        # --- сеть: TCP timestamps
        cn = card()
        ln = vbox(margins=(16, 12, 16, 12), spacing=12)
        cn.setLayout(ln)
        ln.addWidget(label("Сеть", "h2"))
        self.ts_dot = Dot()
        self.ts_state = label("Определяю состояние…", "faint", wrap=True)
        self.ts_switch = Switch(False)
        self.ts_switch.setEnabled(False)
        self.ts_switch.toggled.connect(self._ts_toggled)
        ts_row = hbox(spacing=16)
        ts_row.addLayout(vbox(label("TCP timestamps (RFC 1323)"),
                              label("Метки времени в TCP-пакетах. Нужны части стратегий zapret "
                                    "(с параметром --dpi-desync-fooling=ts).", "faint", wrap=True),
                              self._ts_status_row(), spacing=2), 1)
        ts_row.addWidget(self.ts_switch, 0, Qt.AlignVCenter)
        ln.addLayout(ts_row)
        self.ts_auto = Switch(bool(s.get("tcp_ts_auto")))
        self.ts_auto.toggled.connect(ctl.set_tcp_ts_auto)
        auto_row = hbox(spacing=16)
        auto_row.addLayout(vbox(label("Включать TCP timestamps при запуске обхода"),
                                label("Как это делает service.bat. Выключите, если хотите сами управлять "
                                      "timestamps переключателем выше.", "faint", wrap=True), spacing=2), 1)
        auto_row.addWidget(self.ts_auto, 0, Qt.AlignVCenter)
        ln.addLayout(auto_row)
        root.addWidget(cn)

        # --- zapret
        c2 = card()
        l2 = vbox(margins=(16, 12, 16, 12), spacing=10)
        c2.setLayout(l2)
        l2.addWidget(label("Копия zapret", "h2"))
        self.zinfo = label("", "muted", wrap=True)
        l2.addWidget(self.zinfo)
        self.upd = button("Проверить обновления", icon=icons.icon("refresh"))
        self.upd.clicked.connect(self._check_updates)
        self.reinst = button("Переустановить")
        self.reinst.clicked.connect(self._reinstall)
        self.zipb = button("Установить из архива…")
        self.zipb.clicked.connect(self._zip)
        self.folderb = button("Импорт папки…")
        self.folderb.clicked.connect(self._folder)
        openb = button("Открыть папку", icon=icons.icon("folder"))
        openb.clicked.connect(lambda: winutil.open_path(str(ZAPRET_DIR)))
        l2.addWidget(flow(self.upd, self.reinst, self.zipb, self.folderb, openb, spacing=6))
        l2.addWidget(label("При обновлении и переустановке сохраняются ваши списки, стратегии, Game Filter, "
                           "IPSet, фейки и режим работы. Прошлая версия остаётся в резервной копии.",
                           "faint", wrap=True))
        root.addWidget(c2)

        # --- о программе
        c3 = card()
        l3 = vbox(margins=(16, 12, 16, 12), spacing=6)
        c3.setLayout(l3)
        l3.addWidget(label(f"SimpleZapretGUI {APP_VERSION}", "h2"))
        l3.addWidget(label("Графическая оболочка для zapret-discord-youtube (Flowseal). Приложение не "
                           "изменяет zapret, а управляет его отдельной копией.", "muted", wrap=True))
        l3.addWidget(label(f"Данные: {DATA_ROOT}", "faint"))
        app_gh = button("SimpleZapretGUI на GitHub", "link", icons.icon("external", theme.ACCENT, size=14),
                        APP_REPO_URL)
        app_gh.clicked.connect(lambda: winutil.open_path(APP_REPO_URL))
        gh = button("Релизы zapret на GitHub", "link", icons.icon("external", theme.ACCENT, size=14))
        gh.clicked.connect(lambda: winutil.open_path(RELEASES_PAGE))
        logs = button("Папка журналов", "link", icons.icon("folder", theme.ACCENT, size=14))
        logs.clicked.connect(lambda: winutil.open_path(str(LOG_DIR)))
        self.app_upd = button("Проверить обновления программы", icon=icons.icon("refresh"))
        self.app_upd.clicked.connect(self._check_app_update)
        l3.addWidget(flow(self.app_upd))
        l3.addWidget(flow(app_gh, gh, logs))
        root.addWidget(c3)
        root.addStretch(1)

        ctl.status_changed.connect(self._render)
        self.autostart.blockSignals(True)
        self.autostart.setChecked(system.autostart_enabled() or bool(s.get("start_with_windows")))
        self.autostart.blockSignals(False)

    # ------------------------------------------------------------ TCP timestamps
    def _ts_status_row(self):
        row = hbox(self.ts_dot, self.ts_state, spacing=6)
        row.setStretch(1, 1)
        return row

    def showEvent(self, e):
        super().showEvent(e)
        self.svc_mode.blockSignals(True)            # режим мог смениться на вкладке «Сервис»
        self.svc_mode.setChecked(self.ctl.settings.get("mode") == "service")
        self.svc_mode.blockSignals(False)
        self._ts_refresh()

    def _ts_refresh(self):
        if getattr(self, "_ts_busy", False):
            return
        self._ts_busy = True
        self.ts_switch.setEnabled(False)
        self.ts_dot.set_busy(True)
        self.ts_state.setText("Определяю состояние…")
        self.ctl.tcp_ts_state(self._ts_show)

    def _ts_show(self, state):
        self._ts_busy = False
        self.ts_switch.blockSignals(True)
        self.ts_switch.setChecked(bool(state))
        self.ts_switch.blockSignals(False)
        self.ts_switch.setEnabled(True)
        if state is True:
            self.ts_dot.set_color(theme.OK)
            self.ts_state.setText("Сейчас: включены")
        elif state is False:
            self.ts_dot.set_color(theme.FAINT)
            self.ts_state.setText("Сейчас: выключены")
        else:
            self.ts_dot.set_color(theme.WARN)
            self.ts_state.setText("Не удалось определить состояние — подробности в журнале")

    def _ts_toggled(self, on: bool):
        self._ts_busy = True
        self.ts_switch.setEnabled(False)
        self.ts_dot.set_busy(True)
        self.ts_state.setText("Включаю…" if on else "Выключаю…")
        self.ctl.set_tcp_ts(on, self._ts_show)

    def _render(self, st):
        if st.installed:
            self.zinfo.setText(f"Версия {st.version} · {ZAPRET_DIR}")
        else:
            self.zinfo.setText("zapret не установлен")

    def _autostart(self, on: bool):
        ok = system.set_autostart(on)
        if on and not ok:
            self.ctl.push_error("Не удалось создать задачу автозапуска")
        else:
            log.ok("Автозапуск " + ("включён" if on else "выключен"))

    def _check_app_update(self):
        self.app_upd.set_loading(True, "Проверка…")
        self.ctl.check_app_update(silent=False, finished=lambda: self.app_upd.set_loading(False))

    def _check_updates(self):
        self.upd.set_loading(True, "Проверка…")
        self.ctl.check_updates(silent=False, finished=lambda: self.upd.set_loading(False))

    def _reinstall(self):
        if QMessageBox.question(self, "Переустановка", "Скачать и переустановить текущую версию zapret? "
                                "Настройки будут сохранены.") == QMessageBox.Yes:
            self.reinst.set_loading(True, "Установка…")
            self.ctl.install_update(self.ctl.zap.version() or "", finished=lambda: self.reinst.set_loading(False))

    def _zip(self):
        p, _ = QFileDialog.getOpenFileName(self, "Архив zapret-discord-youtube", "", "ZIP (*.zip)")
        if p:
            self.zipb.set_loading(True, "Установка…")
            self.ctl.install_from_zip(p, lambda: self.zipb.set_loading(False))

    def _folder(self):
        p = QFileDialog.getExistingDirectory(self, "Папка с zapret-discord-youtube")
        if p:
            self.folderb.set_loading(True, "Импорт…")
            self.ctl.install_from_folder(p, lambda: self.folderb.set_loading(False))


class LogPage(QWidget):
    def __init__(self, ctl):
        super().__init__()
        self.setObjectName("page")
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(10)
        clear = button("Очистить", "link")
        root.addLayout(hbox(label("Журнал", "h1"), None, clear))
        self.view = QPlainTextEdit()
        self.view.setObjectName("code")
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(3000)
        root.addWidget(self.view, 1)
        clear.clicked.connect(self.view.clear)
        for e in log.history():
            self.add(e)
        ctl.log_entry.connect(self.add)

    def add(self, e):
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(LEVEL_COLOR.get(e.level, theme.MUTED)))
        cur = self.view.textCursor()
        cur.movePosition(QTextCursor.End)
        cur.insertText(f"{time.strftime('%H:%M:%S', time.localtime(e.ts))}  {e.text}\n", fmt)
        self.view.setTextCursor(cur)
        self.view.ensureCursorVisible()
