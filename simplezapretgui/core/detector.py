"""Детектор блокировок: по домену/IP определяет, как именно провайдер ограничивает ресурс
и можно ли это обойти средствами zapret.

Ограничения бывают на разных уровнях, и они могут сочетаться (так сейчас у YouTube в России:
домен убран из DNS Роскомнадзора, а сам трафик замедляется через ТСПУ). Поэтому проверка НЕ обрывается
на первой же проблеме, а идёт дальше «окольным путём» — по адресу из DoH или серверов Google:

  1. DNS. Системный DNS сравнивается с DoH (Cloudflare/Google). Не отдаёт адрес или подменяет —
     это отмечается, но проверка продолжается по доверенному адресу.
  2. IP. Отвечает ли адрес на TCP-подключение к 443. Молчит — блокировка по IP, zapret её не обходит.
  3. SNI/TLS. Открыть TLS по имени сайта. Сброс/таймаут при живом IP — блокировка по имени (SNI).
  4. Обрыв загрузки. Тело ответа обрывается примерно после 16 КБ — типичный DPI ТСПУ.
  5. Замедление. Скорость скачивания сравнивается с контрольным узлом (speed.cloudflare.com).
     У сайта она в разы ниже при нормальной скорости у контроля — ТСПУ режет её (примерно до 128 кбит/с).
  6. HTTP. Редирект на страницу-заглушку провайдера.

Итог по каждому адресу: основной тип ограничения, проблема DNS (если есть), можно ли обойти zapret.
Проверки переиспользуют tester.check_https / resolve_all_ipv4 / ping.
"""
from __future__ import annotations

import http.client
import json
import re
import socket
import ssl
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

from .tester import (GOOGLE_FRONTENDS, YOUTUBE_SUFFIXES, _PinnedHTTPS, _ctx, check_https, ping,
                     resolve_all_ipv4)

DOH_SERVERS = [
    ("https://cloudflare-dns.com/dns-query", "Cloudflare"),
    ("https://dns.google/resolve", "Google"),
]

# Всё, что отдаётся с серверов Google/YouTube: если DNS не отдал адрес, берём адрес их фронтендов
GOOGLE_FAMILY = YOUTUBE_SUFFIXES + ("ytimg.com", "ggpht.com", "googlevideo.com", "youtube-nocookie.com",
                                    "youtubei.googleapis.com", "googleusercontent.com")

# Скорость: замедление ТСПУ ≈ 128 кбит/с = 16 КБ/с. Читаем до CAP байт не дольше BUDGET секунд.
SPEED_CAP = 160 * 1024
SPEED_BUDGET = 3.0
SLOW_RATE = 40 * 1024            # ниже — подозрение на замедление (~320 кбит/с)
ABS_SLOW_RATE = 25 * 1024        # без контрольного замера судим только по этому порогу
CONTROL_OK_RATE = 120 * 1024     # контроль считается «быстрым», если не медленнее
CONTROL = ("speed.cloudflare.com", "/__down?bytes=400000")

# Тип -> (можно ли обойти через zapret, пояснение)
KINDS = {
    "none":     (False, "Ограничений не найдено: сайт открывается и скачивается с нормальной скоростью."),
    "dns":      (False, "Проблема только с DNS: провайдерский сервер не отдаёт или подменяет адрес. "
                        "Обходится переходом на DNS-over-HTTPS (DoH), а не zapret."),
    "dns_fail": (False, "Адрес сайта не удалось получить ни через DNS, ни через DoH, ни окольным путём."),
    "ip":       (False, "Блокировка по IP-адресу: адрес не отвечает даже на обычное подключение. "
                        "Это блок на сетевом уровне, zapret его не обходит — нужен VPN или прокси."),
    "ip_ok":    (False, "IP отвечает на порт 443. Блокировку по имени сайта (SNI) можно проверить только по домену."),
    "sni":      (True,  "Блокировка по имени сайта (SNI) при установке TLS — то, что обходит zapret."),
    "cut":      (True,  "Провайдер обрывает загрузку (DPI, обычно после ~16 КБ) — обходится zapret."),
    "throttle": (True,  "Соединение искусственно замедлено (ТСПУ режет скорость примерно до 128 кбит/с) — "
                        "обходится zapret."),
    "http":     (True,  "Провайдер подменяет HTTP и перенаправляет на страницу-заглушку — обходится zapret."),
    "tls_err":  (False, "Ошибка TLS-сертификата — похоже на подмену трафика, но не на обычную блокировку. "
                        "Проверьте адрес; zapret здесь, скорее всего, не поможет."),
    "unknown":  (False, "Не удалось однозначно определить тип блокировки."),
}

