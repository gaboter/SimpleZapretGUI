"""Разбор и генерация батников стратегий (general*.bat).

Приложение не запускает батники напрямую: оно вытаскивает из них аргументы winws.exe,
подставляет переменные (%BIN%, %LISTS%, %GameFilterTCP% ...) и запускает winws.exe само.
Так не появляется окно консоли и не срабатывает встроенная проверка обновлений,
открывающая браузер.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

PLACEHOLDERS = ("%BIN%", "%LISTS%", "%GameFilterTCP%", "%GameFilterUDP%", "%GameFilter%", "%~dp0")


@dataclass
class Strategy:
    name: str                 # имя файла без .bat
    path: Path
    template: str             # аргументы winws с плейсхолдерами
    user: bool = False        # пользовательская (создана в редакторе)
    error: str = ""

    @property
    def display(self) -> str:
        return self.name


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1251", "cp866"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def extract_template(text: str) -> str:
    """Достаёт строку аргументов winws.exe с учётом переносов через ^."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    start = None
    for i, ln in enumerate(lines):
        if "winws.exe" in ln.lower() and not ln.strip().startswith(("::", "rem ", "REM ")):
            start = i
            break
    if start is None:
        raise ValueError("в файле не найден вызов winws.exe")
    chunks = []
    i = start
    while i < len(lines):
        ln = lines[i].rstrip()
        cont = ln.endswith("^")
        if cont:
            ln = ln[:-1]
        chunks.append(ln.strip())
        i += 1
        if not cont:
            break
    joined = " ".join(c for c in chunks if c)
    low = joined.lower()
    idx = low.find("winws.exe")
    rest = joined[idx + len("winws.exe"):]
    if rest.startswith('"'):
        rest = rest[1:]
    return rest.strip()


def load_strategy(path: Path, user: bool = False) -> Strategy:
    try:
        tpl = extract_template(read_text(path))
        return Strategy(path.stem, path, tpl, user)
    except Exception as e:  # noqa: BLE001
        return Strategy(path.stem, path, "", user, error=str(e))


# ---------------------------------------------------------------- сортировка
def natural_key(name: str):
    """Та же логика, что в service.bat: числа дополняются нулями; general — первым."""
    base = name.lower()
    padded = re.sub(r"\d+", lambda m: m.group(0).zfill(8), base)
    return (0 if base == "general" else 1, padded)


# ---------------------------------------------------------------- токены / секции
def tokenize(template: str) -> list[str]:
    """Разбивает по пробелам вне кавычек, кавычки сохраняются внутри токена."""
    tokens, cur, q = [], [], False
    for ch in template:
        if ch == '"':
            q = not q
            cur.append(ch)
        elif ch.isspace() and not q:
            if cur:
                tokens.append("".join(cur))
                cur = []
        else:
            cur.append(ch)
    if cur:
        tokens.append("".join(cur))
    return [t for t in tokens if t not in ("^", "^^")]


def split_sections(tokens: list[str]) -> list[list[str]]:
    sections: list[list[str]] = [[]]
    for t in tokens:
        if t == "--new":
            sections.append([])
        else:
            sections[-1].append(t)
    return [s for s in sections if s] or [[]]


def template_to_editor(template: str) -> str:
    """Человекочитаемый вид: по параметру в строке, секции разделены строкой --new."""
    secs = split_sections(tokenize(template))
    out = []
    for i, sec in enumerate(secs):
        if i:
            out.append("--new")
        out.extend(sec)
    return "\n".join(out)


def editor_to_template(text: str) -> str:
    tokens: list[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or s.startswith("::"):
            continue
        tokens.extend(tokenize(s))
    # убираем повторные/крайние --new
    clean: list[str] = []
    for t in tokens:
        if t == "--new" and (not clean or clean[-1] == "--new"):
            continue
        clean.append(t)
    while clean and clean[-1] == "--new":
        clean.pop()
    return " ".join(clean)


def validate_template(template: str, bin_dir: Path, lists_dir: Path) -> list[str]:
    problems = []
    if not template.strip():
        return ["стратегия пустая"]
    secs = split_sections(tokenize(template))
    for n, sec in enumerate(secs, 1):
        if not any(t.startswith(("--filter-", "--wf-")) for t in sec):
            problems.append(f"секция {n}: нет фильтра (--filter-tcp / --filter-udp)")
        if not any(t.startswith("--dpi-desync") for t in sec):
            problems.append(f"секция {n}: не задан метод обхода (--dpi-desync=...)")
    for m in re.finditer(r"%(BIN|LISTS)%([^\"\s]+)", template, flags=re.I):
        base = bin_dir if m.group(1).upper() == "BIN" else lists_dir
        fname = m.group(2)
        if fname.lower().endswith("-user.txt"):
            continue
        if not (base / fname).exists():
            problems.append(f"файл не найден: {m.group(1).lower()}\\{fname}")
    for m in re.finditer(r"%[^%\s\"]+%", template):
        if m.group(0).lower() not in [p.lower() for p in PLACEHOLDERS]:
            problems.append(f"неизвестная переменная {m.group(0)}")
    return problems


# ---------------------------------------------------------------- подстановка
@dataclass
class ResolveContext:
    root: Path
    game_tcp: str = "12"
    game_udp: str = "12"
    extra: dict = field(default_factory=dict)


def resolve(template: str, ctx: ResolveContext) -> str:
    root = str(ctx.root).rstrip("\\/")
    repl = {
        "%bin%": root + "\\bin\\",
        "%lists%": root + "\\lists\\",
        "%gamefiltertcp%": ctx.game_tcp,
        "%gamefilterudp%": ctx.game_udp,
        "%gamefilter%": ctx.game_tcp if ctx.game_tcp != "12" else ctx.game_udp,
        "%~dp0": root + "\\",
    }

    def sub(m: re.Match) -> str:
        return repl.get(m.group(0).lower(), m.group(0))

    out = re.sub(r"%~dp0|%[A-Za-z]+%", sub, template)
    out = out.replace("^!", "!").replace("%%", "%")
    return out


def normalize_cmd(cmd: str) -> list[str]:
    """Для сравнения командных строк: без кавычек, в нижнем регистре, без пути к exe."""
    toks = [t.replace('"', "").lower() for t in tokenize(cmd)]
    if toks and toks[0].endswith("winws.exe"):
        toks = toks[1:]
    return toks


# ---------------------------------------------------------------- запись батника
BAT_HEADER = """@echo off
chcp 65001 > nul
:: 65001 - UTF-8
:: Created with SimpleZapretGUI

cd /d "%~dp0"
call service.bat status_zapret
call service.bat check_updates
call service.bat load_game_filter
call service.bat load_user_lists
echo:

set "BIN=%~dp0bin\\"
set "LISTS=%~dp0lists\\"
cd /d %BIN%

"""


def build_bat(template: str) -> str:
    secs = split_sections(tokenize(template))
    lines = []
    for i, sec in enumerate(secs):
        body = " ".join(sec)
        if i == 0:
            body = 'start "zapret: %~n0" /min "%BIN%winws.exe" ' + body
        if i < len(secs) - 1:
            body += " --new ^"
        lines.append(body)
    return (BAT_HEADER + "\n".join(lines) + "\n").replace("\n", "\r\n")


def safe_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip().rstrip(".")
    return name or "custom"
