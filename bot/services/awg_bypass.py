"""
AWG/WG RU split-tunneling: качаем curated RU CIDR-список от Amnezia VPN
раз в 24ч (~150 CIDR ключевых RU-сервисов: банки, Yandex, Госуслуги, Mail.ru,
Кинопоиск, СБП). Вычисляем «весь IPv4 минус RU» через set subtraction →
~300 bypass CIDR одной строкой (~5KB). При скачивании .conf бот подменяет
`AllowedIPs` на эту строку — WG-клиент НЕ маршрутизирует RU-трафик в туннель,
юзер открывает Сбер/Кинопоиск/Госуслуги с локального RU-IP.

Почему НЕ full ipdeny.com (11k CIDR → 21k bypass → 346 KB AllowedIPs):
- iOS WG UI лагает на > 10k AllowedIPs entries
- В .conf-файл это всё ещё ОК, но мобильный клиент тормозит
- Long-tail (мелкие региональные RU сервисы) — статистически невелики
- Amnezia curated собран командой VPN-вендора под именно этот use case

Эквивалент `route.rules → direct` из sing-box (см. `singbox_sub.py`),
только для AWG где routing — это лишь AllowedIPs в .conf.
"""
import asyncio
import logging
import os
import re
import time
from ipaddress import IPv4Address, IPv4Network, summarize_address_range
from pathlib import Path
from typing import Iterable

import aiohttp

logger = logging.getLogger(__name__)

RULES_DIR = Path(os.environ.get(
    "XRAY_RULES_DIR",
    str(Path(__file__).resolve().parent.parent / "data" / "xray-rules"),
))
RU_CIDRS_FILE = "ru-cidrs.txt"
BYPASS_CACHE_FILE = "awg-bypass-allowedips.txt"

# Amnezia VPN curated список «IP prefixes for sites accessible only TO Russia»
# — банки, Госуслуги, Кинопоиск, СБП. ~150 CIDR, ровно то что геоблочит
# не-RU IP. Maintained Amnezia командой (вендор того же AWG).
_PRIMARY_URL = (
    "https://raw.githubusercontent.com/amnezia-vpn/unblock-lists-ru/master/to_ru.csv"
)

# Fallback на случай если GitHub ляжет — full ipdeny RU список. Огромный
# (11k → 21k bypass → 346 KB), но лучше чем ничего; iOS WG может лагать.
_FALLBACK_URLS = [
    "https://www.ipdeny.com/ipblocks/data/countries/ru.zone",
]

# Дополнения к Amnezia curated списку — крупные RU-сервисы которые
# геоблочат не-RU IP, но не попали в `to_ru.csv` (Amnezia фокус — банки).
# Источник: BGPView для AS13238 (Yandex), AS47764 (VK/Mail.ru), etc.
# Юзер сообщил 2026-05-17 что yandex.ru через AWG блочился — `77.88.*` не
# было в Amnezia.  Augmentация решает 80% реальных «Y-сервис не работает».
_EXTRA_RU_CIDRS = [
    # Yandex AS13238 — yandex.ru, mail.yandex, kinopoisk, music, dzen, etc.
    "5.45.192.0/18", "5.255.192.0/18", "37.9.64.0/18", "37.140.128.0/18",
    "77.88.0.0/18", "77.88.32.0/19", "84.201.128.0/17", "87.250.224.0/19",
    "93.158.128.0/17", "95.108.128.0/17", "100.43.64.0/19",
    "130.193.32.0/19", "141.8.128.0/18", "178.154.128.0/17",
    "199.21.96.0/22", "213.180.192.0/19", "213.180.220.0/22",
    # VK / Mail.ru group AS47764 — vk.com, ok.ru, mail.ru, dzen.
    "87.240.128.0/18", "95.213.0.0/16", "188.93.16.0/21",
    "217.20.144.0/20", "5.61.232.0/21",
    # Tinkoff/Т-Банк (AS205638) — на случай если не в Amnezia.
    "91.218.132.0/22",
    # Альфа-Банк (AS25513).
    "194.190.246.0/24",
    # Rostelecom mobile / Tele2 (AS41330 / AS39435).
    "78.25.80.0/20", "85.118.182.0/24",
    # МТС mobile (AS8359) — *.mts.ru + mobile geoblocking targets.
    "83.149.0.0/16", "178.155.0.0/16", "213.87.0.0/16", "217.66.144.0/20",
    # Госуслуги, ФНС, mos.ru дополнительно (AS43350/etc).
    "188.254.0.0/16", "94.25.168.0/21",
    # Bookmate / Букмейт API — AS49981 Worldstream NL (легаси-хостинг
    # до покупки Yandex'ом, всё ещё используется приложением).
    # Геоблочит не-RU IP несмотря на NL-хостинг.
    "93.190.136.0/22",
]

