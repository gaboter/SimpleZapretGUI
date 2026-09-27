"""Диагностика (пункт 11 service.bat), обновление hosts и очистка кэша Discord."""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import winutil
from .paths import HOSTS_BACKUP, HOSTS_URL, IS_WINDOWS
from .zapret import CONFLICT_SERVICES, Zapret


@dataclass
class Check:
    title: str
    level: str                 # ok | warn | error
    detail: str = ""
    fix: Optional[str] = None  # ключ действия-исправления
    fix_label: str = ""


def run_diagnostics(zap: Zapret) -> list[Check]:
    res: list[Check] = []
    services = winutil.list_services()
    names = " ".join(f"{n} {d}" for n, d in services).lower()

    bfe = winutil.service_query("BFE")
    res.append(Check("Base Filtering Engine", "ok" if bfe.running else "error",
                     "" if bfe.running else "Служба BFE не запущена — без неё zapret не работает"))

    proxy_on = winutil.reg_read("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
                                "ProxyEnable")
    if proxy_on in ("1", "0x1"):
        srv = winutil.reg_read("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
                               "ProxyServer") or ""
        res.append(Check("Системный прокси", "warn", f"Включён прокси {srv}. Убедитесь, что он нужен."))
    else:
        res.append(Check("Системный прокси", "ok"))

    ts = winutil.tcp_timestamps() if IS_WINDOWS else True
    if ts is True:
        res.append(Check("TCP timestamps", "ok"))
    elif ts is False:
        res.append(Check("TCP timestamps", "warn", "Выключены — нужны части стратегий", "tcp_ts", "Включить"))
    else:
        res.append(Check("TCP timestamps", "warn", "Не удалось определить состояние", "tcp_ts", "Включить"))

    adg = winutil.process_running("AdguardSvc.exe")
    res.append(Check("Adguard", "error" if adg else "ok",
                     "Adguard может мешать Discord (issue #417)" if adg else ""))

    for title, pat, hint in (
        ("Killer Network", r"killer", "Службы Killer конфликтуют с zapret"),
        ("Intel Connectivity Network Service", r"intel.*connectivity.*network", "Конфликтует с zapret"),
        ("Check Point", r"tracsrvwrapper|epwd", "Check Point конфликтует с zapret — удалите его"),
        ("SmartByte", r"smartbyte", "Отключите SmartByte в services.msc"),
    ):
        found = bool(re.search(pat, names))
        res.append(Check(title, "error" if found else "ok", hint if found else ""))

    path = str(zap.root)
    cyr = bool(re.search("[\u0400-\u04FF]", path))
    res.append(Check("Путь без кириллицы", "warn" if cyr else "ok", path if cyr else ""))
    od = os.environ.get("OneDrive", "")
    in_od = bool(od) and path.lower().startswith(od.lower())
    res.append(Check("Не в папке OneDrive", "error" if in_od else "ok", path if in_od else ""))

    sys_ok = (zap.bin_dir / "WinDivert64.sys").exists()
    res.append(Check("WinDivert64.sys", "ok" if sys_ok else "error",
                     "" if sys_ok else "Файл отсутствует — добавьте папку zapret в исключения антивируса "
                                       "и переустановите zapret", None if sys_ok else "reinstall",
                     "Переустановить"))

    vpns = [d or n for n, d in services if "vpn" in (n + " " + d).lower()]
    res.append(Check("VPN", "warn" if vpns else "ok",
                     ("Найдены VPN-службы: " + ", ".join(vpns[:5]) + ". Отключите VPN при работе zapret")
                     if vpns else ""))

    doh = _doh_enabled()
    res.append(Check("Защищённый DNS", "ok" if doh else "warn",
                     "" if doh else "Настройте DoH в браузере или в параметрах Windows 11"))

    hosts = hosts_path()
    yt = False
    try:
        txt = hosts.read_text(encoding="utf-8", errors="ignore").lower()
        yt = "youtube.com" in txt or "youtu.be" in txt
    except Exception:
        pass
    res.append(Check("hosts без записей YouTube", "warn" if yt else "ok",
                     "В hosts есть записи youtube.com/youtu.be — они могут ломать YouTube" if yt else ""))

    winws = winutil.process_running("winws.exe")
    wd = winutil.service_query("WinDivert")
    if not winws and wd.exists and wd.state in ("RUNNING", "STOP_PENDING"):
        res.append(Check("Конфликт WinDivert", "warn",
                         "winws не запущен, но драйвер WinDivert активен", "windivert", "Удалить драйвер"))
    else:
        res.append(Check("Конфликт WinDivert", "ok"))

    existing = {n.lower() for n, _ in services}
    conf = [c for c in CONFLICT_SERVICES if c.lower() in existing]
    res.append(Check("Другие обходы", "error" if conf else "ok",
                     ("Найдены службы: " + ", ".join(conf)) if conf else "",
                     "conflicts" if conf else None, "Удалить"))
    return res


