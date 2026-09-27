"""Сервис: все пункты меню service.bat в графическом виде, со статусом в реальном времени."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QLineEdit, QMessageBox, QScrollArea, QVBoxLayout, QWidget)

from ...core import diagnostics, log
from .. import icons, theme
from ..widgets import (Dot, Segmented, StrategyCombo, Switch, button, card, flow, hbox, label, run_bg,
                       vbox)

STATE_RU = {"RUNNING": "работает", "STOPPED": "остановлена", "STOP_PENDING": "зависла при остановке",
            "START_PENDING": "запускается", "": "не установлена"}


def section(title: str, hint: str = ""):
    c = card()
    l = vbox(margins=(16, 14, 16, 14), spacing=10)
    c.setLayout(l)
    l.addWidget(label(title, "h2"))
    if hint:
        l.addWidget(label(hint, "faint", wrap=True))
    return c, l


class ServicePage(QWidget):
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
        root.addWidget(label("Сервис", "h1"))
        root.addWidget(label("Всё, что умеет service.bat, — без консоли. Состояние обновляется "
                             "в реальном времени.", "muted", wrap=True))
        grid = QVBoxLayout()          # одна колонка — удобно и в узком окне
        grid.setSpacing(12)
        root.addLayout(grid)

        # --- служба
        c, l = section("Служба zapret", "Служба запускает обход вместе с Windows, даже без приложения.")
        self.svc_dot = Dot()
        self.svc_state = label("")
        self.svc_strategy = label("", "muted", wrap=True)
        self.svc_combo = StrategyCombo()
        self.svc_install = button("Установить", "primary")
        self.svc_install.clicked.connect(self._install_service)
        self.svc_remove = button("Удалить службы", "danger")
        self.svc_remove.clicked.connect(self._remove_service)
        self.wd_state = label("", "faint", wrap=True)
        l.addLayout(hbox(self.svc_dot, self.svc_state, None, spacing=8))
        l.addWidget(self.svc_strategy)
        l.addWidget(self.wd_state)
        l.addWidget(self.svc_combo)
        l.addWidget(flow(self.svc_install, self.svc_remove, spacing=8))
        grid.addWidget(c)

        # --- Game Filter
        c, l = section("Game Filter", "Обход для игр: широкий диапазон портов TCP/UDP через IPSet.")
        self.gf_mode = Segmented([("disabled", "Выкл"), ("all", "TCP и UDP"), ("tcp", "TCP"),
                                  ("udp", "UDP")])
        self.gf_tcp = QLineEdit()
        self.gf_udp = QLineEdit()
        for e in (self.gf_tcp, self.gf_udp):
            e.setPlaceholderText("1024-65535")
        gf_apply = button("Применить")
        gf_apply.clicked.connect(self._apply_gf)
        self.gf_mode.changed.connect(lambda _: self._apply_gf())
        l.addWidget(flow(self.gf_mode))
        l.addLayout(hbox(label("TCP:", "muted"), self.gf_tcp, label("UDP:", "muted"), self.gf_udp))
        l.addLayout(hbox(label("Пример: 1024-1934,1936-65535", "faint"), None, gf_apply))
        grid.addWidget(c)

        # --- IPSet
        c, l = section("IPSet Filter", "Какие IP обрабатывать помимо списков доменов.")
        self.ip_mode = Segmented([("loaded", "По списку"), ("none", "Выкл"), ("any", "Все IP")])
        self.ip_mode.changed.connect(self._set_ipset)
        self.ip_upd = button("Обновить список IP", icon=icons.icon("download"))
        self.ip_upd.clicked.connect(self._update_ipset)
        l.addWidget(flow(self.ip_mode, self.ip_upd, spacing=8))
        grid.addWidget(c)

        # --- фейки
        c, l = section("Активные фейки", "Какой фейковый пакет использовать для Discord UDP и игр.")
        self.fake_discord = QComboBox()
        self.fake_game = QComboBox()
        self.fake_discord.activated.connect(lambda _: self._set_fake("discord", self.fake_discord))
        self.fake_game.activated.connect(lambda _: self._set_fake("game", self.fake_game))
        l.addLayout(hbox(label("Discord UDP", "muted"), None, self.fake_discord))
        l.addLayout(hbox(label("Game Filter UDP", "muted"), None, self.fake_game))
        for cb in (self.fake_discord, self.fake_game):
            cb.setMinimumWidth(220)
        grid.addWidget(c)

        # --- hosts
        c, l = section("Файл hosts", "Записи из репозитория (GitHub и др.). Приложение добавляет их "
                                    "отдельным блоком и может убрать обратно.")
        self.hosts_state = label("Состояние неизвестно", "muted", wrap=True)
        self.h_check = button("Проверить", icon=icons.icon("search"))
        self.h_check.clicked.connect(self._check_hosts)
        self.h_upd = button("Обновить hosts", icon=icons.icon("download"))
        self.h_upd.clicked.connect(self._update_hosts)
        h_rm = button("Убрать записи", "link")
        h_rm.clicked.connect(self._remove_hosts)
        l.addWidget(self.hosts_state)
        l.addWidget(flow(self.h_check, self.h_upd, h_rm, spacing=8))
        grid.addWidget(c)

        # --- обновления батников
        c, l = section("Проверка обновлений в батниках",
                       "Штатная проверка открывает браузер при запуске батника. SimpleZapretGUI "
                       "проверяет обновления сам, в фоне, поэтому здесь она выключена.")
        self.bat_upd = Switch(False)
        self.bat_upd.toggled.connect(self._toggle_bat_updates)
        l.addLayout(hbox(label("Проверять при запуске .bat вручную"), None, self.bat_upd))
        self.upd_now = button("Проверить обновления zapret", icon=icons.icon("refresh"))
        self.upd_now.clicked.connect(self._check_updates)
        l.addWidget(flow(self.upd_now))
        grid.addWidget(c)

        # --- диагностика
        c, l = section("Диагностика", "Поиск конфликтов и типичных проблем (BFE, VPN, прокси, "
                                     "Adguard, Killer, WinDivert, DNS...).")
        self.run_d = button("Запустить диагностику", "primary", icons.icon("search", "#06111D"))
        self.run_d.clicked.connect(self._diagnose)
        self.diag_box = vbox(spacing=6)
        l.addWidget(flow(self.run_d))
        l.addLayout(self.diag_box)
        grid.addWidget(c)
        root.addStretch(1)

        ctl.status_changed.connect(self._render_status)
        ctl.strategies_changed.connect(self._fill_strategies)
        ctl.installed_changed.connect(lambda _: self.reload())
        self.reload()

    # ------------------------------------------------------------ отображение
    def reload(self):
        z = self.ctl.zap
        if not z.installed():
            return
        self._fill_strategies()
        gf = z.game_filter()
        self.gf_mode.set_value(gf["mode"])
        self.gf_tcp.setText(gf["tcp"])
        self.gf_udp.setText(gf["udp"])
        self.ip_mode.set_value(z.ipset_mode())
        fk = z.fakes()
        for combo, key in ((self.fake_discord, "discord"), (self.fake_game, "game")):
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(sorted(fk["files"].keys()))
            i = combo.findText(fk["active"].get(key, ""))
            combo.setCurrentIndex(i)
            combo.blockSignals(False)
        self.bat_upd.blockSignals(True)
        self.bat_upd.setChecked(z.bat_update_check_enabled())
        self.bat_upd.blockSignals(False)
        self._render_status(self.ctl.status)

    def _fill_strategies(self):
        cur = self.ctl.status.service_strategy or self.ctl.settings.get("last_strategy")
        self.svc_combo.fill(self.ctl.sorted_strategies(), cur)

    def _render_status(self, st):
        state = st.service_state if st.service_exists else ""
        self.svc_dot.set_color({"RUNNING": theme.OK, "STOP_PENDING": theme.ERR}.get(state,
                               theme.WARN if st.service_exists else theme.FAINT))
        self.svc_state.setText(f"Служба: {STATE_RU.get(state, state.lower())}")
        txt = f"Стратегия службы: {st.service_strategy}" if st.service_strategy else ""
        if st.service_foreign_path:
            txt += "\nУстановлена из другой папки — нажмите «Установить», чтобы перенести."
        if st.running and st.source != "service":
            txt += ("\n" if txt else "") + f"Сейчас работает winws.exe ({'сеанс' if st.source == 'app' else 'батник'})"
        self.svc_strategy.setText(txt)
        self.svc_strategy.setVisible(bool(txt))
        self.wd_state.setText(f"Драйвер WinDivert: {st.windivert_state.lower() or 'не загружен'}")
        self.svc_remove.setEnabled(st.service_exists or st.running or bool(st.windivert_state))
        self.svc_install.setText("Переустановить" if st.service_exists else "Установить")

    # ------------------------------------------------------------ действия
    def _install_service(self):
        self.svc_install.set_loading(True, "Установка…")
        self.ctl.install_service(self.svc_combo.current_name(), lambda: self.svc_install.set_loading(False))

    def _remove_service(self):
        if QMessageBox.question(self, "Службы", "Остановить обход и удалить службы zapret и WinDivert?") \
                == QMessageBox.Yes:
            self.svc_remove.set_loading(True, "Удаление…")
            self.ctl.remove_service(lambda: self.svc_remove.set_loading(False))

    def _check_updates(self):
        self.upd_now.set_loading(True, "Проверка…")
        self.ctl.check_updates(silent=False, finished=lambda: self.upd_now.set_loading(False))

    def _apply_gf(self):
        try:
            self.ctl.zap.set_game_filter(self.gf_mode.value() or "disabled", self.gf_tcp.text(),
                                         self.gf_udp.text())
            log.ok("Game Filter сохранён")
            self.ctl.restart_if_running("Game Filter")
            if self.ctl.status.service_exists and not self.ctl.status.running:
                log.warn("Переустановите службу, чтобы она использовала новый Game Filter")
        except Exception as e:  # noqa: BLE001
            self.ctl.push_error(f"Game Filter: {e}")

    def _set_ipset(self, mode):
        try:
            self.ctl.zap.set_ipset_mode(mode)
            log.ok(f"IPSet: {mode}")
            self.ctl.restart_if_running("IPSet")
        except Exception as e:  # noqa: BLE001
            self.ctl.push_error(f"IPSet: {e}")
            self.ip_mode.set_value(self.ctl.zap.ipset_mode())

    def _update_ipset(self):
        self.ip_upd.set_loading(True, "Загрузка…")

        def fin():
            self.ip_upd.set_loading(False)
            self.reload()
            self.ctl.restart_if_running("IPSet обновлён")
        self.ctl.update_ipset(fin)

    def _set_fake(self, kind, combo):
        try:
            self.ctl.zap.set_fake(kind, combo.currentText())
            log.ok(f"Активный фейк ({'Discord' if kind == 'discord' else 'игры'}): {combo.currentText()}")
            self.ctl.restart_if_running("фейки")
        except Exception as e:  # noqa: BLE001
            self.ctl.push_error(str(e))

    def _toggle_bat_updates(self, on: bool):
        f = self.ctl.zap.utils_dir / "check_updates.enabled"
        if on:
            if QMessageBox.question(self, "Проверка обновлений",
                                    "При ручном запуске .bat снова будет открываться браузер, если "
                                    "вышла новая версия. Включить?") != QMessageBox.Yes:
                self.bat_upd.setChecked(False)
                return
            f.write_text("ENABLED\r\n")
        else:
            f.unlink(missing_ok=True)

    def _check_hosts(self):
        self.h_check.set_loading(True, "Проверка…")
        self.hosts_state.setText("Сравниваю hosts с репозиторием zapret…")

        def work():
            return diagnostics.hosts_state(diagnostics.fetch_hosts_block())

        def done(s):
            self.h_check.set_loading(False)
            self.hosts_state.setText({"ok": "✓ hosts актуален", "partial": "Часть записей устарела — обновите",
                                      "missing": "Записей из репозитория в hosts нет"}.get(s, s))

        def fail(e):
            self.h_check.set_loading(False)
            self.hosts_state.setText(f"Не удалось скачать: {e}")

        run_bg(work, done, fail)

    def _update_hosts(self):
        if QMessageBox.question(self, "hosts", "Добавить в системный hosts записи из репозитория zapret?\n"
                                "Копия текущего файла будет сохранена.") != QMessageBox.Yes:
            return
        self.h_upd.set_loading(True, "Обновление…")

        def fin():
            self.h_upd.set_loading(False)
            self._check_hosts()
        self.ctl.update_hosts(fin)

    def _remove_hosts(self):
        if diagnostics.hosts_remove():
            log.ok("Записи SimpleZapretGUI убраны из hosts")
        else:
            log.info("В hosts нет записей, добавленных приложением")
        self._check_hosts()

    def run_diagnostics(self):
        """Вызов с главной: открыть «Сервис» и сразу запустить диагностику."""
        if not self.run_d.loading():
            self._diagnose()

    def _clear_diag(self):
        while self.diag_box.count():
            it = self.diag_box.takeAt(0)
            if it.layout():
                while it.layout().count():
                    w = it.layout().takeAt(0).widget()
                    if w:
                        w.deleteLater()
            elif it.widget():
                it.widget().deleteLater()

    def _diagnose(self):
        self._clear_diag()
        self.run_d.set_loading(True, "Проверяю…")
        wait_dot = Dot()
        wait_dot.set_busy(True)
        wait = label("Проверяю службы, сеть, DNS и конфликтующие программы…", "muted", wrap=True)
        self.diag_box.addLayout(hbox(wait_dot, wait, None, spacing=10))

        def done(checks):
            self.run_d.set_loading(False)
            self._clear_diag()
            for ch in checks:
                d = Dot({"ok": theme.OK, "warn": theme.WARN, "error": theme.ERR}[ch.level])
                t = label(ch.title + (f" — {ch.detail}" if ch.detail else ""), wrap=True)
                row = hbox(d, t, spacing=10)
                row.setStretch(1, 1)
                if ch.fix:
                    b = button(ch.fix_label or "Исправить")
                    b.clicked.connect(lambda _=False, k=ch.fix, bb=b: self._fix(k, bb))
                    row.addWidget(b)
                self.diag_box.addLayout(row)
            bad = [c for c in checks if c.level != "ok"]
            log.ok("Диагностика: проблем не найдено") if not bad else \
                log.warn(f"Диагностика: замечаний — {len(bad)}")

        def fail(e):
            self.run_d.set_loading(False)
            self._clear_diag()
            self.diag_box.addWidget(label(f"Ошибка: {e}", "muted"))

        run_bg(lambda: diagnostics.run_diagnostics(self.ctl.zap), done, fail)

    def _fix(self, key, b=None):
        if key == "reinstall":
            self.ctl.install_update(self.ctl.zap.version())
            return
        if b is not None:
            b.set_loading(True)

        def done(r):
            log.ok(r)
            self._diagnose()

        def fail(e):
            if b is not None:
                b.set_loading(False)
            self.ctl.push_error(e)

        run_bg(lambda: diagnostics.apply_fix(key, self.ctl.zap), done, fail)
