"""Автотест стратегий — аналог utils/test zapret.ps1, но без окна PowerShell.

Для каждой стратегии: запускаем winws, проверяем цели из targets.txt тремя способами
(HTTP/1.1 поверх TLS с чтением ~40 КБ тела — ловит «обрыв после 16 КБ», TLS 1.2, TLS 1.3),
считаем долю успешных проверок и сохраняем как оценку стратегии.
"""
from __future__ import annotations

import http.client
import re
import shutil
import socket
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import winutil
from .paths import IS_WINDOWS, TARGETS_FILE

DEFAULT_TARGETS = """# targets.txt — цели для автотеста
#   Имя = "https://host"   -> проверки HTTP/TLS
#   Имя = "PING:1.2.3.4"   -> только ping (на оценку не влияет)

DiscordMain           = "https://discord.com"
DiscordGateway        = "https://gateway.discord.gg"
DiscordCDN            = "https://cdn.discordapp.com"
DiscordUpdates        = "https://updates.discord.com"
YouTubeWeb            = "https://www.youtube.com"
YouTubeShort          = "https://youtu.be"
YouTubeImage          = "https://i.ytimg.com"
YouTubeVideoRedirect  = "https://redirector.googlevideo.com"
GoogleMain            = "https://www.google.com"
GoogleGstatic         = "https://www.gstatic.com"
CloudflareWeb         = "https://www.cloudflare.com"
CloudflareCDN         = "https://cdnjs.cloudflare.com"
CloudflareDNS1111     = "PING:1.1.1.1"
GoogleDNS8888         = "PING:8.8.8.8"
"""


@dataclass
class Target:
    name: str
    url: str

    @property
    def ping_only(self) -> bool:
        return self.url.upper().startswith("PING:")

    @property
    def host(self) -> str:
        if self.ping_only:
            return self.url[5:].strip()
        return re.sub(r"^https?://", "", self.url).split("/")[0]


def parse_targets(text: str) -> list[Target]:
    res = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(r'([A-Za-z0-9_]+)\s*=\s*"([^"]+)"', s)
        if m:
            res.append(Target(m.group(1), m.group(2).strip()))
    return res


def ensure_targets_file(zapret_utils: Optional[Path] = None) -> Path:
    if not TARGETS_FILE.exists():
        TARGETS_FILE.parent.mkdir(parents=True, exist_ok=True)
        src = zapret_utils / "targets.txt" if zapret_utils else None
        if src and src.exists():
            shutil.copyfile(src, TARGETS_FILE)
        else:
            TARGETS_FILE.write_text(DEFAULT_TARGETS, encoding="utf-8")
    return TARGETS_FILE


