"""Палитра и таблица стилей. Тёмная, спокойная, «как у VPN-клиента»."""

BG = "#0E141B"
SIDEBAR = "#0B1016"
SURFACE = "#151D27"
SURFACE2 = "#1C2632"
LINE = "#263241"
TEXT = "#E7EDF3"
MUTED = "#8A9AAD"
FAINT = "#5B6B7E"
ACCENT = "#5AB0FF"
OK = "#3FD49B"
WARN = "#F0B44C"
ERR = "#F06A77"

FONT = '"Segoe UI Variable Text", "Segoe UI", "Inter", sans-serif'


def score_color(score: float | None) -> str:
    if score is None:
        return FAINT
    if score >= 0.85:
        return OK
    if score >= 0.6:
        return "#9BD64A"
    if score >= 0.35:
        return WARN
    return ERR


def _asset(name: str, body: str, color: str = MUTED, width: float = 2.2) -> str:
    """SVG для QSS (стрелки селектов/спинбоксов) — пишется во временную папку приложения."""
    from ..core.paths import TEMP_DIR
    d = TEMP_DIR / "ui"
    d.mkdir(parents=True, exist_ok=True)
    f = d / name
    data = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')
    try:
        if not f.exists() or f.read_text(encoding="utf-8") != data:
            f.write_text(data, encoding="utf-8")
    except OSError:
        pass
    return f.as_posix()


