"""Автоматический подбор стратегии под сеть пользователя — комбинаторный поиск.

Подбор НЕ копирует готовые стратегии, а собирает приём обхода из элементарных «кирпичиков»
и находит сочетание, которое пробивает DPI именно у этого провайдера. Оси перебора:
  • метод рассинхронизации (fake / multisplit / multidisorder / fakedsplit / fakeddisorder);
  • содержимое фейкового пакета — ВСЕ .bin из папки bin (google, max.ru, sferum, 5ka, 4pda, sochi…),
    у российских ТСПУ часто работает только «отечественный» ClientHello;
  • число повторов фейка (1, 2, 6, 8, 11 — многие ТСПУ ловят лишь при многих повторах);
  • позиция и перекрытие нарезки (seqovl), способ «испортить» фейк (badseq, ts, md5sig…), TTL.

Чтобы это не превратилось в часы перебора:
1. Много вариантов за один запуск winws: ТСПУ решает судьбу соединения по имени сайта (SNI) в первом
   пакете, а winws держит много секций с фильтром по домену. На разных заблокированных адресах группы
   одновременно проверяются разные варианты — один раунд (~3 с) проверяет десятки.
2. Быстрый отсев (только установление TLS), полная проверка (TLS 1.2/1.3, обрыв 16 КБ, трижды) —
   лишь для прошедших отсев; финалисты разных групп проверяются одновременно.
3. «Умная лесенка»: сначала по отдельности выясняем, какой МЕТОД и какой ФЕЙК вообще что-то дают,
   затем комбинируем повторы/seqovl/искажение/TTL ВОКРУГ найденного — без комбинаторного взрыва.
4. Готовые стратегии в поиске не участвуют вовсе — приём собирается с нуля. Они используются
   только для честного итогового сравнения с лучшей по автотесту.
5. Найденный приём докручивается по соседним значениям осей ради большей стабильности.
6. Неподдерживаемые этой сборкой winws параметры находятся делением пачки пополам и запоминаются.

В конце из победителей собирается полная стратегия в формате zapret-discord-youtube и честно
сравнивается с лучшей по автотесту стратегией на тех же целях.
"""
from __future__ import annotations

import random
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import log, winutil
from .lists import ListManager
from .strategy import ResolveContext, resolve, split_sections, tokenize
from .tester import check_https as _check_https_raw, resolve_all_ipv4

# Адреса сайтов определяются ОДИН раз за подбор: при десятках одновременных соединений Windows
# начинает отвечать на DNS-запросы отказом, и рабочий вариант проваливался бы из-за «DNS», а не DPI.
_PIN: dict[str, list] = {}               # адрес сайта -> все его IPv4 (чередуются, как в браузере)


def check_https(host, path, version, timeout, read_body=0):
    return _check_https_raw(host, path, version, timeout, read_body, ip=_PIN.get(host))


def pin_hosts(hosts) -> dict[str, Optional[str]]:
    """Определить и запомнить IPv4 для каждого адреса (параллельно). None — адрес не определился."""
    hosts = [h for h in dict.fromkeys(hosts) if h not in _PIN]
    with ThreadPoolExecutor(max_workers=8) as ex:          # умеренно: сам DNS тоже не любит шторм
        for h, ips in zip(hosts, ex.map(resolve_all_ipv4, hosts)):
            if ips:
                _PIN[h] = ips
    return {h: _PIN.get(h) for h in hosts}

# ---------------------------------------------------------------- группы ресурсов
DISCORD_DOMAINS = ["discord.com", "discord.gg", "discordapp.com", "discordapp.net", "discord.media",
                   "discord.co", "discord.dev", "discord.new", "discord.gift", "discordstatus.com",
                   "dis.gd", "discordcdn.com"]
YOUTUBE_DOMAINS = ["youtube.com", "youtu.be", "ytimg.com", "ggpht.com", "googlevideo.com",
                   "youtubei.googleapis.com", "yt3.ggpht.com", "googleusercontent.com"]
SKIP_FOR_SITES = ("discord", "dis.gd", "youtube", "ytimg", "ggpht", "googlevideo", "google", "gstatic",
                  "cloudflare", "cloudfront", "encryptedsni", "dns.", "doh.", "quad9", "nextdns")
# так провайдерский DPI блокирует TLS: сбрасывает соединение, молчит или обрывает после ~16 КБ.
# Ошибка сертификата или DNS — не блокировка, такой сайт для проверки не годится.
BLOCKED_CODES = ("RESET", "TIMEOUT", "CUT")
SITE_STATUS = {
    "OK": ("открывается", "ok"),
    "RESET": ("заблокирован — соединение сбрасывается", "blocked"),
    "TIMEOUT": ("заблокирован — нет ответа", "blocked"),
    "CUT": ("заблокирован — обрыв после 16 КБ", "blocked"),
    "SSL": ("ошибка сертификата — для проверки не подходит", "skip"),
    "DNS": ("адрес не найден — для проверки не подходит", "skip"),
    "UNSUP": ("открывается", "ok"),
}


@dataclass
class Group:
    key: str
    title: str
    targets: list[str]                       # что проверяем
    filter_domains: list[str]                # на что действует winws во время проверки
    big: set = field(default_factory=set)    # где проверяем обрыв после 16 КБ
    probes: list = field(default_factory=list)  # адреса для быстрого отсева (каждый — свой вариант)
    # параметры, с которыми секция группы стоит в итоговой стратегии: проверять нужно в тех же условиях
    extra: list = field(default_factory=list)
    # если DNS не отдал адрес сайта группы — берём адрес другого её сервера (тот же фронтенд, другое имя)
    ip_donors: list = field(default_factory=list)


def default_groups() -> dict[str, Group]:
    return {
        "youtube": Group("youtube", "YouTube",
                         ["www.youtube.com", "i.ytimg.com", "redirector.googlevideo.com", "www.gstatic.com"],
                         YOUTUBE_DOMAINS + ["gstatic.com"], {"www.youtube.com"},
                         ["www.youtube.com", "i.ytimg.com", "redirector.googlevideo.com", "m.youtube.com",
                          "yt3.ggpht.com", "music.youtube.com", "s.ytimg.com", "youtubei.googleapis.com"],
                         ["--ip-id=zero"],        # секция Google в стратегии всегда с --ip-id=zero
                         # фронтенды Google обслуживают все домены YouTube — их адрес годится для www.youtube.com
                         ["youtubei.googleapis.com", "yt3.ggpht.com", "i.ytimg.com", "www.gstatic.com"]),
        "discord": Group("discord", "Discord",
                         ["discord.com", "gateway.discord.gg", "cdn.discordapp.com", "updates.discord.com"],
                         DISCORD_DOMAINS, {"discord.com"},
                         ["discord.com", "gateway.discord.gg", "cdn.discordapp.com", "media.discordapp.net",
                          "dl.discordapp.net", "discord.gg", "discordstatus.com"]),
        "cloudflare": Group("cloudflare", "Cloudflare", ["www.cloudflare.com", "cdnjs.cloudflare.com"],
                            ["cloudflare.com"], {"www.cloudflare.com"},
                            ["www.cloudflare.com", "cdnjs.cloudflare.com", "dash.cloudflare.com",
                             "developers.cloudflare.com", "blog.cloudflare.com"]),
        "sites": Group("sites", "Другие сайты", [], [], set()),
    }


# ---------------------------------------------------------------- проверки
@dataclass
class Probe:
    ok: int = 0
    total: int = 0
    time: float = 0.0
    fails: list = field(default_factory=list)

    @property
    def perfect(self) -> bool:
        return self.total > 0 and self.ok == self.total

    @property
    def score(self) -> float:
        return self.ok / self.total if self.total else 0.0


def _resolves(host: str) -> bool:
    if host in _PIN:
        return True
    ips = resolve_all_ipv4(host)
    if ips:
        _PIN[host] = ips
        return True
    return False


