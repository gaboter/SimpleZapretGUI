"""Главная: уведомления, кнопка подключения, выбор стратегии, доступность, быстрые действия."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QSizePolicy,
                               QVBoxLayout, QWidget)

from ...core import winutil
from .. import icons, theme
from ..widgets import Dot, PowerButton, StrategyCombo, button, card, flow, hbox, label, vbox

SOURCE_TEXT = {"app": "Сеанс", "service": "Служба", "external": "Запущен вне приложения"}
HEALTH_TEXT = {"OK": "доступен", "CUT": "обрыв после 16 КБ", "TIMEOUT": "нет ответа", "RESET": "сброс",
               "SSL": "ошибка TLS", "DNS": "нет DNS", "ERROR": "ошибка", "UNSUP": "н/д"}
HEALTH_TIP = {
    "Discord": "Открывается ли discord.com",
    "YouTube": "Загружается ли страница YouTube целиком (провайдеры часто обрывают её после 16 КБ)",
    "Видео YouTube": "Доступен ли сервер видеопотока YouTube — без него видео не воспроизводится",
}


def fmt_uptime(sec: float) -> str:
    sec = int(max(0, sec))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def icon_label(name: str, color: str) -> QLabel:
    l = QLabel()
    l.setPixmap(icons.pixmap(name, color, 18))
    l.setFixedWidth(24)
    l.setAlignment(Qt.AlignCenter)          # значок строго по центру карточки
    return l


class Notice(QFrame):
    """Карточка уведомления: значок, текст и кнопки действий."""

    def __init__(self, kind: str, text: str, actions: list):
        super().__init__()
        self.setObjectName("errorCard" if kind == "error" else "warnCard")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 10, 10)
        lay.setSpacing(10)
        lay.addWidget(icon_label("alert", theme.ERR if kind == "error" else theme.WARN), 0, Qt.AlignVCenter)
        t = label(text, wrap=True)
        t.setTextInteractionFlags(Qt.TextSelectableByMouse)
        t.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        t.setMinimumWidth(160)
        lay.addWidget(t, 1, Qt.AlignVCenter)
        self.buttons = []
        btns = []
        for text_, obj, fn in actions:
            b = button(text_, obj)
            b.clicked.connect(lambda _=False, f=fn, bb=b: self._run(f, bb))
            btns.append(b)
            self.buttons.append(b)
        if btns:
            lay.addWidget(flow(*btns, align_right=True), 0, Qt.AlignVCenter)

    @staticmethod
    def _run(fn, b):
        def done():
            try:
                b.set_loading(False)
            except RuntimeError:
                pass          # карточка уже пересоздана — кнопки больше нет
        b.set_loading(True)
        fn(done)


class HomePage(QWidget):
    def __init__(self, ctl, go_to, run_test, run_diag):
        super().__init__()
        self.setObjectName("page")
        self.ctl = ctl
        self.go_to = go_to
        self.run_test = run_test
        self.run_diag = run_diag
        self._best_seen = None
        self._notice_sig = None
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 16, 22, 14)
        root.setSpacing(10)

        # --- новая версия самого SimpleZapretGUI
        self.app_banner = card("banner")
        ab = QHBoxLayout(self.app_banner)
        ab.setContentsMargins(14, 10, 10, 10)
        ab.setSpacing(10)
        self.app_banner_text = label("", wrap=True)
        self.app_banner_text.setMinimumWidth(160)
        self.app_notes_b = button("Что нового", "link")
        self.app_notes_b.clicked.connect(lambda: self._app_rel and winutil.open_path(self._app_rel.page))
        self.app_later_b = button("Позже", "link")
        self.app_later_b.clicked.connect(self._app_later)
        self.app_update_b = button("Обновить", "primary", icons.icon("download", "#06111D"))
        self.app_update_b.clicked.connect(self._app_update)
        ab.addWidget(icon_label("refresh", theme.ACCENT), 0, Qt.AlignVCenter)
        ab.addWidget(self.app_banner_text, 1, Qt.AlignVCenter)
        ab.addWidget(flow(self.app_notes_b, self.app_later_b, self.app_update_b, align_right=True),
                     0, Qt.AlignVCenter)
        self.app_banner.hide()
        self._app_rel = None
        root.addWidget(self.app_banner)

        # --- баннер обновления / установки
        self.banner = card("banner")
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(14, 10, 10, 10)
        bl.setSpacing(10)
        self.banner_text = label("", wrap=True)
        self.banner_text.setMinimumWidth(160)
        self.banner_btn = button("Обновить", "primary", icons.icon("download", "#06111D"))
        self.banner_btn.clicked.connect(self._banner_action)
        self.banner_zip = button("Из архива…", tip="Если GitHub недоступен — выберите скачанный zip")
        self.banner_zip.clicked.connect(self._pick_zip)
        self.banner_folder = button("Указать папку…", tip="Импортировать уже распакованный zapret")
        self.banner_folder.clicked.connect(self._pick_folder)
        self.banner_later = button("Позже", "link")
        self.banner_later.clicked.connect(self._skip_update)
        bl.addWidget(icon_label("download", theme.ACCENT), 0, Qt.AlignVCenter)
        bl.addWidget(self.banner_text, 1, Qt.AlignVCenter)
        bl.addWidget(flow(self.banner_zip, self.banner_folder, self.banner_later, self.banner_btn,
                          align_right=True), 0, Qt.AlignVCenter)
        self.banner.hide()
        root.addWidget(self.banner)

        # --- уведомления (вверху окна)
        self.notices = QVBoxLayout()
        self.notices.setSpacing(8)
        root.addLayout(self.notices)

        root.addStretch(1)

        # --- центр
        self.power = PowerButton(168)
        self.power.clicked.connect(self._toggle)
        self.state_title = label("Отключено", "h1")
        self.state_title.setAlignment(Qt.AlignCenter)
        self.state_title.setWordWrap(True)
        self.state_sub = label("", "muted", wrap=True)
        self.state_sub.setAlignment(Qt.AlignCenter)

        self.combo = StrategyCombo()
        self.combo.setMinimumWidth(240)
        self.combo.setMaximumWidth(420)
        self.combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.combo.currentIndexChanged.connect(self._strategy_changed)

        # приглашение на автотест (пока тестов не было)
        self.invite = card("invite")
        il = QHBoxLayout(self.invite)
        il.setContentsMargins(14, 10, 10, 10)
        il.setSpacing(10)
        il.addWidget(icon_label("gauge", theme.ACCENT), 0, Qt.AlignVCenter)
        it = label("Не знаете, какую стратегию выбрать? Автотест проверит все и сам подставит лучшую.",
                   wrap=True)
        it.setMinimumWidth(160)
        il.addWidget(it, 1, Qt.AlignVCenter)
        self.invite_btn = button("Запустить автотест", "primary", icons.icon("play", "#06111D"))
        self.invite_btn.clicked.connect(lambda: self.run_test(None))
        il.addWidget(self.invite_btn, 0, Qt.AlignVCenter)
        self.invite.setMaximumWidth(560)

        self.q_discord = button("Очистить кэш Discord", icon=icons.icon("broom"),
                                tip="Закрыть Discord и удалить его кэш — помогает, если после включения "
                                    "обхода Discord не грузится")
        self.q_discord.clicked.connect(self._discord)

        center = vbox(hbox(None, self.power, None), 2, self.state_title, self.state_sub, 10,
                      hbox(None, self.combo, None), 2, hbox(None, self.q_discord, None), 6,
                      hbox(None, self.invite, None), spacing=6)
        root.addLayout(center)
        root.addStretch(1)

        # --- доступность
        self.health_box = card()
        hl = QHBoxLayout(self.health_box)
        hl.setContentsMargins(14, 8, 10, 8)
        hl.setSpacing(10)
        self.health_items = {}
        cells = [label("Доступность:", "muted")]
        for title in ("Discord", "YouTube", "Видео YouTube"):
            d = Dot()
            l = label(f"{title}: —")
            w = QWidget()
            w.setToolTip(HEALTH_TIP[title])
            w.setLayout(hbox(d, l, spacing=6))
            self.health_items[title] = (d, l)
            cells.append(w)
        hl.addWidget(flow(*cells, spacing=14), 1)
        self.health_btn = button("Проверить", icon=icons.icon("refresh"))
        self.health_btn.clicked.connect(self.ctl.check_health)
        hl.addWidget(self.health_btn, 0, Qt.AlignVCenter)

        self.info = label("", "faint", wrap=True)
        self.info.setAlignment(Qt.AlignCenter)
        root.addWidget(self.info)
        root.addWidget(self.health_box)          # доступность — в самом низу

        self.tick = QTimer(self)
        self.tick.timeout.connect(self._render_status)
        self.tick.start(1000)

        ctl.status_changed.connect(lambda _: self._render_status())
        ctl.busy_changed.connect(lambda *_: self._render_status())
        ctl.strategies_changed.connect(self.reload)
        ctl.health_changed.connect(self._render_health)
        ctl.health_checking.connect(self._health_checking)
        ctl.error_raised.connect(lambda _: self._render_notices(self.ctl.status, force=True))
        ctl.update_available.connect(self._on_update)
        ctl.app_update_available.connect(self._on_app_update)
        ctl.installed_changed.connect(lambda _: self.reload())
        self.reload()

    # ------------------------------------------------------------ данные
    def reload(self):
        items = self.ctl.sorted_strategies()
        st = self.ctl.status
        cur = self.combo.current_name() or self.ctl.settings.get("last_strategy") or "general"
        best = self.ctl.best_strategy()
        if best and best != self._best_seen and not st.running:
            cur = best                                   # лучшая по тесту — сразу в селект
            self.ctl.settings.set("last_strategy", best)
        self._best_seen = best
        if st.running and st.strategy:
            cur = st.strategy
        self.combo.fill(items, cur)
        tested = any(i["score"] is not None for i in items)
        self.invite.setVisible(not tested and bool(items) and not self.ctl.testing)
        self._render_status()

    def _render_status(self):
        st = self.ctl.status
        busy = self.ctl.busy
        installed = st.installed or self.ctl.zap.installed()
        self.power.setEnabled(installed and not busy)
        if busy:
            self.power.set_state("busy")
            self.state_title.setText(busy.replace("...", "…"))
            self.state_sub.setText("")
        elif not installed:
            self.power.set_state("off")
            self.state_title.setText("zapret не установлен")
            self.state_sub.setText("Установите его кнопкой в баннере сверху")
        elif st.running:
            ext = st.source == "external"
            self.power.set_state("external" if ext else "on")
            self.state_title.setText("Обход работает" if not ext else "Работает вне приложения")
            parts = [st.strategy or "стратегия не определена", SOURCE_TEXT.get(st.source, "")]
            if st.started_at:
                parts.append(fmt_uptime(time.time() - st.started_at))
            self.state_sub.setText("   ·   ".join(p for p in parts if p))
        elif st.service_exists and st.service_state not in ("RUNNING", ""):
            self.power.set_state("error" if st.service_state == "STOP_PENDING" else "off")
            self.state_title.setText("Отключено")
            self.state_sub.setText(f"Служба zapret установлена, но {st.service_state.lower()}"
                                   + (f" ({st.service_strategy})" if st.service_strategy else ""))
        else:
            self.power.set_state("error" if self.ctl.errors() else "off")
            self.state_title.setText("Отключено")
            self.state_sub.setText("Нажмите, чтобы включить обход блокировок")
        if st.running and st.strategy and not busy and self.combo.current_name() != st.strategy:
            i = self.combo.findText(st.strategy)
            if i >= 0:
                self.combo.blockSignals(True)          # только отображение, без перезапуска
                self.combo.setCurrentIndex(i)
                self.combo.blockSignals(False)
        self.combo.setEnabled(installed and not busy)
        self._render_info(st)
        self._render_notices(st)

    def _render_info(self, st):
        if not st.installed:
            self.info.setText("")
            return
        z = self.ctl.zap
        try:
            gf = z.game_filter()["mode"]
            ip = z.ipset_mode()
        except Exception:
            gf, ip = "?", "?"
        gf_t = {"disabled": "выкл", "all": "TCP+UDP", "tcp": "TCP", "udp": "UDP"}.get(gf, gf)
        wd = st.windivert_state.lower() if st.windivert_state else "не загружен"
        self.info.setText(f"zapret {st.version}   ·   Game Filter: {gf_t}   ·   IPSet: {ip}   ·   "
                          f"WinDivert: {wd}")

    # ------------------------------------------------------------ уведомления
    def _actions_for(self, code: str) -> list:
        c = self.ctl
        off = ("Выключить", "danger", c.shutdown_everything)
        diag = ("Диагностика", "", lambda f: (f(), self.run_diag()))
        return {
            "external": [off],
            "foreign": [("Перенести в приложение", "", c.migrate_foreign_service), off],
            "stop_pending": [off, diag],
            "windivert": [("Выгрузить драйвер", "", c.unload_driver), diag],
            "conflicts": [off, ("Удалить эти службы", "", c.remove_conflicts)],
            "no_sys": [("Переустановить zapret", "",
                        lambda f: c.install_update(c.zap.version() or "", finished=f)), diag],
        }.get(code, [diag])

    def _render_notices(self, st, force: bool = False):
        errs = self.ctl.errors()[-3:]
        sig = (tuple(st.issues), tuple(errs))
        if sig == self._notice_sig and not force:
            return
        self._notice_sig = sig
        while self.notices.count():
            w = self.notices.takeAt(0).widget()
            if w:
                w.deleteLater()
        for code, text in st.issues:
            self.notices.addWidget(Notice("warn", text, self._actions_for(code)))
        if errs:
            self.notices.addWidget(Notice("error", "\n".join(errs), [
                ("Диагностика", "", lambda f: (f(), self.run_diag())),
                ("Скрыть", "link", lambda f: (f(), self.ctl.clear_errors())),
            ]))

    # ------------------------------------------------------------ доступность
    def _health_checking(self, on: bool):
        self.health_btn.set_loading(on, "Проверка…" if on else None)
        if on:
            for title, (d, l) in self.health_items.items():
                d.set_busy(True)
                l.setText(f"{title}: проверка…")

    def _render_health(self, h: dict):
        for title, (d, l) in self.health_items.items():
            r = h.get(title)
            if r is None:
                d.set_color(theme.FAINT)
                l.setText(f"{title}: —")
            else:
                d.set_color(theme.OK if r == "OK" else theme.ERR)
                l.setText(f"{title}: {HEALTH_TEXT.get(r, r)}")

    # ------------------------------------------------------------ быстрые действия
    def _discord(self):
        if QMessageBox.question(self, "Discord", "Закрыть Discord и очистить его кэш?") != QMessageBox.Yes:
            return
        self.q_discord.set_loading(True, "Очистка…")
        self.ctl.clear_discord(lambda: self.q_discord.set_loading(False))

    # ------------------------------------------------------------ обновления
    def _on_update(self, remote: str, local: str):
        self._pending_update = remote
        if not self.ctl.zap.installed():
            self.banner_text.setText("zapret ещё не установлен. SimpleZapretGUI скачает последнюю версию "
                                     f"{remote or ''} с GitHub и настроит её.")
            self.banner_btn.setText("Установить")
            self.banner_later.hide()
            self.banner_zip.show()
            self.banner_folder.show()
            self.banner.show()
        elif remote and remote != self.ctl.settings.get("skipped_version"):
            self.banner_text.setText(f"Доступна новая версия zapret {remote} (у вас {local}). "
                                     "Ваши списки, стратегии и настройки будут перенесены.")
            self.banner_btn.setText("Обновить")
            self.banner_later.show()
            self.banner_zip.hide()
            self.banner_folder.hide()
            self.banner.show()
        else:
            self.banner.hide()

    def _on_app_update(self, rel):
        from ...core.paths import APP_VERSION
        self._app_rel = rel
        self.app_banner_text.setText(f"Вышла новая версия SimpleZapretGUI {rel.version} (у вас {APP_VERSION}). "
                                     "Обновление займёт минуту, настройки и zapret сохранятся.")
        self.app_notes_b.setVisible(bool(rel.page))
        self.app_banner.show()

    def _app_later(self):
        if self._app_rel:
            self.ctl.settings.set("skipped_app_version", self._app_rel.version)
        self.app_banner.hide()

    def _app_update(self):
        if not self._app_rel:
            return
        self.app_update_b.set_loading(True, "Загрузка…")
        self.ctl.install_app_update(self._app_rel, lambda: self.app_update_b.set_loading(False))

    def _banner_action(self):
        self.banner_btn.set_loading(True, "Установка…")

        def fin():
            self.banner_btn.set_loading(False)
        self.ctl.install_update(getattr(self, "_pending_update", ""), finished=fin)

    def _skip_update(self):
        self.ctl.settings.set("skipped_version", getattr(self, "_pending_update", ""))
        self.banner.hide()

    def _pick_zip(self):
        p, _ = QFileDialog.getOpenFileName(self, "Архив zapret-discord-youtube", "", "ZIP (*.zip)")
        if p:
            self.banner_zip.set_loading(True, "Установка…")
            self.ctl.install_from_zip(p, lambda: self.banner_zip.set_loading(False))

    def _pick_folder(self):
        p = QFileDialog.getExistingDirectory(self, "Папка с zapret-discord-youtube")
        if p:
            self.banner_folder.set_loading(True, "Импорт…")
            self.ctl.install_from_folder(p, lambda: self.banner_folder.set_loading(False))

    # ------------------------------------------------------------ подключение
    def _toggle(self):
        st = self.ctl.status
        if self.ctl.busy:
            return
        if st.running:
            if st.source == "external":
                r = QMessageBox.question(self, "SimpleZapretGUI",
                                         "Обход запущен не из приложения (вручную батником). Выключить его?")
                if r != QMessageBox.Yes:
                    return
                self.ctl.shutdown_everything()
                return
            self.ctl.disconnect()
        else:
            name = self.combo.current_name()
            if name:
                self.ctl.connect(name)      # режим (сеанс/служба) — из настроек

    def _strategy_changed(self, _i):
        name = self.combo.current_name()
        if not name:
            return
        self.ctl.settings.set("last_strategy", name)
        st = self.ctl.status
        if st.running and st.source in ("app", "service") and name != st.strategy:
            self.ctl.connect(name, "service" if st.source == "service" else "session")
