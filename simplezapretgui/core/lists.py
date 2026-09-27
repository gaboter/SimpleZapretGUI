"""Управление списками сайтов и IP.

Встроенные файлы (list-general.txt и др.) перезаписываются при обновлении zapret,
поэтому правки пользователя хранятся отдельно:
  * добавленные домены — в *-user.txt (их zapret читает сам) либо в overrides «added»;
  * удалённые встроенные — в overrides «removed» и повторно применяются после обновления.
"""
from __future__ import annotations

import ipaddress
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .paths import OVERRIDES_FILE, USER_DIR

CUSTOM_FILE = USER_DIR / "custom_lists.json"

PLACEHOLDER_ENTRIES = {"domain.example.abc", "203.0.113.113/32"}


@dataclass(frozen=True)
class ListCategory:
    key: str
    title: str
    description: str
    builtin: str                 # имя встроенного файла
    user: Optional[str]          # пользовательский файл (или None)
    kind: str = "domain"         # domain | ip
    editable: bool = True
    custom: bool = False         # категория, созданная пользователем
    target: str = ""             # для своей категории: general | exclude | ipexclude


CATEGORIES = [
    ListCategory("general", "Сайты для обхода",
                 "Домены, к которым применяется обход. Поддомены включаются автоматически.",
                 "list-general.txt", "list-general-user.txt"),
    ListCategory("google", "YouTube и Google",
                 "Домены Google/YouTube, для них в стратегиях отдельные правила.",
                 "list-google.txt", None),
    ListCategory("exclude", "Исключения",
                 "Сайты, которые обход никогда не трогает (банки, игры, Steam...).",
                 "list-exclude.txt", "list-exclude-user.txt"),
    ListCategory("ipexclude", "IP-исключения",
                 "IP-адреса и подсети, которые не обрабатываются.",
                 "ipset-exclude.txt", "ipset-exclude-user.txt", kind="ip"),
    ListCategory("ipset", "IPSet (все IP)",
                 "Большой список IP-подсетей для режима IPSet «loaded». Обновляется из репозитория.",
                 "ipset-all.txt", None, kind="ip", editable=False),
]
CAT_BY_KEY = {c.key: c for c in CATEGORIES}

# Свои категории хранятся в custom_lists.json, а их содержимое дописывается в
# пользовательский файл zapret выбранного типа — так оно работает с любой стратегией.
CUSTOM_TARGETS = {
    "general": ("Сайты для обхода", "domain"),
    "exclude": ("Исключения (сайты)", "domain"),
    "ipexclude": ("Исключения (IP)", "ip"),
}


@dataclass
class Entry:
    value: str
    source: str        # builtin | user | added
    category: str


def normalize(value: str, kind: str) -> str:
    v = value.strip()
    if kind == "domain":
        v = re.sub(r"^[a-z]+://", "", v, flags=re.I)
        v = v.split("/")[0].split("?")[0].split("#")[0]
        prefix = "^" if v.startswith("^") else ""
        v = v.lstrip("^")
        v = v.split(":")[0].strip(".").lower()
        try:
            v = v.encode("idna").decode("ascii")
        except Exception:
            pass
        if v.startswith("www.") and v.count(".") >= 2:
            v = v[4:]
        return prefix + v
    return v


def validate(value: str, kind: str) -> Optional[str]:
    if not value:
        return "пустое значение"
    if kind == "domain":
        v = value.lstrip("^")
        if not re.fullmatch(r"(?=.{1,253}$)([a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?\.)+[a-z0-9-]{2,63}", v):
            try:
                ipaddress.ip_address(v)
                return None
            except ValueError:
                return f"«{value}» не похоже на домен"
        return None
    try:
        ipaddress.ip_network(value, strict=False)
        return None
    except ValueError:
        return f"«{value}» не похоже на IP или подсеть"


def read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    out = []
    for ln in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        s = ln.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


