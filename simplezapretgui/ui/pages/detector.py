"""Детектор блокировок: ввод домена/IP/списка → таблица с типом блокировки и добавлением в обход."""
from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal, QObject
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QHeaderView, QPlainTextEdit, QProgressBar, QTableView,
                               QVBoxLayout, QWidget)

from ...core import log
from ...core.detector import Detection, detect_many, parse_queries
from .. import icons, theme
from ..widgets import button, flow, label, run_bg, setup_rows

# тип блокировки → (короткая подпись для таблицы, цвет)
KIND_LABEL = {
    "none":     ("Не заблокирован", theme.OK),
    "dns":      ("Подмена DNS", theme.WARN),
    "dns_fail": ("DNS не отвечает", theme.WARN),
    "ip":       ("Блокировка по IP", theme.ERR),
    "ip_ok":    ("IP доступен", theme.OK),
    "sni":      ("Блокировка по имени (SNI)", theme.ACCENT),
    "cut":      ("Обрыв загрузки (DPI)", theme.ACCENT),
    "throttle": ("Замедление (ТСПУ)", theme.ACCENT),
    "http":     ("Подмена HTTP", theme.ACCENT),
    "tls_err":  ("Ошибка TLS", theme.WARN),
    "unknown":  ("Не определено", theme.FAINT),
    "":         ("Проверяю…", theme.MUTED),
}


class DetModel(QAbstractTableModel):
    HEAD = ["Адрес", "Тип блокировки", "Обход zapret", "Пояснение"]

    def __init__(self):
        super().__init__()
        self.rows: list[Detection] = []

    def set_rows(self, rows: list[Detection]):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def update_one(self, d: Detection):
        for i, r in enumerate(self.rows):
            if r.query == d.query:
                self.rows[i] = d
                self.dataChanged.emit(self.index(i, 0), self.index(i, 3))
                return

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 4

    def headerData(self, s, o, role=Qt.DisplayRole):
        if o == Qt.Horizontal and role == Qt.DisplayRole:
            return self.HEAD[s]
        return None

    def data(self, idx, role=Qt.DisplayRole):
        if not idx.isValid():
            return None
        d = self.rows[idx.row()]
        c = idx.column()
        pending = not d.detail and d.kind == "unknown"
        label_txt, color = KIND_LABEL.get("" if pending else d.kind, KIND_LABEL["unknown"])
        if d.kind == "dns" and d.dns == "fail":
            label_txt = "DNS не отдаёт адрес"            # не «подмена»: адрес просто не приходит
        if role == Qt.DisplayRole:
            if c == 0:
                return d.host or d.query
            if c == 1:
                # проблема DNS идёт вторым слоем: «Замедление (ТСПУ) + DNS»
                return label_txt + (" + DNS" if d.dns != "ok" and d.kind not in ("dns", "dns_fail") else "")
            if c == 2:
                if pending:
                    return "…"
                if d.kind == "dns":
                    return "нужен DoH, не zapret"        # блокировку DNS обходит DNS-over-HTTPS
                if d.listed == "include":
                    return "уже в списке"
                if d.listed == "exclude":
                    return "в исключениях"
                if d.can_add:
                    return "можно добавить" + (" + DoH" if d.dns != "ok" else "")
                return "помогает" if d.bypassable else "—"
            if c == 3:
                return d.detail
        if role == Qt.ForegroundRole:
            if c == 1:
                return QColor(color)
            if c == 2:
                return QColor(theme.OK if d.bypassable else (theme.WARN if d.kind == "dns" else theme.MUTED))
            if c == 3:
                return QColor(theme.MUTED)
        if role == Qt.ToolTipRole:
            tip = list(d.extra)
            if d.listed == "include":
                tip.append("Этот домен уже есть в ваших списках обхода — добавлять не нужно.")
            elif d.listed == "exclude":
                tip.append("Этот домен в исключениях: обход его не трогает.")
            return "\n\n".join(tip) if tip else None
        return None


class _Bridge(QObject):
    one = Signal(object)
    done = Signal(object)