def site_pool(lists_dir, limit: int = 40) -> list[str]:
    """Сайты из списка обхода, среди которых подбор ищет заблокированные («Другие сайты»).

    Discord, YouTube/Google, Cloudflare и DNS-сервисы исключены — для них свои группы.
    """
    lm = ListManager(lists_dir)
    general = [e.value for e in lm.entries("general")]
    own = [e.value for c in lm.categories() if c.custom and c.target == "general" for e in lm.entries(c.key)]
    pool, seen = [], set()
    for d in own + general:                           # свои сайты пользователя — в первую очередь
        d = d.lstrip("^").lower()
        if d in seen or d.count(".") < 1 or any(s in d for s in SKIP_FOR_SITES):
            continue
        seen.add(d)
        pool.append(d)
    head, tail = pool[:len(own)], pool[len(own):]
    random.Random(7).shuffle(tail)
    return (head + tail)[:limit]


def check_site(domain: str, timeout: float = 3.0) -> str:
    """OK — открывается, DNS — адрес не найден, иначе — код ошибки (сайт заблокирован)."""
    if not _resolves(domain):
        return "DNS"
    return check_https(domain, "/", ssl.TLSVersion.TLSv1_3, timeout, 0)


def probe_group(g: Group, timeout: float, cancel: threading.Event) -> Probe:
    """TLS 1.2 + TLS 1.3 для каждой цели, для «больших» — ещё загрузка ~64 КБ (обрыв 16 КБ)."""
    jobs = []
    for host in g.targets:
        jobs.append((host, "TLS1.2", ssl.TLSVersion.TLSv1_2, 0))
        jobs.append((host, "TLS1.3", ssl.TLSVersion.TLSv1_3, 0))
        if host in g.big:
            jobs.append((host, "HTTP", None, 64 * 1024))
    pr = Probe()
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=min(16, len(jobs) or 1)) as ex:
        futs = [(h, lab, ex.submit(check_https, h, "/", v, timeout, body)) for h, lab, v, body in jobs]
        for h, lab, f in futs:
            if cancel.is_set():
                break
            try:
                r = f.result()
            except Exception:  # noqa: BLE001
                r = "ERROR"
            if r == "UNSUP":
                continue
            pr.total += 1
            if r == "OK":
                pr.ok += 1
            else:
                pr.fails.append(f"{h} {lab}: {r}")
    pr.time = time.time() - t0
    return pr


# ---------------------------------------------------------------- варианты
@dataclass
class Candidate:
    args: list[str]          # параметры в виде шаблона (%BIN% ...)
    label: str               # понятное описание
    cost: int                # «сложность» — при равенстве выбираем более простой
    phase: str

    def key(self) -> str:
        return " ".join(self.args)


GOOGLE_TLS = '"%BIN%tls_clienthello_www_google_com.bin"'
PDA_TLS = '"%BIN%tls_clienthello_4pda_to.bin"'

SPLITS_TLS = ["2", "1", "sniext+1", "sniext+4", "host+1", "midsld", "1,midsld",
              "1,sniext+1,host+1,midsld-2,midsld,midsld+2,endhost-1"]
FOOLINGS = ["badseq", "ts", "md5sig", "badsum", "datanoack"]
FOOL_TEXT = {"badseq": "неверный номер пакета", "ts": "старая метка времени", "md5sig": "подпись MD5",
             "badsum": "неверная контрольная сумма", "datanoack": "без флага ACK"}
DESYNC_TEXT = {"multisplit": "нарезка", "multidisorder": "нарезка в обратном порядке", "fake": "фейк",
               "fakedsplit": "нарезка с фейками", "fakeddisorder": "обратная нарезка с фейками",
               "hostfakesplit": "подмена имени сайта"}


def _desync_label(desync: str) -> str:
    return " + ".join(DESYNC_TEXT.get(d, d) for d in desync.split(","))


def _has(desync: str, *names) -> bool:
    parts = desync.split(",")
    return any(n in parts for n in names)


# Содержимое фейка решает исход у российских ТСПУ: для многих провайдеров работает только
# «отечественный» ClientHello (max.ru, sferum, 5ka…), поэтому перебираем их все, а не только google.
FAKE_TLS = [
    ("tls_clienthello_www_google_com.bin", "google"),
    ("tls_clienthello_max_ru.bin", "max.ru"),
    ("tls_clienthello_www_sferum_ru.bin", "sferum"),
    ("tls_clienthello_5ka_ru.bin", "5ka"),
    ("tls_clienthello_4pda_to.bin", "4pda"),
    ("tls_clienthello_sochi_park.bin", "sochi"),
]
REPEATS = [1, 2, 6, 8, 11]        # число повторов фейка: ТСПУ часто ловят лишь при многих повторах
SPLIT_POS = ["1", "2", "midsld", "sniext+1", "host+1", "1,midsld"]
SEQOVL = ["0", "1", "336", "568", "664", "681"]       # перекрытие sequence
FOOL = ["none", "badseq", "ts", "md5sig", "badsum", "datanoack"]
TTL = ["0", "1", "2", "3", "4", "5", "6", "a2", "a3", "a4"]   # aN = autottl -N


def _bins_present(bin_dir) -> set:
    try:
        return {p.name for p in bin_dir.glob("*.bin")}
    except Exception:
        return set()


FAKE_SHORT = {f: n for f, n in FAKE_TLS}


def axis_label(desync, fake, rep, pos, ovl, fool, ttl) -> str:
    bits = [_desync_label(desync)]
    if fake:
        bits.append("шаблон " + FAKE_SHORT.get(fake, fake.replace("tls_clienthello_", "").replace(".bin", "")))
    if rep and rep != 1:
        bits.append(f"×{rep}")
    if pos and _has(desync, "multisplit", "multidisorder", "fakedsplit", "fakeddisorder"):
        bits.append("поз " + pos)
    if ovl and ovl != "0":
        bits.append("seqovl " + ovl)
    if fool and fool != "none":
        bits.append(FOOL_TEXT.get(fool, fool))
    if ttl and ttl != "0":
        bits.append(("авто-TTL −" + ttl[1:]) if ttl.startswith("a") else "TTL " + ttl)
    return ", ".join(bits)


def make_candidate(desync, fake="", rep=1, pos="1", ovl="0", fool="none", ttl="0",
                   bin_dir=None) -> Optional[Candidate]:
    """Собрать вариант из элементарных «кирпичиков». None — если нужного .bin нет в сборке."""
    args = [f"--dpi-desync={desync}"]
    split = _has(desync, "multisplit", "multidisorder", "fakedsplit", "fakeddisorder")
    if split:
        args.append(f"--dpi-desync-split-pos={pos}")
        if ovl and ovl != "0":
            args.append(f"--dpi-desync-split-seqovl={ovl}")
            if fake:
                args.append(f'--dpi-desync-split-seqovl-pattern="%BIN%{fake}"')
    if _has(desync, "fake") and fake:
        args.append(f'--dpi-desync-fake-tls="%BIN%{fake}"')
    if fake and bin_dir is not None and fake not in _bins_present(bin_dir):
        return None
    if rep and rep != 1:
        args.append(f"--dpi-desync-repeats={rep}")
    if fool and fool != "none":
        args.append(f"--dpi-desync-fooling={fool}")
    if ttl and ttl != "0":
        if ttl.startswith("a"):
            args += ["--dpi-desync-ttl=1", f"--dpi-desync-autottl=-{ttl[1:]}"]
        else:
            args.append(f"--dpi-desync-ttl={ttl}")
    cost = len(args) + (REPEATS.index(rep) if rep in REPEATS else 0)
    return Candidate(args, axis_label(desync, fake, rep, pos, ovl, fool, ttl), cost, "перебор")