DNS_NOTE = {
    "fail": "Кроме того, ваш DNS не отдаёт адрес сайта — включите DNS-over-HTTPS, иначе сайт не откроется, "
            "даже когда zapret справится с блокировкой.",
    "spoof": "Кроме того, ваш DNS подменяет адрес сайта — включите DNS-over-HTTPS, иначе сайт не откроется, "
             "даже когда zapret справится с блокировкой.",
}
YT_DNS_FACT = ("По сообщениям РБК и «Фонтанки» от 10.02.2026, youtube.com убран из DNS-серверов Роскомнадзора — "
               "DNS провайдера может не отдавать его адрес.")
YT_VIDEO_NOTE = ("Видео грузится с *.googlevideo.com — у него отдельное замедление, которое здесь не измеряется "
                 "(нужна подписанная ссылка на видео). Его обходит стратегия с секцией Google.")


@dataclass
class Detection:
    query: str                      # что ввёл пользователь
    host: str                       # домен для проверки
    is_ip: bool = False
    kind: str = "unknown"           # основной тип ограничения
    dns: str = "ok"                 # ok | fail (не отдаёт адрес) | spoof (подменяет)
    detail: str = ""
    bypassable: bool = False
    sys_ip: str = ""                # адрес от системного DNS
    doh_ip: str = ""                # адрес через DoH
    listed: str = ""                # "" | include (уже в списке обхода) | exclude (в исключениях)
    extra: list = field(default_factory=list)   # заметки для подсказки в таблице

    @property
    def can_add(self) -> bool:
        """Добавлять в список обхода имеет смысл только для домена, который zapret реально поможет
        и которого ещё нет в списках."""
        return self.bypassable and not self.is_ip and not self.listed


def is_google_family(host: str) -> bool:
    return any(host == s or host.endswith("." + s) for s in GOOGLE_FAMILY)


def _clean_host(query: str) -> tuple[str, bool]:
    """Из ввода (домен, IP, ссылка) получить хост и признак «это IP»."""
    q = query.strip()
    q = re.sub(r"^[a-z]+://", "", q, flags=re.I)        # убрать схему
    q = q.split("/")[0].split("?")[0].split("#")[0]     # убрать путь
    q = q.split("@")[-1]                                 # убрать user@
    if q.startswith("[") and "]" in q:                   # [IPv6]
        q = q[1:q.index("]")]
    else:
        q = q.split(":")[0]                              # убрать порт
    q = q.strip().strip(".").lower()
    try:
        socket.inet_aton(q)
        return q, True
    except OSError:
        try:
            q = q.encode("idna").decode("ascii")         # кириллические домены
        except Exception:
            pass
        return q, False


def doh_resolve(host: str, timeout: float = 5.0) -> Optional[str]:
    """Первый A-адрес сайта через DoH (в обход провайдерского DNS)."""
    for url, _name in DOH_SERVERS:
        try:
            req = urllib.request.Request(f"{url}?name={host}&type=A",
                                         headers={"accept": "application/dns-json"})
            with urllib.request.urlopen(req, timeout=timeout,
                                        context=ssl.create_default_context()) as r:
                data = json.loads(r.read().decode("utf-8", "ignore"))
            for ans in data.get("Answer", []):
                ip = ans.get("data", "")
                if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", ip):
                    return ip
        except Exception:
            continue
    return None


def _resolve_retry(host: str, tries: int = 3) -> list[str]:
    """Системный DNS с повторами: при одновременных запросах он иногда отвечает отказом."""
    for i in range(tries):
        ips = resolve_all_ipv4(host)
        if ips:
            return ips
        time.sleep(0.25 * (i + 1))
    return []


def _donor_ips() -> list[str]:
    """Адреса фронтендов Google — они обслуживают все домены YouTube."""
    for d in GOOGLE_FRONTENDS:
        ips = resolve_all_ipv4(d)
        if ips:
            return ips
    return []


def _tcp_open(ip: str, port: int = 443, timeout: float = 4.0) -> bool:
    """Открывается ли TCP-соединение (без TLS) — проверка живости IP."""
    try:
        with socket.create_connection((ip, port), timeout):
            return True
    except OSError:
        return False


