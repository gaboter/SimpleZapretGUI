"""Поиск по настройкам — как Ctrl+F в браузере.

Ищет по «Настройкам» и «Сервису»: подсвечивает все совпадения, текущее — ярче,
переключается между ними (Enter / Shift+Enter / F3 / стрелки), сам открывает нужную
вкладку и прокручивает к найденному.
"""
from __future__ import annotations

import html
from typing import Callable

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QAbstractButton, QFrame, QHBoxLayout, QLabel, QLineEdit, QScrollArea,
                               QWidget)

from . import icons, theme
from .widgets import button, label

HL_BG = "#5A4716"          # фон найденного текста
HL_CUR_BG = "#F0B44C"      # фон текущего совпадения
HL_CUR_FG = "#1A1206"


def _norm(s: str) -> str:
    """Для сравнения без учёта регистра и «ё». Длина строки не меняется — позиции совпадают."""
    return s.lower().replace("ё", "е")


class Match:
    __slots__ = ("page", "widget", "start", "length")

    def __init__(self, page: str, widget: QWidget, start: int, length: int):
        self.page, self.widget, self.start, self.length = page, widget, start, length


class HighlightLayer(QWidget):
    """Прозрачный слой поверх страницы: рамки вокруг найденных кнопок и текущего совпадения."""

    def __init__(self, root: QWidget):
        super().__init__(root)
        self.root = root
        self.items: list[tuple[QWidget, bool]] = []
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        root.installEventFilter(self)
        self.setGeometry(root.rect())
        self.hide()

    def eventFilter(self, obj, ev):
        if obj is self.root and ev.type() in (QEvent.Resize, QEvent.LayoutRequest):
            self.setGeometry(self.root.rect())
            self.raise_()
        return False

    def set_items(self, items):
        self.items = items
        self.setVisible(bool(items))
        self.setGeometry(self.root.rect())
        self.raise_()
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        for w, current in self.items:
            try:
                if not w.isVisibleTo(self.root):
                    continue
                r = QRect(w.mapTo(self.root, QPoint(0, 0)), w.size())
            except RuntimeError:          # виджет уже удалён
                continue
            rf = QRectF(r).adjusted(-3, -3, 3, 3)
            if current:
                p.setPen(QPen(QColor(HL_CUR_BG), 2.2))
                fill = QColor(HL_CUR_BG)
                fill.setAlpha(45)
            else:
                c = QColor(HL_CUR_BG)
                c.setAlpha(120)
                p.setPen(QPen(c, 1.2))
                fill = QColor(HL_CUR_BG)
                fill.setAlpha(18)
            p.setBrush(fill)
            p.drawRoundedRect(rf, 7, 7)


