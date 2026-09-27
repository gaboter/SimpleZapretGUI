"""Списки: просмотр, добавление и удаление сайтов и IP; свои категории."""
from __future__ import annotations

import re

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QBoxLayout, QComboBox, QDialog, QHeaderView, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QTableView,
                               QVBoxLayout, QWidget)

from ...core import log, winutil
from ...core.lists import CUSTOM_TARGETS
from .. import icons, theme
from ..widgets import Segmented, button, card, flow, hbox, label, run_bg, setup_rows, vbox

SOURCE_TEXT = {"builtin": "встроенный", "user": "ваш", "added": "ваш (во встроенном)"}


class EntryModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.rows: list[tuple[str, str, str]] = []
        self.titles: dict[str, str] = {}
        self.show_cat = False

    def set_rows(self, rows, show_cat: bool, titles: dict):
        self.beginResetModel()
        self.rows = rows
        self.show_cat = show_cat
        self.titles = titles
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 3 if self.show_cat else 2

    def headerData(self, section, orient, role=Qt.DisplayRole):
        if orient == Qt.Horizontal and role == Qt.DisplayRole:
            return (["Ресурс", "Источник", "Категория"])[section]
        if orient == Qt.Horizontal and role == Qt.ToolTipRole and section == 1:
            return "встроенный — из zapret; ваш — добавлен вами (сохраняется при обновлениях)"
        return None

    def data(self, idx, role=Qt.DisplayRole):
        if not idx.isValid():
            return None
        v, src, cat = self.rows[idx.row()]
        if role == Qt.DisplayRole:
            return (v, SOURCE_TEXT.get(src, src), self.titles.get(cat, cat))[idx.column()]
        if role == Qt.ForegroundRole and idx.column() == 1:
            return QColor(theme.ACCENT if src != "builtin" else theme.MUTED)
        if role == Qt.ForegroundRole and idx.column() == 2:
            return QColor(theme.MUTED)
        return None


class AddDialog(QDialog):
    """Добавление ресурсов: одним или сразу списком, с выбором категории."""

    def __init__(self, parent, cats: list, current: str):
        super().__init__(parent)
        self.setWindowTitle("Добавить ресурсы")
        self.resize(480, 420)
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        self.cat = QComboBox()
        for c in cats:
            self.cat.addItem(c.title, c.key)
        i = self.cat.findData(current)
        self.cat.setCurrentIndex(i if i >= 0 else 0)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("example.com\nsub.example.org\n\nПо одному в строке, можно через пробел или "
                                     "запятую. Ссылки тоже подойдут — домен выделится сам.")
        self.hint = label("", "faint", wrap=True)
        self.cat.currentIndexChanged.connect(self._hint)
        ok = button("Добавить", "primary", icons.icon("plus", "#06111D"))
        ok.clicked.connect(self.accept)
        cancel = button("Отмена")
        cancel.clicked.connect(self.reject)
        lay.addLayout(hbox(label("Категория:", "muted"), self.cat, spacing=8))
        lay.addWidget(self.text, 1)
        lay.addWidget(self.hint)
        lay.addLayout(hbox(None, cancel, ok))
        self._cats = {c.key: c for c in cats}
        self._hint()
        self.text.setFocus()

    def _hint(self, *_):
        c = self._cats.get(self.cat.currentData())
        if c:
            self.hint.setText(("IP-адреса или подсети, например 1.2.3.4 или 10.0.0.0/8. "
                               if c.kind == "ip" else "Поддомены включаются автоматически. ") + c.description)

    def values(self) -> list[str]:
        return [v for v in re.split(r"[\s,;]+", self.text.toPlainText()) if v.strip()]

    def key(self) -> str:
        return self.cat.currentData()


class NewCategoryDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Новая категория")
        self.resize(420, 200)
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Например: Игры")
        self.kind = QComboBox()
        for k, (t, _) in CUSTOM_TARGETS.items():
            self.kind.addItem(t, k)
        ok = button("Создать", "primary")
        ok.clicked.connect(self.accept)
        cancel = button("Отмена")
        cancel.clicked.connect(self.reject)
        lay.addWidget(label("Название", "muted"))
        lay.addWidget(self.name)
        lay.addWidget(label("Что делать с ресурсами категории", "muted"))
        lay.addWidget(self.kind)
        lay.addWidget(label("«Сайты для обхода» — обход включается для этих сайтов; «Исключения» — обход их "
                            "никогда не трогает.", "faint", wrap=True))
        lay.addLayout(hbox(None, cancel, ok))


