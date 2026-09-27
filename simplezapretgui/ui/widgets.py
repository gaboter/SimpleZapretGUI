"""Фирменные виджеты и помощник фоновых задач."""
from __future__ import annotations

import sys
import threading
import traceback
from typing import Callable, Optional

from PySide6.QtCore import (Property, QEasingCurve, QEvent, QObject, QPoint, QPointF, QPropertyAnimation, QRect,
                            QRectF, QSize, Qt, QTimer, Signal)
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLabel,
                               QLayout, QPushButton, QScrollArea, QSizePolicy, QStyledItemDelegate, QStyle,
                               QStyleOptionViewItem, QVBoxLayout, QWidget)

from . import theme


# ================================================================ фоновые задачи
class _Bridge(QObject):
    done = Signal(object)
    failed = Signal(str)


_alive: set = set()


def run_bg(fn: Callable, on_done: Optional[Callable] = None, on_error: Optional[Callable] = None):
    """Выполнить fn в потоке, результат вернуть в UI-поток."""
    b = _Bridge()
    _alive.add(b)

    def finish_ok(r):
        _alive.discard(b)
        if on_done:
            on_done(r)

    def finish_err(msg):
        _alive.discard(b)
        if on_error:
            on_error(msg)

    b.done.connect(finish_ok)
    b.failed.connect(finish_err)

    def work():
        try:
            r = fn()
            b.done.emit(r)
        except Exception as e:  # noqa: BLE001
            tb = traceback.format_exc()
            try:
                from ..core import log
                log.trace(tb)
                sys.stderr.write(tb)
            except Exception:
                pass
            b.failed.emit(str(e) or e.__class__.__name__)

    threading.Thread(target=work, daemon=True).start()


# ================================================================ простые блоки
def card(obj: str = "card") -> QFrame:
    f = QFrame()
    f.setObjectName(obj)
    return f


def label(text: str = "", obj: str = "", wrap: bool = False) -> QLabel:
    l = QLabel(text)
    if obj:
        l.setObjectName(obj)
    l.setWordWrap(wrap)
    return l


def spinner_pixmap(color: str, angle: float, size: int = 16) -> QPixmap:
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    faint = QColor(color)
    faint.setAlpha(60)
    r = QRectF(3, 3, size * 2 - 6, size * 2 - 6)
    p.setPen(QPen(faint, 3.2))
    p.drawEllipse(r)
    p.setPen(QPen(QColor(color), 3.2, Qt.SolidLine, Qt.RoundCap))
    p.drawArc(r, int(-angle * 16), 100 * 16)
    p.end()
    pm.setDevicePixelRatio(2)
    return pm


class LoadingButton(QPushButton):
    """Кнопка, которая умеет показывать крутилку на время своей операции."""

    def __init__(self, text: str = ""):
        super().__init__(text)
        self._icon = QIcon()
        self._loading = False
        self._angle = 0.0
        self._saved = ""
        self._t = QTimer(self)
        self._t.setInterval(30)
        self._t.timeout.connect(self._tick)

    def set_base_icon(self, ic: QIcon):
        self._icon = ic
        if not self._loading:
            self.setIcon(ic)

    def _spin_color(self) -> str:
        return "#06111D" if self.objectName() == "primary" else theme.ACCENT

    def _tick(self):
        self._angle = (self._angle + 14) % 360
        self.setIcon(QIcon(spinner_pixmap(self._spin_color(), self._angle)))

    def set_loading(self, on: bool, text: Optional[str] = None):
        if on == self._loading:
            if on and text:
                self.setText(text)
            return
        self._loading = on
        if on:
            self._saved = self.text()
            if text:
                self.setText(text)
            self.setIconSize(QSize(16, 16))
            self._tick()
            self._t.start()
            self.setCursor(Qt.BusyCursor)
        else:
            self._t.stop()
            self.setText(self._saved)
            self.setIcon(self._icon)
            self.setCursor(Qt.PointingHandCursor)

    def loading(self) -> bool:
        return self._loading

    def mousePressEvent(self, e):
        if self._loading:
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        if self._loading:
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if self._loading:
            return
        super().keyPressEvent(e)


