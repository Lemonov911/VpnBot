"""
AWG/WG RU split-tunneling: качаем RU CIDR-список от ipdeny.com раз в 24ч,
вычисляем «весь IPv4 минус RU» (`0.0.0.0/0 - 11k RU блоков` → ~6-7k не-RU
CIDR), кешируем строку. При скачивании .conf бот подменяет `AllowedIPs`
на эту строку — WG-клиент НЕ маршрутизирует RU-трафик в туннель, юзер
открывает Сбер/Кинопоиск/Госуслуги с локального RU-IP.

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

# ipdeny.com — public GeoIP CIDR в plain-text. ~11k RU блоков, обновляется
# ежедневно. Без аутентификации, давно живёт, многими используется.
_IPDENY_URL = "https://www.ipdeny.com/ipblocks/data/countries/ru.zone"

# Тоже самое только free-mirror на случай если ipdeny ляжет.
_FALLBACK_URLS = [
    "https://raw.githubusercontent.com/herrbischoff/country-ip-blocks/master/ipv4/ru.cidr",
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

    text: str | None = None
    for url in [_IPDENY_URL, *_FALLBACK_URLS]:
        try:
            text = await _fetch_text(url)
            stats["downloaded_bytes"] = len(text)
            break
        except Exception as e:
            logger.warning("awg-bypass: %s failed: %s", url, e)
            continue

    if text is None:
        stats["error"] = "all sources failed"
        stats["took_ms"] = int((time.monotonic() - t0) * 1000)
        return stats

    ru_lines = [l for l in text.splitlines() if l.strip() and not l.startswith("#")]
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
    (RULES_DIR / RU_CIDRS_FILE).write_text(text)

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


def rewrite_allowedips(conf_text: str, bypass: str) -> str:
    """Заменяет строку `AllowedIPs = ...` в WG/AWG-conf на bypass-вариант.
    Если строки нет — возвращает оригинал (не наш .conf).

    Идемпотентно: повторный вызов с тем же bypass-стрингом ничего не меняет.
    """
    if not bypass:
        return conf_text
    if "AllowedIPs" not in conf_text:
        return conf_text
    return _ALLOWED_IPS_RE.sub(f"AllowedIPs = {bypass}", conf_text)