def build_qss() -> str:
    down = _asset("chevron-down.svg", '<path d="M6 9l6 6 6-6"/>')
    up = _asset("chevron-up.svg", '<path d="M6 15l6-6 6 6"/>')
    check = _asset("check.svg", '<path d="M5 12.5l4.5 4.5L19 7.5"/>', "#06111D", 3.2)
    return f"""
* {{ font-family: {FONT}; font-size: 13px; color: {TEXT}; }}
QMainWindow, QWidget#root, QDialog, QMessageBox, QInputDialog {{ background: {BG}; }}
QWidget#sidebar {{ background: {SIDEBAR}; border-right: 1px solid {LINE}; }}
QWidget#page {{ background: {BG}; }}
QLabel {{ background: transparent; }}
QLabel#h1 {{ font-size: 20px; font-weight: 600; }}
QLabel#h2 {{ font-size: 15px; font-weight: 600; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#faint {{ color: {FAINT}; font-size: 12px; }}
QFrame#card {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 10px; }}
QFrame#errorCard {{ background: #2A1A20; border: 1px solid #5A2A33; border-radius: 10px; }}
QFrame#warnCard {{ background: #2A2418; border: 1px solid #5A4A26; border-radius: 10px; }}
QFrame#banner {{ background: #13263A; border: 1px solid #1F4467; border-radius: 10px; }}
QFrame#invite {{ background: #13263A; border: 1px solid #1F4467; border-radius: 10px; }}

QPushButton {{
    background: {SURFACE2}; border: 1px solid {LINE}; border-radius: 7px;
    padding: 7px 14px; min-height: 18px;
}}
QPushButton:hover {{ background: #233040; border-color: #33445A; }}
QPushButton:pressed {{ background: #1A2430; }}
QPushButton:disabled {{ color: {FAINT}; background: {SURFACE}; }}
QPushButton#primary {{ background: {ACCENT}; color: #06111D; border: none; font-weight: 600; }}
QPushButton#primary:hover {{ background: #74BEFF; }}
QPushButton#primary:disabled {{ background: #2B4460; color: #7F93A8; }}
QPushButton#danger {{ color: {ERR}; }}
QPushButton#link {{ background: transparent; border: none; color: {ACCENT}; padding: 2px 4px; }}
QPushButton#link:hover {{ text-decoration: underline; }}
QPushButton#seg {{ border-radius: 0; padding: 6px 12px; }}
QPushButton#seg:checked {{ background: #22364C; border-color: {ACCENT}; color: {ACCENT}; }}
QDialogButtonBox QPushButton {{ min-width: 80px; }}
QToolButton#nav {{ background: transparent; border: none; border-radius: 9px; padding: 8px; }}
QToolButton#nav:hover {{ background: {SURFACE}; }}
QToolButton#nav:checked {{ background: {SURFACE2}; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {{
    background: {SURFACE}; border: 1px solid {LINE}; border-radius: 7px; padding: 6px 8px;
    selection-background-color: #2C4A6B;
}}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QPlainTextEdit#code {{ font-family: "Cascadia Mono", "Consolas", monospace; font-size: 12px; }}
QComboBox {{ padding-right: 30px; min-height: 18px; combobox-popup: 0; }}
QComboBox:hover {{ border-color: #33445A; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 28px;
    border: none; background: transparent; }}
QComboBox::down-arrow {{ image: url("{down}"); width: 12px; height: 12px; }}
QComboBox::down-arrow:on {{ image: url("{up}"); }}
QComboBox QAbstractItemView {{
    background: {SURFACE}; border: 1px solid {LINE}; border-radius: 8px; outline: none; padding: 4px;
    selection-background-color: {SURFACE2};
}}
QComboBox QAbstractItemView::item {{ min-height: 28px; padding: 0 8px; border-radius: 5px; }}
QComboBox QAbstractItemView::item:hover {{ background: #1B2735; }}
QComboBox QAbstractItemView::item:selected {{ background: {SURFACE2}; color: {TEXT}; }}
QSpinBox {{ padding-right: 24px; }}
QSpinBox::up-button, QSpinBox::down-button {{ subcontrol-origin: border; width: 22px; border: none;
    background: transparent; }}
QSpinBox::up-button {{ subcontrol-position: top right; }}
QSpinBox::down-button {{ subcontrol-position: bottom right; }}
QSpinBox::up-arrow {{ image: url("{up}"); width: 10px; height: 10px; }}
QSpinBox::down-arrow {{ image: url("{down}"); width: 10px; height: 10px; }}

QListWidget, QTreeWidget, QTableView, QListView, QTreeView {{
    background: {SURFACE}; border: 1px solid {LINE}; border-radius: 8px; outline: none;
}}
QListWidget::item {{ padding: 6px 6px; border-radius: 6px; }}
QListWidget::item:selected {{ background: #22364C; color: {TEXT}; }}
QListWidget::item:hover:!selected {{ background: #1B2735; }}
QTreeView::item, QTableView::item {{ background: transparent; border: none; padding: 0 6px; }}
QTreeView::branch {{ background: transparent; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{
    background: {SURFACE}; color: {MUTED}; border: none; border-bottom: 1px solid {LINE};
    padding: 6px 8px;
}}
QTableCornerButton::section {{ background: {SURFACE}; border: none; }}
QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid #3A4A5E;
    background: {SURFACE}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url("{check}"); }}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QTreeView::indicator {{ width: 15px; height: 15px; border-radius: 4px; border: 1px solid #3A4A5E;
    background: {BG}; }}
QTreeView::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url("{check}"); }}
QListView::indicator {{ width: 15px; height: 15px; border-radius: 4px; border: 1px solid #3A4A5E;
    background: {BG}; }}
QListView::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url("{check}"); }}
QRadioButton::indicator {{ width: 14px; height: 14px; border-radius: 8px; border: 1px solid #3A4A5E;
    background: {SURFACE}; }}
QRadioButton::indicator:checked {{ background: {ACCENT}; border: 3px solid {SURFACE}; }}
QProgressBar {{ background: {SURFACE2}; border: none; border-radius: 3px; height: 6px;
    text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}

QScrollBar:vertical {{ background: transparent; width: 12px; margin: 3px 2px 3px 2px; border: none; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px 3px 2px 3px; border: none; }}
QScrollBar::handle:vertical {{ background: #2A3748; border-radius: 4px; min-height: 36px; margin: 0 1px; }}
QScrollBar::handle:horizontal {{ background: #2A3748; border-radius: 4px; min-width: 36px; margin: 1px 0; }}
QScrollBar::handle:hover {{ background: #3A4B61; }}
QScrollBar::handle:pressed {{ background: #4A6180; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; background: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QAbstractScrollArea::corner {{ background: transparent; border: none; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 8px 14px; border: none;
    border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom-color: {ACCENT}; }}
QToolTip {{ background: {SURFACE2}; color: {TEXT}; border: 1px solid {LINE}; padding: 6px;
    border-radius: 6px; }}
QMenu {{ background: {SURFACE}; border: 1px solid {LINE}; padding: 4px; border-radius: 8px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 5px; background: transparent; }}
QMenu::item:selected {{ background: {SURFACE2}; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 4px 8px; }}
QWidget#statusbar {{ background: {SIDEBAR}; border-top: 1px solid {LINE}; }}
QFrame#findbar {{ background: {SURFACE}; border: none; border-bottom: 1px solid {LINE}; }}
QSplitter::handle {{ background: {BG}; }}
"""


def apply(app) -> None:
    """Единый тёмный вид для всего приложения, включая диалоги и системные окна Qt."""
    from PySide6.QtGui import QColor, QPalette
    app.setStyle("Fusion")
    pal = QPalette()
    roles = {
        QPalette.Window: BG, QPalette.WindowText: TEXT, QPalette.Base: SURFACE,
        QPalette.AlternateBase: "#17202B", QPalette.Text: TEXT, QPalette.Button: SURFACE2,
        QPalette.ButtonText: TEXT, QPalette.Highlight: "#2C4A6B", QPalette.HighlightedText: TEXT,
        QPalette.ToolTipBase: SURFACE2, QPalette.ToolTipText: TEXT, QPalette.PlaceholderText: FAINT,
        QPalette.Link: ACCENT, QPalette.BrightText: TEXT, QPalette.Mid: LINE, QPalette.Dark: SIDEBAR,
        QPalette.Light: SURFACE2, QPalette.Shadow: "#000000",
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor(FAINT))
    app.setPalette(pal)
    app.setStyleSheet(build_qss())
