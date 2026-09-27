"""Стратегии: список с оценками, редактор и создатель стратегий."""
from __future__ import annotations

import time

from PySide6.QtCore import QRegularExpression, Qt, QUrl
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QSyntaxHighlighter, QTextCharFormat,
                           QTextCursor)
from PySide6.QtWidgets import (QDialog, QInputDialog, QListWidget, QListWidgetItem,
                               QMenu, QMessageBox, QPlainTextEdit, QSplitter, QVBoxLayout, QWidget,
                               QLineEdit)

from ...core import log
from ...core.paths import USER_STRATEGIES_DIR
from ...core.strategy import (build_bat, editor_to_template, safe_filename, template_to_editor,
                              tokenize, validate_template)
from .. import icons, theme
from ..widgets import (ROLE_NAME, ROLE_SCORE, ROLE_STALE, ROLE_USER, StrategyDelegate, button, card,
                       flow, hbox, label)

GUIDES = [
    ("Как подбирать стратегии", "https://github.com/Flowseal/zapret-discord-youtube#readme",
     "README zapret-discord-youtube: что такое стратегии ALT/FAKE и почему нужно пробовать разные"),
    ("Гайд: своя стратегия", "https://github.com/Flowseal/zapret-discord-youtube/discussions/15541",
     "Как собрать свою стратегию из частей работающих (обсуждение в репозитории)"),
    ("Все параметры winws", "https://github.com/bol-van/zapret",
     "Оригинальный zapret от bol-van: подробная документация по всем параметрам --dpi-desync и др."),
]

L = '"%LISTS%'
GEN = (f'--hostlist={L}list-general.txt" --hostlist={L}list-general-user.txt" '
       f'--hostlist-exclude={L}list-exclude.txt" --hostlist-exclude={L}list-exclude-user.txt" '
       f'--ipset-exclude={L}ipset-exclude.txt" --ipset-exclude={L}ipset-exclude-user.txt"')

SNIPPETS = [
    ("YouTube QUIC (UDP 443)",
     f'--filter-udp=443 {GEN} --dpi-desync=fake --dpi-desync-repeats=6 '
     '--dpi-desync-fake-quic="%BIN%quic_initial_www_google_com.bin"'),
    ("Discord голос (UDP)",
     '--filter-udp=19294-19344,50000-50100 --filter-l7=discord,stun --dpi-desync=fake '
     '--dpi-desync-fake-discord="%BIN%ACTIVE_DISCORD_UDP.bin" '
     '--dpi-desync-fake-stun="%BIN%ACTIVE_DISCORD_UDP.bin" --dpi-desync-repeats=6'),
    ("Discord media (TCP)",
     '--filter-tcp=2053,2083,2087,2096,8443 --hostlist-domains=discord.media --dpi-desync=multisplit '
     '--dpi-desync-split-seqovl=681 --dpi-desync-split-pos=1 '
     '--dpi-desync-split-seqovl-pattern="%BIN%tls_clienthello_www_google_com.bin"'),
    ("Google/YouTube TLS (TCP 443)",
     f'--filter-tcp=443 --hostlist={L}list-google.txt" --ip-id=zero --dpi-desync=multisplit '
     '--dpi-desync-split-seqovl=681 --dpi-desync-split-pos=1 '
     '--dpi-desync-split-seqovl-pattern="%BIN%tls_clienthello_www_google_com.bin"'),
    ("Сайты: multisplit (TCP 80,443)",
     f'--filter-tcp=80,443 {GEN} --dpi-desync=multisplit --dpi-desync-split-seqovl=568 '
     '--dpi-desync-split-pos=1 --dpi-desync-split-seqovl-pattern="%BIN%tls_clienthello_4pda_to.bin"'),
    ("Сайты: fake + multidisorder",
     f'--filter-tcp=80,443 {GEN} --dpi-desync=fake,multidisorder --dpi-desync-split-pos=1,midsld '
     '--dpi-desync-repeats=11 --dpi-desync-fooling=badseq '
     '--dpi-desync-fake-tls="%BIN%tls_clienthello_www_google_com.bin"'),
    ("IPSet: TCP 80,443,8443",
     f'--filter-tcp=80,443,8443 --ipset={L}ipset-all.txt" --hostlist-exclude={L}list-exclude.txt" '
     f'--hostlist-exclude={L}list-exclude-user.txt" --ipset-exclude={L}ipset-exclude.txt" '
     f'--ipset-exclude={L}ipset-exclude-user.txt" --dpi-desync=multisplit --dpi-desync-split-seqovl=568 '
     '--dpi-desync-split-pos=1 --dpi-desync-split-seqovl-pattern="%BIN%tls_clienthello_4pda_to.bin"'),
    ("Game Filter (TCP)",
     f'--filter-tcp=%GameFilterTCP% --ipset={L}ipset-all.txt" --ipset-exclude={L}ipset-exclude.txt" '
     f'--ipset-exclude={L}ipset-exclude-user.txt" --dpi-desync=multisplit --dpi-desync-any-protocol=1 '
     '--dpi-desync-cutoff=n3 --dpi-desync-split-seqovl=568 --dpi-desync-split-pos=1 '
     '--dpi-desync-split-seqovl-pattern="%BIN%tls_clienthello_4pda_to.bin"'),
    ("Game Filter (UDP)",
     f'--filter-udp=%GameFilterUDP% --ipset={L}ipset-all.txt" --ipset-exclude={L}ipset-exclude.txt" '
     f'--ipset-exclude={L}ipset-exclude-user.txt" --dpi-desync=fake --dpi-desync-repeats=12 '
     '--dpi-desync-any-protocol=1 --dpi-desync-fake-unknown-udp="%BIN%ACTIVE_GAME_UDP.bin" '
     '--dpi-desync-cutoff=n2'),
]

