"""Подбор стратегии — автоматический поиск работающих приёмов обхода по методике blockcheck."""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QDialog, QFrame, QLineEdit, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QProgressBar, QVBoxLayout, QWidget)

from ...core import log
from ...core.builder import SITE_STATUS, Builder, GroupResult, check_site, site_pool
from ...core.lists import normalize, validate
from ...core.paths import USER_STRATEGIES_DIR
from ...core.strategy import build_bat, safe_filename
from .. import icons, theme
from ..widgets import CardGrid, Dot, button, card, flow, hbox, label, run_bg, vbox

STATE_COLOR = {"найдено": theme.OK, "не блокируется": theme.ACCENT, "не найдено": theme.ERR,
               "остановлено": theme.WARN, "ожидание": theme.FAINT, "пропущено": theme.FAINT}
GROUP_HINT = {
    "youtube": "Сайт, превью и видеопоток YouTube, служебные файлы Google (gstatic)",
    "discord": "Сайт, чат (gateway) и файлы Discord",
    "cloudflare": "Сайты на Cloudflare — через него работает большая часть интернета. Обходятся через IPSet",
    "sites": "Несколько заблокированных сайтов из вашего списка обхода",
}
GROUPS = (("youtube", "YouTube"), ("discord", "Discord"), ("cloudflare", "Cloudflare"), ("sites", "Другие сайты"))
GOOD = 0.95          # стратегия с таким результатом автотеста считается хорошей


class _Bridge(QObject):
    progress = Signal(str, int, int)
    group = Signal(object)
    log = Signal(str)


class GroupCard(QFrame):
    def __init__(self, key: str, title: str):
        super().__init__()
        self.setObjectName("card")
        self.key = key
        lay = vbox(margins=(14, 12, 14, 12), spacing=6)
        self.setLayout(lay)
        self.dot = Dot()
        self.state = label("ожидание", "muted")
        lay.addLayout(hbox(label(title, "h2"), None, self.dot, self.state, spacing=8))
        lay.addWidget(label(GROUP_HINT.get(key, ""), "faint", wrap=True))
        self.detail = label("", "muted", wrap=True)
        self.detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.detail)

    def show_result(self, r: GroupResult):
        self.state.setText(r.state)
        if r.state == "проверка":
            self.dot.set_busy(True)
            self.state.setStyleSheet(f"color:{theme.ACCENT};")
        else:
            self.dot.set_color(STATE_COLOR.get(r.state, theme.FAINT))
            self.state.setStyleSheet(f"color:{STATE_COLOR.get(r.state, theme.MUTED)};")
        lines = []
        if r.targets:
            lines.append("Проверяю: " + ", ".join(r.targets))
        if r.baseline:
            lines.append(r.baseline)
        if r.tried:
            lines.append(f"Испробовано вариантов: {r.tried}, рабочих: {len(r.working)}")
        if r.winner:
            lines.append(f"Лучший: {r.winner.label}")
            lines.append(f"Стабильность: {round(r.stability * 100)}%")
        if r.note:
            lines.append(r.note)
        self.detail.setText("\n".join(lines))


STATUS_COLOR = {"ok": theme.OK, "blocked": theme.ERR, "skip": theme.FAINT}


