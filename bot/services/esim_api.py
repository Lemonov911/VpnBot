from __future__ import annotations
"""
eSIM Access API client (esimaccess.com).

Pricing model:
  wholesale price: целое число в 1/10000 USD (18000 = $1.80 wholesale)
  markup        : 2.2× от wholesale (retail у esimaccess = ×2, +10% буфер на Stars-комиссию)
  Stars         : 1⭐ ≈ $0.02 (текущий курс Telegram)
  RUB           : фиксируем 95 ₽/$ — закладываем 14% буфер от рыночного

  Примеры:
    wholesale $2.30 (Turkey 5GB) → retail $5.06 → 480 ₽ → 253⭐

eSIM async flow:
  1. POST /esim/order → возвращает orderNo (профиль ещё не готов)
  2. ~5–30 сек SM-DP+ аллоцирует профиль
  3. либо webhook ORDER_STATUS, либо polling /esim/query?orderNo=
  4. в ответе esimList[] с iccid / ac / qrCodeUrl / shortUrl

Primary key для eSIM-профилей — esimTranNo (iccid переиспользуется операторами!).
"""

import asyncio
import json
import logging
import time
from math import ceil

import aiohttp

from config import ESIM_API_KEY

logger = logging.getLogger(__name__)

BASE = "https://api.esimaccess.com/api/v1/open"

# Pricing
MARKUP       = 2.2
UNIT2USD     = 1 / 10_000
STAR_USD     = 0.02
RUB_PER_USD  = 95.0

# MVP-страны: только эти показываем в UI
MVP_LOCATIONS = {"TR", "GE", "AE", "TH", "VN", "EU-42", "RU"}

# Исключаем технические варианты — пусть пользователь видит только основной набор
SKIP_SLUG_TOKENS = ("_nonhkip", "_1Mbps")


# ── HTTP layer ────────────────────────────────────────────────────────────────
# Shared session — переиспользует TCP/TLS соединения. Без него каждый _post
# делает full handshake (~100-200ms overhead на запрос). При polling order
# raz в 5 сек × 6 попыток = 1+ секунды лишних handshake'ов.
_session: aiohttp.ClientSession | None = None
_session_lock = asyncio.Lock()


async def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        async with _session_lock:
            if _session is None or _session.closed:
                connector = aiohttp.TCPConnector(ssl=False, limit=10)
                _session = aiohttp.ClientSession(connector=connector)
    return _session


async def _post(endpoint: str, body: dict, timeout: int = 60) -> dict:
    headers = {"RT-AccessCode": ESIM_API_KEY, "Content-Type": "application/json"}
    session = await _get_session()
    async with session.post(
        f"{BASE}{endpoint}", json=body, headers=headers,
        timeout=aiohttp.ClientTimeout(total=timeout),
    ) as r:
        text = await r.text()
        try:
            return json.loads(text)
        except Exception as exc:
            logger.error("eSIM API JSON parse error: %s | body: %.200s", exc, text)
            return {}


# ── Catalog cache ─────────────────────────────────────────────────────────────

_cache: list[dict] = []
_cache_expires: float = 0.0
_cache_lock = asyncio.Lock()


async def _all_packages() -> list[dict]:
    global _cache, _cache_expires
    if time.time() < _cache_expires and _cache:
        return _cache
    async with _cache_lock:
        if time.time() < _cache_expires and _cache:
            return _cache
        logger.info("eSIM: обновляем кеш пакетов...")
        data = await _post("/package/list", {})
        _cache = (data.get("obj") or {}).get("packageList") or []
        _cache_expires = time.time() + 3600
        logger.info("eSIM: кеш обновлён, пакетов: %d", len(_cache))
        return _cache


async def warm_cache():
    try:
        await _all_packages()
    except Exception as e:
        logger.warning("eSIM cache warm-up failed: %r", e)


# ── Pricing helpers ───────────────────────────────────────────────────────────

def usd_for(price_units: int) -> float:
    """Wholesale units → USD цена клиенту."""
    return price_units * UNIT2USD * MARKUP