def _http_redirect_host(host: str, ip: str, timeout: float = 4.0) -> Optional[str]:
    """Куда редиректит сервер по HTTP (порт 80). Чужой хост в Location — страница-заглушка провайдера."""
    try:
        conn = http.client.HTTPConnection(ip or host, 80, timeout=timeout)
        conn.request("GET", "/", headers={"Host": host, "User-Agent": "Mozilla/5.0"})
        resp = conn.getresponse()
        loc = resp.getheader("Location") or ""
        conn.close()
        if 300 <= resp.status < 400 and loc:
            dest = re.sub(r"^[a-z]+://", "", loc, flags=re.I).split("/")[0].lower()
            if dest and host not in dest and dest not in host:
                return dest
    except Exception:
        return None
    return None


# ---------------------------------------------------------------- скорость
_speed_lock = threading.Lock()       # замеры идут по одному: параллельные делили бы канал и врали
_control_cache: dict = {"t": 0.0, "rate": None}


def measure_speed(host: str, ip: Optional[str], path: str = "/", budget: float = SPEED_BUDGET,
                  cap: int = SPEED_CAP, timeout: float = 5.0):
    """Скачать до cap байт не дольше budget секунд. Возвращает (байт, секунд, дочитано_до_конца)
    или None, если подключиться не удалось."""
    ctx = _ctx(None)
    try:
        if ip:
            conn = _PinnedHTTPS(host, ip, port=443, timeout=timeout, context=ctx)
        else:
            conn = http.client.HTTPSConnection(host, 443, timeout=timeout, context=ctx)
        conn.request("GET", path, headers={"User-Agent": "Mozilla/5.0 SimpleZapretGUI-test", "Accept": "*/*",
                                           "Accept-Encoding": "identity"})
        resp = conn.getresponse()
    except Exception:
        return None
    got, finished, t0 = 0, False, time.time()
    try:
        if conn.sock is not None:
            conn.sock.settimeout(1.0)               # зависшее чтение не должно тянуть время за бюджет
        while got < cap and time.time() - t0 < budget:
            chunk = resp.read(8192)
            if not chunk:
                finished = True
                break
            got += len(chunk)
    except Exception:
        pass                                          # обрыв/пауза — берём то, что успели скачать
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return got, max(time.time() - t0, 1e-3), finished


def control_rate() -> Optional[float]:
    """Скорость (байт/с) до нейтрального узла — с чем сравниваем сайт. Кэш на 2 минуты.
    Вызывать только под _speed_lock."""
    if _control_cache["t"] and time.time() - _control_cache["t"] < 120:
        return _control_cache["rate"]
    host, path = CONTROL
    ips = resolve_all_ipv4(host)
    m = measure_speed(host, ips[0] if ips else None, path)
    rate = None
    if m and m[0] >= 20 * 1024:
        rate = m[0] / m[1]
    _control_cache.update(t=time.time(), rate=rate)
    return rate


def speed_verdict(host: str, ip: Optional[str]) -> tuple[str, str]:
    """('ok' | 'slow' | 'na', подпись). slow — сайт медленный при быстром контроле."""
    with _speed_lock:
        m = measure_speed(host, ip)
        if m is None:
            return "na", ""
        got, secs, finished = m
        if finished or got >= SPEED_CAP:
            return "ok", ""                         # короткий ответ или быстро скачали достаточно
        rate = got / secs
        if rate >= SLOW_RATE:
            return "ok", ""
        ctrl = control_rate()
        label = f"{rate / 1024:.0f} КБ/с"
        if ctrl is None:
            # контроль недоступен — судим по жёсткому порогу, близкому к 128 кбит/с
            return ("slow", label) if rate < ABS_SLOW_RATE else ("na", label)
        label += f" при {ctrl / 1024:.0f} КБ/с у контрольного узла"
        return ("slow", label) if ctrl >= CONTROL_OK_RATE else ("na", label)


# ---------------------------------------------------------------- диагностика
def _apply(d: Detection) -> None:
    bypass, detail = KINDS.get(d.kind, KINDS["unknown"])
    d.bypassable = bypass
    d.detail = detail


def _finish(d: Detection) -> Detection:
    """Дополнить итог: проблема DNS и пояснения для семейства YouTube."""
    if d.dns != "ok":
        if d.kind == "none":
            d.kind = "dns"                           # ограничение только на уровне DNS
            _apply(d)
        else:
            d.detail += " " + DNS_NOTE[d.dns]
        if is_google_family(d.host):
            d.extra.append(YT_DNS_FACT)
    if is_google_family(d.host) and d.kind in ("sni", "cut", "throttle", "none", "dns"):
        d.extra.append(YT_VIDEO_NOTE)
    return d