class SitesDialog(QDialog):
    """Какие сайты проверяет группа «Другие сайты» — и возможность выбрать свои."""

    def __init__(self, parent, pool: list[str], chosen: list[str], bypass_on: bool):
        super().__init__(parent)
        self.setWindowTitle("Другие сайты для подбора")
        self.resize(560, 600)
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        lay.addWidget(label("Какие сайты проверяются", "h2"))
        lay.addWidget(label(
            "Подбор берёт сайты из вашего списка обхода (Discord, YouTube и служебные домены проверяются "
            "отдельно) и смотрит, какие из них у вас сейчас не открываются. На заблокированных — до четырёх — "
            "он и подбирает приёмы обхода.\n\nGoogle и Cloudflare здесь нет: их нет в списке обхода — они "
            "открываются через IPSet или вообще без обхода. Их проверяет итоговое сравнение, как и автотест.\n\n"
            "Можно отметить свои сайты — тогда проверяться будут только они.",
            "muted", wrap=True))
        if bypass_on:
            w = label("Сейчас обход включён — сайты могут открываться благодаря ему. Для точной проверки "
                      "выключите обход на главной.", wrap=True)
            w.setStyleSheet(f"color:{theme.WARN};")
            lay.addWidget(w)
        self.list = QListWidget()
        self.list.itemChanged.connect(lambda _: self._count())
        lay.addWidget(self.list, 1)
        for d in list(dict.fromkeys(chosen + pool)):
            self._add_item(d, d in chosen)
        self.add_edit = QLineEdit()
        self.add_edit.setPlaceholderText("Добавить свой сайт, например rutracker.org")
        self.add_edit.returnPressed.connect(self._add_typed)
        add_b = button("Добавить", icon=icons.icon("plus"))
        add_b.clicked.connect(self._add_typed)
        lay.addLayout(hbox(self.add_edit, add_b, spacing=6))
        self.check_b = button("Проверить доступность", icon=icons.icon("search"),
                              tip="Проверить без обхода, какие сайты у вас сейчас заблокированы")
        self.check_b.clicked.connect(self._check)
        lay.addWidget(flow(self.check_b, spacing=8))
        self.count = label("", "faint", wrap=True)
        lay.addWidget(self.count)
        auto_b = button("Выбирать автоматически", tip="Снять все отметки — подбор выберет сайты сам")
        auto_b.clicked.connect(self._auto)
        ok = button("Готово", "primary")
        ok.clicked.connect(self.accept)
        cancel = button("Отмена")
        cancel.clicked.connect(self.reject)
        lay.addWidget(flow(auto_b, cancel, ok, spacing=8, align_right=True))
        self._count()

    def _add_item(self, domain: str, checked: bool):
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == domain:
                self.list.item(i).setCheckState(Qt.Checked if checked else self.list.item(i).checkState())
                return
        it = QListWidgetItem(domain)
        it.setData(Qt.UserRole, domain)
        it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
        it.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        self.list.addItem(it)

    def _add_typed(self):
        d = normalize(self.add_edit.text(), "domain").lstrip("^")
        if not d:
            return
        err = validate(d, "domain")
        if err:
            self.count.setText(err)
            return
        self._add_item(d, True)
        self.add_edit.clear()
        self.list.scrollToBottom()
        self._count()

    def _auto(self):
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(Qt.Unchecked)

    def _check(self):
        self.check_b.set_loading(True, "Проверяю…")
        domains = [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]

        def work():
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(12) as ex:
                return dict(zip(domains, ex.map(lambda d: check_site(d, 4), domains)))

        def done(res):
            try:
                self.check_b.set_loading(False)
            except RuntimeError:
                return                              # окно уже закрыли
            for i in range(self.list.count()):
                it = self.list.item(i)
                code = res.get(it.data(Qt.UserRole), "")
                text, kind = SITE_STATUS.get(code, ("не отвечает — для проверки не подходит", "skip"))
                it.setText(f"{it.data(Qt.UserRole)}   —   {text}")
                it.setForeground(QColor(STATUS_COLOR[kind]))
            blocked = sum(1 for c in res.values() if SITE_STATUS.get(c, ("", "skip"))[1] == "blocked")
            self.count.setText(f"Заблокировано у вас сейчас: {blocked} из {len(res)}. " + self._mode_text())

        run_bg(work, done, lambda e: self.check_b.set_loading(False))

    def _mode_text(self) -> str:
        n = len(self.selected())
        return (f"Отмечено {n} — подбор будет проверять только их." if n else
                "Ничего не отмечено — подбор сам выберет до четырёх заблокированных из списка.")

    def _count(self):
        self.count.setText(self._mode_text())

    def selected(self) -> list[str]:
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]


