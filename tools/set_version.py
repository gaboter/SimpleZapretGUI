"""Номер версии хранится в одном месте — APP_VERSION в simplezapretgui/core/paths.py.

    python tools/set_version.py 1.2.3   — записать новую версию в код и в version_info.txt
    python tools/set_version.py         — только синхронизировать version_info.txt с кодом

Печатает итоговый номер версии (им пользуются build.bat, installer.bat и GitHub Actions).
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATHS = ROOT / "simplezapretgui" / "core" / "paths.py"
VINFO = ROOT / "version_info.txt"


def read(p: Path) -> str:
    with open(p, encoding="utf-8", newline="") as f:      # сохраняем переводы строк как есть
        return f.read()


def write(p: Path, text: str) -> None:
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def main() -> int:
    src = read(PATHS)
    if len(sys.argv) > 1:
        v = sys.argv[1].strip().lstrip("vV")
        if not re.fullmatch(r"\d+\.\d+\.\d+", v):
            print("Версия должна быть вида 1.2.3", file=sys.stderr)
            return 1
        new = re.sub(r'APP_VERSION = "[^"]*"', f'APP_VERSION = "{v}"', src, count=1)
        if new != src:
            write(PATHS, new)
    else:
        m = re.search(r'APP_VERSION = "([^"]+)"', src)
        if not m:
            print("APP_VERSION не найден в paths.py", file=sys.stderr)
            return 1
        v = m.group(1)
    a, b, c = (int(x) for x in v.split("."))
    t = read(VINFO)
    t2 = re.sub(r"filevers=\([^)]*\)", f"filevers=({a}, {b}, {c}, 0)", t)
    t2 = re.sub(r"prodvers=\([^)]*\)", f"prodvers=({a}, {b}, {c}, 0)", t2)
    t2 = re.sub(r"StringStruct\('FileVersion', '[^']*'\)", f"StringStruct('FileVersion', '{v}')", t2)
    t2 = re.sub(r"StringStruct\('ProductVersion', '[^']*'\)", f"StringStruct('ProductVersion', '{v}')", t2)
    if t2 != t:
        write(VINFO, t2)
    print(v)
    return 0


if __name__ == "__main__":
    sys.exit(main())