class FindBar(QFrame):
    """Панель поиска. pages: [(ключ, страница)] — у страницы есть search_root и search_scroll."""

    def __init__(self, pages: list[tuple[str, QWidget]], go_to: Callable[[str], None],
                 current_page: Callable[[], str]):
        super().__init__()
        self.setObjectName("findbar")
        self.pages = pages
        self.go_to = go_to
        self.current_page = current_page
        self.matches: list[Match] = []
        self.index = -1
        self._orig: dict[QLabel, tuple[str, Qt.TextFormat]] = {}
        self._html: dict[QLabel, str] = {}
        self._layers: dict[str, HighlightLayer] = {}

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 10, 8)
        lay.setSpacing(6)
        ic = QLabel()
        ic.setPixmap(icons.pixmap("search", theme.MUTED, 16))
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Найти в настройках и сервисе")
        self.edit.setClearButtonEnabled(True)
        self.edit.installEventFilter(self)
        self.count = label("", "faint")
        self.count.setMinimumWidth(80)
        self.count.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.prev_b = button("", icon=icons.icon("chevron_up"), tip="Предыдущее (Shift+Enter)")
        self.next_b = button("", icon=icons.icon("chevron"), tip="Следующее (Enter, F3)")
        self.close_b = button("", icon=icons.icon("close"), tip="Закрыть (Esc)")
        for b in (self.prev_b, self.next_b, self.close_b):
            b.setFixedSize(32, 32)
        self.prev_b.clicked.connect(self.prev)
        self.next_b.clicked.connect(self.next)
        self.close_b.clicked.connect(self.close_bar)
        lay.addWidget(ic)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.count)
        lay.addWidget(self.prev_b)
        lay.addWidget(self.next_b)
        lay.addWidget(self.close_b)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(160)
        self._debounce.timeout.connect(self._search_start)
        self.edit.textChanged.connect(lambda _: self._debounce.start())
        # подсветка кнопок следует за раскладкой (прокрутка, перестроение)
        self._repaint = QTimer(self)
        self._repaint.setInterval(200)
        self._repaint.timeout.connect(self._update_layers)
        self.hide()

    # ------------------------------------------------------------ открытие / закрытие
    def open_bar(self):
        self.show()
        self.edit.setFocus()
        self.edit.selectAll()
        if self.edit.text():
            self._search_start()

    def close_bar(self):
        self._clear()
        self.matches, self.index = [], -1
        self._repaint.stop()
        self.hide()

    def eventFilter(self, obj, ev):
        if obj is self.edit and ev.type() == QEvent.KeyPress:
            k = ev.key()
            if k == Qt.Key_Escape:
                self.close_bar()
                return True
            if k in (Qt.Key_Return, Qt.Key_Enter):
                if self._debounce.isActive():
                    self._debounce.stop()
                    self._search_start()
                elif ev.modifiers() & Qt.ShiftModifier:
                    self.prev()
                else:
                    self.next()
                return True
            if k == Qt.Key_Down:
                self.next()
                return True
            if k == Qt.Key_Up:
                self.prev()
                return True
        return False

    # ------------------------------------------------------------ поиск
    def _texts(self, root: QWidget):
        items = []
        for w in root.findChildren(QWidget):
            if isinstance(w, HighlightLayer) or not w.isVisibleTo(root):
                continue
            if isinstance(w, QLabel):
                if w in self._orig:
                    text = self._orig[w][0]
                else:
                    if w.textFormat() == Qt.RichText or w.pixmap() is not None and not w.pixmap().isNull():
                        continue
                    text = w.text()
            elif isinstance(w, QAbstractButton):
                text = w.text().replace("&", "")
            else:
                continue
            if text.strip():
                pos = w.mapTo(root, QPoint(0, 0))
                items.append((pos.y(), pos.x(), w, text))
        items.sort(key=lambda t: (t[0], t[1]))
        return items

    def _search_start(self):
        """Новый поиск: к первому совпадению на текущей вкладке, иначе — к самому первому."""
        self._search()
        if not self.matches:
            return
        cur = self.current_page()
        start = next((i for i, m in enumerate(self.matches) if m.page == cur), 0)
        self._go(start)

    def _search(self):
        self._clear()
        q = _norm(self.edit.text().strip())
        self.matches, self.index = [], -1
        if not q:
            self._show_count()
            return
        for key, page in self.pages:
            for _y, _x, w, text in self._texts(page.search_root):
                t = _norm(text)
                pos = t.find(q)
                while pos >= 0:
                    self.matches.append(Match(key, w, pos, len(q)))
                    if not isinstance(w, QLabel):
                        break                   # у кнопки одно совпадение на всю кнопку
                    pos = t.find(q, pos + len(q))
        self._render()
        self._repaint.start()

    def next(self):
        if self.matches:
            self._go((self.index + 1) % len(self.matches))

    def prev(self):
        if self.matches:
            self._go((self.index - 1) % len(self.matches))

    def _go(self, i: int):
        self.index = i
        m = self.matches[i]
        if self.current_page() != m.page:
            self.go_to(m.page)
        self._render()
        page = dict(self.pages)[m.page]
        QTimer.singleShot(0, lambda: self._scroll_to(page, m.widget))

    @staticmethod
    def _scroll_to(page, w):
        try:
            sa: QScrollArea = page.search_scroll
            sa.ensureWidgetVisible(w, 40, 90)
        except RuntimeError:
            pass

    # ------------------------------------------------------------ подсветка
    def _render(self):
        cur = self.matches[self.index] if 0 <= self.index < len(self.matches) else None
        by_label: dict[QLabel, list[Match]] = {}
        for m in self.matches:
            if isinstance(m.widget, QLabel):
                by_label.setdefault(m.widget, []).append(m)
        for lab, ms in by_label.items():
            self._paint_label(lab, ms, cur)
        self._update_layers()
        self._show_count()

    def _paint_label(self, lab: QLabel, ms: list[Match], cur):
        try:
            if lab not in self._orig:
                self._orig[lab] = (lab.text(), lab.textFormat())
            text = self._orig[lab][0]
            out, last = [], 0
            for m in sorted(ms, key=lambda m: m.start):
                out.append(html.escape(text[last:m.start]))
                frag = html.escape(text[m.start:m.start + m.length])
                if m is cur:
                    out.append(f'<span style="background-color:{HL_CUR_BG}; color:{HL_CUR_FG};">{frag}</span>')
                else:
                    out.append(f'<span style="background-color:{HL_BG};">{frag}</span>')
                last = m.start + m.length
            out.append(html.escape(text[last:]))
            h = "".join(out).replace("\n", "<br>")
            lab.setTextFormat(Qt.RichText)
            lab.setText(h)
            self._html[lab] = h
        except RuntimeError:
            pass

    def _update_layers(self):
        cur = self.matches[self.index] if 0 <= self.index < len(self.matches) else None
        per_page: dict[str, list] = {k: [] for k, _ in self.pages}
        seen = set()
        for m in self.matches:
            if id(m.widget) in seen:
                continue
            is_cur = cur is not None and m.widget is cur.widget
            if isinstance(m.widget, QLabel) and not is_cur:
                continue                        # текст подсвечен прямо в строке
            seen.add(id(m.widget))
            per_page[m.page].append((m.widget, is_cur))
        for key, page in self.pages:
            layer = self._layers.get(key)
            if layer is None:
                layer = HighlightLayer(page.search_root)
                self._layers[key] = layer
            layer.set_items(per_page[key])

    def _show_count(self):
        if not self.edit.text().strip():
            self.count.setText("")
        elif not self.matches:
            self.count.setText("Не найдено")
            self.count.setStyleSheet(f"color:{theme.ERR}; font-size:12px;")
        else:
            self.count.setText(f"{self.index + 1} из {len(self.matches)}")
            self.count.setStyleSheet(f"color:{theme.MUTED}; font-size:12px;")
        found = bool(self.matches) or not self.edit.text().strip()
        self.edit.setStyleSheet("" if found else f"border-color:{theme.ERR};")
        self.prev_b.setEnabled(len(self.matches) > 1)
        self.next_b.setEnabled(len(self.matches) > 1)

    def _clear(self):
        """Вернуть исходный текст подписям (если приложение не успело его обновить само)."""
        for lab, (text, fmt) in list(self._orig.items()):
            try:
                if lab.text() == self._html.get(lab):
                    lab.setTextFormat(fmt)
                    lab.setText(text)
            except RuntimeError:
                pass
        self._orig.clear()
        self._html.clear()
        for layer in self._layers.values():
            try:
                layer.set_items([])
            except RuntimeError:
                pass