class ListsPage(QWidget):
    def __init__(self, ctl):
        super().__init__()
        self.setObjectName("page")
        self.ctl = ctl
        self.cat = "all"
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 14)
        root.setSpacing(10)
        root.addWidget(label("Списки ресурсов", "h1"))
        root.addWidget(label("Какие сайты и IP обрабатывает обход. Ваши добавления, удаления и свои категории "
                             "сохраняются при обновлении zapret.", "muted", wrap=True))

        self.body = QBoxLayout(QBoxLayout.LeftToRight)
        self.body.setSpacing(12)

        left = QWidget()
        ll = vbox(spacing=6)
        left.setLayout(ll)
        self.cats = QListWidget()
        self.cats.currentItemChanged.connect(self._cat_changed)
        self.new_cat = button("Категория", icon=icons.icon("plus"), tip="Создать свою категорию")
        self.new_cat.clicked.connect(self._create_cat)
        self.del_cat = button("", icon=icons.icon("trash", theme.ERR), tip="Удалить выбранную свою категорию")
        self.del_cat.clicked.connect(self._delete_cat)
        ll.addWidget(self.cats, 1)
        ll.addLayout(hbox(self.new_cat, None, self.del_cat, spacing=6))
        self.left = left
        self.body.addWidget(left)

        right = vbox(spacing=8)
        self.desc = label("", "muted", wrap=True)

        # проверка домена
        self.check_box = card()
        cl = vbox(margins=(12, 10, 12, 10), spacing=6)
        self.check_box.setLayout(cl)
        self.check_edit = QLineEdit()
        self.check_edit.setPlaceholderText("Проверить сайт, например cdn.discordapp.com")
        self.check_edit.returnPressed.connect(self._check)
        self.check_btn = button("Проверить", icon=icons.icon("search"))
        self.check_btn.clicked.connect(self._check)
        self.check_res = label("", "muted", wrap=True)
        self.check_res.hide()
        cl.addLayout(hbox(self.check_edit, self.check_btn))
        cl.addWidget(self.check_res)

        # IPSet
        self.ipset_box = card()
        il = vbox(margins=(12, 10, 12, 10), spacing=6)
        self.ipset_box.setLayout(il)
        self.ipset_mode = Segmented([("loaded", "По списку IP"), ("none", "Выкл"), ("any", "Все IP")])
        self.ipset_mode.changed.connect(self._set_ipset)
        self.ipset_upd = button("Обновить список IP", icon=icons.icon("download"))
        self.ipset_upd.clicked.connect(self._update_ipset)
        il.addWidget(flow(label("Режим IPSet:"), self.ipset_mode, self.ipset_upd, spacing=8))
        il.addWidget(label("По списку — обход для IP из списка; Выкл — IPSet не используется; "
                           "Все IP — для всех адресов (может ломать другие сайты).", "faint", wrap=True))

        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по списку")
        self.search.textChanged.connect(lambda t: self.proxy.setFilterFixedString(t.strip()))

        self.model = EntryModel()
        self.proxy = QSortFilterProxyModel()
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.proxy.setFilterKeyColumn(0)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().hide()
        setup_rows(self.table, hover=True, select=True, row_height=30)
        self.table.horizontalHeader().setHighlightSections(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setMinimumHeight(240)

        self.add_b = button("Добавить", "primary", icons.icon("plus", "#06111D"))
        self.add_b.clicked.connect(self._add)
        self.rm_b = button("Убрать выбранные", "danger", icons.icon("minus", theme.ERR))
        self.rm_b.clicked.connect(self._remove)
        self.open_file = button("Файл", icon=icons.icon("folder"), tip="Открыть файл списка")
        self.open_file.clicked.connect(self._open_file)

        right.addWidget(self.desc)
        right.addWidget(self.check_box)
        right.addWidget(self.ipset_box)
        right.addLayout(hbox(self.search, spacing=6))
        right.addWidget(self.table, 1)
        right.addWidget(flow(self.add_b, self.rm_b, self.open_file, spacing=6))
        self.body.addLayout(right, 1)
        root.addLayout(self.body, 1)

        self._restart_timer = QTimer(self)
        self._restart_timer.setSingleShot(True)
        self._restart_timer.setInterval(1500)
        self._restart_timer.timeout.connect(lambda: self.ctl.restart_if_running("списки изменены"))

        ctl.installed_changed.connect(lambda _: self.reload())
        self.reload()

    # ------------------------------------------------------------ адаптивность
    def resizeEvent(self, e):
        super().resizeEvent(e)
        narrow = self.width() < 780
        d = QBoxLayout.TopToBottom if narrow else QBoxLayout.LeftToRight
        if self.body.direction() != d:
            self.body.setDirection(d)
        if narrow:
            self.left.setMinimumWidth(0)
            self.left.setMaximumWidth(16777215)
            self.left.setMaximumHeight(200)
        else:
            self.left.setFixedWidth(240)
            self.left.setMaximumHeight(16777215)

    # ------------------------------------------------------------ данные
    def lm(self):
        return self.ctl.list_manager()

    def reload(self):
        if not self.ctl.zap.lists_dir.exists():
            return
        lm = self.lm()
        cur = self.cat
        cats = lm.categories()
        if cur != "all" and cur not in {c.key for c in cats}:
            cur = "all"
        self.cats.blockSignals(True)
        self.cats.clear()
        total = sum(lm.count(c.key) for c in cats if c.key != "ipset")
        it = QListWidgetItem(icons.icon("globe"), f"Все ресурсы  ·  {total}")
        it.setData(Qt.UserRole, "all")
        self.cats.addItem(it)
        for c in cats:
            it = QListWidgetItem(icons.icon("star" if c.custom else "list", theme.ACCENT if c.custom else theme.MUTED),
                                 f"{c.title}  ·  {lm.count(c.key)}")
            it.setData(Qt.UserRole, c.key)
            if c.custom:
                it.setToolTip(f"Своя категория · {CUSTOM_TARGETS[c.target][0]}")
            self.cats.addItem(it)
        self.cats.blockSignals(False)
        for i in range(self.cats.count()):
            if self.cats.item(i).data(Qt.UserRole) == cur:
                self.cats.setCurrentRow(i)
                break
        self._show(cur)

    def _cat_changed(self, cur, _prev):
        if cur:
            self._show(cur.data(Qt.UserRole))

    def _show(self, key: str):
        self.cat = key
        lm = self.lm()
        cats = lm.categories()
        titles = {c.key: c.title for c in cats}
        if key == "all":
            rows = [(e.value, e.source, e.category) for e in lm.all_entries()]
            self.desc.setText("Все домены и IP из списков обхода, исключений и ваших категорий.")
            self.model.set_rows(rows, True, titles)
        else:
            c = lm.cat(key)
            self.desc.setText(c.description)
            self.model.set_rows([(e.value, e.source, e.category) for e in lm.entries(key)], False, titles)
        editable = key == "all" or lm.cat(key).editable
        self.add_b.setVisible(editable)
        self.rm_b.setVisible(editable)
        custom = key != "all" and lm.cat(key).custom
        self.open_file.setVisible(not custom)
        self.open_file.setText("Папка" if key == "all" else "Файл")
        self.open_file.setToolTip("Открыть папку со списками zapret" if key == "all" else "Открыть файл списка")
        self.del_cat.setEnabled(custom)
        self.ipset_box.setVisible(key == "ipset")
        self.check_box.setVisible(key == "all")
        if key == "ipset":
            self.ipset_mode.set_value(self.ctl.zap.ipset_mode())
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in range(1, self.model.columnCount()):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)

    def _editable_cats(self):
        return [c for c in self.lm().categories() if c.editable]

    # ------------------------------------------------------------ действия
    def _changed(self):
        self.reload()
        self._restart_timer.start()

    def _add(self):
        cur = self.cat if self.cat != "all" else "general"
        d = AddDialog(self, self._editable_cats(), cur)
        if not d.exec():
            return
        vals = d.values()
        if not vals:
            return
        key = d.key()
        lm = self.lm()
        added, errors = lm.add(key, vals)
        title = lm.cat(key).title
        if added:
            log.ok(f"Добавлено в «{title}»: {', '.join(added[:5])}"
                   + (f" и ещё {len(added) - 5}" if len(added) > 5 else ""))
            self._changed()
        elif not errors:
            log.info("Эти ресурсы уже есть в списке")
        if errors:
            QMessageBox.warning(self, "Не всё добавлено", "\n".join(errors[:10]))

    def _remove(self):
        rows = sorted({self.proxy.mapToSource(i).row() for i in self.table.selectionModel().selectedRows()})
        if not rows:
            QMessageBox.information(self, "Убрать", "Выделите строки, которые нужно убрать.")
            return
        picked = [self.model.rows[r] for r in rows]
        builtin = sum(1 for _, src, _c in picked if src == "builtin")
        msg = f"Убрать записей: {len(picked)}?"
        if builtin:
            msg += (f"\n\n{builtin} из них встроенные — приложение запомнит удаление и будет применять его "
                    "после каждого обновления zapret.")
        if QMessageBox.question(self, "Убрать", msg) != QMessageBox.Yes:
            return
        by_cat: dict[str, list[str]] = {}
        for v, _src, cat in picked:
            by_cat.setdefault(cat, []).append(v)
        lm = self.lm()
        for cat, vals in by_cat.items():
            lm.remove(cat, vals)
        log.ok(f"Убрано записей: {len(picked)}")
        self._changed()

    def _create_cat(self):
        d = NewCategoryDialog(self)
        if not d.exec():
            return
        try:
            key = self.lm().create_category(d.name.text(), d.kind.currentData())
        except ValueError as e:
            QMessageBox.warning(self, "Категория", str(e))
            return
        log.ok(f"Создана категория «{d.name.text().strip()}»")
        self.cat = key
        self.reload()

    def _delete_cat(self):
        if self.cat == "all":
            return
        lm = self.lm()
        c = lm.cat(self.cat)
        if not c.custom:
            return
        n = lm.count(c.key)
        if QMessageBox.question(self, "Удалить категорию",
                                f"Удалить категорию «{c.title}»" + (f" и {n} её записей?" if n else "?")) \
                != QMessageBox.Yes:
            return
        lm.delete_category(c.key)
        log.ok(f"Категория «{c.title}» удалена")
        self.cat = "all"
        self._changed()

    def _check(self):
        d = self.check_edit.text().strip()
        if not d:
            return
        self.check_btn.set_loading(True, "Проверка…")
        self.check_res.hide()

        def done(hits):
            self.check_btn.set_loading(False)
            self.check_res.show()
            if not hits:
                self.check_res.setText(f"«{d}» нет в списках доменов. Обход может применяться к нему "
                                       "только через IPSet (по IP-адресу).")
                return
            incl = [(t, v) for t, v, ex in hits if not ex]
            excl = [(t, v) for t, v, ex in hits if ex]
            parts = []
            if incl:
                parts.append("✓ Обход применяется: " + ", ".join(f"{t} ({v})" for t, v in incl))
            if excl:
                parts.append("✕ Но есть в исключениях — обход его не трогает: "
                             + ", ".join(f"{t} ({v})" for t, v in excl))
            self.check_res.setText("\n".join(parts))

        run_bg(lambda: self.lm().lookup_ex(d), done,
               lambda e: (self.check_btn.set_loading(False), self.ctl.push_error(e)))

    def _open_file(self):
        if self.cat == "all":
            winutil.open_path(str(self.ctl.zap.lists_dir))
            return
        c = self.lm().cat(self.cat)
        if not c.custom:
            winutil.open_path(str(self.ctl.zap.lists_dir / (c.user or c.builtin)))

    def _set_ipset(self, mode: str):
        try:
            self.ctl.zap.set_ipset_mode(mode)
            log.ok(f"Режим IPSet: {mode}")
            self._changed()
        except Exception as e:  # noqa: BLE001
            self.ctl.push_error(f"IPSet: {e}")
            self.ipset_mode.set_value(self.ctl.zap.ipset_mode())

    def _update_ipset(self):
        self.ipset_upd.set_loading(True, "Загрузка…")

        def fin():
            self.ipset_upd.set_loading(False)
            self._changed()
        self.ctl.update_ipset(fin)