def button(text: str, obj: str = "", icon=None, tip: str = "") -> LoadingButton:
    b = LoadingButton(text)
    if obj:
        b.setObjectName(obj)
    if icon is not None:
        b.set_base_icon(icon)
        b.setIconSize(QSize(16, 16))
    if tip:
        b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    return b


def hbox(*items, spacing: int = 8, margins=(0, 0, 0, 0)) -> QHBoxLayout:
    l = QHBoxLayout()
    l.setSpacing(spacing)
    l.setContentsMargins(*margins)
    for it in items:
        if it is None:
            l.addStretch(1)
        elif isinstance(it, int):
            l.addSpacing(it)
        elif isinstance(it, QWidget):
            l.addWidget(it)
        else:
            l.addLayout(it)
    return l


def vbox(*items, spacing: int = 8, margins=(0, 0, 0, 0)) -> QVBoxLayout:
    l = QVBoxLayout()
    l.setSpacing(spacing)
    l.setContentsMargins(*margins)
    for it in items:
        if it is None:
            l.addStretch(1)
        elif isinstance(it, int):
            l.addSpacing(it)
        elif isinstance(it, QWidget):
            l.addWidget(it)
        else:
            l.addLayout(it)
    return l


class Dot(QWidget):
    """Цветная точка статуса; в режиме busy — крутится, показывая, что идёт проверка."""

    def __init__(self, color: str = theme.FAINT, size: int = 10):
        super().__init__()
        self._c = QColor(color)
        self._busy = False
        self._a = 0.0
        self.setFixedSize(size + 4, size + 4)
        self._t = QTimer(self)
        self._t.setInterval(30)
        self._t.timeout.connect(self._tick)

    def _tick(self):
        self._a = (self._a + 12) % 360
        self.update()

    def set_color(self, c: str):
        self._c = QColor(c)
        self.set_busy(False)
        self.update()

    def set_busy(self, on: bool):
        self._busy = on
        self._t.start() if on else self._t.stop()
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        if self._busy:
            faint = QColor(theme.ACCENT)
            faint.setAlpha(55)
            p.setPen(QPen(faint, 2))
            p.drawEllipse(r)
            p.setPen(QPen(QColor(theme.ACCENT), 2, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(r, int(-self._a * 16), 110 * 16)
            return
        p.setPen(Qt.NoPen)
        p.setBrush(self._c)
        p.drawEllipse(r.adjusted(1, 1, -1, -1))


# ================================================================ индикатор-«батарейка»
def paint_battery(p: QPainter, rect: QRectF, score: Optional[float], stale: bool = False,
                  with_text: bool = True) -> None:
    """4 деления + процент. score=None — не тестировалась."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    col = QColor(theme.score_color(score))
    if stale:
        col.setAlpha(140)
    h = min(rect.height(), 12.0)
    w = 24.0
    x = rect.left()
    y = rect.center().y() - h / 2
    body = QRectF(x, y, w, h)
    p.setPen(QPen(QColor(theme.FAINT if score is None else col), 1.2))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(body, 2.5, 2.5)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(theme.FAINT if score is None else col))
    p.drawRoundedRect(QRectF(x + w + 1, y + h * 0.3, 2, h * 0.4), 1, 1)
    bars = 0 if score is None else max(1 if score > 0 else 0, min(4, round(score * 4 + 0.0001)))
    bw = (w - 3 - 3 * 1.5) / 4
    for i in range(4):
        r = QRectF(x + 1.5 + i * (bw + 1.5), y + 1.5, bw, h - 3)
        c = QColor(col) if i < bars else QColor("#223040")
        p.setBrush(c)
        p.drawRoundedRect(r, 1, 1)
    if with_text:
        p.setPen(QColor(theme.MUTED if score is None else col))
        f = QFont(p.font())
        f.setPointSizeF(8.5)
        f.setBold(score is not None)
        p.setFont(f)
        txt = "—" if score is None else f"{round(score * 100)}%"
        p.drawText(QRectF(x + w + 6, rect.top(), 40, rect.height()), Qt.AlignVCenter | Qt.AlignLeft, txt)
    p.restore()


class ScoreBadge(QWidget):
    def __init__(self, score: Optional[float] = None):
        super().__init__()
        self.score = score
        self.stale = False
        self.setFixedSize(74, 18)

    def set_score(self, s: Optional[float], stale: bool = False):
        self.score, self.stale = s, stale
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        paint_battery(p, QRectF(self.rect()), self.score, self.stale)


# ================================================================ выбор стратегии
ROLE_SCORE = Qt.UserRole + 1
ROLE_NAME = Qt.UserRole + 2
ROLE_USER = Qt.UserRole + 3
ROLE_STALE = Qt.UserRole + 4


class StrategyDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(option.rect.width(), 34)

    def paint(self, p: QPainter, option, index):
        p.save()
        r = QRectF(option.rect).adjusted(2, 1, -2, -1)
        if option.state & QStyle.State_Selected or option.state & QStyle.State_MouseOver:
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.SURFACE2))
            p.drawRoundedRect(r, 6, 6)
        name = index.data(ROLE_NAME) or index.data(Qt.DisplayRole)
        score = index.data(ROLE_SCORE)
        paint_battery(p, QRectF(r.left() + 10, r.top(), 70, r.height()), score, bool(index.data(ROLE_STALE)))
        p.setPen(QColor(theme.TEXT))
        tr = QRectF(r.left() + 84, r.top(), r.width() - 150, r.height())
        p.drawText(tr, Qt.AlignVCenter | Qt.AlignLeft,
                   p.fontMetrics().elidedText(name, Qt.ElideRight, int(tr.width())))
        if index.data(ROLE_USER):
            p.setPen(QColor(theme.ACCENT))
            f = QFont(p.font())
            f.setPointSizeF(8)
            p.setFont(f)
            p.drawText(QRectF(r.right() - 64, r.top(), 56, r.height()), Qt.AlignVCenter | Qt.AlignRight, "своя")
        p.restore()


class StrategyCombo(QComboBox):
    """Выпадающий список: лучшие по тесту сверху, иначе — по алфавиту."""

    def __init__(self):
        super().__init__()
        self.setItemDelegate(StrategyDelegate(self))
        self.setMinimumHeight(40)
        self.setMaxVisibleItems(14)
        self.setCursor(Qt.PointingHandCursor)
        self.view().setMinimumWidth(360)

    def fill(self, items: list[dict], current: str = ""):
        """items: [{name, score, user, stale}] — уже отсортированы."""
        self.blockSignals(True)
        self.clear()
        for it in items:
            self.addItem(it["name"])
            i = self.count() - 1
            self.setItemData(i, it["name"], ROLE_NAME)
            self.setItemData(i, it.get("score"), ROLE_SCORE)
            self.setItemData(i, it.get("user", False), ROLE_USER)
            self.setItemData(i, it.get("stale", False), ROLE_STALE)
        idx = self.findText(current)
        self.setCurrentIndex(idx if idx >= 0 else 0)
        self.blockSignals(False)

    def current_name(self) -> str:
        return self.currentText()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(theme.ACCENT if self.hasFocus() else theme.LINE), 1))
        p.setBrush(QColor(theme.SURFACE))
        p.drawRoundedRect(r, 9, 9)
        i = self.currentIndex()
        if i >= 0:
            paint_battery(p, QRectF(r.left() + 12, r.top(), 70, r.height()),
                          self.itemData(i, ROLE_SCORE), bool(self.itemData(i, ROLE_STALE)))
            p.setPen(QColor(theme.TEXT))
            tr = QRectF(r.left() + 88, r.top(), r.width() - 118, r.height())
            p.drawText(tr, Qt.AlignVCenter | Qt.AlignLeft,
                       p.fontMetrics().elidedText(self.currentText(), Qt.ElideRight, int(tr.width())))
        else:
            p.setPen(QColor(theme.FAINT))
            p.drawText(r.adjusted(14, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, "Нет стратегий")
        # стрелка
        p.setPen(QPen(QColor(theme.MUTED), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        cx, cy = r.right() - 18, r.center().y()
        path = QPainterPath()
        path.moveTo(cx - 4, cy - 2)
        path.lineTo(cx, cy + 2)
        path.lineTo(cx + 4, cy - 2)
        p.drawPath(path)


# ================================================================ кнопка подключения
class PowerButton(QAbstractButton):
    """Большая круглая кнопка. Состояния: off | busy | on | error | external."""

    COLORS = {"off": "#3A4859", "busy": theme.ACCENT, "on": theme.OK, "error": theme.ERR,
              "external": theme.WARN}

    def __init__(self, size: int = 176):
        super().__init__()
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self._state = "off"
        self._angle = 0.0
        self._glow = 0.0
        self._spin = QTimer(self)
        self._spin.setInterval(16)
        self._spin.timeout.connect(self._tick)
        self._anim = QPropertyAnimation(self, b"glow", self)
        self._anim.setDuration(420)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def _tick(self):
        self._angle = (self._angle + 4.5) % 360
        self.update()

    def get_glow(self):
        return self._glow

    def set_glow(self, v):
        self._glow = v
        self.update()

    glow = Property(float, get_glow, set_glow)

    def set_state(self, s: str):
        if s == self._state:
            return
        self._state = s
        if s == "busy":
            self._spin.start()
        else:
            self._spin.stop()
        self._anim.stop()
        self._anim.setStartValue(self._glow)
        self._anim.setEndValue(1.0 if s in ("on", "external", "error") else 0.0)
        self._anim.start()
        self.update()

    def state(self) -> str:
        return self._state

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W = self.width()
        c = self.rect().center()
        col = QColor(self.COLORS.get(self._state, "#3A4859"))
        # мягкое свечение
        if self._glow > 0.01:
            g = QRadialGradient(c, W / 2)
            gc = QColor(col)
            gc.setAlpha(int(70 * self._glow))
            g.setColorAt(0.55, gc)
            g.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(g)
            p.drawEllipse(self.rect())
        m = W * 0.13
        ring = QRectF(self.rect()).adjusted(m, m, -m, -m)
        # основа
        base = QRadialGradient(ring.center(), ring.width() / 2)
        base.setColorAt(0, QColor("#1D2835"))
        base.setColorAt(1, QColor("#141C26"))
        p.setBrush(base)
        hover = self.underMouse()
        p.setPen(QPen(QColor("#2A3748" if not hover else "#34465B"), 1.5))
        p.drawEllipse(ring)
        # кольцо состояния
        pen = QPen(col, 4, Qt.SolidLine, Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        rr = ring.adjusted(6, 6, -6, -6)
        if self._state == "busy":
            p.drawArc(rr, int(-self._angle * 16), 100 * 16)
            faint = QColor(col)
            faint.setAlpha(50)
            p.setPen(QPen(faint, 4))
            p.drawArc(rr, int((-self._angle + 110) * 16), 250 * 16)
        else:
            if self._state == "off":
                c2 = QColor(col)
                p.setPen(QPen(c2, 3))
            p.drawEllipse(rr)
        # символ питания, геометрически по центру кольца
        sym = QColor(col if self._state != "off" else ("#8A9AAD" if hover else "#6B7B8E"))
        p.setPen(QPen(sym, 5.5, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        rad = ring.width() * 0.19
        cx, cy = ring.center().x(), ring.center().y() + rad * 0.06
        p.drawArc(QRectF(cx - rad, cy - rad, rad * 2, rad * 2), 122 * 16, 296 * 16)
        p.drawLine(QPointF(cx, cy - rad * 1.22), QPointF(cx, cy - rad * 0.18))

    def enterEvent(self, e):
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.update()
        super().leaveEvent(e)


# ================================================================ переключатель
class Switch(QAbstractButton):
    def __init__(self, checked: bool = False):
        super().__init__()
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(38, 22)
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        on = self.isChecked()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.ACCENT if on else "#2A3646"))
        if not self.isEnabled():
            p.setOpacity(0.45)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 6
        x = r.right() - d - 3 if on else r.left() + 3
        p.setBrush(QColor("#06111D" if on else "#9FB0C2"))
        p.drawEllipse(QRectF(x, r.top() + 3, d, d))


class Segmented(QWidget):
    """Сегментированный выбор. changed(key)."""
    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]]):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        for i, (key, text) in enumerate(options):
            b = QPushButton(text)
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            rad = "7px"
            style = ""
            if i == 0:
                style = f"border-top-left-radius:{rad};border-bottom-left-radius:{rad};"
            if i == len(options) - 1:
                style += f"border-top-right-radius:{rad};border-bottom-right-radius:{rad};"
            if style:
                b.setStyleSheet(f"QPushButton#seg{{{style}}}")
            self.group.addButton(b)
            self.buttons[key] = b
            lay.addWidget(b)
            b.clicked.connect(lambda _=False, k=key: self.changed.emit(k))

    def set_value(self, key: str):
        b = self.buttons.get(key)
        if b:
            b.setChecked(True)

    def value(self) -> str:
        for k, b in self.buttons.items():
            if b.isChecked():
                return k
        return ""


# ================================================================ строки таблиц
class RowDelegate(QStyledItemDelegate):
    """Рисует фон строки «таблеткой»: скругляются только крайние ячейки строки.

    hover/select — подсвечивать ли строку при наведении и выделении;
    highlight(row) — дополнительная подсветка строки (например, проверяемой сейчас).
    """

    def __init__(self, view, hover: bool = True, select: bool = True, row_height: int = 32,
                 highlight: Optional[Callable] = None):
        super().__init__(view)
        self.view = view
        self.hover = hover
        self.select = select
        self.row_height = row_height
        self.highlight = highlight
        self._hover_row = -1
        view.setMouseTracking(True)
        view.viewport().installEventFilter(self)

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.MouseMove and self.hover:
            idx = self.view.indexAt(ev.position().toPoint())
            row = idx.row() if idx.isValid() else -1
            key = (row, idx.parent().row() if idx.isValid() else -1)
            if key != self._hover_row:
                self._hover_row = key
                self.view.viewport().update()
        elif t == QEvent.Leave:
            self._hover_row = -1
            self.view.viewport().update()
        return False

    def sizeHint(self, option, index):
        s = super().sizeHint(option, index)
        return QSize(s.width(), max(s.height(), self.row_height))

    def _bg(self, option, index) -> Optional[QColor]:
        if self.highlight and self.highlight(index):
            return QColor("#17324A")
        if self.select and option.state & QStyle.State_Selected:
            return QColor("#22364C")
        if self.hover and self._hover_row == (index.row(), index.parent().row()):
            return QColor("#1B2735")
        return None

    def paint_background(self, p: QPainter, option, index):
        bg = self._bg(option, index)
        if bg is None:
            return
        model = index.model()
        last = model.columnCount(index.parent()) - 1
        r = QRectF(option.rect).adjusted(0, 1, 0, -1)
        rad = 7.0
        left = index.column() == 0
        right = index.column() == last
        if left:
            r.adjust(3, 0, 0, 0)
        if right:
            r.adjust(0, 0, -3, 0)
        path = QPainterPath()
        path.setFillRule(Qt.WindingFill)
        path.addRoundedRect(r, rad if (left or right) else 0, rad if (left or right) else 0)
        if not left:
            path.addRect(QRectF(r.left(), r.top(), min(r.width(), rad * 2), r.height()))
        if not right:
            path.addRect(QRectF(max(r.left(), r.right() - rad * 2), r.top(), min(r.width(), rad * 2),
                                r.height()))
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawPath(path.simplified())
        p.restore()

    def plain_option(self, option, index) -> QStyleOptionViewItem:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.state &= ~(QStyle.State_Selected | QStyle.State_MouseOver | QStyle.State_HasFocus)
        return opt

    def paint(self, p, option, index):
        self.paint_background(p, option, index)
        opt = self.plain_option(option, index)
        opt.widget.style().drawControl(QStyle.CE_ItemViewItem, opt, p, opt.widget) if opt.widget else \
            super().paint(p, opt, index)


def setup_rows(view, hover: bool = True, select: bool = True, row_height: int = 32,
               highlight: Optional[Callable] = None) -> RowDelegate:
    """Подключить «табличные» строки к QTreeView/QTableView."""
    d = RowDelegate(view, hover, select, row_height, highlight)
    view.setItemDelegate(d)
    view.setAlternatingRowColors(False)
    view.setFocusPolicy(Qt.NoFocus if not select else Qt.StrongFocus)
    if hasattr(view, "setShowGrid"):
        view.setShowGrid(False)
    return d


# ================================================================ раскладка с переносом
class FlowLayout(QLayout):
    """Элементы идут в строку и переносятся, когда не помещаются (для узкого окна)."""

    def __init__(self, parent=None, spacing: int = 6, align_right: bool = False):
        super().__init__(parent)
        self._items = []
        self._sp = spacing
        self.align_right = align_right
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._layout(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._layout(rect, False)

    def sizeHint(self):
        """Желаемый размер — все элементы в одну строку; переносим, только если не влезает."""
        items = [it for it in self._items if not (it.widget() is not None and it.widget().isHidden())]
        if not items:
            return QSize(0, 0)
        w = sum(it.sizeHint().width() for it in items) + self._sp * (len(items) - 1)
        h = max(it.sizeHint().height() for it in items)
        return QSize(w, h)

    def minimumSize(self):
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _layout(self, rect, test: bool) -> int:
        x, y, line_h = rect.x(), rect.y(), 0
        rows, cur = [], []
        for it in self._items:
            if it.widget() is not None and it.widget().isHidden():
                continue                       # явно скрытые кнопки места не занимают
            hint = it.sizeHint()
            nx = x + hint.width()
            if cur and nx > rect.right() + 1:
                rows.append((cur, line_h))
                x, y = rect.x(), y + line_h + self._sp
                cur, line_h = [], 0
                nx = x + hint.width()
            cur.append((it, QPoint(x, y), hint))
            x = nx + self._sp
            line_h = max(line_h, hint.height())
        if cur:
            rows.append((cur, line_h))
        if not test:
            for items, h in rows:
                shift = 0
                if self.align_right and items:
                    last_it, last_pt, last_hint = items[-1]
                    shift = rect.right() + 1 - (last_pt.x() + last_hint.width())
                for it, pt, hint in items:
                    it.setGeometry(QRect(QPoint(pt.x() + shift, pt.y() + (h - hint.height()) // 2), hint))
        return y + line_h - rect.y()


def flow(*widgets, spacing: int = 6, align_right: bool = False) -> QWidget:
    """Контейнер-строка с переносом: flow(btn1, btn2, ...)."""
    w = QWidget()
    w.setAttribute(Qt.WA_TranslucentBackground)
    lay = FlowLayout(w, spacing, align_right)
    for x in widgets:
        if x is not None:
            lay.addWidget(x)
    sp = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
    sp.setHeightForWidth(True)
    w.setSizePolicy(sp)
    return w


# ================================================================ прокручиваемая страница
class ScrollPage(QScrollArea):
    """Страница, которая прокручивается по вертикали, если не помещается в окно.

    Высота содержимого считается с учётом переноса текста, поэтому ничего не наезжает.
    """

    def __init__(self, page: QWidget):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWidget(page)
        self.page = page

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def _fit(self):
        lay = self.page.layout()
        if lay is None:
            return
        w = self.viewport().width()
        h = lay.totalHeightForWidth(w) if lay.hasHeightForWidth() else lay.totalMinimumSize().height()
        h = max(h, lay.totalMinimumSize().height())
        if self.page.minimumHeight() != h:
            self.page.setMinimumHeight(h)