def rub_for(price_units: int) -> int:
    """Wholesale units → ₽ для клиента, округлено до 10."""
    rub = usd_for(price_units) * RUB_PER_USD
    return int(ceil(rub / 10) * 10)


def stars_for(price_units: int) -> int:
    """Wholesale units → Telegram Stars (для invoice)."""
    return max(1, ceil(usd_for(price_units) / STAR_USD))


def fmt_bytes(b: int) -> str:
    gb = b / 1_073_741_824
    if gb < 1:
        return f"{round(gb * 1024)} MB"
    return f"{int(gb)} GB" if gb == int(gb) else f"{gb:.1f} GB"


def _is_visible_pkg(p: dict) -> bool:
    """Скрываем технические варианты (HK-IP routing, FUP-1Mbps)."""
    slug = p.get("slug", "")
    return not any(tok in slug for tok in SKIP_SLUG_TOKENS)


# ── Public catalog API ────────────────────────────────────────────────────────

# Карта ISO-кодов → флаги/русские названия для красивого UI.
COUNTRY_DISPLAY = {
    "TR":    {"flag": "🇹🇷", "name_ru": "Турция",       "name_en": "Turkey"},
    "GE":    {"flag": "🇬🇪", "name_ru": "Грузия",       "name_en": "Georgia"},
    "AE":    {"flag": "🇦🇪", "name_ru": "ОАЭ",          "name_en": "UAE"},
    "TH":    {"flag": "🇹🇭", "name_ru": "Таиланд",      "name_en": "Thailand"},
    "VN":    {"flag": "🇻🇳", "name_ru": "Вьетнам",      "name_en": "Vietnam"},
    "EU-42": {"flag": "🇪🇺", "name_ru": "Европа (42 страны)", "name_en": "Europe (42 countries)"},
    "RU":    {"flag": "🇷🇺", "name_ru": "Россия",       "name_en": "Russia"},
}


async def get_countries() -> list[dict]:
    """MVP-страны с количеством доступных тарифов. RU помечена флагом is_russia."""
    pkgs = await _all_packages()
    counts: dict[str, int] = {}
    for p in pkgs:
        if not _is_visible_pkg(p):
            continue
        code = p.get("location") or p.get("locationCode") or ""
        if code in MVP_LOCATIONS:
            counts[code] = counts.get(code, 0) + 1

    out = []
    for code in MVP_LOCATIONS:
        if code not in counts:
            continue
        disp = COUNTRY_DISPLAY.get(code, {})
        out.append({
            "code":      code,
            "name":      disp.get("name_ru", code),
            "name_en":   disp.get("name_en", code),
            "flag":      disp.get("flag", "🌍"),
            "count":     counts[code],
            "is_russia": code == "RU",
        })
    # Сортировка: сначала туристические, потом RU отдельной группой
    out.sort(key=lambda c: (c["is_russia"], c["name"]))
    return out


def _enrich_pkg(p: dict) -> dict:
    """Wholesale-пакет → структура для UI с RUB/USD/Stars."""
    price = p.get("price", 0)
    return {
        "packageCode":  p["packageCode"],
        "slug":         p.get("slug", ""),
        "name":         p.get("name", ""),
        "location":     p.get("location") or p.get("locationCode") or "",
        "volume":       p.get("volume", 0),
        "dataLabel":    fmt_bytes(p.get("volume", 0)),
        "dataType":     p.get("dataType", 1),  # 1=Total, 2=Daily-FUP-slow
        "duration":     p.get("duration", 0),
        "durationUnit": (p.get("durationUnit") or "DAY").capitalize(),
        "speed":        p.get("speed", ""),
        "ipExport":     p.get("ipExport", ""),
        "fupPolicy":    p.get("fupPolicy", ""),
        # Цены
        "price":        price,            # wholesale units (для invoice payload)
        "priceRub":     rub_for(price),
        "priceUsd":     round(usd_for(price), 2),
        "stars":        stars_for(price),
    }