def _doh_enabled() -> bool:
    if not IS_WINDOWS:
        return True
    rc, out = winutil.powershell(
        "(Get-ChildItem -Recurse -Path 'HKLM:System\\CurrentControlSet\\Services\\Dnscache\\"
        "InterfaceSpecificParameters\\' -ErrorAction SilentlyContinue | Get-ItemProperty | "
        "Where-Object { $_.DohFlags -gt 0 } | Measure-Object).Count", timeout=20)
    try:
        return int(out.strip().splitlines()[-1]) > 0
    except Exception:
        return False


def apply_fix(key: str, zap: Zapret) -> str:
    if key == "tcp_ts":
        state = winutil.set_tcp_timestamps(True)
        if state is not True:
            raise RuntimeError("Windows не включила TCP timestamps"
                               + (" (они по-прежнему выключены)" if state is False else
                                  " — не удалось проверить результат"))
        return "TCP timestamps включены"
    if key == "windivert":
        for drv in ("WinDivert", "WinDivert14"):
            winutil.service_stop(drv)
            winutil.service_delete(drv)
        return "Драйвер WinDivert удалён"
    if key == "conflicts":
        existing = {n.lower(): n for n, _ in winutil.list_services()}
        removed = []
        for c in CONFLICT_SERVICES:
            if c.lower() in existing:
                winutil.service_stop(existing[c.lower()])
                winutil.service_delete(existing[c.lower()])
                removed.append(c)
        for drv in ("WinDivert", "WinDivert14"):
            winutil.service_stop(drv)
            winutil.service_delete(drv)
        return "Удалены: " + ", ".join(removed)
    return ""


# ---------------------------------------------------------------- Discord
def clear_discord_cache() -> list[str]:
    out = []
    appdata = Path(os.environ.get("APPDATA", ""))
    for proc, title, folder in (("Discord.exe", "Discord", "discord"),
                                ("DiscordPTB.exe", "Discord PTB", "discordptb"),
                                ("DiscordCanary.exe", "Discord Canary", "discordcanary"),
                                ("DiscordDevelopment.exe", "Discord Development", "discorddevelopment")):
        d = appdata / folder
        if not d.exists():
            continue
        if winutil.process_running(proc):
            winutil.kill_image(proc)
            out.append(f"{title} закрыт")
        for sub in ("Cache", "Code Cache", "GPUCache"):
            p = d / sub
            if p.exists():
                shutil.rmtree(p, ignore_errors=True)
                out.append(f"{title}: {sub} {'удалён' if not p.exists() else 'не удалось удалить'}")
    return out or ["Установки Discord не найдены"]


# ---------------------------------------------------------------- hosts
BEGIN = "# >>> SimpleZapretGUI (zapret-discord-youtube) >>>"
END = "# <<< SimpleZapretGUI <<<"


def hosts_path() -> Path:
    return Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "drivers" / "etc" / "hosts"


def fetch_hosts_block() -> list[str]:
    from .updater import http_get
    import time
    data = http_get(f"{HOSTS_URL}?t={int(time.time())}", timeout=10).decode("utf-8", errors="ignore")
    return [ln.strip() for ln in data.splitlines() if ln.strip()]


def hosts_state(block: list[str]) -> str:
    """ok — все строки на месте, partial — часть, missing — нет."""
    try:
        cur = hosts_path().read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return "missing"
    lines = {re.sub(r"\s+", " ", ln.strip()) for ln in cur.splitlines()}
    want = [re.sub(r"\s+", " ", ln) for ln in block if not ln.startswith("#")]
    have = sum(1 for w in want if w in lines)
    if want and have == len(want):
        return "ok"
    return "partial" if have else "missing"


def hosts_apply(block: list[str]) -> None:
    p = hosts_path()
    cur = p.read_text(encoding="utf-8", errors="ignore") if p.exists() else ""
    if not HOSTS_BACKUP.exists():
        HOSTS_BACKUP.parent.mkdir(parents=True, exist_ok=True)
        HOSTS_BACKUP.write_text(cur, encoding="utf-8")
    cur = _strip_block(cur).rstrip()
    new = cur + ("\r\n\r\n" if cur else "") + BEGIN + "\r\n" + "\r\n".join(block) + "\r\n" + END + "\r\n"
    p.write_text(new.replace("\r\n", "\n").replace("\n", "\r\n"), encoding="utf-8", newline="")
    winutil.run(["ipconfig", "/flushdns"], timeout=10)


def hosts_remove() -> bool:
    p = hosts_path()
    try:
        cur = p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False
    if BEGIN not in cur:
        return False
    p.write_text(_strip_block(cur).rstrip() + "\r\n", encoding="utf-8", newline="")
    winutil.run(["ipconfig", "/flushdns"], timeout=10)
    return True


def _strip_block(text: str) -> str:
    return re.sub(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\s*", "", text, flags=re.S)