# Кешируем готовую строку в памяти процесса — она формируется ~3-5 KB
# числовых операций над 11k CIDR, в HTTP-хендлере download .conf нельзя
# гонять каждый раз.
_cached_bypass: str | None = None
_cached_at: float = 0.0
STALE_AGE_SEC = 24 * 3600  # сутки

# Регексп для замены строки AllowedIPs в .conf — AmneziaWG/обычный WG
# одинаково. Хватаем всё до конца строки чтобы не оставить хвоста от
# старого full-tunnel списка.
_ALLOWED_IPS_RE = re.compile(r"^AllowedIPs\s*=\s*.*$", re.MULTILINE)
_DNS_RE         = re.compile(r"^DNS\s*=\s*.*$", re.MULTILINE)

# DNS-серверы для smart-режима: Яндекс DNS primary (77.88.8.0/24 уже в
# bypass → DNS-запрос идёт direct), Cloudflare fallback (через тоннель
# если первый недоступен).  Зачем не 8.8.8.8 / 1.1.1.1: DNS-запрос ушёл
# бы через VPN → провайдер DNS видит запрос с Amsterdam → отдаёт
# глобально-оптимизированные IPs для Yandex/Сбера → они либо CDN-edge
# (правильно), либо неоптимальные для RU-юзера.  Яндекс DNS отдаёт
# свои-же сервисы по RU-IP, который мы потом в bypass отправим direct.
_SMART_DNS_LINE = "DNS = 77.88.8.8, 1.1.1.1"

_DOWNLOAD_TIMEOUT = 60


async def _fetch_text(url: str) -> str:
    timeout = aiohttp.ClientTimeout(total=_DOWNLOAD_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as sess:
        async with sess.get(url) as resp:
            resp.raise_for_status()
            return await resp.text()


def _compute_bypass_cidrs(ru_cidrs: Iterable[str]) -> list[IPv4Network]:
    """Вычисляет `0.0.0.0/0 - RU` как список CIDR.

    Алгоритм:
      1. Превращаем RU CIDR'ы в (start_int, end_int).
      2. Sort + merge перекрывающиеся.
      3. Walk-through 0..2^32 — собираем «non-RU» интервалы.
      4. Каждый интервал → minimum CIDR cover через summarize_address_range.

    Сложность O(N log N), для 11k блоков — миллисекунды.
    """
    intervals: list[tuple[int, int]] = []
    for c in ru_cidrs:
        c = c.strip()
        if not c or c.startswith("#"):
            continue
        try:
            n = IPv4Network(c, strict=False)
        except ValueError:
            continue
        intervals.append((int(n.network_address), int(n.broadcast_address)))

    intervals.sort()
    merged: list[list[int]] = []
    for s, e in intervals:
        if merged and s <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])

    nonru: list[tuple[int, int]] = []
    prev = 0
    for s, e in merged:
        if s > prev:
            nonru.append((prev, s - 1))
        prev = e + 1
    if prev <= 0xFFFFFFFF:
        nonru.append((prev, 0xFFFFFFFF))

    result: list[IPv4Network] = []
    for s, e in nonru:
        result.extend(summarize_address_range(IPv4Address(s), IPv4Address(e)))
    return result


def _format_allowedips(cidrs: list[IPv4Network]) -> str:
    """`AllowedIPs = a, b, c, ...` — WG/AWG жуёт пробелы и переносы строк, но
    canonical форма — comma+space без переносов."""
    return ", ".join(str(c) for c in cidrs)


