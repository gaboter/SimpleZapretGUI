"""Поведение для всего приложения: плавный скролл и тёмные заголовки окон Windows."""
from __future__ import annotations

import ctypes

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPropertyAnimation, Qt
from PySide6.QtWidgets import (QAbstractItemView, QAbstractScrollArea, QApplication, QComboBox,
                               QPlainTextEdit, QScrollBar, QStyle, QTextEdit, QWidget)

from ..core.paths import IS_WINDOWS

STEP_PX = 110          # прокрутка на один «щелчок» колеса, px
DURATION = 230         # длительность анимации, мс
POPUP_ROWS = 9         # выпадающий список показывает не больше 9 строк, дальше — скролл


def fit_combo_popup(combo: QComboBox) -> None:
    """Ширина выпадающего списка — по самому длинному пункту (но не уже самого селекта)."""
    view = combo.view()
    fm = view.fontMetrics()
    widest = 0
    for i in range(combo.count()):
        widest = max(widest, fm.horizontalAdvance(combo.itemText(i)))
    extra = int(combo.property("popupExtra") or 0)          # иконки/бейджи в своих делегатах
    scroll = combo.style().pixelMetric(QStyle.PM_ScrollBarExtent) if combo.count() > POPUP_ROWS else 0
    w = widest + extra + 40 + scroll                          # отступы пункта и рамки списка
    view.setMinimumWidth(max(w, combo.width()))


def setup_combo(combo: QComboBox) -> None:
    if combo.property("_szg_combo"):
        return
    combo.setProperty("_szg_combo", True)
    combo.setMaxVisibleItems(POPUP_ROWS)
    m = combo.model()
    fit = lambda *_a: fit_combo_popup(combo)  # noqa: E731
    for sig in (m.rowsInserted, m.rowsRemoved, m.modelReset, m.dataChanged):
        sig.connect(fit)
    fit()


def _colorref(hex_color: str) -> int:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return b << 16 | g << 8 | r


def dark_title_bar(w: QWidget, caption: str = "#0B1016") -> None:
    """Тёмная рамка окна (Windows 10 1809+ / 11) в цвет приложения."""
    if not IS_WINDOWS:
        return
    try:
        hwnd = ctypes.c_void_p(int(w.winId()))
        dwm = ctypes.windll.dwmapi
        on = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (новый и старый номер)
            if dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(on), ctypes.sizeof(on)) == 0:
                break
        color = ctypes.c_int(_colorref(caption))
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(color), ctypes.sizeof(color))   # цвет заголовка
        border = ctypes.c_int(_colorref("#263241"))
        dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(border), ctypes.sizeof(border))  # цвет рамки
    except Exception:
        pass


class AppBehavior(QObject):
    """Фильтр событий на всё приложение."""

    def __init__(self, app: QApplication):
        super().__init__(app)
        self._anims: dict[int, QPropertyAnimation] = {}
        app.installEventFilter(self)

    # ------------------------------------------------------------ события
    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.Wheel:
            return self._wheel(obj, ev)
        if t == QEvent.Polish and isinstance(obj, QComboBox):
            setup_combo(obj)
        elif t == QEvent.Resize and isinstance(obj, QComboBox) and obj.property("_szg_combo"):
            fit_combo_popup(obj)
        if t == QEvent.Polish and isinstance(obj, QAbstractItemView):
            obj.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
            obj.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
            obj.verticalScrollBar().setSingleStep(18)
        elif t == QEvent.Show and isinstance(obj, QWidget) and obj.isWindow():
            if obj.windowType() in (Qt.Window, Qt.Dialog) and not obj.property("_szg_dark"):
                obj.setProperty("_szg_dark", True)
                dark_title_bar(obj)
        return False

    # ------------------------------------------------------------ плавный скролл
    def _wheel(self, obj, ev) -> bool:
        area = obj.parent() if isinstance(obj, QWidget) else None
        if not isinstance(area, QAbstractScrollArea) or area.viewport() is not obj:
            return False
        if ev.modifiers() & Qt.ControlModifier:
            return False
        if not ev.pixelDelta().isNull():
            return False                       # тачпад: прокрутка и так плавная
        d = ev.angleDelta()
        horizontal = abs(d.x()) > abs(d.y()) or bool(ev.modifiers() & Qt.ShiftModifier)
        delta = (d.x() or d.y()) if horizontal else d.y()
        if not delta:
            return False
        bar: QScrollBar = area.horizontalScrollBar() if horizontal else area.verticalScrollBar()
        if bar.maximum() <= bar.minimum():
            return False                       # прокручивать нечего — отдаём родителю
        text_lines = isinstance(area, (QPlainTextEdit,)) and not horizontal
        step = 3 if text_lines else STEP_PX
        if isinstance(area, QTextEdit) and not horizontal:
            step = STEP_PX
        amount = -delta / 120.0 * step
        anim = self._anims.get(id(bar))
        base = anim.endValue() if anim and anim.state() == QPropertyAnimation.Running else bar.value()
        target = int(max(bar.minimum(), min(bar.maximum(), base + amount)))
        if target == bar.value() and not (anim and anim.state() == QPropertyAnimation.Running):
            return False                       # упёрлись в край — пусть прокрутится родитель
        if anim is None:
            anim = QPropertyAnimation(bar, b"value", self)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            self._anims[id(bar)] = anim
            bar.destroyed.connect(lambda *_a, k=id(bar): self._anims.pop(k, None))
        anim.stop()
        anim.setDuration(DURATION)
        anim.setStartValue(bar.value())
        anim.setEndValue(target)
        anim.start()
        return True