def load_targets() -> list[Target]:
    try:
        return parse_targets(TARGETS_FILE.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return parse_targets(DEFAULT_TARGETS)


# ---------------------------------------------------------------- одиночные проверки
def _ctx(version: Optional[ssl.TLSVersion]) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if version is not None:
        ctx.minimum_version = version
        ctx.maximum_version = version
    return ctx


def _classify(e: BaseException) -> str:
    msg = str(e).lower()
    if isinstance(e, ssl.SSLError):
        if "unsupported protocol" in msg or "protocol version" in msg or "no protocols available" in msg:
            return "UNSUP"
        return "SSL"
    if isinstance(e, socket.gaierror):
        return "DNS"
    if isinstance(e, (socket.timeout, TimeoutError)):
        return "TIMEOUT"
    if isinstance(e, ConnectionResetError):
        return "RESET"
    return "ERROR"


def check_https(host: str, path: str, version: Optional[ssl.TLSVersion], timeout: float,
                read_body: int = 0) -> str:
    conn = http.client.HTTPSConnection(host, 443, timeout=timeout, context=_ctx(version))
    try:
        conn.request("GET" if read_body else "HEAD", path or "/",
                     headers={"User-Agent": "Mozilla/5.0 SimpleZapretGUI-test", "Accept": "*/*"})
        resp = conn.getresponse()
        if read_body:
            length = int(resp.getheader("Content-Length") or 0)
            got, deadline = 0, time.time() + timeout
            try:
                while got < read_body and time.time() < deadline:
                    chunk = resp.read(4096)
                    if not chunk:
                        break
                    got += len(chunk)
            except (socket.timeout, TimeoutError, ConnectionResetError):
                # сервер начал отдавать тело и «замолчал» — типичная блокировка ТСПУ после 16-20 КБ
                if got >= 8 * 1024 and (length == 0 or length > got):
                    return "CUT"
                return "TIMEOUT"
        return "OK"
    except Exception as e:  # noqa: BLE001
        return _classify(e)
    finally:
        conn.close()


def ping(host: str) -> str:
    if not IS_WINDOWS:
        return "n/a"
    rc, out = winutil.run(["ping", "-n", "1", "-w", "1500", host], timeout=5)
    if rc == 0:
        m = re.search(r"[=<]\s*(\d+)\s*(?:ms|мс)", out)
        return f"{m.group(1)} мс" if m else "OK"
    return "нет ответа"


TESTS = (("HTTP", None, 40 * 1024), ("TLS1.2", ssl.TLSVersion.TLSv1_2, 0), ("TLS1.3", ssl.TLSVersion.TLSv1_3, 0))


def check_target(t: Target, timeout: float) -> dict:
    if t.ping_only:
        return {"name": t.name, "url": t.url, "ping": ping(t.host), "tests": {}}
    path = "/" + re.sub(r"^https?://[^/]+/?", "", t.url)
    res = {}
    for label, ver, body in TESTS:
        res[label] = check_https(t.host, path, ver, timeout, body)
    return {"name": t.name, "url": t.url, "tests": res}


def score_details(details: list[dict]) -> tuple[int, int]:
    ok = total = 0
    for d in details:
        for v in d.get("tests", {}).values():
            if v == "UNSUP":
                continue
            total += 1
            ok += v == "OK"
    return ok, total


def _one_check(t: Target, label: str, ver, body: int, timeout: float, cancel: threading.Event) -> str:
    if cancel.is_set():
        return "ERROR"
    path = "/" + re.sub(r"^https?://[^/]+/?", "", t.url)
    return check_https(t.host, path, ver, timeout, body)


def run_targets(targets: list[Target], timeout: float, parallel: int,
                cancel: threading.Event) -> list[dict]:
    """Все проверки (цель × HTTP/TLS1.2/TLS1.3) идут параллельно — так тест в разы быстрее."""
    out = [{"name": t.name, "url": t.url, "tests": {}} for t in targets]
    with ThreadPoolExecutor(max_workers=max(1, parallel)) as ex:
        futs = []
        for i, t in enumerate(targets):
            if t.ping_only:
                futs.append((i, "ping", ex.submit(ping, t.host)))
                continue
            for label, ver, body in TESTS:
                futs.append((i, label, ex.submit(_one_check, t, label, ver, body, timeout, cancel)))
        for i, label, f in futs:
            try:
                r = f.result()
            except Exception:  # noqa: BLE001
                r = "ERROR"
            if label == "ping":
                out[i]["ping"] = r
            else:
                out[i]["tests"][label] = r
    return out


def resource_targets(domains: list[str]) -> list[Target]:
    """Цели теста из доменов списков ресурсов."""
    res, seen = [], set()
    for d in domains:
        d = d.strip().lstrip("^").lower()
        if not d or d in seen or "/" in d or d.replace(".", "").isdigit():
            continue
        seen.add(d)
        res.append(Target(d, f"https://{d}"))
    return res


TARGET_INFO = {
    "DiscordMain": "Сайт Discord",
    "DiscordGateway": "Шлюз Discord — через него работают чат и статусы в реальном времени",
    "DiscordCDN": "Картинки, аватарки и файлы в Discord",
    "DiscordUpdates": "Сервер обновлений Discord — без него клиент может не запуститься",
    "YouTubeWeb": "Сайт YouTube",
    "YouTubeShort": "Короткие ссылки youtu.be",
    "YouTubeImage": "Превью и картинки YouTube",
    "YouTubeVideoRedirect": "Сервер видеопотока YouTube — если он не работает, видео не грузится",
    "GoogleMain": "Поиск Google",
    "GoogleGstatic": "Служебные файлы Google (скрипты, шрифты), нужны многим сайтам",
    "CloudflareWeb": "Сайт Cloudflare — через Cloudflare работает большая часть интернета",
    "CloudflareCDN": "Сеть доставки Cloudflare — библиотеки, которые подгружают другие сайты",
    "CloudflareDNS1111": "DNS-сервер Cloudflare 1.1.1.1",
    "GoogleDNS8888": "DNS-сервер Google 8.8.8.8",
}


# ---------------------------------------------------------------- прогон стратегий
@dataclass
class StrategyResult:
    name: str
    score: float
    ok: int
    total: int
    details: list
    error: str = ""


class TestRunner:
    """Прогоняет список стратегий. Колбэки вызываются из рабочего потока."""

    BASELINE = "Без обхода"

    def __init__(self, zap, names: list[str], timeout: float = 5, parallel: int = 16,
                 baseline: bool = False, targets: Optional[list[Target]] = None,
                 on_progress: Callable[[int, int, str], None] = lambda i, n, s: None,
                 on_result: Callable[[StrategyResult], None] = lambda r: None):
        self.zap = zap
        self.names = names
        self.timeout = timeout
        self.parallel = parallel
        self.baseline = baseline
        self.on_progress = on_progress
        self.on_result = on_result
        self.targets = targets
        self.cancel = threading.Event()

    def run(self) -> list[StrategyResult]:
        targets = self.targets or load_targets()
        if not targets:
            raise RuntimeError("список целей пуст — добавьте цели во вкладке «Тесты»")
        st = self.zap.status()
        prev_service = st.service_state == "RUNNING"
        prev_session = st.strategy if st.source in ("app", "external") else ""
        results: list[StrategyResult] = []
        queue = ([self.BASELINE] if self.baseline else []) + list(self.names)
        try:
            self.zap.stop_all(unload_driver=False)
            for i, name in enumerate(queue):
                if self.cancel.is_set():
                    break
                self.on_progress(i, len(queue), name)
                r = self._one(name, targets)
                results.append(r)
                self.on_result(r)
        finally:
            self.zap.stop_process()
            restored = False
            try:
                if prev_service:
                    self.zap.service_start()
                    restored = True
                elif prev_session:
                    s = self.zap.strategy(prev_session)
                    if s:
                        self.zap.start(s)
                        restored = True
            except Exception:
                pass
            if not restored:
                self.zap.unload_driver()
        self.on_progress(len(queue), len(queue), "")
        return results

    def _one(self, name: str, targets: list[Target]) -> StrategyResult:
        if name != self.BASELINE:
            s = self.zap.strategy(name)
            if s is None:
                return StrategyResult(name, 0, 0, 0, [], "стратегия не найдена")
            try:
                self.zap.start(s, wait=2.5)
            except Exception as e:  # noqa: BLE001
                return StrategyResult(name, 0, 0, 0, [], str(e))
            time.sleep(0.5)
        try:
            details = run_targets(targets, self.timeout, self.parallel, self.cancel)
        finally:
            if name != self.BASELINE:
                self.zap.stop_process()
                time.sleep(0.4)
        ok, total = score_details(details)
        return StrategyResult(name, ok / total if total else 0, ok, total, details)