def detect(query: str, timeout: float = 5.0) -> Detection:
    """Полная диагностика одного адреса."""
    host, is_ip = _clean_host(query)
    d = Detection(query=query.strip(), host=host, is_ip=is_ip)
    if not host:
        d.kind = "unknown"
        d.detail = "Пустой или неразборчивый адрес."
        return d

    # --- кандидаты адреса, по которому будем проверять
    cands: list[str] = []
    if is_ip:
        d.sys_ip = host
        cands = [host]
    else:
        sys_ips = _resolve_retry(host)
        doh = doh_resolve(host, timeout) or ""
        d.sys_ip, d.doh_ip = (sys_ips[0] if sys_ips else ""), doh
        if not sys_ips:
            d.dns = "fail"
            d.extra.append("системный DNS не вернул адрес" + (f"; DoH даёт {doh}" if doh else "; DoH тоже молчит"))
        elif doh and doh not in sys_ips and not any(_tcp_open(i) for i in sys_ips[:2]):
            d.dns = "spoof"
            d.extra.append(f"системный DNS даёт {', '.join(sys_ips[:2])} (не отвечают), настоящий адрес {doh}")
        if doh:
            cands.append(doh)
        if d.dns == "ok":
            cands += [i for i in sys_ips if i not in cands]
        if not cands and is_google_family(host):
            donors = _donor_ips()
            if donors:
                cands = donors
                d.extra.append("адрес взят у серверов Google (они обслуживают все домены YouTube)")
        if not cands:
            d.kind = "dns_fail"
            _apply(d)
            return d

    # --- IP жив?
    probe_ip = next((ip for ip in cands[:4] if _tcp_open(ip)), None)
    if probe_ip is None:
        d.kind = "ip"
        d.extra.append(f"{cands[0]}:443 не отвечает; ping: {ping(cands[0])}")
        _apply(d)
        return _finish(d)
    if is_ip:
        d.kind = "ip_ok"
        _apply(d)
        return d

    # --- TLS по имени сайта (SNI)
    tls = check_https(host, "/", ssl.TLSVersion.TLSv1_3, timeout, 0, ip=probe_ip)
    if tls == "UNSUP":
        tls = check_https(host, "/", ssl.TLSVersion.TLSv1_2, timeout, 0, ip=probe_ip)
    if tls in ("RESET", "TIMEOUT"):
        d.kind = "sni"
        d.extra.append(f"TLS к {probe_ip} по имени {host}: {tls}, при живом соединении")
        _apply(d)
        return _finish(d)
    if tls == "SSL":
        d.kind = "tls_err"
        _apply(d)
        return _finish(d)

    # --- обрыв загрузки (≈16 КБ)
    if check_https(host, "/", None, timeout, 40 * 1024, ip=probe_ip) == "CUT":
        d.kind = "cut"
        _apply(d)
        return _finish(d)

    # --- замедление
    verdict, label = speed_verdict(host, probe_ip)
    if label:
        d.extra.append("скорость: " + label)
    if verdict == "slow":
        d.kind = "throttle"
        _apply(d)
        return _finish(d)

    # --- заглушка по HTTP
    redir = _http_redirect_host(host, probe_ip, timeout)
    if redir:
        d.kind = "http"
        d.extra.append(f"HTTP перенаправляет на {redir}")
        _apply(d)
        return _finish(d)

    d.kind = "none"
    _apply(d)
    return _finish(d)


def detect_many(queries: list[str], timeout: float = 5.0, workers: int = 6,
                on_result=None) -> list[Detection]:
    """Диагностика списка адресов параллельно. on_result(Detection) вызывается по мере готовности."""
    seen, uniq = set(), []
    for q in queries:
        key = q.strip().lower()
        if key and key not in seen:
            seen.add(key)
            uniq.append(q.strip())
    results: dict[str, Detection] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(uniq) or 1))) as ex:
        futs = {ex.submit(detect, q, timeout): q for q in uniq}
        for f in futs:
            q = futs[f]
            try:
                d = f.result()
            except Exception as e:  # noqa: BLE001
                d = Detection(query=q, host=q, kind="unknown", detail=f"Ошибка проверки: {e}")
            results[q] = d
            if on_result:
                on_result(d)
    return [results[q] for q in uniq]


def parse_queries(text: str) -> list[str]:
    return [p for p in re.split(r"[\s,;]+", text) if p.strip()]