def axis_pools(ts_enabled: bool, bin_dir) -> list[tuple[str, list[Candidate]]]:
    """«Лесенка»: сначала каждую ось по отдельности (быстрый отсев), сильные сочетаем потом.

    Возвращает именованные этапы по возрастанию стоимости. Runner комбинирует находки сам.
    """
    bins = _bins_present(bin_dir)
    fakes = [(f, n) for f, n in FAKE_TLS if f in bins] or [("tls_clienthello_www_google_com.bin", "google")]
    seen: set = set()

    def uniq(cands):
        out = []
        for c in cands:
            if c and c.key() not in seen:
                seen.add(c.key())
                out.append(c)
        return out

    stages: list[tuple[str, list[Candidate]]] = []
    # 1. базовые методы без фейка — какой вообще тип рассинхронизации пробивает DPI
    base = []
    for desync in ("multisplit", "multidisorder", "fake", "fakedsplit", "fakeddisorder"):
        for pos in ("1", "midsld", "2"):
            base.append(make_candidate(desync, pos=pos, bin_dir=bin_dir))
    stages.append(("метод рассинхронизации", uniq(base)))
    # 2. фейк + содержимое: для fake/fakedsplit прогоняем все .bin с умеренными повторами
    fk = []
    for desync in ("fake", "fakedsplit", "fake,multisplit"):
        for fake, _n in fakes:
            for rep in (6, 11):
                fk.append(make_candidate(desync, fake=fake, rep=rep, pos="1", bin_dir=bin_dir))
    stages.append(("содержимое фейка", uniq(fk)))
    # 3. нарезка с перекрытием и шаблоном-фейком
    ov = []
    for desync in ("multisplit", "multidisorder"):
        for pos in ("1", "2"):
            for ovl in ("1", "568", "664", "681"):
                for fake, _n in fakes[:3]:
                    ov.append(make_candidate(desync, fake=fake, pos=pos, ovl=ovl, bin_dir=bin_dir))
    stages.append(("перекрытие sequence", uniq(ov)))
    # 4. способы «испортить» фейк
    fo = []
    for fool in FOOL[1:]:
        if fool == "ts" and not ts_enabled:
            continue
        for desync in ("fake", "fake,multisplit"):
            for fake, _n in fakes[:3]:
                fo.append(make_candidate(desync, fake=fake, rep=6, fool=fool, pos="1", bin_dir=bin_dir))
    stages.append(("искажение фейка", uniq(fo)))
    # 5. TTL
    tt = []
    for ttl in TTL[1:]:
        for fake, _n in fakes[:2]:
            tt.append(make_candidate("fake", fake=fake, rep=6, ttl=ttl, bin_dir=bin_dir))
    stages.append(("TTL", uniq(tt)))
    return stages


def promising_fakes(cands_passed: list[Candidate]) -> list[str]:
    """Из прошедших отсев вариантов — какие .bin-фейки реально работают (их и крутим дальше)."""
    fakes = []
    for c in cands_passed:
        for t in c.args:
            if t.startswith("--dpi-desync-fake-tls=") or t.startswith("--dpi-desync-split-seqovl-pattern="):
                f = t.split("%BIN%", 1)[1].rstrip('"')
                if f not in fakes:
                    fakes.append(f)
    return fakes


def promising_desyncs(cands_passed: list[Candidate]) -> list[str]:
    out = []
    for c in cands_passed:
        d = next((t.split("=", 1)[1] for t in c.args if t.startswith("--dpi-desync=")), "")
        if d and d not in out:
            out.append(d)
    return out


def combo_stage(fakes: list[str], desyncs: list[str], ts_enabled: bool, bin_dir,
                thorough: bool) -> list[Candidate]:
    """Главный комбинаторный этап: вокруг работающих фейков и методов крутим повторы, seqovl,
    искажение и TTL ВМЕСТЕ. Запускается только для перспективных фейков, поэтому не взрывается."""
    if not fakes:
        # ни один шаблон не прошёл простой отсев — пробуем все доступные .bin
        fakes = [f for f, _n in FAKE_TLS if bin_dir is None or f in _bins_present(bin_dir)]
        fakes = fakes or ["tls_clienthello_www_google_com.bin"]
    if not desyncs:
        desyncs = ["fake", "fake,multisplit"]
    reps = REPEATS if thorough else [2, 6, 8, 11]
    ttls = ["0"] + (TTL[1:] if thorough else ["2", "3", "4", "5", "a2", "a3"])
    fools = FOOL if ts_enabled else [f for f in FOOL if f != "ts"]
    out = []
    for desync in desyncs:
        split = _has(desync, "multisplit", "multidisorder", "fakedsplit", "fakeddisorder")
        ovls = (["0", "568", "664", "681"] if split else ["0"])
        for fake in fakes:
            for rep in reps:
                for fool in fools:
                    for ttl in ttls:
                        for ovl in ovls:
                            out.append(make_candidate(desync, fake=fake, rep=rep, pos="1", ovl=ovl,
                                                      fool=fool, ttl=ttl, bin_dir=bin_dir))
    out = [c for c in out if c]
    # от простого к сложному; в худшем случае ограничиваем объём, чтобы подбор не растянулся на часы
    out.sort(key=lambda c: c.cost)
    return out[:1200 if thorough else 600]


def _axes_of(c: Candidate) -> dict:
    """Разобрать вариант обратно на оси (метод, фейк, повторы, позиция, seqovl, искажение, TTL)."""
    ax = {"desync": "fake", "fake": "", "rep": 1, "pos": "1", "ovl": "0", "fool": "none", "ttl": "0"}
    for t in c.args:
        name, _, val = t.partition("=")
        if name == "--dpi-desync":
            ax["desync"] = val
        elif name in ("--dpi-desync-fake-tls", "--dpi-desync-split-seqovl-pattern") and "%BIN%" in val and not ax["fake"]:
            ax["fake"] = val.split("%BIN%", 1)[1].rstrip('"')
        elif name == "--dpi-desync-repeats":
            ax["rep"] = int(val) if val.isdigit() else 1
        elif name == "--dpi-desync-split-pos":
            ax["pos"] = val
        elif name == "--dpi-desync-split-seqovl":
            ax["ovl"] = val
        elif name == "--dpi-desync-fooling":
            ax["fool"] = val
        elif name == "--dpi-desync-autottl":
            ax["ttl"] = "a" + val.lstrip("-")
        elif name == "--dpi-desync-ttl" and ax["ttl"] == "0":
            ax["ttl"] = val
    return ax


def near_miss_candidates(c: Candidate, bin_dir, limit: int = 40) -> list[Candidate]:
    """Соседи «почти рабочего» варианта: меняем по одной-две оси, от ближайших к дальним.

    Так подбор доходит, например, от «фейк + нарезка, google ×6, ts» до «… seqovl 681, ×8» —
    именно таких небольших доводок обычно и не хватает до 100%.
    """
    ax = _axes_of(c)
    desyncs = [ax["desync"]] + [d for d in ("fake,multisplit", "fake,multidisorder", "fakedsplit")
                                if d != ax["desync"] and _has(ax["desync"], "fake")]
    fakes = [ax["fake"]] + [f for f, _n in FAKE_TLS[:3] if f != ax["fake"]]
    reps = sorted({ax["rep"], 6, 8, 11})
    fools = [ax["fool"]] + [f for f in ("ts", "badseq", "none") if f != ax["fool"]]
    out, seen = [], {c.key()}
    for d in desyncs:
        split = _has(d, "multisplit", "multidisorder", "fakedsplit", "fakeddisorder")
        ovls = [ax["ovl"]] + ([o for o in ("681", "664", "568") if o != ax["ovl"]] if split else [])
        for f in fakes:
            for rp in reps:
                for fo in fools:
                    for ov in ovls:
                        cand = make_candidate(d, fake=f, rep=rp, pos=ax["pos"], ovl=ov, fool=fo,
                                              ttl=ax["ttl"], bin_dir=bin_dir)
                        if not cand or cand.key() in seen:
                            continue
                        seen.add(cand.key())
                        dist = ((d != ax["desync"]) + (f != ax["fake"]) + (rp != ax["rep"])
                                + (fo != ax["fool"]) + (ov != ax["ovl"]))
                        out.append((dist, cand.cost, cand))
    out.sort(key=lambda t: (t[0], t[1]))
    return [t[2] for t in out[:limit]]


def strengthen_candidates(c: Candidate, bin_dir) -> list[Candidate]:
    """«Усилить» приём, который проходит на грани: больше повторов фейка и перекрытие с шаблоном.
    Так обычно и доводят нестабильный обход видеопотока до надёжного (например, ×6 → ×8 + seqovl 681)."""
    ax = _axes_of(c)
    out, seen = [], {c.key()}
    desyncs = [ax["desync"]]
    if ax["desync"] == "fake":
        desyncs.append("fake,multisplit")              # у одиночного фейка перекрытия нет — добавим нарезку
    for d in desyncs:
        split = _has(d, "multisplit", "multidisorder", "fakedsplit", "fakeddisorder")
        for rp in (8, 11):
            if rp <= ax["rep"]:
                continue
            for ov in (["681", "664"] if split else ["0"]):
                for f in [ax["fake"]] + [x for x, _n in FAKE_TLS[:2] if x != ax["fake"]]:
                    cand = make_candidate(d, fake=f, rep=rp, pos=ax["pos"], ovl=ov, fool=ax["fool"],
                                          ttl=ax["ttl"], bin_dir=bin_dir)
                    if cand and cand.key() not in seen:
                        seen.add(cand.key())
                        out.append(cand)
    return out