PARAMS = [
    ("--wf-tcp=", "Порты TCP, которые перехватывает WinDivert (общий параметр, в начале)"),
    ("--wf-udp=", "Порты UDP, которые перехватывает WinDivert"),
    ("--filter-tcp=", "Порты TCP, к которым относится секция"),
    ("--filter-udp=", "Порты UDP, к которым относится секция"),
    ("--filter-l7=", "Протоколы уровня приложения: discord, stun, quic, tls, http"),
    ("--hostlist=", "Файл со списком доменов"),
    ("--hostlist-exclude=", "Файл доменов-исключений"),
    ("--hostlist-domains=", "Домены прямо в строке, через запятую"),
    ("--ipset=", "Файл со списком IP/подсетей"),
    ("--ipset-exclude=", "Файл IP-исключений"),
    ("--dpi-desync=", "Метод: fake, multisplit, multidisorder, fakedsplit, fakeddisorder, syndata"),
    ("--dpi-desync-repeats=", "Сколько раз повторять фейковый пакет"),
    ("--dpi-desync-split-pos=", "Где резать пакет: 1, 2, midsld, sniext+1, host+1"),
    ("--dpi-desync-split-seqovl=", "Перекрытие sequence при разрезании (байт)"),
    ("--dpi-desync-split-seqovl-pattern=", "Файл-шаблон для перекрытия"),
    ("--dpi-desync-fooling=", "Как «испортить» фейк: md5sig, badseq, badsum, ts, datanoack"),
    ("--dpi-desync-fake-tls=", "Файл фейкового TLS ClientHello"),
    ("--dpi-desync-fake-quic=", "Файл фейкового QUIC Initial"),
    ("--dpi-desync-fake-discord=", "Фейк для Discord UDP"),
    ("--dpi-desync-fake-stun=", "Фейк для STUN"),
    ("--dpi-desync-fake-unknown-udp=", "Фейк для прочего UDP"),
    ("--dpi-desync-autottl=", "Автоматический TTL фейков (например 2)"),
    ("--dpi-desync-ttl=", "Фиксированный TTL фейков"),
    ("--dpi-desync-cutoff=", "Ограничить обработку первыми пакетами: n2, n3, d4"),
    ("--dpi-desync-any-protocol=1", "Обрабатывать любой протокол, не только TLS/HTTP"),
    ("--ip-id=zero", "Обнулять IP ID"),
    ("--new", "Начать новую секцию"),
]


