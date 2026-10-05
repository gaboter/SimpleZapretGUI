"""Автотест стратегий."""
from __future__ import annotations

import re
import time

from PySide6.QtCore import QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QHeaderView, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressBar, QSpinBox,
                               QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ...core import log
from ...core.paths import TARGETS_FILE
from ...core.tester import (TARGET_INFO, TestRunner, ensure_targets_file, parse_targets, resource_targets)
from .. import icons, theme
from ..widgets import (RowDelegate, Segmented, button, card, flow, hbox, label, paint_battery, run_bg,
                       setup_rows, vbox)

RES_COLOR = {"OK": theme.OK, "UNSUP": theme.FAINT, "CUT": theme.WARN}
RES_TEXT = {"OK": "OK", "UNSUP": "н/д", "CUT": "обрыв 16 КБ", "TIMEOUT": "нет ответа", "RESET": "сброс",
            "SSL": "ошибка TLS", "DNS": "нет DNS", "ERROR": "ошибка"}
RES_TIP = {
    "OK": "Работает: соединение установлено, данные получены.",
    "CUT": "Обрыв после ~16 КБ: соединение начинается, но провайдер его обрывает. Сайт будет "
           "грузиться бесконечно — типичный признак блокировки.",
    "TIMEOUT": "Нет ответа за отведённое время: ресурс заблокирован или очень медленный.",
    "RESET": "Соединение сброшено — обычно так срабатывает блокировка провайдера.",
    "SSL": "Ошибка защищённого соединения — провайдер вмешивается в TLS.",
    "DNS": "Не удалось узнать адрес сайта: проблема с DNS, а не со стратегией. На оценку не влияет.",
    "UNSUP": "Сервер не поддерживает этот вариант протокола. Это не ошибка, на оценку не влияет.",
    "ERROR": "Другая ошибка соединения.",
}
COL_TIP = {
    1: "HTTP — загрузка страницы целиком (до 40 КБ). Так видно, не обрывает ли провайдер соединение "
       "после первых 16 КБ.",
    2: "TLS 1.2 — установка защищённого соединения по протоколу TLS 1.2.",
    3: "TLS 1.3 — установка защищённого соединения по протоколу TLS 1.3 (так работает большинство "
       "современных сайтов).",
}
PING_HELP = ("Пинг — время ответа узла. До 60 мс — отлично, 60–150 мс — нормально, больше 150 мс — "
             "медленно. Это только проверка, что интернет вообще работает, на оценку стратегии не влияет.")


def ping_quality(v: str) -> tuple[str, str]:
    m = re.match(r"(\d+)", v or "")
    if not m:
        return theme.ERR, "Узел не ответил — возможно, нет интернета или ping заблокирован."
    ms = int(m.group(1))
    if ms <= 60:
        return theme.OK, f"{ms} мс — отлично."
    if ms <= 150:
        return theme.WARN, f"{ms} мс — нормально."
    return theme.ERR, f"{ms} мс — медленно."


class _Bridge(QObject):
    progress = Signal(int, int, str)
    result = Signal(object)


class StrategyRows(RowDelegate):
    """Строки списка стратегий; в колонке «Оценка» — батарейка, у проверяемой — анимация."""

    def __init__(self, view, page):
        super().__init__(view, hover=True, select=True, row_height=34,
                         highlight=lambda idx: page.testing_name and
                         view.topLevelItem(idx.row()) is not None and
                         view.topLevelItem(idx.row()).text(0) == page.testing_name)
        self.page = page

    def paint(self, p: QPainter, option, index):
        if index.column() != 1:
            return super().paint(p, option, index)
        self.paint_background(p, option, index)
        item = self.view.topLevelItem(index.row())
        r = QRectF(option.rect).adjusted(8, 0, 0, 0)
        if item is not None and item.text(0) == self.page.testing_name:
            # «заряжающаяся» батарейка — стратегия проверяется прямо сейчас
            phase = (time.time() * 2.2) % 5
            paint_battery(p, r, min(1.0, int(phase) / 4) if phase >= 1 else 0.0, False, with_text=False)
            p.save()
            p.setPen(QColor(theme.ACCENT))
            p.drawText(QRectF(r.left() + 30, r.top(), 60, r.height()), Qt.AlignVCenter | Qt.AlignLeft, "тест…")
            p.restore()
            return
        paint_battery(p, r, index.data(Qt.UserRole), bool(index.data(Qt.UserRole + 1)))