def refine_candidates(winner: Candidate, bin_dir) -> list[Candidate]:
    """Докрутить найденный приём по соседним значениям осей — вдруг станет ещё стабильнее.

    Разбираем аргументы победителя и варьируем повторы, seqovl, fooling и содержимое вокруг них.
    """
    desync = next((t.split("=", 1)[1] for t in winner.args if t.startswith("--dpi-desync=")), "fake")
    cur_fake = ""
    for t in winner.args:
        if t.startswith("--dpi-desync-fake-tls="):
            cur_fake = t.split("%BIN%", 1)[1].rstrip('"')
    pos = next((t.split("=", 1)[1] for t in winner.args if t.startswith("--dpi-desync-split-pos=")), "1")
    ovl = next((t.split("=", 1)[1] for t in winner.args if t.startswith("--dpi-desync-split-seqovl=")), "0")
    fool = next((t.split("=", 1)[1] for t in winner.args if t.startswith("--dpi-desync-fooling=")), "none")
    out, seen = [], {winner.key()}
    for rep in REPEATS:
        for ov in ({ovl, "0", "664", "681"} if "multisplit" in desync or "multidisorder" in desync else {ovl}):
            for fo in {fool, "none", "ts", "badseq"}:
                c = make_candidate(desync, fake=cur_fake, rep=rep, pos=pos, ovl=ov, fool=fo, bin_dir=bin_dir)
                if c and c.key() not in seen:
                    seen.add(c.key())
                    out.append(c)
    return out[:24]


# ---------------------------------------------------------------- итоговая стратегия
LG = '"%LISTS%'
GEN = (f'--hostlist={LG}list-general.txt" --hostlist={LG}list-general-user.txt" '
       f'--hostlist-exclude={LG}list-exclude.txt" --hostlist-exclude={LG}list-exclude-user.txt" '
       f'--ipset-exclude={LG}ipset-exclude.txt" --ipset-exclude={LG}ipset-exclude-user.txt"')
EXCL = (f'--hostlist-exclude={LG}list-exclude.txt" --hostlist-exclude={LG}list-exclude-user.txt" '
        f'--ipset-exclude={LG}ipset-exclude.txt" --ipset-exclude={LG}ipset-exclude-user.txt"')
STD_SITES = ('--dpi-desync=multisplit --dpi-desync-split-seqovl=568 --dpi-desync-split-pos=1 '
             f'--dpi-desync-split-seqovl-pattern={PDA_TLS}')
STD_GOOGLE = ('--dpi-desync=multisplit --dpi-desync-split-seqovl=681 --dpi-desync-split-pos=1 '
              f'--dpi-desync-split-seqovl-pattern={GOOGLE_TLS}')


def section_args(template: str) -> dict[str, str]:
    """Приёмы обхода по секциям готовой стратегии: google, general, ipset (TCP), media (discord.media)."""
    out: dict[str, str] = {}
    for sec in split_sections(tokenize(template)):
        text = " ".join(sec)
        if "--filter-udp" in text and "--filter-tcp" not in text:
            continue
        args = " ".join(t for t in sec if t.startswith(("--dpi-desync", "--ip-id", "--orig-", "--dup"))
                        and not t.startswith("--ip-id"))
        if not args:
            continue
        if "list-google.txt" in text:
            out.setdefault("google", args)
        elif "list-general.txt" in text:
            out.setdefault("general", args)
        elif "ipset-all.txt" in text and "GameFilter" not in text:
            out.setdefault("ipset", args)
        elif "discord.media" in text:
            out.setdefault("media", args)
    return out


def all_section_args(template: str) -> dict[str, list[str]]:
    """Как section_args, но все секции каждого вида (у стратегий бывает по несколько)."""
    out: dict[str, list[str]] = {}
    for sec in split_sections(tokenize(template)):
        text = " ".join(sec)
        if "--filter-udp" in text and "--filter-tcp" not in text:
            continue
        args = " ".join(t for t in sec if t.startswith(("--dpi-desync", "--orig-", "--dup")))
        if not args:
            continue
        kind = ("google" if "list-google.txt" in text else "general" if "list-general.txt" in text else
                "ipset" if "ipset-all.txt" in text and "GameFilter" not in text else
                "media" if "discord.media" in text else "")
        if kind and args not in out.setdefault(kind, []):
            out[kind].append(args)
    return out


POOL_KINDS = {"youtube": ("google", "general"), "discord": ("general", "media"),
              "cloudflare": ("ipset", "general"), "sites": ("general", "ipset")}


def build_pool(key: str, named_templates: list[tuple[str, str]], ts_enabled: bool,
               thorough: bool, bin_dir=None) -> list[tuple[str, list[Candidate]]]:
    """Этапы перебора для группы: сначала самые вероятные приёмы, затем систематические оси.

    Возвращает список (название этапа, варианты). Runner идёт по этапам и внутри каждого проверяет
    варианты пачками; найдя рабочий, может докрутить его соседними значениями (refine_candidates).

    Секции готовых стратегий добавляются только как ОДИН быстрый этап-подсказка, а не как основа:
    дальше подбор собирает приём из «кирпичиков» сам, независимо от того, есть ли похожая стратегия.
    """
    stages: list[tuple[str, list[Candidate]]] = []
    seen: set = set()

    def uniq(cands):
        out = []
        for c in cands:
            if c and c.key() not in seen:
                seen.add(c.key())
                out.append(c)
        return out

    hint = []
    for name, tpl in named_templates:
        secs = all_section_args(tpl)
        for kind in POOL_KINDS.get(key, ("general",)):
            for args_s in secs.get(kind, []):
                args = tokenize(args_s)
                desync = next((a.split("=", 1)[1] for a in args if a.startswith("--dpi-desync=")), "?")
                hint.append(Candidate(args, f"из «{name}»: {_desync_label(desync)}", len(args), "подсказка"))
    if hint:
        stages.append(("готовые приёмы (подсказка)", uniq(hint)))
    for name, cands in axis_pools(ts_enabled, bin_dir):
        st = uniq(cands)
        if st:
            stages.append((name, st))
    return stages


def compose(winners: dict[str, Optional[Candidate]], inherit: Optional[dict[str, str]] = None) -> str:
    """Собрать полную стратегию. winners[key] = None — группа не подбиралась / не блокируется / не найдено.

    Всё, что не подбиралось, берётся из inherit — секций лучшей по автотесту стратегии пользователя
    (она уже доказала, что работает), а если её нет — из стандартных приёмов zapret-discord-youtube.
    """
    inherit = inherit or {}

    def win(k: str) -> Optional[str]:
        c = winners.get(k)
        return " ".join(c.args) if c else None

    sites = win("sites") or inherit.get("general") or win("discord") or STD_SITES
    discord = win("discord") or sites
    google = win("youtube") or inherit.get("google") or STD_GOOGLE
    ipset = win("cloudflare") or win("sites") or inherit.get("ipset") or sites
    media = win("discord") or inherit.get("media") or discord
    parts = [
        "--wf-tcp=80,443,2053,2083,2087,2096,8443,%GameFilterTCP% --wf-udp=443,19294-19344,50000-50100,%GameFilterUDP%",
        f'--filter-udp=443 {GEN} --dpi-desync=fake --dpi-desync-repeats=6 '
        '--dpi-desync-fake-quic="%BIN%quic_initial_www_google_com.bin"',
        '--filter-udp=19294-19344,50000-50100 --filter-l7=discord,stun --dpi-desync=fake '
        '--dpi-desync-fake-discord="%BIN%ACTIVE_DISCORD_UDP.bin" --dpi-desync-fake-stun="%BIN%ACTIVE_DISCORD_UDP.bin" '
        '--dpi-desync-repeats=6',
        f"--filter-tcp=2053,2083,2087,2096,8443 --hostlist-domains=discord.media {media}",
        f'--filter-tcp=443 --hostlist={LG}list-google.txt" --ip-id=zero {google}',
    ]
    if discord != sites:
        parts.append(f"--filter-tcp=80,443 --hostlist-domains={','.join(DISCORD_DOMAINS)} {EXCL} {discord}")
    parts += [
        f"--filter-tcp=80,443 {GEN} {sites}",
        f'--filter-udp=443 --ipset={LG}ipset-all.txt" {EXCL} --dpi-desync=fake --dpi-desync-repeats=6 '
        '--dpi-desync-fake-quic="%BIN%quic_initial_www_google_com.bin"',
        f'--filter-tcp=80,443,8443 --ipset={LG}ipset-all.txt" {EXCL} {ipset}',
        f'--filter-tcp=%GameFilterTCP% --ipset={LG}ipset-all.txt" --ipset-exclude={LG}ipset-exclude.txt" '
        f'--ipset-exclude={LG}ipset-exclude-user.txt" --dpi-desync=multisplit --dpi-desync-any-protocol=1 '
        f'--dpi-desync-cutoff=n3 --dpi-desync-split-seqovl=568 --dpi-desync-split-pos=1 '
        f'--dpi-desync-split-seqovl-pattern={PDA_TLS}',
        f'--filter-udp=%GameFilterUDP% --ipset={LG}ipset-all.txt" --ipset-exclude={LG}ipset-exclude.txt" '
        f'--ipset-exclude={LG}ipset-exclude-user.txt" --dpi-desync=fake --dpi-desync-repeats=12 '
        '--dpi-desync-any-protocol=1 --dpi-desync-fake-unknown-udp="%BIN%ACTIVE_GAME_UDP.bin" --dpi-desync-cutoff=n2',
    ]
    # --wf-* идут в одной секции с первым фильтром, как в стратегиях zapret-discord-youtube
    return " --new ".join([parts[0] + " " + parts[1]] + parts[2:])