class ListManager:
    def __init__(self, lists_dir: Path, overrides_file: Path = OVERRIDES_FILE,
                 custom_file: Path = CUSTOM_FILE):
        self.dir = lists_dir
        self.ofile = overrides_file
        self.cfile = custom_file
        self.overrides = self._load()
        self.custom: list[dict] = self._load_custom()

    # ------------------------------------------------------------ свои категории
    def _load_custom(self) -> list[dict]:
        try:
            data = json.loads(self.cfile.read_text(encoding="utf-8"))
            return [c for c in data if c.get("target") in CUSTOM_TARGETS and c.get("key")]
        except Exception:
            return []

    def _save_custom(self) -> None:
        self.cfile.parent.mkdir(parents=True, exist_ok=True)
        self.cfile.write_text(json.dumps(self.custom, ensure_ascii=False, indent=2), encoding="utf-8")

    def categories(self) -> list[ListCategory]:
        res = list(CATEGORIES)
        for c in self.custom:
            ttl, kind = CUSTOM_TARGETS[c["target"]]
            res.append(ListCategory(c["key"], c["title"], f"Своя категория · {ttl}. Записи работают с любой "
                                    "стратегией и сохраняются при обновлении zapret.", "", None, kind,
                                    custom=True, target=c["target"]))
        return res

    def cat(self, key: str) -> ListCategory:
        for c in self.categories():
            if c.key == key:
                return c
        raise KeyError(key)

    def _group(self, key: str) -> Optional[dict]:
        return next((c for c in self.custom if c["key"] == key), None)

    def group_items(self, target: str) -> list[str]:
        out: list[str] = []
        for c in self.custom:
            if c["target"] == target:
                out.extend(v for v in c.get("items", []) if v not in out)
        return out

    def create_category(self, title: str, target: str) -> str:
        title = title.strip()
        if not title:
            raise ValueError("укажите название категории")
        if target not in CUSTOM_TARGETS:
            raise ValueError("неизвестный тип категории")
        if any(c.title.lower() == title.lower() for c in self.categories()):
            raise ValueError("категория с таким названием уже есть")
        key = "c_" + uuid.uuid4().hex[:8]
        self.custom.append({"key": key, "title": title, "target": target, "items": []})
        self._save_custom()
        return key

    def delete_category(self, key: str) -> None:
        g = self._group(key)
        if not g:
            return
        old = set(self.group_items(g["target"]))
        self.custom.remove(g)
        self._save_custom()
        self._sync(g["target"], old)

    def _sync(self, target: str, old_union: set) -> None:
        """Переписать пользовательский файл: свои строки пользователя + все свои категории."""
        cat = CAT_BY_KEY[target]
        base = [x for x in read_lines(self.dir / cat.user)
                if x not in PLACEHOLDER_ENTRIES and x not in old_union]
        self._write_user(cat, base)

    def _load(self) -> dict:
        try:
            return json.loads(self.ofile.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save(self) -> None:
        self.ofile.parent.mkdir(parents=True, exist_ok=True)
        self.ofile.write_text(json.dumps(self.overrides, ensure_ascii=False, indent=2), encoding="utf-8")

    def _ov(self, key: str) -> dict:
        return self.overrides.setdefault(key, {"added": [], "removed": []})

    # ------------------------------------------------------------ чтение
    def entries(self, key: str) -> list[Entry]:
        cat = self.cat(key)
        if cat.custom:
            return [Entry(v, "user", key) for v in (self._group(key) or {}).get("items", [])]
        ov = self._ov(key)
        added = set(ov["added"])
        res = []
        for v in read_lines(self.dir / cat.builtin):
            if v in PLACEHOLDER_ENTRIES:
                continue
            res.append(Entry(v, "added" if v in added else "builtin", key))
        if cat.user:
            groups = set(self.group_items(key))
            for v in read_lines(self.dir / cat.user):
                if v in PLACEHOLDER_ENTRIES or v in groups:
                    continue
                res.append(Entry(v, "user", key))
        return res

    def all_entries(self) -> list[Entry]:
        res = []
        for c in self.categories():
            if c.key == "ipset":
                continue
            res.extend(self.entries(c.key))
        return res

    def count(self, key: str) -> int:
        cat = self.cat(key)
        if cat.custom:
            return len((self._group(key) or {}).get("items", []))
        n = sum(1 for v in read_lines(self.dir / cat.builtin) if v not in PLACEHOLDER_ENTRIES)
        if cat.user:
            groups = set(self.group_items(key))
            n += sum(1 for v in read_lines(self.dir / cat.user) if v not in PLACEHOLDER_ENTRIES
                     and v not in groups)
        return n

    def domains_for_test(self) -> list[str]:
        """Все сайты, к которым применяется обход (для автотеста «все ресурсы»)."""
        out = []
        for c in self.categories():
            if c.kind == "domain" and (c.key in ("general", "google") or (c.custom and c.target == "general")):
                out.extend(e.value for e in self.entries(c.key))
        return out

    # ------------------------------------------------------------ изменение
    def add(self, key: str, values: list[str]) -> tuple[list[str], list[str]]:
        """Возвращает (добавленные, ошибки)."""
        cat = self.cat(key)
        existing = {e.value for e in self.entries(key)}
        good, errors = [], []
        for raw in values:
            v = normalize(raw, cat.kind)
            if not v:
                continue
            err = validate(v, cat.kind)
            if err:
                errors.append(err)
                continue
            if v in existing or v in good:
                continue
            good.append(v)
        if not good:
            return [], errors
        if cat.custom:
            g = self._group(key)
            old = set(self.group_items(cat.target))
            g["items"] = g.get("items", []) + good
            self._save_custom()
            self._sync(cat.target, old)
            return good, errors
        ov = self._ov(key)
        builtin = read_lines(self.dir / cat.builtin)
        restore = [v for v in good if v in ov["removed"]]
        for v in restore:
            ov["removed"].remove(v)
        rest = [v for v in good if v not in restore]
        if cat.user:
            groups = set(self.group_items(key))
            cur = [x for x in read_lines(self.dir / cat.user) if x not in PLACEHOLDER_ENTRIES
                   and x not in groups]
            if rest:
                self._write_user(cat, cur + rest)
        else:
            ov["added"] = sorted(set(ov["added"]) | set(rest))
            builtin = builtin + rest
        if restore or not cat.user:
            self._write_builtin(cat, builtin + restore)
        self._save()
        return good, errors

    def remove(self, key: str, values: list[str]) -> None:
        cat = self.cat(key)
        vals = set(values)
        if cat.custom:
            g = self._group(key)
            old = set(self.group_items(cat.target))
            g["items"] = [v for v in g.get("items", []) if v not in vals]
            self._save_custom()
            self._sync(cat.target, old)
            return
        ov = self._ov(key)
        if cat.user:
            groups = set(self.group_items(key))
            cur = [x for x in read_lines(self.dir / cat.user) if x not in vals
                   and x not in groups and x not in PLACEHOLDER_ENTRIES]
            self._write_user(cat, cur)
        builtin = read_lines(self.dir / cat.builtin)
        hit = [v for v in builtin if v in vals]
        if hit:
            for v in hit:
                if v in ov["added"]:
                    ov["added"].remove(v)
                elif v not in ov["removed"]:
                    ov["removed"].append(v)
            self._write_builtin(cat, [v for v in builtin if v not in vals])
        self._save()

    def _write_user(self, cat: ListCategory, values: list[str]) -> None:
        f = self.dir / cat.user
        values = list(values) + [v for v in self.group_items(cat.key) if v not in values]
        if not values:  # «Never leave this file empty»
            content = ("# Never leave this file empty\r\ndomain.example.abc\r\n" if cat.kind == "domain"
                       else "203.0.113.113/32\r\n")
            if cat.key == "exclude":
                content = "domain.example.abc\r\n"
        else:
            content = "\r\n".join(values) + "\r\n"
        f.write_text(content, encoding="utf-8", newline="")

    def _write_builtin(self, cat: ListCategory, values: list[str]) -> None:
        seen, out = set(), []
        for v in values:
            if v not in seen:
                seen.add(v)
                out.append(v)
        f = self.dir / cat.builtin
        f.write_text("\r\n".join(out) + ("\r\n" if out else ""), encoding="utf-8", newline="")

    def reapply_overrides(self) -> int:
        """После обновления zapret: снова убрать удалённые и добавить добавленные."""
        n = 0
        for c in CATEGORIES:
            ov = self.overrides.get(c.key)
            if not ov or not c.editable:
                continue
            lines = read_lines(self.dir / c.builtin)
            removed = set(ov.get("removed", []))
            new = [v for v in lines if v not in removed] + [v for v in ov.get("added", []) if v not in lines]
            if new != lines:
                self._write_builtin(c, new)
                n += 1
        return n

    # ------------------------------------------------------------ проверка домена
    def lookup(self, domain: str) -> list[tuple[str, str]]:
        """В каких списках домен (с учётом поддоменов). [(категория, совпавшая запись)]"""
        return [(t, v) for t, v, _ in self.lookup_ex(domain)]

    def lookup_ex(self, domain: str) -> list[tuple[str, str, bool]]:
        """[(категория, запись, это_исключение)]"""
        d = normalize(domain, "domain").lstrip("^")
        hits = []
        for c in self.categories():
            if c.kind != "domain":
                continue
            is_excl = c.key == "exclude" or (c.custom and c.target == "exclude")
            for e in self.entries(c.key):
                v = e.value
                exact = v.startswith("^")
                v = v.lstrip("^")
                if d == v or (not exact and d.endswith("." + v)):
                    hits.append((c.title, e.value, is_excl))
        return hits
