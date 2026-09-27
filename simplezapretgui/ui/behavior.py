"""Поведение для всего приложения: плавный скролл и тёмные заголовки окон Windows."""
from __future__ import annotations

import ctypes

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPropertyAnimation, Qt
from PySide6.QtWidgets import (QAbstractItemView, QAbstractScrollArea, QApplication, QPlainTextEdit,
                               QScrollBar, QTextEdit, QWidget)

from ..core.paths import IS_WINDOWS

STEP_PX = 110          # прокрутка на один «щелчок» колеса, px
DURATION = 230         # длительность анимации, мс


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