# ---------------------------------------------------------------- подбор
@dataclass
class GroupResult:
    key: str
    title: str
    state: str = "ожидание"          # ожидание | проверка | не блокируется | найдено | не найдено | пропущено
    targets: list = field(default_factory=list)
    baseline: str = ""
    tried: int = 0
    working: list = field(default_factory=list)      # [(Candidate, Probe)]
    winner: Optional[Candidate] = None
    stability: float = 0.0
    note: str = ""


def batch_has(batch: list, key: str) -> bool:
    return any(k == key for k, _ in batch)


class Builder:
    """Подбор. Колбэки вызываются из рабочего потока."""

    def __init__(self, zap, groups: list[str], thorough: bool = False, timeout: float = 5.0,
                 proven_templates: Optional[list[str]] = None, custom_sites: Optional[list[str]] = None,
                 best_existing: Optional[tuple[str, str]] = None, inherit_template: str = "",
                 on_progress: Callable[[str, int, int], None] = lambda s, i, n: None,
                 on_group: Callable[[GroupResult], None] = lambda r: None,
                 on_log: Callable[[str], None] = lambda s: None):
        self.zap = zap
        self.keys = groups
        self.thorough = thorough
        self.timeout = timeout
        self.proven_templates = proven_templates or []
        self.custom_sites = custom_sites or []
        self.best_existing = best_existing        # (имя, шаблон) лучшей по автотесту — для сравнения
        self.inherit_template = inherit_template  # хорошая стратегия, из которой брать неподобранное
        self.screen_timeout = min(2.0, self.timeout)   # для отсева: TLS с фейками устанавливается < 1 с
        self.good_features: set = set()      # параметры, которые winws точно понимает
        self.bad_features: set = set()       # параметры, которые этот winws не поддерживает
        # все установленные стратегии: сначала лучшие по автотесту, потом остальные
        named = [("лучшей по автотесту", t) for t in self.proven_templates]
        try:
            named += [(s.name, s.template) for s in zap.strategies() if not s.error]
        except Exception:
            pass
        self.named_templates = named
        self.compare: dict = {}
        self.dns_borrowed: set = set()       # адреса, которые DNS не отдал (проверены через соседний сервер)
        self.on_progress = on_progress
        self.on_group = on_group
        self.on_log = on_log
        self.cancel = threading.Event()
        self.results: dict[str, GroupResult] = {}
        self._done = 0
        self._total = 1

    def log_detail(self, text: str) -> None:
        """Ход подбора: в окно (как было) и подробно в файл журнала — для диагностики."""
        self.on_log(text)
        log.detail(text)

    # ------------------------------------------------------------ запуск winws
    def _start(self, sections: list[list[str]]) -> bool:
        """winws с несколькими секциями. False — winws не принял параметры."""
        ctx = self.zap.context()
        args = ["--wf-tcp=80,443"]
        for i, sec in enumerate(sections):
            if i:
                args.append("--new")
            args += [resolve(a, ctx).replace('"', "") for a in sec]
        try:
            self.zap.start_raw(args, wait=0.15)
            if not self.zap.wait_ready(3.0):
                raise RuntimeError("не запустился")
        except Exception:  # noqa: BLE001
            self.zap.stop_process()
            return False
        time.sleep(0.12)
        return True

    @staticmethod
    def _screen_check(host: str, mode: str, timeout: float) -> bool:
        if mode == "data":                      # провайдер обрывает загрузку, а не соединение
            return check_https(host, "/", None, timeout, 40 * 1024) == "OK"
        r = check_https(host, "/", ssl.TLSVersion.TLSv1_3, timeout, 0)
        if r == "UNSUP":
            r = check_https(host, "/", ssl.TLSVersion.TLSv1_2, timeout, 0)
        return r == "OK"

    # ------------------------------------------------------------ «другие сайты»
    def _pick_sites(self) -> list[str]:
        """Несколько сайтов из списка обхода, которые у пользователя реально блокируются."""
        if self.custom_sites:
            self.on_log("проверяю выбранные вами сайты: " + ", ".join(self.custom_sites))
            pool = self.custom_sites
        else:
            pool = site_pool(self.zap.lists_dir)
            self.on_log(f"ищу заблокированные сайты среди {len(pool)} из списка…")
        blocked = []
        with ThreadPoolExecutor(max_workers=12) as ex:
            for d, r in zip(pool, ex.map(lambda x: check_site(x, self.timeout), pool)):
                if r in BLOCKED_CODES:
                    blocked.append(d)
        return blocked[:8]

    # ------------------------------------------------------------ шаги
    def _tick(self, text: str):
        self._done += 1
        self.on_progress(text, self._done, max(self._total, self._done + 1))
        log.detail(text)                 # раунды с отметкой времени — видно, куда уходят минуты

    def _pin_with_donors(self, hosts, groups) -> list[str]:
        """Определить адреса; если DNS не отдал адрес сайта, взять адрес «донора» из той же группы.
        Возвращает адреса, которые пришлось взять у донора (о них стоит сказать пользователю)."""
        pin_hosts(hosts)
        borrowed = []
        for h in dict.fromkeys(hosts):
            if _PIN.get(h):
                continue
            for g in groups:
                if not any(h == d or h.endswith("." + d) for d in (g.filter_domains or g.targets)):
                    continue
                pin_hosts(g.ip_donors)
                donor = next((d for d in g.ip_donors if _PIN.get(d)), None)
                if donor:
                    _PIN[h] = _PIN[donor]
                    borrowed.append(h)
                    break
        if borrowed:
            self.dns_borrowed |= set(borrowed)
            self.log_detail("DNS не отдал адрес " + ", ".join(borrowed)
                            + " — проверяю через адрес соседних серверов той же группы")
        return borrowed

    def _baseline(self, st: dict) -> None:
        """Какие адреса заблокированы и как: это и пробные адреса для отсева, и режим проверки."""
        # адреса определяем один раз; сайты без адреса исключаем — обход их всё равно не откроет
        hosts = [h for g, _r in st.values() for h in g.probes + g.targets]
        self._pin_with_donors(hosts, [g for g, _r in st.values()])
        pinned = None
        lost = sorted(h for h in set(hosts) if not _PIN.get(h))
        if lost:
            self.log_detail("не удалось определить адрес (исключены из проверки): " + ", ".join(lost))
        log.detail("адреса: " + "; ".join(f"{h} → {', '.join(_PIN[h][:4])}" for h in dict.fromkeys(hosts)
                                          if _PIN.get(h)))
        for g, _r in st.values():
            g.targets = [h for h in g.targets if _PIN.get(h)]
            g.probes = [h for h in g.probes if _PIN.get(h)]
            g.big = {h for h in g.big if _PIN.get(h)}
        del pinned
        jobs = []
        for k, (g, r) in st.items():
            for h in dict.fromkeys(g.probes + g.targets):
                jobs.append((k, h))

        def one(job):
            k, h = job
            if not _resolves(h):
                return k, h, "DNS", "DNS"
            tls = check_https(h, "/", ssl.TLSVersion.TLSv1_3, self.screen_timeout, 0)
            data = check_https(h, "/", None, self.screen_timeout, 40 * 1024) if tls in ("OK", "UNSUP") else tls
            return k, h, tls, data

        with ThreadPoolExecutor(max_workers=24) as ex:
            res = list(ex.map(one, jobs))
        for k, h, tls, data in res:
            g, r = st[k]
            if tls in BLOCKED_CODES:
                g.probes_ready.append((h, "tls"))
            elif tls in ("OK", "UNSUP") and data == "CUT":
                g.probes_ready.append((h, "data"))
        for k, (g, r) in st.items():
            g.blocked = {h for h, _m in g.probes_ready}
        # секции winws не должны пересекаться: «discord.gg» поймал бы и «gateway.discord.gg».
        # Оставляем более конкретный адрес.
        for k, (g, r) in st.items():
            hosts = [h for h, _ in g.probes_ready]
            g.probes_ready = [(h, m) for h, m in g.probes_ready
                              if not any(o != h and o.endswith("." + h) for o in hosts)]

    def _screen(self, st: dict, need: int, max_rounds: int = 10_000) -> None:
        """Раунды отсева: в каждом — по варианту на каждый заблокированный адрес каждой группы."""
        rnd = 0
        while not self.cancel.is_set() and rnd < max_rounds:
            assign = []                  # (группа, адрес, режим, вариант)
            for k, (g, r) in st.items():
                if g.done or len(g.finalists) - g.cpos >= need:      # ещё не перепроверенные финалисты
                    continue
                for host, mode in g.probes_ready:
                    while g.pos < len(g.pool) and not self._supported(g.pool[g.pos]):
                        g.pos += 1
                        r.tried += 1
                    if g.pos >= len(g.pool):
                        break
                    assign.append((k, host, mode, g.pool[g.pos]))
                    g.pos += 1
            if not assign:
                return
            rnd += 1
            self._tick(f"Раунд {rnd}: проверяю {len(assign)} вариантов одновременно")
            self._screen_batch(st, assign)
            for k, (g, r) in st.items():
                self.on_group(r)

    @staticmethod
    def _features(c: Candidate) -> set:
        """Из чего состоит вариант: названия параметров и режимы --dpi-desync."""
        f = set()
        for t in c.args:
            name, _, val = t.partition("=")
            f.add(name)
            if name == "--dpi-desync":
                f |= {"mode:" + m for m in val.split(",")}
        return f

    def _supported(self, c: Candidate) -> bool:
        return not (self._features(c) & self.bad_features)

    def _screen_batch(self, st: dict, assign: list) -> None:
        assign = [a for a in assign if self._supported(a[3])]
        if not assign:
            return
        sections = [["--filter-tcp=443", f"--hostlist-domains={host}"] + st[k][0].extra + c.args
                    for k, host, _m, c in assign]
        if not self._start(sections):
            if len(assign) == 1:          # этот вариант winws не понимает — пропускаем
                c = assign[0][3]
                st[assign[0][0]][1].tried += 1
                # запоминаем непонятый параметр, чтобы больше не тратить на него запуски
                bad = self._features(c) - self.good_features
                if bad:
                    self.bad_features |= bad
                    self.on_log("winws не поддерживает " + ", ".join(sorted(b.replace("mode:", "")
                                                                                 for b in bad))
                                + " — варианты с этим пропускаются")
                else:
                    self.on_log(f"пропущен вариант (не поддерживается): {c.label}")
                return
            half = len(assign) // 2       # ищем виновника делением пополам
            self._screen_batch(st, assign[:half])
            self._screen_batch(st, assign[half:])
            return
        for _k, _h, _m, c in assign:
            self.good_features |= self._features(c)
        try:
            with ThreadPoolExecutor(max_workers=min(32, len(assign))) as ex:
                oks = list(ex.map(lambda a: self._screen_check(a[1], a[2], self.screen_timeout), assign))
        finally:
            self.zap.stop_process()
        for (k, host, _m, c), ok in zip(assign, oks):
            g, r = st[k]
            r.tried += 1
            if ok:
                if not hasattr(g, "passed"):
                    g.passed = []
                g.passed.append(c)
                if c.key() not in {f.key() for f in g.finalists}:
                    g.finalists.append(c)
                    self.log_detail(f"{g.title}: прошёл отсев — {c.label}")
                    self._share(st, k, c)

    def _share(self, st: dict, src: str, c: Candidate) -> None:
        """ТСПУ применяет одну логику ко всем заблокированным сайтам, поэтому вариант, сработавший
        в одной группе, почти наверняка подойдёт и другим — ставим его первым в их очередь."""
        for k, (g, r) in st.items():
            if k == src or g.done:
                continue
            keys = [x.key() for x in g.pool]
            if c.key() in keys[:g.pos]:
                continue                       # уже проверяли
            if c.key() in keys:
                g.pool.pop(keys.index(c.key()))
            g.pool.insert(g.pos, c)

    def _confirm(self, st: dict, rounds: int) -> None:
        """Полная проверка финалистов (TLS 1.2, TLS 1.3, обрыв 16 КБ) на всех адресах группы.
        Финалисты разных групп проверяются одновременно — их домены не пересекаются."""
        while not self.cancel.is_set():
            batch = []
            for k, (g, r) in st.items():
                if g.done or (r.winner and r.stability >= 1.0):
                    continue                     # уже есть вариант, прошедший 3 из 3 — хватит
                while g.cpos < len(g.finalists) and not batch_has(batch, k):
                    batch.append((k, g.finalists[g.cpos]))
                    g.cpos += 1
            if not batch:
                return
            passes = {k: 0 for k, _ in batch}
            times = {k: [] for k, _ in batch}
            why = {k: [] for k, _ in batch}
            oks = {k: 0 for k, _ in batch}
            tots = {k: 0 for k, _ in batch}
            tried_batch = list(batch)
            need_ok = 2 if rounds >= 3 else rounds         # сколько раз из rounds нужно пройти
            for i in range(rounds):
                if self.cancel.is_set():
                    return
                # кто уже не может набрать нужное — дальше не перепроверяем (ранний выход)
                batch = [(k, c) for k, c in batch if passes[k] + (rounds - i) >= need_ok]
                if not batch:
                    break
                self._tick("Перепроверка: " + "; ".join(f"{st[k][0].title} — {c.label}" for k, c in batch))
                sections = [["--filter-tcp=80,443",
                             "--hostlist-domains=" + ",".join(st[k][0].filter_domains or st[k][0].targets)]
                            + st[k][0].extra + c.args for k, c in batch]
                if not self._start(sections):
                    break
                try:
                    with ThreadPoolExecutor(max_workers=len(batch)) as ex:
                        probes = list(ex.map(lambda kc: probe_group(st[kc[0]][0], self.timeout, self.cancel), batch))
                finally:
                    self.zap.stop_process()
                for (k, _c), pr in zip(batch, probes):
                    oks[k] += pr.ok
                    tots[k] += pr.total
                    if pr.perfect:
                        passes[k] += 1
                        times[k].append(pr.time)
                    else:
                        why[k] += [f for f in pr.fails if f not in why[k]]
            for k, c in tried_batch:
                g, r = st[k]
                stab = passes[k] / rounds
                if stab >= 0.6:
                    avg = sum(times[k]) / len(times[k])
                    r.working.append((c, Probe(ok=1, total=1, time=avg)))
                    self.log_detail(f"{g.title}: подтверждён — {c.label} (прошёл {passes[k]} из {rounds})")
                    best = r.winner is None or (stab, -avg, -c.cost) > (r.stability, -g.best_time, -r.winner.cost)
                    if best:
                        r.winner, r.stability, g.best_time = c, stab, avg
                else:
                    frac = oks[k] / tots[k] if tots[k] else 0.0
                    g.partials.append((frac, c))          # «почти рабочие» — кандидаты на доводку
                    self.log_detail(f"{g.title}: не подтвердился — {c.label} (прошёл {passes[k]} из {rounds}, "
                                    f"проверок {round(frac * 100)}%)"
                                    + (f"; не прошло: {', '.join(why[k][:4])}" if why[k] else ""))
                self.on_group(r)

    def _harden(self, st: dict, bin_dir, rounds: int) -> None:
        """Победитель прошёл не все перепроверки — пробуем его «усиленные» версии и ближайших соседей
        и оставляем самый стабильный (у прошедшего 3 из 3 дальше ничего не крутим)."""
        todo = {}
        for k, (g, r) in st.items():
            if not r.winner or r.stability >= 1.0:
                continue
            seen, cands = set(), []
            for c in strengthen_candidates(r.winner, bin_dir) + refine_candidates(r.winner, bin_dir):
                if c.key() not in seen and self._supported(c):
                    seen.add(c.key())
                    cands.append(c)
            if not cands:
                continue
            g.pool, g.pos, g.cpos, g.finalists = cands[:30], 0, 0, []
            self.log_detail(f"{g.title}: найденное прошло {round(r.stability * 100)}% перепроверок — "
                            f"укрепляю ({len(g.pool)} вариантов)")
            todo[k] = (g, r)
        if todo and not self.cancel.is_set():
            self._screen(todo, 3)
            self._confirm(todo, rounds)
            for k, (g, r) in todo.items():
                self.log_detail(f"{g.title}: после укрепления — {r.winner.label} "
                                f"({round(r.stability * 100)}% перепроверок)")

    def _near_miss(self, st: dict, bin_dir, rounds: int) -> None:
        todo = {}
        for k, (g, r) in st.items():
            if r.winner or g.done:
                continue
            best = sorted(g.partials, key=lambda t: -t[0])
            seeds = [c for frac, c in best if frac >= 0.5][:2]
            if not seeds:
                continue
            cands, seen = [], set()
            for sd in seeds:
                for c in near_miss_candidates(sd, bin_dir):
                    if c.key() not in seen and self._supported(c):
                        seen.add(c.key())
                        cands.append(c)
            g.pool, g.pos, g.cpos, g.finalists = cands, 0, 0, []
            g.partials = [t for t in g.partials if t[1] not in seeds]
            self.log_detail(f"{g.title}: доводка почти рабочих ({', '.join(s.label for s in seeds)}) — "
                            f"{len(cands)} соседних вариантов")
            todo[k] = (g, r)
        if todo and not self.cancel.is_set():
            self._screen(todo, 3)
            self._confirm(todo, rounds)
            self._harden({k: v for k, v in todo.items() if v[1].winner}, bin_dir, rounds)
            for k, (g, r) in todo.items():
                if r.winner:
                    g.done = True

    def run(self) -> dict[str, GroupResult]:
        _PIN.clear()                              # адреса могли смениться с прошлого подбора
        groups = default_groups()
        st = self.zap.status()
        prev_service = st.service_state == "RUNNING"
        prev_session = st.strategy if st.source in ("app", "external") else ""
        ts = winutil.tcp_timestamps() is not False
        # готовые стратегии в поиске НЕ участвуют: приём собирается из «кирпичиков» с нуля,
        # чтобы результат не был копией существующей стратегии (они нужны только для сравнения в конце)
        named: list = []
        bin_dir = getattr(self.zap, "bin_dir", None)
        state: dict = {}
        for k in self.keys:
            g = groups[k]
            self.results[k] = GroupResult(k, g.title, targets=list(g.targets))
            g.stages = build_pool(k, named, ts, self.thorough, bin_dir)
            g.pool, g.pos, g.cpos = [], 0, 0
            g.finalists, g.probes_ready, g.done, g.best_time = [], [], False, 0.0
            g.passed = []
            g.partials = []
            state[k] = (g, self.results[k])
        rounds = 3
        try:
            self.zap.stop_all(unload_driver=False)
            if "sites" in state:
                g, r = state["sites"]
                g.targets = self._pick_sites()
                g.filter_domains = list(g.targets)
                g.big = set(g.targets)
                g.probes = list(g.targets)
                r.targets = list(g.targets)
            self._tick("Проверка без обхода: что и как заблокировано")
            self._baseline(state)
            active = {}
            for k, (g, r) in state.items():
                main_blocked = set(g.targets) & g.blocked
                if not g.probes_ready or not main_blocked:
                    r.state = "не блокируется"
                    if k == "sites" and not g.targets:
                        r.note = "среди сайтов из списка не нашлось заблокированных"
                    elif g.probes_ready:
                        # основные адреса (их проверяет автотест) открываются; закрыты только второстепенные —
                        # скорее всего по IP-адресу, обход такое не пробивает, и тратить время незачем
                        r.note = ("основные адреса открываются без обхода; заблокированы только "
                                  + ", ".join(h for h, _m in g.probes_ready))
                    else:
                        r.note = "открывается и без обхода"
                    self.on_group(r)
                    continue
                r.state = "проверка"
                r.baseline = f"Без обхода заблокировано адресов: {len(g.probes_ready)}"
                log.detail(f"{g.title}: заблокированы " + ", ".join(f"{h} ({m})" for h, m in g.probes_ready))
                self.on_group(r)
                active[k] = (g, r)
            total_variants = sum(sum(len(c) for _n, c in g.stages) for g, _ in active.values())
            per_round = max(1, sum(len(g.probes_ready) for g, _ in active.values()))
            self._total = 3 + total_variants // per_round + 8 * rounds
            n_stages = max((len(g.stages) for g, _ in active.values()), default=0)

            # идём по этапам (метод → фейк → seqovl → искажение → TTL); после каждого подтверждаем.
            for si in range(n_stages):
                if not active or self.cancel.is_set():
                    break
                round_active = {}
                for k, (g, r) in active.items():
                    if g.done or si >= len(g.stages):
                        continue
                    name, cands = g.stages[si]
                    g.pool = [c for c in cands if self._supported(c)]
                    g.pos, g.cpos, g.finalists = 0, 0, []
                    self.log_detail(f"{g.title}: этап «{name}» — {len(g.pool)} вариантов")
                    round_active[k] = (g, r)
                if not round_active:
                    continue
                self._screen(round_active, 6 if self.thorough else 3)
                self._confirm(round_active, rounds)
                # нашли рабочий — пробуем докрутить до большей стабильности, затем группа готова
                found = {k: v for k, v in round_active.items() if v[1].winner and not v[0].done}
                self._harden(found, bin_dir, rounds)
                for k, (g, r) in found.items():
                    g.done = True
                active = {k: v for k, v in active.items() if not v[0].done}

            # доводка «почти рабочих»: варианты, прошедшие большую часть проверок, улучшаем соседними
            # значениями осей — дешевле и точнее, чем перебирать всё заново
            self._near_miss(active, bin_dir, rounds)
            active = {k: v for k, v in active.items() if not v[0].done}

            # комбинаторный добор: вокруг фейков/методов, что хоть раз прошли отсев, крутим
            # повторы, seqovl, искажение и TTL ВМЕСТЕ — так находятся редкие рабочие сочетания
            combo = {k: v for k, v in active.items() if not v[0].done}
            if combo and not self.cancel.is_set():
                allpassed = []
                for _k, (g, _r) in state.items():           # находки любых групп — ТСПУ один на всех
                    allpassed += getattr(g, "passed", [])
                fakes = promising_fakes(allpassed)
                desyncs = promising_desyncs(allpassed)
                for k, (g, r) in combo.items():
                    cands = [c for c in combo_stage(fakes, desyncs, ts, bin_dir, self.thorough)
                             if self._supported(c)]
                    g.pool, g.pos, g.cpos, g.finalists = cands, 0, 0, []
                    self.log_detail(f"{g.title}: комбинаторный добор — {len(cands)} вариантов "
                                    f"(фейки: {len(fakes)}, методы: {len(desyncs)})")
                # если в группе ни один вариант не прошёл даже отсев — вероятна блокировка по IP:
                # ограничиваем добор парой минут, а не перебираем сотни вариантов
                hopeless = all(not getattr(g, "passed", []) for g, _r in combo.values())
                if hopeless:
                    self.log_detail("ни один вариант не прошёл даже отсев — добор ограничен (похоже на блокировку по IP)")
                self._screen(combo, 4 if self.thorough else 2, max_rounds=60 if hopeless else 80)
                self._confirm(combo, rounds)
                self._near_miss({k: v for k, v in combo.items() if not v[1].winner}, bin_dir, rounds)
                self._harden({k: v for k, v in combo.items() if v[1].winner and not v[0].done}, bin_dir, rounds)
                for k, (g, r) in combo.items():
                    g.done = True

            for k, (g, r) in state.items():
                if r.state == "проверка":
                    r.state = "найдено" if r.winner else ("остановлено" if self.cancel.is_set() else "не найдено")
                    if not r.winner and not self.cancel.is_set():
                        r.note = "ни один вариант не открыл все проверки — возможно, блокировка по IP"
                        if self.inherit_template and self.best_existing:
                            r.note += (f". В итоговую стратегию для этой группы взяты приёмы из "
                                       f"«{self.best_existing[0]}»")
                    self.on_group(r)
            if not self.cancel.is_set() and any(r.winner or groups[k].partials for k, r in self.results.items()):
                self._finalize(groups, bin_dir)
            if not self.cancel.is_set() and any(r.winner for r in self.results.values()):
                self._compare(groups)
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
        self.on_progress("", self._total, self._total)
        return self.results

    def _full_probe(self, template: str, g: Group, rounds: int = 2) -> Probe:
        """Запустить стратегию целиком (как при обычном включении) и проверить цели."""
        from .strategy import Strategy
        total = Probe()
        for _ in range(rounds):
            if self.cancel.is_set():
                break
            try:
                self.zap.start(Strategy("сравнение", self.zap.root / "_compare.bat", template), wait=2.5)
                time.sleep(0.5)
                pr = probe_group(g, self.timeout, self.cancel)
            except Exception as e:  # noqa: BLE001
                self.on_log(f"сравнение: не удалось запустить стратегию — {e}")
                pr = Probe(total=1)
            finally:
                self.zap.stop_process()
            total.ok += pr.ok
            total.total += pr.total
            total.fails += [f for f in pr.fails if f not in total.fails]
        return total

    def _autotest_group(self, groups: dict) -> Optional[Group]:
        """Те же цели, что в автотесте (Discord, YouTube, Google, Cloudflare…), плюс сайты из подбора —
        чтобы итог совпадал с тем, что потом покажет автотест."""
        from .tester import load_targets
        targets, big = [], set()
        for t in load_targets():
            if not t.ping_only and t.host not in targets:
                targets.append(t.host)
                big.add(t.host)
        self._pin_with_donors(targets, list(groups.values()))
        targets = [h for h in targets if _PIN.get(h)]
        big = {h for h in big if _PIN.get(h)}
        for k in self.keys:
            g = groups[k]
            targets += [t for t in g.targets if t not in targets]
            big |= g.big
        return Group("compare", "Сравнение", targets, [], big) if targets else None

    @staticmethod
    def _host_group(host: str, groups: dict) -> Optional[str]:
        for k, g in groups.items():
            if any(host == d or host.endswith("." + d) for d in (g.filter_domains or g.targets)):
                return k
        return None

    def _finalize(self, groups: dict, bin_dir) -> None:
        """Итоговая доводка по образцу автотеста: стратегия проверяется ЦЕЛИКОМ; если какая-то группа там
        проваливается, пробуем её запасные варианты и ближайших соседей прямо в составе стратегии."""
        allg = self._autotest_group(groups)
        if not allg:
            return
        self._tick("Итоговая проверка: стратегия целиком, как в автотесте")
        pr = self._full_probe(self.strategy_template(), allg, rounds=1)
        bad_hosts = {f.split(" ", 1)[0] for f in pr.fails}
        bad_groups = {self._host_group(h, groups) for h in bad_hosts} & set(self.results)
        if not bad_groups:
            self.log_detail(f"итоговая проверка: всё прошло ({pr.ok} из {pr.total})")
            return
        for k in bad_groups:
            if self.cancel.is_set():
                return
            g, r = groups[k], self.results[k]
            hosts = [h for h in allg.targets if self._host_group(h, groups) == k]
            sub = Group(k, g.title, hosts, [], {h for h in allg.big if h in hosts})
            seed = r.winner or (max(g.partials, key=lambda t: t[0])[1] if g.partials else None)
            if seed is None:
                continue
            alts = [c for c, _p in r.working if c is not r.winner]
            alts += strengthen_candidates(seed, bin_dir)              # сначала — «усиление» приёма
            alts += near_miss_candidates(seed, bin_dir, limit=12)
            uniq, seen_k = [], set()
            for c in alts:
                if c.key() not in seen_k and self._supported(c):
                    seen_k.add(c.key())
                    uniq.append(c)
            alts = uniq[:24]
            self.log_detail(f"{g.title}: в стратегии целиком не прошло "
                            + ", ".join(sorted(h for h in bad_hosts if h in hosts))
                            + f" — пробую {len(alts)} вариантов прямо в составе стратегии")
            cur = r.winner
            base = self._full_probe(self.strategy_template(), sub, rounds=1) if cur else Probe(total=1)
            best_c, best_score = cur, base.score
            for alt in alts:
                if self.cancel.is_set() or best_score >= 1.0:
                    break
                r.winner = alt
                self._tick(f"Итоговая доводка — {g.title}: {alt.label}")
                p1 = self._full_probe(self.strategy_template(), sub, rounds=1)
                if p1.score > best_score:
                    if p1.perfect:                         # убедимся, что не случайность
                        p2 = self._full_probe(self.strategy_template(), sub, rounds=1)
                        score = (p1.ok + p2.ok) / max(1, p1.total + p2.total)
                    else:
                        score = p1.score
                    if score > best_score:
                        best_c, best_score = alt, score
            if cur is None and best_score < 0.9:
                best_c = None                       # без победителя посредственный вариант не ставим
            r.winner = best_c
            if best_c is not cur:
                r.stability = max(r.stability, best_score)
                if r.state != "найдено":
                    r.state = "найдено"
                    r.note = ""
                self.log_detail(f"{g.title}: в стратегии целиком лучше всего — {best_c.label} "
                                f"({round(best_score * 100)}% проверок)")
            self.on_group(r)

    def _compare(self, groups: dict):
        """Честное сравнение: подобранная стратегия против лучшей по автотесту, в одних условиях."""
        g = self._autotest_group(groups)
        if not g:
            return
        self._tick("Сравнение с вашей лучшей стратегией: подобранная")
        new = self._full_probe(self.strategy_template(), g)
        self.compare = {"new": (new.ok, new.total)}
        if self.best_existing:
            name, tpl = self.best_existing
            self._tick(f"Сравнение с вашей лучшей стратегией: {name}")
            old = self._full_probe(tpl, g)
            self.compare["best"] = (name, old.ok, old.total)
            self.on_log(f"сравнение: подобранная {new.ok} из {new.total}, «{name}» {old.ok} из {old.total}")
            # группа не подобрана, и лучшая готовая стратегия её сейчас тоже не открывает — значит, дело не
            # в подборе: провайдер сейчас блокирует жёстче (так бывает в часы нагрузки) или блокирует по IP
            old_bad = {f.split(" ", 1)[0] for f in old.fails}
            for k, r in self.results.items():
                hosts = [h for h in g.targets if self._host_group(h, groups) == k]
                if r.winner or not hosts or not all(h in old_bad for h in hosts):
                    continue
                r.note = (f"Даже ваша лучшая стратегия «{name}» сейчас не открывает {r.title}. Похоже, провайдер "
                          "сейчас блокирует жёстче обычного (так бывает в часы нагрузки) или закрыл адреса по IP. "
                          "Повторите подбор позже.")
                self.log_detail(f"{r.title}: лучшая готовая стратегия «{name}» сейчас тоже не открывает эту группу")
                self.on_group(r)
        else:
            self.on_log(f"проверка подобранной стратегии целиком: {new.ok} из {new.total}")

    def strategy_template(self) -> str:
        inherit = section_args(self.inherit_template) if self.inherit_template else {}
        return compose({k: r.winner for k, r in self.results.items()}, inherit)


def resolve_ctx_template(template: str, ctx: ResolveContext) -> str:
    return resolve(template, ctx)