async def get_packages_for(location_code: str) -> list[dict]:
    """Все видимые пакеты для страны, отсортированы по объёму/сроку."""
    pkgs = await _all_packages()
    out = [
        _enrich_pkg(p) for p in pkgs
        if (p.get("location") or p.get("locationCode")) == location_code
        and _is_visible_pkg(p)
    ]
    return sorted(out, key=lambda x: (x["dataType"], x["duration"], x["volume"]))


async def find_package(package_code: str) -> dict | None:
    """Поиск тарифа по packageCode (для invoice handler)."""
    pkgs = await _all_packages()
    for p in pkgs:
        if p.get("packageCode") == package_code:
            return _enrich_pkg(p)
    return None


# ── Order / fulfillment API ───────────────────────────────────────────────────

async def get_balance() -> dict:
    """Текущий баланс merchant-аккаунта в wholesale units (×10000)."""
    return await _post("/balance/query", {})


async def place_order(package_code: str, wholesale_price: int, tx_id: str) -> dict:
    """POST /esim/order. Идемпотентно по transactionId.
    Возвращает {'success': bool, 'obj': {'orderNo': '...'}}."""
    return await _post("/esim/order", {
        "transactionId":   tx_id,
        "packageInfoList": [{"packageCode": package_code, "count": 1, "price": wholesale_price}],
    })


async def query_by_order_no(order_no: str) -> dict:
    """Проверка статуса заказа. errorCode=200010 ⇒ профиль ещё аллоцируется."""
    return await _post("/esim/query", {
        "orderNo": order_no,
        "pager":   {"pageNum": 1, "pageSize": 50},
    })


async def query_by_tran_no(esim_tran_no: str) -> dict:
    """Детали одного eSIM-профиля по esimTranNo.

    API требует pageSize в [5, 500] (упал с 1: errorCode=000105). Просим
    минимально допустимое 5 — фильтра по tran_no достаточно чтобы вернулась
    одна запись.
    """
    return await _post("/esim/query", {
        "esimTranNo": esim_tran_no,
        "pager":      {"pageNum": 1, "pageSize": 5},
    })


async def cancel_order(esim_tran_no: str) -> dict:
    """Отмена eSIM (рефанд на наш баланс).
    Доступно только пока smdpStatus=RELEASED & esimStatus=GOT_RESOURCE."""
    return await _post("/esim/cancel", {"esimTranNo": esim_tran_no})


async def usage_query(esim_tran_nos: list[str]) -> dict:
    """Батчевый запрос юзеджа по esimTranNo (макс 10 за раз)."""
    if not esim_tran_nos:
        return {"success": True, "obj": {"esimUsageList": []}}
    if len(esim_tran_nos) > 10:
        esim_tran_nos = esim_tran_nos[:10]
    return await _post("/esim/usage/query", {"esimTranNoList": esim_tran_nos})


async def set_webhook(url: str) -> dict:
    """Регистрация webhook URL у esimaccess (idempotent — перезаписывает)."""
    return await _post("/webhook/save", {"webhook": url})


# ── Polling helper для async fulfillment ──────────────────────────────────────

async def poll_order_until_ready(order_no: str, max_wait_sec: int = 60) -> dict | None:
    """Опрашивает /esim/query до получения профиля или таймаута.
    Возвращает первый esim из esimList или None если не успело."""
    delays = [3, 5, 7, 10, 15, 20]  # ~60 сек суммарно
    waited = 0
    for delay in delays:
        if waited >= max_wait_sec:
            break
        await asyncio.sleep(delay)
        waited += delay
        try:
            resp = await query_by_order_no(order_no)
        except Exception as e:
            logger.warning("eSIM poll error for %s: %r", order_no, e)
            continue
        # 200010 = SM-DP+ ещё аллоцирует
        if resp.get("errorCode") == "200010":
            continue
        esim_list = (resp.get("obj") or {}).get("esimList") or []
        if esim_list and esim_list[0].get("ac"):
            return esim_list[0]
    return None