class ArgsHighlighter(QSyntaxHighlighter):
    def __init__(self, doc):
        super().__init__(doc)

        def fmt(color, bold=False, bg=None):
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            if bold:
                f.setFontWeight(QFont.Bold)
            if bg:
                f.setBackground(QColor(bg))
            return f

        self.rules = [
            (QRegularExpression(r"--[a-z0-9\-]+"), fmt(theme.ACCENT)),
            (QRegularExpression(r'"[^"]*"'), fmt("#9CD6A8")),
            (QRegularExpression(r"%[A-Za-z~0-9]+%"), fmt(theme.WARN, True)),
        ]
        self.new_fmt = fmt(theme.OK, True, "#16302A")
        self.comment_fmt = fmt(theme.FAINT)

    def highlightBlock(self, text):
        s = text.strip()
        if s == "--new":
            self.setFormat(0, len(text), self.new_fmt)
            return
        if s.startswith("#"):
            self.setFormat(0, len(text), self.comment_fmt)
            return
        for rx, f in self.rules:
            it = rx.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), f)


class StrategiesPage(QWidget):
    def __init__(self, ctl, go_to, run_test):
        super().__init__()
        self.setObjectName("page")
        self.ctl = ctl
        self.go_to = go_to
        self.run_test = run_test
        self.current = None          # имя открытой стратегии
        self.current_user = False
        self.dirty = False

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(12)
        root.addWidget(label("Стратегии", "h1"))
        root.addWidget(label("Стратегия — набор приёмов, которыми zapret обманывает блокировку провайдера. "
                             "У разных провайдеров работают разные стратегии. Встроенные обновляются вместе "
                             "с zapret, изменённая встроенная сохраняется как ваша копия.", "muted", wrap=True))
        links = []
        for text, url, tip in GUIDES:
            b = button(text, "link", icons.icon("external", theme.ACCENT, size=14), tip)
            b.clicked.connect(lambda _=False, u=url: QDesktopServices.openUrl(QUrl(u)))
            links.append(b)
        root.addWidget(flow(icons_label("book"), *links, spacing=4))

        self.invite = card("invite")
        il = hbox(margins=(14, 10, 10, 10), spacing=10)
        self.invite.setLayout(il)
        il.addWidget(icons_label("gauge"))
        il.addWidget(label("Стратегии ещё не проверялись. Автотест проверит все и покажет, какие работают "
                           "у вашего провайдера.", wrap=True), 1)
        inv_b = button("Запустить автотест", "primary", icons.icon("play", "#06111D"))
        inv_b.clicked.connect(lambda: self.run_test(None))
        il.addWidget(inv_b)
        root.addWidget(self.invite)

        split = QSplitter(Qt.Horizontal)
        self.split = split
        split.setChildrenCollapsible(False)

        # --- список
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 8, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск стратегии")
        self.search.textChanged.connect(self._filter)
        self.list = QListWidget()
        self.list.setItemDelegate(StrategyDelegate(self.list))
        self.list.currentItemChanged.connect(self._select)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        new_b = button("Новая", icon=icons.icon("plus"))
        new_b.clicked.connect(self._new)
        dup_b = button("Копия", icon=icons.icon("copy"))
        dup_b.clicked.connect(self._duplicate)
        self.del_b = button("", icon=icons.icon("trash", theme.ERR), tip="Удалить свою стратегию")
        self.del_b.clicked.connect(self._delete)
        ll.addWidget(self.search)
        ll.addWidget(self.list, 1)
        ll.addLayout(hbox(new_b, dup_b, None, self.del_b, spacing=6))
        left.setMinimumWidth(240)

        # --- редактор
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 0, 0, 0)
        rl.setSpacing(8)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Название стратегии")
        self.name_edit.textEdited.connect(self._mark_dirty)
        self.kind = label("", "faint")
        self.editor = QPlainTextEdit()
        self.editor.setObjectName("code")
        self.editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.editor.textChanged.connect(self._on_text)
        self.hl = ArgsHighlighter(self.editor.document())
        self.problems = label("", "faint", wrap=True)

        ins_snip = button("Секция-шаблон", icon=icons.icon("plus"))
        ins_snip.clicked.connect(self._snippet_menu)
        ins_param = button("Параметр", icon=icons.icon("sliders"))
        ins_param.clicked.connect(self._param_menu)
        preview = button("Команда", tip="Показать итоговую команду winws.exe")
        preview.clicked.connect(self._preview)

        self.save_b = button("Сохранить", "primary", icons.icon("save", "#06111D"))
        self.save_b.clicked.connect(self._save)
        test_b = button("Протестировать", icon=icons.icon("gauge"))
        test_b.clicked.connect(self._test)
        run_b = button("Запустить", icon=icons.icon("play", theme.OK))
        run_b.clicked.connect(self._run)

        rl.addLayout(hbox(self.name_edit, self.kind, spacing=10))
        rl.addWidget(flow(ins_snip, ins_param, preview, spacing=6))
        rl.addWidget(label("Один параметр в строке. Строка «--new» начинает новую секцию. "
                           "Переменные %BIN%, %LISTS%, %GameFilterTCP%, %GameFilterUDP% подставляются "
                           "автоматически.", "faint", wrap=True))
        rl.addWidget(self.editor, 1)
        rl.addWidget(self.problems)
        rl.addWidget(flow(run_b, test_b, self.save_b, spacing=6, align_right=True))
        self.editor.setPlaceholderText("Пустая стратегия.\n\nДобавьте секции кнопкой «Секция-шаблон» или "
                                       "впишите параметры winws.exe — по одному в строке.\n"
                                       "Строка --new начинает новую секцию.")

        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([290, 640])
        split.setMinimumHeight(380)
        root.addWidget(split, 1)
        self.is_new = False

        ctl.strategies_changed.connect(self.reload)
        ctl.installed_changed.connect(lambda _: self.reload())
        self.reload()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        o = Qt.Vertical if self.width() < 760 else Qt.Horizontal
        if self.split.orientation() != o:
            self.split.setOrientation(o)
            self.split.setSizes([220, 480] if o == Qt.Vertical else [290, 640])
            self.split.setMinimumHeight(620 if o == Qt.Vertical else 380)

    # ------------------------------------------------------------ список
    def reload(self):
        keep = self.current
        self.list.blockSignals(True)
        self.list.clear()
        for it in self.ctl.sorted_strategies():
            item = QListWidgetItem(it["name"])
            item.setData(ROLE_NAME, it["name"] + ("  ⚠" if it["error"] else ""))
            item.setData(ROLE_SCORE, it["score"])
            item.setData(ROLE_USER, it["user"])
            item.setData(ROLE_STALE, it["stale"])
            tip = "Не тестировалась" if it["score"] is None else (
                f"Оценка {round(it['score'] * 100)}% · тест {time.strftime('%d.%m %H:%M', time.localtime(it['tested']))}"
                + (" (на прошлой версии zapret)" if it["stale"] else ""))
            item.setToolTip(tip)
            self.list.addItem(item)
        self.list.blockSignals(False)
        self.invite.setVisible(not self.ctl.results.has_any([self.list.item(i).text()
                                                             for i in range(self.list.count())])
                               and not self.ctl.testing)
        self._filter(self.search.text())
        if keep:
            self._select_name(keep, load=False)
        elif self.list.count():
            self.list.setCurrentRow(0)

    def _select_name(self, name: str, load: bool = True):
        for i in range(self.list.count()):
            if self.list.item(i).text() == name:
                if load:
                    self.list.setCurrentRow(i)
                else:
                    self.list.blockSignals(True)
                    self.list.setCurrentRow(i)
                    self.list.blockSignals(False)
                return

    def _filter(self, text: str):
        t = text.lower().strip()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(t) and t not in it.text().lower())

    def _select(self, cur, _prev):
        if cur is None:
            return
        if self.dirty and (self.current or self.is_new) and cur.text() != self.current:
            r = QMessageBox.question(self, "Несохранённые изменения",
                                     f"Сохранить изменения в «{self.current or self.name_edit.text()}»?",
                                     QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
            if r == QMessageBox.Cancel:
                self._select_name(self.current, load=False)
                return
            if r == QMessageBox.Save and not self._save():
                self._select_name(self.current, load=False)
                return
        self._open(cur.text())

    def _open(self, name: str):
        s = self.ctl.zap.strategy(name)
        if not s:
            return
        self.current, self.current_user = s.name, s.user
        self.is_new = False
        self.name_edit.setText(s.name)
        self.kind.setText("своя стратегия" if s.user else "встроенная")
        self.editor.blockSignals(True)
        self.editor.setPlainText(template_to_editor(s.template) if not s.error else f"# Ошибка: {s.error}")
        self.editor.blockSignals(False)
        self.del_b.setEnabled(s.user)
        self.dirty = False
        self._validate()

    def _menu(self, pos):
        it = self.list.itemAt(pos)
        if not it:
            return
        m = QMenu(self)
        m.addAction("Запустить", self._run)
        m.addAction("Протестировать", self._test)
        m.addAction("Сделать копию", self._duplicate)
        if it.data(ROLE_USER):
            m.addAction("Удалить", self._delete)
        m.exec(self.list.mapToGlobal(pos))

    # ------------------------------------------------------------ редактор
    def _mark_dirty(self, *_):
        self.dirty = True

    def _on_text(self):
        self.dirty = True
        self._validate()

    def _validate(self) -> list[str]:
        if not self.editor.toPlainText().strip():
            self.problems.setStyleSheet(f"color:{theme.FAINT}")
            self.problems.setText("Стратегия пустая — добавьте хотя бы одну секцию.")
            return ["стратегия пустая"]
        tpl = editor_to_template(self.editor.toPlainText())
        z = self.ctl.zap
        probs = validate_template(tpl, z.bin_dir, z.lists_dir)
        if probs:
            self.problems.setStyleSheet(f"color:{theme.WARN}")
            self.problems.setText("• " + "\n• ".join(probs[:6]))
        else:
            n = len([x for x in self.editor.toPlainText().splitlines() if x.strip() == "--new"]) + 1
            self.problems.setStyleSheet(f"color:{theme.FAINT}")
            self.problems.setText(f"Секций: {n}. Ошибок не найдено.")
        return probs

    def _insert(self, text: str, as_section: bool):
        if as_section:
            body = "\n".join(tokenize(text))
            doc = self.editor.toPlainText().rstrip()
            self.editor.setPlainText((doc + "\n--new\n" + body) if doc else body)
            self.editor.moveCursor(QTextCursor.End)
        else:
            cur = self.editor.textCursor()
            cur.movePosition(QTextCursor.EndOfBlock)
            cur.insertText("\n" + text)
            self.editor.setTextCursor(cur)
        self.editor.setFocus()

    def _snippet_menu(self):
        m = QMenu(self)
        for title, body in SNIPPETS:
            a = m.addAction(title)
            a.triggered.connect(lambda _=False, b=body: self._insert(b, True))
        m.exec(self.sender().mapToGlobal(self.sender().rect().bottomLeft()))

    def _param_menu(self):
        m = QMenu(self)
        for p, desc in PARAMS:
            a = m.addAction(f"{p}    —  {desc}")
            a.triggered.connect(lambda _=False, x=p: self._insert(x, False))
        m.exec(self.sender().mapToGlobal(self.sender().rect().bottomLeft()))

    def _preview(self):
        tpl = editor_to_template(self.editor.toPlainText())
        from ...core.strategy import resolve
        cmd = f'"{self.ctl.zap.winws}" ' + resolve(tpl, self.ctl.zap.context())
        d = QDialog(self)
        d.setWindowTitle("Итоговая команда")
        d.resize(760, 360)
        t = QPlainTextEdit(cmd)
        t.setObjectName("code")
        t.setReadOnly(True)
        lay = QVBoxLayout(d)
        lay.addWidget(t)
        d.exec()

    # ------------------------------------------------------------ сохранение
    def _save(self) -> bool:
        tpl = editor_to_template(self.editor.toPlainText())
        if not tpl:
            QMessageBox.warning(self, "Стратегия", "Стратегия пустая.")
            return False
        probs = self._validate()
        if probs and QMessageBox.question(self, "Есть замечания",
                                          "Найдены проблемы:\n\n" + "\n".join(probs[:8]) +
                                          "\n\nВсё равно сохранить?") != QMessageBox.Yes:
            return False
        name = safe_filename(self.name_edit.text())
        builtin_names = {s.name.lower() for s in self.ctl.zap.strategies() if not s.user}
        if self.is_new and self.ctl.zap.strategy(name) and name.lower() not in builtin_names:
            QMessageBox.warning(self, "Стратегия", "Стратегия с таким именем уже есть — выберите другое.")
            return False
        if not self.current_user or name.lower() in builtin_names:
            base = name if name.lower() not in builtin_names else f"{name} (моя)"
            name, ok = QInputDialog.getText(self, "Сохранить как свою",
                                            "Встроенные стратегии перезаписываются при обновлении zapret,\n"
                                            "поэтому изменения сохраняются в вашу копию. Название:",
                                            text=base)
            name = safe_filename(name)
            if not ok or not name:
                return False
            if name.lower() in builtin_names:
                QMessageBox.warning(self, "Стратегия", "Это имя занято встроенной стратегией.")
                return False
        old = self.current if self.current_user else None
        data = build_bat(tpl)
        USER_STRATEGIES_DIR.mkdir(parents=True, exist_ok=True)
        (USER_STRATEGIES_DIR / f"{name}.bat").write_bytes(data.encode("utf-8"))
        (self.ctl.zap.root / f"{name}.bat").write_bytes(data.encode("utf-8"))
        if old and old != name:
            for p in (USER_STRATEGIES_DIR / f"{old}.bat", self.ctl.zap.root / f"{old}.bat"):
                p.unlink(missing_ok=True)
            self.ctl.results.rename(old, name)
        elif old == name:
            self.ctl.results.remove(name)  # стратегия изменилась — старая оценка неактуальна
        self.current, self.current_user, self.dirty, self.is_new = name, True, False, False
        log.ok(f"Стратегия «{name}» сохранена")
        self.ctl.strategies_changed.emit()
        self._select_name(name)
        st = self.ctl.status
        if st.running and st.strategy == name:
            self.ctl.restart_if_running("стратегия изменена")
        return True

    def _new(self):
        """Новая стратегия — пустая; файл появится при первом сохранении."""
        if self.dirty and self.current and QMessageBox.question(
                self, "Несохранённые изменения", f"Отбросить изменения в «{self.current}»?") != QMessageBox.Yes:
            return
        base, n = "Новая стратегия", 1
        name = base
        while self.ctl.zap.strategy(name):
            n += 1
            name = f"{base} {n}"
        self.list.blockSignals(True)
        self.list.clearSelection()
        self.list.setCurrentItem(None)
        self.list.blockSignals(False)
        self.current, self.current_user, self.is_new = None, True, True
        self.name_edit.setText(name)
        self.kind.setText("новая, не сохранена")
        self.editor.blockSignals(True)
        self.editor.setPlainText("")
        self.editor.blockSignals(False)
        self.del_b.setEnabled(False)
        self.dirty = False
        self._validate()
        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def _duplicate(self):
        if not self.current:
            return
        s = self.ctl.zap.strategy(self.current)
        name, ok = QInputDialog.getText(self, "Копия стратегии", "Название копии:", text=f"{s.name} (моя)")
        name = safe_filename(name)
        if ok and name:
            if self.ctl.zap.strategy(name):
                QMessageBox.warning(self, "Стратегия", "Стратегия с таким именем уже есть.")
                return
            self._create(name, editor_to_template(self.editor.toPlainText()) or s.template)

    def _create(self, name: str, tpl: str):
        data = build_bat(tpl).encode("utf-8")
        USER_STRATEGIES_DIR.mkdir(parents=True, exist_ok=True)
        (USER_STRATEGIES_DIR / f"{name}.bat").write_bytes(data)
        (self.ctl.zap.root / f"{name}.bat").write_bytes(data)
        self.dirty = False
        self.current = name
        self.ctl.strategies_changed.emit()
        self._select_name(name)
        self._open(name)
        log.ok(f"Создана стратегия «{name}»")

    def _delete(self):
        if not self.current or not self.current_user:
            return
        if QMessageBox.question(self, "Удалить", f"Удалить стратегию «{self.current}»?") != QMessageBox.Yes:
            return
        for p in (USER_STRATEGIES_DIR / f"{self.current}.bat", self.ctl.zap.root / f"{self.current}.bat"):
            p.unlink(missing_ok=True)
        self.ctl.results.remove(self.current)
        log.info(f"Стратегия «{self.current}» удалена")
        self.current, self.dirty = None, False
        self.ctl.strategies_changed.emit()

    def _test(self):
        if self.current:
            if self.dirty and not self._save():
                return
            self.run_test([self.current])

    def _run(self):
        if self.current:
            if self.dirty and not self._save():
                return
            self.ctl.connect(self.current)
            self.go_to("home")


def icons_label(name: str, color: str = theme.ACCENT):
    from PySide6.QtWidgets import QLabel
    l = QLabel()
    l.setPixmap(icons.pixmap(name, color, 18))
    l.setFixedWidth(24)
    l.setAlignment(Qt.AlignCenter)
    return l