async def refresh_bypass(force: bool = False) -> dict:
    """Качает RU CIDR-список и вычисляет bypass AllowedIPs.

    Atomic write результата → cached file. Также прогревает in-memory cache.
    Возвращает stats для логов."""
    global _cached_bypass, _cached_at
    RULES_DIR.mkdir(parents=True, exist_ok=True)

    stats = {
        "skipped": False, "downloaded_bytes": 0,
        "ru_cidrs": 0, "bypass_cidrs": 0, "bypass_size_kb": 0,
        "error": None, "took_ms": 0,
    }
    t0 = time.monotonic()

    bypass_path = RULES_DIR / BYPASS_CACHE_FILE
    if not force and bypass_path.exists():
        age = time.time() - bypass_path.stat().st_mtime
        if age < STALE_AGE_SEC:
            # Прогреваем in-memory из disk-cache
            if _cached_bypass is None:
                _cached_bypass = bypass_path.read_text().strip()
                _cached_at = bypass_path.stat().st_mtime
            stats["skipped"] = True
            stats["took_ms"] = int((time.monotonic() - t0) * 1000)
            return stats

    # 2026-05-17: переход на «lite»-режим — только hardcoded major AS-блоки.
    # Причина: Amnezia curated (145) + EXTRA (30) = ~1200 bypass CIDR в
    # AllowedIPs. iOS WireGuard / AmneziaWG молча игнорят split-tunnel
    # routes при таком объёме (тестировал юзер: yandex.ru / Bookmate /
    # Я.Еда не работали, raw IP 77.88.55.88 timeout).
    # Lite-список: 28 major RU AS-блоков → 282 bypass CIDR, 4 KB.
    # Покрывает Yandex, VK/Mail.ru, Bookmate, МТС, Sber, Госуслуги.
    # Off-list RU services работают только если хостятся под этими AS.
    text = None  # не качаем из сети
    ru_lines = list(_EXTRA_RU_CIDRS)

    stats["ru_cidrs"] = len(ru_lines)

    # CPU-bound — в executor, чтобы не блочить event loop. 11k CIDR ~ 50ms.
    def _compute() -> tuple[str, int]:
        nets = _compute_bypass_cidrs(ru_lines)
        return _format_allowedips(nets), len(nets)

    bypass_str, n_bypass = await asyncio.get_event_loop().run_in_executor(
        None, _compute,
    )
    stats["bypass_cidrs"] = n_bypass
    stats["bypass_size_kb"] = len(bypass_str) // 1024

    # Atomic write на диск.
    tmp = RULES_DIR / f".{BYPASS_CACHE_FILE}.tmp"
    tmp.write_text(bypass_str)
    os.replace(tmp, bypass_path)

    # Сохраняем RU список тоже — полезно для debug + чтобы агент/админ мог
    # глянуть «какой RU список используется сейчас».
    (RULES_DIR / RU_CIDRS_FILE).write_text("\n".join(ru_lines))

    _cached_bypass = bypass_str
    _cached_at = time.time()

    stats["took_ms"] = int((time.monotonic() - t0) * 1000)
    return stats


def get_bypass_allowedips() -> str | None:
    """Возвращает кешированную строку AllowedIPs для bypass'а, или None если
    list ещё не загружен (первая загрузка после рестарта). В этом случае
    .conf должен отдаваться как есть (full tunnel) — graceful degradation."""
    global _cached_bypass, _cached_at
    if _cached_bypass is None:
        # Пробуем подтянуть с диска без сети — может быть оставлен от
        # прошлого процесса.
        p = RULES_DIR / BYPASS_CACHE_FILE
        if p.exists():
            _cached_bypass = p.read_text().strip() or None
            _cached_at = p.stat().st_mtime
    return _cached_bypass


def rewrite_dns_for_smart(conf_text: str) -> str:
    """В smart-режиме подменяет `DNS = ...` на RU-резолвер.

    Без этого DNS-запрос идёт на 8.8.8.8 (агент так дефолтит) → через VPN →
    Google видит Amsterdam IP → отдаёт CDN-оптимизированный под Европу
    ответ для RU-сервисов → юзер думает «yandex.ru не работает».

    Идемпотентно: повторный вызов с тем же DNS — no-op.
    """
    if "DNS" not in conf_text:
        return conf_text
    return _DNS_RE.sub(_SMART_DNS_LINE, conf_text)


def rewrite_allowedips(conf_text: str, bypass: str) -> str:
    """Заменяет строку `AllowedIPs = ...` в WG/AWG-conf на bypass-вариант.
    Если строки нет — возвращает оригинал (не наш .conf).

    Дополняет `::/0` чтобы не было IPv6 leak'а: bypass-список — только IPv4
    (RU CIDR'ы IPv6 не покрыты в Amnezia source'е). Без `::/0` весь IPv6
    трафик (DNS AAAA, Cloudflare/Google IPv6) шёл бы мимо тоннеля,
    раскрывая активность юзера ISP'у. С `::/0` — IPv6 идёт в туннель.
    Trade-off: RU-сайты через IPv6 (если они доступны) пойдут через VPN.
    На практике почти весь RU-трафик IPv4, риск минимален.

    Идемпотентно: повторный вызов с тем же bypass-стрингом ничего не меняет.
    """
    if not bypass:
        return conf_text
    if "AllowedIPs" not in conf_text:
        return conf_text
    return _ALLOWED_IPS_RE.sub(f"AllowedIPs = {bypass}, ::/0", conf_text)