class TargetPicker(QDialog):
    """Выбор ресурсов из списков для автотеста."""

    def __init__(self, parent, domains: list[str], selected: list[str]):
        super().__init__(parent)
        self.setWindowTitle("Цели автотеста из списка ресурсов")
        self.resize(520, 560)
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        lay.addWidget(label("Отметьте сайты, доступность которых проверять при автотесте.", "muted", wrap=True))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск")
        self.search.textChanged.connect(self._filter)
        self.list = QListWidget()
        sel = set(selected)
        for d in sorted(set(domains)):
            it = QListWidgetItem(d)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if d in sel else Qt.Unchecked)
            self.list.addItem(it)
        all_b = button("Все видимые")
        all_b.clicked.connect(lambda: self._set_all(True))
        none_b = button("Снять все")
        none_b.clicked.connect(lambda: self._set_all(False))
        ok = button("Готово", "primary")
        ok.clicked.connect(self.accept)
        self.count = label("", "faint")
        self.list.itemChanged.connect(lambda _: self._count())
        lay.addWidget(self.search)
        lay.addWidget(self.list, 1)
        lay.addLayout(hbox(all_b, none_b, self.count, None, ok))
        self._count()

    def _filter(self, t):
        t = t.lower().strip()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(t) and t not in it.text())

    def _set_all(self, on: bool):
        for i in range(self.list.count()):
            it = self.list.item(i)
            if not it.isHidden():
                it.setCheckState(Qt.Checked if on else Qt.Unchecked)

    def _count(self):
        self.count.setText(f"Выбрано: {len(self.selected())}")

    def selected(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]