class DetectorPage(QWidget):
    def __init__(self, ctl):
        super().__init__()
        self.setObjectName("page")
        self.ctl = ctl
        self.busy = False
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 14)
        root.setSpacing(10)
        root.addWidget(label("Детектор блокировок", "h1"))
        root.addWidget(label(
            "Введите домен, IP или ссылку — по одному или списком (каждый с новой строки). Детектор определит, "
            "как именно провайдер блокирует каждый ресурс и можно ли это обойти средствами zapret. Что обходимо, "
            "одной кнопкой добавляется в списки обхода.", "muted", wrap=True))

        self.warn = label("Обход сейчас включён — сайты могут открываться благодаря ему, и блокировка не будет "
                          "видна. Для точной проверки выключите обход на главной.", wrap=True)
        self.warn.setStyleSheet(f"color:{theme.WARN};")
        self.warn.setVisible(bool(ctl.status.running))
        ctl.status_changed.connect(lambda st: self.warn.setVisible(bool(st.running)))
        root.addWidget(self.warn)

        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText("Например:\ndiscord.com\nrutracker.org\n2.2.2.2")
        self.edit.setMaximumHeight(96)
        root.addWidget(self.edit)

        self.check_b = button("Проверить", "primary", icons.icon("search", "#06111D"))
        self.check_b.clicked.connect(self.start)
        self.stop_b = button("Остановить", "danger", icons.icon("stop", theme.ERR))
        self.stop_b.clicked.connect(self._stop)
        self.stop_b.hide()
        self.add_b = button("Добавить обходимые в список", icon=icons.icon("plus", theme.OK))
        self.add_b.clicked.connect(self._add_all)
        self.add_b.setEnabled(False)
        root.addWidget(flow(self.check_b, self.stop_b, self.add_b, spacing=8))

        self.progress = QProgressBar()
        self.progress.setFixedHeight(6)
        self.progress.hide()
        self.status = label("", "muted", wrap=True)
        root.addWidget(self.progress)
        root.addWidget(self.status)

        self.model = DetModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(30)
        setup_rows(self.table, hover=True, select=True, row_height=30)
        hh = self.table.horizontalHeader()
        hh.setHighlightSections(False)
        self._size_columns()
        self.table.setMinimumHeight(240)
        root.addWidget(self.table, 1)

        self.bridge = _Bridge()
        self.bridge.one.connect(self._on_one)
        self.bridge.done.connect(self._on_done)
        self._cancel = False
        self._total = 0
        self._done = 0

    # ------------------------------------------------------------ запуск
    def start(self):
        if self.busy:
            return
        queries = parse_queries(self.edit.toPlainText())
        if not queries:
            self.status.setText("Впишите хотя бы один адрес.")
            return
        # уникальные, с сохранением порядка
        seen, uniq = set(), []
        for q in queries:
            if q.lower() not in seen:
                seen.add(q.lower())
                uniq.append(q)
        self.model.set_rows([Detection(query=q, host=q) for q in uniq])
        self.busy = True
        self._cancel = False
        self._total = len(uniq)
        self._done = 0
        self.check_b.set_loading(True, "Проверка…")
        self.stop_b.show()
        self.add_b.setEnabled(False)
        self.progress.setRange(0, self._total)
        self.progress.setValue(0)
        self.progress.show()
        self.status.setText(f"Проверяю {self._total} адресов…")
        timeout = float(self.ctl.settings.get("test_timeout") or 5)

        def work():
            return detect_many(uniq, timeout=timeout,
                               on_result=lambda d: (None if self._cancel else self.bridge.one.emit(d)))

        run_bg(work, lambda res: self.bridge.done.emit(res), self._fail)

    def _stop(self):
        self._cancel = True
        self.stop_b.set_loading(True, "Останавливаю…")

    def _fail(self, msg: str):
        self._finish()
        self.status.setText(f"Ошибка: {msg}")

    def _on_one(self, d: Detection):
        self.model.update_one(d)
        self._done += 1
        self.progress.setValue(self._done)
        self.status.setText(f"Проверено {self._done} из {self._total}…")

    def _on_done(self, results: list):
        self._finish()
        rows = self.model.rows
        self._mark_listed(rows)
        self.model.layoutChanged.emit()
        block = [r for r in rows if r.kind not in ("none", "ip_ok", "")]
        add = [r for r in rows if r.can_add]
        self._addable = add
        self.add_b.setEnabled(bool(add))
        parts = [f"проверено {len(rows)}"]
        if block:
            parts.append(f"заблокировано {len(block)}")
        if add:
            parts.append(f"обходится zapret {len(add)}")
        self.status.setText("Готово: " + ", ".join(parts) + ("." if not add else
                            " — нажмите «Добавить обходимые в список»."))
        self._size_columns()

    def _mark_listed(self, rows):
        """Домены, которые уже есть в списках обхода (или в исключениях), добавлять повторно не нужно."""
        try:
            lm = self.ctl.list_manager()
            for d in rows:
                if d.is_ip or not d.host:
                    continue
                hits = lm.lookup_ex(d.host)
                if any(not ex for _t, _v, ex in hits):
                    d.listed = "include"
                elif hits:
                    d.listed = "exclude"
        except Exception:
            pass                            # списки недоступны — просто не помечаем

    def _size_columns(self):
        hh = self.table.horizontalHeader()
        for c, mode in ((0, QHeaderView.Stretch), (1, QHeaderView.ResizeToContents),
                        (2, QHeaderView.ResizeToContents), (3, QHeaderView.Stretch)):
            hh.setSectionResizeMode(c, mode)

    def _finish(self):
        self.busy = False
        self.check_b.set_loading(False)
        self.stop_b.set_loading(False)
        self.stop_b.hide()
        self.progress.hide()

    # ------------------------------------------------------------ добавление в обход
    def _add_all(self):
        add = getattr(self, "_addable", [])
        domains = [d.host for d in add if d.can_add]
        if not domains:
            return
        lm = self.ctl.list_manager()
        added, errors = lm.add("general", domains)
        if added:
            log.ok(f"Добавлено в обход: {', '.join(added[:6])}" + (f" и ещё {len(added) - 6}" if len(added) > 6 else ""))
            self.status.setText(f"Добавлено в «Сайты для обхода»: {len(added)}. "
                                "Перезапускаю обход, если он включён.")
            self.ctl.restart_if_running("списки изменены детектором")
        elif not errors:
            self.status.setText("Эти сайты уже есть в списке обхода.")
        self.add_b.setEnabled(False)