class BuilderPage(QWidget):
    def __init__(self, ctl, open_strategy, run_test):
        super().__init__()
        self.setObjectName("page")
        self.ctl = ctl
        self.open_strategy = open_strategy
        self.run_test = run_test
        self.builder: Builder | None = None
        self.saved_name = ""
        self._started = 0.0
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 14)
        root.setSpacing(10)
        root.addWidget(label("Подбор стратегии", "h1"))
        root.addWidget(label(
            "Программа сама переберёт приёмы обхода — нарезку пакетов, фейки, перекрытие sequence, TTL — и "
            "соберёт стратегию, которая работает именно у вашего провайдера. Десятки вариантов проверяются "
            "одновременно на разных заблокированных адресах, поэтому подбор занимает минуты, а не часы. "
            "Пока он идёт, обход временно выключен.", "muted", wrap=True))
        how = button("Как устроены приёмы обхода (документация zapret)", "link",
                     icons.icon("external", theme.ACCENT, size=14))
        how.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://github.com/bol-van/zapret#nfqws")))
        root.addWidget(flow(how))

        # --- «у вас уже есть хорошая стратегия» / «сначала автотест»
        self.gate = card("invite")
        gl = vbox(margins=(14, 12, 14, 12), spacing=8)
        self.gate.setLayout(gl)
        self.gate_text = label("", wrap=True)
        self.gate_on = button("Включить её", "primary", icons.icon("power", "#06111D"))
        self.gate_on.clicked.connect(lambda: self._gate_best and self.ctl.connect(self._gate_best.name))
        self.gate_test = button("Запустить автотест", "primary", icons.icon("play", "#06111D"))
        self.gate_test.clicked.connect(lambda: self.run_test(None))
        gl.addWidget(self.gate_text)
        gl.addWidget(flow(self.gate_on, self.gate_test, spacing=8))
        self._gate_best = None
        root.addWidget(self.gate)

        # --- настройки подбора
        opt = card()
        ol = vbox(margins=(14, 12, 14, 12), spacing=10)
        opt.setLayout(ol)
        ol.addWidget(label("Что подбирать", "h2"))
        self.cb = {}
        boxes = []
        for key, title in GROUPS:
            c = QCheckBox(title)
            c.setChecked(True)
            c.setToolTip(GROUP_HINT[key])
            self.cb[key] = c
            boxes.append(c)
        self.sites_b = button("Какие сайты?", "link", icons.icon("list", theme.ACCENT, size=14),
                              "Показать, какие сайты проверяются в группе «Другие сайты», и выбрать свои")
        self.sites_b.clicked.connect(self._choose_sites)
        ol.addWidget(flow(*boxes, self.sites_b, spacing=18))
        self.sites_info = label("", "faint", wrap=True)
        ol.addWidget(self.sites_info)
        self.inherit_info = label("", "faint", wrap=True)
        ol.addWidget(self.inherit_info)
        ol.addWidget(label("Каждый найденный вариант перепроверяется трижды, а затем докручивается до самого "
                           "стабильного. Обычно занимает 3–10 минут.", "faint", wrap=True))
        self.start_b = button("Найти стратегию", "primary", icons.icon("wand", "#06111D"))
        self.start_b.clicked.connect(self.start)
        self.stop_b = button("Остановить", "danger", icons.icon("stop", theme.ERR))
        self.stop_b.clicked.connect(self.stop)
        self.stop_b.hide()
        ol.addWidget(flow(self.start_b, self.stop_b, spacing=8))
        self.progress = QProgressBar()
        self.progress.setFixedHeight(6)
        self.progress.hide()
        self.progress_text = label("", "muted", wrap=True)
        ol.addWidget(self.progress)
        ol.addWidget(self.progress_text)
        root.addWidget(opt)

        # --- результат
        self.done_card = card("invite")
        dl = vbox(margins=(14, 12, 14, 12), spacing=8)
        self.done_card.setLayout(dl)
        self.done_text = label("", wrap=True)
        self.on_b = button("Включить", "primary", icons.icon("power", "#06111D"))
        self.on_b.clicked.connect(lambda: self.saved_name and (self.ctl.connect(self.saved_name)))
        self.test_b = button("Проверить автотестом", icon=icons.icon("gauge"))
        self.test_b.clicked.connect(lambda: self.saved_name and self.run_test([self.saved_name]))
        self.edit_b = button("Открыть в редакторе", icon=icons.icon("sliders"))
        self.edit_b.clicked.connect(lambda: self.saved_name and self.open_strategy(self.saved_name))
        dl.addWidget(self.done_text)
        dl.addWidget(flow(self.on_b, self.test_b, self.edit_b, spacing=8))
        self.done_card.hide()
        root.addWidget(self.done_card)

        # --- группы
        self.grid = CardGrid(min_card_width=260)
        self.cards = {}
        for key, title in GROUPS:
            gc = GroupCard(key, title)
            self.cards[key] = gc
            self.grid.add(gc)
        root.addWidget(self.grid)

        root.addWidget(label("Ход подбора", "h2"))
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("code")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(500)
        self.log_view.setMinimumHeight(140)
        root.addWidget(self.log_view, 1)

        self.bridge = _Bridge()
        self.bridge.progress.connect(self._on_progress)
        self.bridge.group.connect(self._on_group)
        self.bridge.log.connect(self._on_log)
        self.clock = QTimer(self)
        self.clock.setInterval(1000)
        self.clock.timeout.connect(self._tick)
        self._sites_summary()
        self._inherit_summary()
        ctl.installed_changed.connect(lambda _: (self._sites_summary(), self._inherit_summary()))
        ctl.strategies_changed.connect(self._inherit_summary)

    def _best_existing(self):
        """(стратегия, оценка) лучшей по автотесту или (None, None)."""
        for it in self.ctl.sorted_strategies():
            if it["score"] is not None and not it["error"]:
                s = self.ctl.zap.strategy(it["name"])
                if s and not s.error:
                    return s, it["score"]
            break
        return None, None

    def _inherit_summary(self):
        best, score = self._best_existing()
        tested = any(i["score"] is not None for i in self.ctl.sorted_strategies())
        # подсказка над подбором: подбор нужен, когда хороших стратегий нет
        self._gate_best = best if best and score >= GOOD else None
        if self._gate_best:
            self.gate_text.setText(
                f"У вас уже есть стратегия, которая хорошо работает: «{best.name}» прошла автотест на "
                f"{round(score * 100)}%. Подбор нужен, когда ни одна стратегия не работает как следует — "
                "можно просто включить эту.")
            self.gate_on.show()
            self.gate_test.hide()
            self.gate.show()
        elif not tested and self.ctl.zap.installed():
            self.gate_text.setText(
                "Автотест ещё не запускался. Сначала проверьте готовые стратегии — возможно, хорошая уже есть, "
                "и подбирать ничего не придётся. Подбор нужен, если ни одна не сработает.")
            self.gate_on.hide()
            self.gate_test.show()
            self.gate.show()
        else:
            self.gate.hide()
        if self._gate_best:
            self.inherit_info.setText(
                f"Для групп, которые вы снимете, стратегия возьмёт приёмы из «{best.name}». В конце подобранная "
                "стратегия сравнивается с ней на тех же целях, что и в автотесте.")
        else:
            self.inherit_info.setText(
                "Хорошей готовой стратегии у вас нет, поэтому для снятых групп возьмутся стандартные приёмы "
                "zapret-discord-youtube. Чтобы новая стратегия работала везде, оставьте отмеченными все группы.")

    # ------------------------------------------------------------ «другие сайты»
    def _custom_sites(self) -> list[str]:
        return list(self.ctl.settings.get("builder_sites") or [])

    def _sites_summary(self):
        custom = self._custom_sites()
        if custom:
            shown = ", ".join(custom[:5]) + (f" и ещё {len(custom) - 5}" if len(custom) > 5 else "")
            self.sites_info.setText(f"Другие сайты: выбраны вами — {shown}.")
        else:
            n = len(site_pool(self.ctl.zap.lists_dir)) if self.ctl.zap.lists_dir.exists() else 0
            self.sites_info.setText(f"Другие сайты: подбор сам выберет до четырёх заблокированных из {n} "
                                    "сайтов вашего списка обхода.")

    def _choose_sites(self):
        pool = site_pool(self.ctl.zap.lists_dir) if self.ctl.zap.lists_dir.exists() else []
        d = SitesDialog(self, pool, self._custom_sites(), self.ctl.status.running)
        if d.exec():
            self.ctl.settings.set("builder_sites", d.selected())
            self._sites_summary()

    # ------------------------------------------------------------ управление
    def start(self):
        if self.builder:
            return
        if not self.ctl.zap.installed():
            self.ctl.push_error("zapret не установлен")
            return
        if self.ctl.busy:
            log.warn(f"Подождите: {self.ctl.busy}")
            return
        keys = [k for k, c in self.cb.items() if c.isChecked()]
        if not keys:
            self._on_log("Отметьте хотя бы одну группу.")
            return
        custom = self._custom_sites()
        proven, best, good_tpl = [], None, ""
        for it in self.ctl.sorted_strategies()[:3]:
            if it["score"] is not None:
                s = self.ctl.zap.strategy(it["name"])
                if s and not s.error:
                    proven.append(s.template)
                    if best is None:
                        best = (s.name, s.template)
                        good_tpl = s.template if it["score"] >= GOOD else ""
        self.builder = Builder(self.ctl.zap, keys, thorough=False,
                               timeout=float(self.ctl.settings.get("test_timeout") or 5),
                               proven_templates=proven, custom_sites=custom, best_existing=best,
                               inherit_template=good_tpl,
                               on_progress=lambda s, i, n: self.bridge.progress.emit(s, i, n),
                               on_group=lambda r: self.bridge.group.emit(r),
                               on_log=lambda s: self.bridge.log.emit(s))
        for k, gc in self.cards.items():
            gc.setVisible(k in keys)
            gc.show_result(GroupResult(k, k, state="ожидание"))
        self.log_view.clear()
        self.done_card.hide()
        self.saved_name = ""
        self.ctl.testing = True
        self.ctl.set_busy_external("Подбор стратегии…")
        self.start_b.set_loading(True, "Идёт подбор…")
        self.stop_b.show()
        self.progress.setRange(0, 0)
        self.progress.show()
        self._started = time.time()
        self.clock.start()
        self._on_log("Начинаю подбор. Обход на время подбора выключен.")
        run_bg(self.builder.run, self._finished, self._failed)

    def stop(self):
        if self.builder:
            self.builder.cancel.set()
            self.stop_b.set_loading(True, "Останавливаю…")
            self._on_log("Останавливаю после текущего варианта…")

    def _reset(self):
        self.builder = None
        self.ctl.testing = False
        self.clock.stop()
        self.ctl.set_busy_external("")
        self.start_b.set_loading(False)
        self.stop_b.set_loading(False)
        self.stop_b.hide()
        self.progress.hide()
        self.ctl.refresh_status()
        self.ctl.builder_finished.emit()

    def _failed(self, msg: str):
        self._reset()
        self.ctl.push_error(f"Ошибка подбора: {msg}")

    def _finished(self, results: dict):
        b = self.builder
        self._reset()
        found = {k: r for k, r in results.items() if r.winner}
        spent = int(time.time() - self._started)
        if not found:
            unblocked = all(r.state == "не блокируется" for r in results.values())
            self.done_text.setText(
                "Всё выбранное открывается и без обхода — подбирать нечего." if unblocked else
                "Не удалось найти рабочий вариант. Возможные причины: блокировка по IP-адресу (её обходом не "
                "победить) или VPN и другая программа мешает zapret — проверьте «Сервис» → «Диагностика».")
            self.done_card.show()
            for w in (self.on_b, self.test_b, self.edit_b):
                w.hide()
            self.progress_text.setText(f"Подбор завершён за {spent // 60} мин {spent % 60} с.")
            return
        name = safe_filename(time.strftime("Подобранная %d.%m %H-%M"))
        data = build_bat(b.strategy_template()).encode("utf-8")
        USER_STRATEGIES_DIR.mkdir(parents=True, exist_ok=True)
        (USER_STRATEGIES_DIR / f"{name}.bat").write_bytes(data)
        (self.ctl.zap.root / f"{name}.bat").write_bytes(data)
        self.saved_name = name
        self.ctl.strategies_changed.emit()
        parts = [f"{r.title}: {r.winner.label}" for r in found.values()]
        verdict = ""
        cmp = b.compare
        if "new" in cmp:
            n_ok, n_tot = cmp["new"]
            if "best" in cmp:
                bname, b_ok, b_tot = cmp["best"]
                if b_ok > n_ok:
                    verdict = (f"\n\nВаша «{bname}» в той же проверке справилась лучше: {b_ok} из {b_tot} против "
                               f"{n_ok} из {n_tot}. Подобранную стратегию включать не обязательно.")
                elif b_ok == n_ok:
                    verdict = (f"\n\nПодобранная работает так же, как ваша «{bname}»: по {n_ok} из {n_tot} "
                               "проверок.")
                else:
                    verdict = (f"\n\nПодобранная лучше вашей «{bname}»: {n_ok} из {n_tot} против "
                               f"{b_ok} из {b_tot}.")
            else:
                verdict = f"\n\nПроверка стратегии целиком: {n_ok} из {n_tot}."
        if b.dns_borrowed:
            verdict += ("\n\nВаш DNS не отдаёт адрес " + ", ".join(sorted(b.dns_borrowed)) + ". Подбор проверил эти "
                        "сайты через соседние серверы, но браузер и автотест могут показывать «нет DNS» — это не вина "
                        "стратегии. Помогает DNS-over-HTTPS: «Параметры Windows» → «Сеть и Интернет» → свойства "
                        "подключения → «Назначение DNS-сервера» → 1.1.1.1 или 8.8.8.8 с шифрованием.")
        self.done_text.setText(f"Готово! Стратегия «{name}» сохранена среди ваших стратегий.\n" + "\n".join(parts)
                               + verdict)
        for w in (self.on_b, self.test_b, self.edit_b):
            w.show()
        self.done_card.show()
        self.progress_text.setText(f"Подбор завершён за {spent // 60} мин {spent % 60} с.")
        log.ok(f"Подобрана стратегия «{name}»")

    # ------------------------------------------------------------ события подбора
    def _tick(self):
        s = int(time.time() - self._started)
        base = self.progress_text.text().split("   ·   ")[0]
        self.progress_text.setText(f"{base}   ·   {s // 60}:{s % 60:02d}")

    def _on_progress(self, text: str, i: int, n: int):
        if n > 0 and text:
            self.progress.setRange(0, n)
            self.progress.setValue(min(i, n))
            self.progress_text.setText(text)
            self.ctl.test_progress.emit(min(i, n), n, text)

    def _on_group(self, r: GroupResult):
        gc = self.cards.get(r.key)
        if gc:
            gc.show_result(r)

    def _on_log(self, text: str):
        self.log_view.appendPlainText(f"{time.strftime('%H:%M:%S')}  {text}")