class TestsPage(QWidget):
    def __init__(self, ctl):
        super().__init__()
        self.setObjectName("page")
        self.ctl = ctl
        self.runner: TestRunner | None = None
        self.testing_name = ""
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 14)
        root.setSpacing(10)
        root.addWidget(label("Автотест стратегий", "h1"))
        root.addWidget(label("Каждая стратегия запускается по очереди, затем проверяется доступность "
                             "выбранных целей (HTTP, TLS 1.2, TLS 1.3 и обрыв после 16 КБ). Лучшая стратегия "
                             "сама подставится на главной.", "muted", wrap=True))

        ctrl = card()
        cl = vbox(margins=(14, 12, 14, 12), spacing=10)
        ctrl.setLayout(cl)
        self.run_all = button("Тест всех", "primary", icons.icon("play", "#06111D"))
        self.run_all.clicked.connect(lambda: self.run(None))
        self.run_sel = button("Тест отмеченных")
        self.run_sel.clicked.connect(self._run_checked)
        self.stop_b = button("Остановить", "danger", icons.icon("stop", theme.ERR))
        self.stop_b.clicked.connect(self._stop)
        self.stop_b.hide()
        cl.addWidget(flow(self.run_all, self.run_sel, self.stop_b, spacing=8))

        self.tmode = Segmented([("standard", "Стандартные цели"), ("all", "Все ресурсы"),
                                ("selected", "Выбранные")])
        self.tmode.setToolTip("Стандартные — Discord, YouTube, Google, Cloudflare.\n"
                              "Все ресурсы — все сайты из списков обхода.\n"
                              "Выбранные — сайты, которые вы отметите сами.")
        self.tmode.set_value(ctl.settings.get("test_targets_mode") or "standard")
        self.tmode.changed.connect(self._tmode_changed)
        self.pick_b = button("Выбрать…", icon=icons.icon("list"))
        self.pick_b.clicked.connect(self._pick_targets)
        self.targets_b = button("Изменить цели…", icon=icons.icon("sliders"),
                                tip="Редактировать стандартный список целей (targets.txt)")
        self.targets_b.clicked.connect(self._edit_targets)
        self.targets_info = label("", "faint", wrap=True)
        cl.addWidget(flow(label("Цели:", "muted"), self.tmode, self.pick_b, self.targets_b, spacing=8))
        cl.addWidget(self.targets_info)

        self.baseline = QCheckBox("Сначала проверить без обхода")
        self.baseline.setToolTip("Покажет, что доступно и без zapret — для сравнения")
        self.timeout = QSpinBox()
        self.timeout.setRange(2, 20)
        self.timeout.setSuffix(" с")
        self.timeout.setValue(int(ctl.settings.get("test_timeout")))
        self.timeout.valueChanged.connect(lambda v: ctl.settings.set("test_timeout", v))
        self.timeout.setToolTip("Сколько ждать ответа от каждой цели")
        cl.addWidget(flow(self.baseline, label("Таймаут:", "muted"), self.timeout, spacing=10))
        self.progress = QProgressBar()
        self.progress.setFixedHeight(6)
        self.progress.hide()
        self.progress_text = label("", "muted", wrap=True)
        cl.addWidget(self.progress)
        cl.addWidget(self.progress_text)
        root.addWidget(ctrl)

        self.split = QSplitter(Qt.Horizontal)
        self.split.setChildrenCollapsible(False)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Стратегия", "Оценка", "Успешно"])
        self.tree.headerItem().setToolTip(1, "Доля успешных проверок: чем больше, тем лучше стратегия")
        self.tree.headerItem().setToolTip(2, "Сколько проверок прошло из всех")
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(0)
        self.rows = StrategyRows(self.tree, self)
        self.tree.setItemDelegate(self.rows)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setStretchLastSection(False)
        self.tree.setColumnWidth(1, 96)
        self.tree.setColumnWidth(2, 76)
        self.tree.currentItemChanged.connect(self._details)

        self.detail = QTreeWidget()
        self.detail.setHeaderLabels(["Цель", "HTTP", "TLS 1.2", "TLS 1.3"])
        for c, tip in COL_TIP.items():
            self.detail.headerItem().setToolTip(c, tip)
        self.detail.headerItem().setToolTip(0, "Что проверялось. Наведите на ячейку — будет подсказка.")
        self.detail.setRootIsDecorated(False)
        self.detail.setIndentation(0)
        self.detail.setSelectionMode(QAbstractItemView.NoSelection)
        setup_rows(self.detail, hover=False, select=False, row_height=30)
        self.detail.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.detail.header().setStretchLastSection(False)
        for c in (1, 2, 3):
            self.detail.header().setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.split.addWidget(self.tree)
        self.split.addWidget(self.detail)
        self.split.setSizes([460, 440])
        self.split.setMinimumHeight(300)
        root.addWidget(self.split, 1)

        self.bridge = _Bridge()
        self.bridge.progress.connect(self._on_progress)
        self.bridge.result.connect(self._on_result)
        self._baseline_result = None
        self.anim = QTimer(self)
        self.anim.setInterval(110)
        self.anim.timeout.connect(self.tree.viewport().update)
        ctl.strategies_changed.connect(self.reload)
        ctl.installed_changed.connect(lambda _: (self.reload(), self._tmode_changed(self.tmode.value(), False)))
        self.reload()
        self._tmode_changed(self.tmode.value(), False)

    # ------------------------------------------------------------ адаптивность
    def resizeEvent(self, e):
        super().resizeEvent(e)
        o = Qt.Vertical if self.width() < 760 else Qt.Horizontal
        if self.split.orientation() != o:
            self.split.setOrientation(o)
            self.split.setMinimumHeight(520 if o == Qt.Vertical else 300)

    # ------------------------------------------------------------ цели
    def _tmode_changed(self, mode: str, save: bool = True):
        if save:
            self.ctl.settings.set("test_targets_mode", mode)
        self.pick_b.setVisible(mode == "selected")
        self.targets_b.setVisible(mode == "standard")
        t = self._targets()
        n = len(t or [])
        checks = sum(1 if x.ping_only else 3 for x in (t or []))
        warn = ""
        if checks > 150:
            warn = f" Это долго: около {max(1, round(checks / 16 * 1.2 / 60))} мин на каждую стратегию."
        if mode == "selected" and not n:
            self.targets_info.setText("Нажмите «Выбрать…» и отметьте сайты для проверки.")
        else:
            self.targets_info.setText(f"Целей: {n}, проверок на стратегию: {checks}.{warn}")

    def _targets(self):
        mode = self.tmode.value()
        if mode == "standard":
            if not self.ctl.zap.installed():
                return []
            ensure_targets_file(self.ctl.zap.utils_dir)
            from ...core.tester import load_targets
            return load_targets()
        if not self.ctl.zap.lists_dir.exists():
            return []
        if mode == "all":
            return resource_targets(self.ctl.list_manager().domains_for_test())
        return resource_targets(self.ctl.settings.get("test_targets_selected") or [])

    def _pick_targets(self):
        domains = self.ctl.list_manager().domains_for_test()
        d = TargetPicker(self, [x.lstrip("^") for x in domains], self.ctl.settings.get("test_targets_selected") or [])
        if d.exec():
            self.ctl.settings.set("test_targets_selected", d.selected())
            self._tmode_changed("selected")

    # ------------------------------------------------------------ таблица
    def reload(self):
        checked = self._checked_names()
        cur = self.tree.currentItem().text(0) if self.tree.currentItem() else ""
        self.tree.clear()
        ver = self.ctl.zap.version()
        for it in self.ctl.sorted_strategies():
            r = self.ctl.results.get(it["name"])
            row = QTreeWidgetItem([it["name"], "", f"{r['ok']} из {r['total']}" if r else "—"])
            row.setFlags(row.flags() | Qt.ItemIsUserCheckable)
            row.setCheckState(0, Qt.Checked if it["name"] in checked else Qt.Unchecked)
            row.setData(1, Qt.UserRole, it["score"])
            stale = bool(r and r.get("zapret_version") != ver)
            row.setData(1, Qt.UserRole + 1, stale)
            row.setToolTip(0, "Отметьте галочкой, чтобы проверить только выбранные стратегии")
            if r:
                tip = (f"Оценка {round(it['score'] * 100)}%: прошло {r['ok']} проверок из {r['total']}. "
                       f"Тест {time.strftime('%d.%m %H:%M', time.localtime(r['time']))}")
                if stale:
                    tip += f" на прошлой версии zapret ({r.get('zapret_version')}) — стоит перепроверить"
            else:
                tip = "Ещё не проверялась"
            row.setToolTip(1, tip)
            row.setToolTip(2, tip)
            row.setForeground(2, QColor(theme.MUTED))
            self.tree.addTopLevelItem(row)
            if it["name"] == cur:
                self.tree.setCurrentItem(row)

    def _checked_names(self) -> set:
        res = set()
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.checkState(0) == Qt.Checked:
                res.add(it.text(0))
        return res

    def _details(self, cur, _prev):
        self.detail.clear()
        if not cur:
            return
        name = cur.text(0)
        r = self.ctl.results.get(name) if name != TestRunner.BASELINE else self._baseline_result
        if not r:
            return
        for d in r.get("details", []):
            info = TARGET_INFO.get(d["name"], "")
            name_tip = (info + "\n" if info else "") + d.get("url", "").replace("PING:", "ping ")
            if "ping" in d:
                col, q = ping_quality(d["ping"])
                row = QTreeWidgetItem([d["name"], f"пинг {d['ping']}", "", ""])
                row.setForeground(1, QColor(col))
                row.setToolTip(1, f"{q}\n\n{PING_HELP}")
                row.setFirstColumnSpanned(False)
            else:
                t = d.get("tests", {})
                vals = [t.get("HTTP", ""), t.get("TLS1.2", ""), t.get("TLS1.3", "")]
                row = QTreeWidgetItem([d["name"]] + [RES_TEXT.get(v, v) for v in vals])
                for c, v in enumerate(vals, 1):
                    row.setForeground(c, QColor(RES_COLOR.get(v, theme.ERR)))
                    row.setToolTip(c, f"{COL_TIP[c].split(' — ')[0]}: {RES_TIP.get(v, v)}")
            row.setToolTip(0, name_tip)
            self.detail.addTopLevelItem(row)

    # ------------------------------------------------------------ запуск
    def _run_checked(self):
        names = sorted(self._checked_names())
        if not names:
            QMessageBox.information(self, "Автотест", "Отметьте галочками стратегии для теста.")
            return
        self.run(names)

    def run(self, names):
        if self.runner:
            return
        if not self.ctl.zap.installed():
            self.ctl.push_error("zapret не установлен")
            return
        if self.ctl.busy:
            log.warn(f"Подождите: {self.ctl.busy}")
            return
        targets = self._targets()
        if not targets:
            QMessageBox.information(self, "Автотест", "Не выбрано ни одной цели для проверки.")
            return
        if names is None:
            names = [s.name for s in self.ctl.zap.strategies() if not s.error]
        self.runner = TestRunner(self.ctl.zap, names, timeout=self.timeout.value(),
                                 parallel=max(16, int(self.ctl.settings.get("test_parallel"))),
                                 baseline=self.baseline.isChecked(), targets=targets,
                                 on_progress=lambda i, n, s: self.bridge.progress.emit(i, n, s),
                                 on_result=lambda r: self.bridge.result.emit(r))
        self.ctl.testing = True
        self.ctl.set_busy_external(f"Тест стратегий (0/{len(names)})")
        self.run_all.set_loading(True, "Идёт тест…")
        self.run_sel.setEnabled(False)
        self.stop_b.show()
        total = len(names) + (1 if self.baseline.isChecked() else 0)
        self.progress.setRange(0, total)
        self.progress.setValue(0)
        self.progress.show()
        self.anim.start()
        self.ctl.test_progress.emit(0, total, "")
        started = time.time()

        runner = self.runner

        def done(results):
            self._finish()
            if runner.dns_borrowed:
                log.warn("Ваш DNS не отдаёт адрес " + ", ".join(runner.dns_borrowed) + " — проверено через адрес "
                         "серверов Google. Браузер может не открывать эти сайты независимо от стратегии; помогает "
                         "DNS-over-HTTPS (Параметры Windows → Сеть и Интернет → DNS-сервер → 1.1.1.1 с шифрованием)")
            ok = [r for r in results if r.name != TestRunner.BASELINE and not r.error]
            if ok:
                best = max(ok, key=lambda r: r.score)
                log.ok(f"Тест завершён за {int(time.time() - started)} с. Лучшая: {best.name} "
                       f"({round(best.score * 100)}%)")
            else:
                log.warn("Тест завершён без результатов")

        def fail(msg):
            self._finish()
            self.ctl.push_error(f"Ошибка автотеста: {msg}")

        run_bg(self.runner.run, done, fail)

    def _finish(self):
        self.runner = None
        self.testing_name = ""
        self.ctl.testing = False
        self.anim.stop()
        self.ctl.set_busy_external("")
        self.run_all.set_loading(False)
        self.run_sel.setEnabled(True)
        self.stop_b.set_loading(False)
        self.stop_b.hide()
        self.progress.hide()
        self.progress_text.setText("")
        self.ctl.refresh_status()
        self.reload()
        self.ctl.test_finished.emit()
        self.ctl.strategies_changed.emit()

    def _stop(self):
        if self.runner:
            self.runner.cancel.set()
            self.stop_b.set_loading(True, "Останавливаю…")
            self.progress_text.setText("Останавливаю после текущей стратегии…")

    def _on_progress(self, i: int, n: int, name: str):
        self.progress.setValue(i)
        self.testing_name = name
        self.tree.viewport().update()
        if name:
            self.progress_text.setText(f"Проверяется «{name}» — {i + 1} из {n}")
            self.ctl.set_busy_external(f"Тест стратегий ({i + 1}/{n})")
        self.ctl.test_progress.emit(i, n, name)
        for k in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(k)
            if it.text(0) == name:
                self.tree.scrollToItem(it)
                break

    def _on_result(self, r):
        if r.error:
            log.warn(f"{r.name}: {r.error}")
        if r.name == TestRunner.BASELINE:
            self._baseline_result = {"details": r.details}
            log.info(f"Без обхода доступно {r.ok} из {r.total} проверок")
            return
        if not r.error:
            self.ctl.save_result(r)
            log.info(f"{r.name}: {round(r.score * 100)}% ({r.ok}/{r.total})")
        else:
            self.ctl.results.put(r.name, 0, 0, 0, self.ctl.zap.version(), [])
            self.ctl.strategies_changed.emit()

    # ------------------------------------------------------------ стандартные цели
    def _edit_targets(self):
        ensure_targets_file(self.ctl.zap.utils_dir)
        d = QDialog(self)
        d.setWindowTitle("Стандартные цели автотеста")
        d.resize(620, 480)
        t = QPlainTextEdit(TARGETS_FILE.read_text(encoding="utf-8", errors="ignore"))
        t.setObjectName("code")
        info = label('Формат: Имя = "https://host" или Имя = "PING:1.2.3.4"', "faint")
        save = button("Сохранить", "primary")
        reset = button("Сбросить к стандартным")

        def do_save():
            if not parse_targets(t.toPlainText()):
                QMessageBox.warning(d, "Цели", "Не найдено ни одной цели.")
                return
            TARGETS_FILE.write_text(t.toPlainText(), encoding="utf-8")
            log.ok("Цели автотеста сохранены")
            d.accept()

        def do_reset():
            TARGETS_FILE.unlink(missing_ok=True)
            ensure_targets_file(self.ctl.zap.utils_dir)
            t.setPlainText(TARGETS_FILE.read_text(encoding="utf-8", errors="ignore"))

        save.clicked.connect(do_save)
        reset.clicked.connect(do_reset)
        lay = QVBoxLayout(d)
        lay.addWidget(info)
        lay.addWidget(t)
        lay.addLayout(hbox(reset, None, save))
        d.exec()
        self._tmode_changed(self.tmode.value(), False)
