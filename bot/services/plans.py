"""
Тарифы — единственный источник истины.

Эта таблица читалась из двух мест (handlers/vpn.py, webapp_api.py) что один раз
привело к рассинхрону при добавлении wg_slots. Теперь импорт `from services.plans
import VPN_PLANS` — единственный путь.

Поля:
  name           — заголовок в UI (RU)
  stars          — Telegram Stars
  rub, usd       — фиатные цены для CryptoBot (строки, т.к. CryptoBot принимает текст)
  duration_days  — срок подписки
  awg_slots      — слотов AmneziaWG конфигов
  vless_slots    — слотов VLESS-Reality
  wg_slots       — слотов plain WireGuard (для роутеров)
  speed_mbps     — гарантированная скорость
  soft_cap_gb    — мягкий лимит трафика, после которого throttle (None для legacy)
  throttle_mbps  — скорость после soft_cap
  description    — короткое описание для UI
  legacy         — True если тариф спрятан в новом UI (для уже-купивших)
"""

VPN_PLANS: dict[str, dict] = {
    # ── v2 тарифы по скорости (Reality + plain WG) ──
    "vpn_base": {
        "name":           "База",
        "stars":          145,            # ≈ 200 ₽
        "rub":            "200",
        "usd":            "2.20",
        "duration_days":  30,
        # 2 AWG = телефон + ноут (обход МТС DPI).
        # 1 VLESS = универсальная subscription-ссылка, импортируется в Happ —
        # можно поделиться с близким (1 UUID разделится между устройствами).
        "awg_slots":      2,
        "vless_slots":    1,
        "wg_slots":       0,
        "speed_mbps":     60,
        "soft_cap_gb":    500,
        "throttle_mbps":  5,
        "description":    "Один человек — телефон + ноут + VLESS-ссылка",
    },
    "vpn_max": {
        "name":           "Макс",
        "stars":          360,            # ≈ 500 ₽
        "rub":            "500",
        "usd":            "5.50",
        "duration_days":  30,
        # 3 AWG для основной семьи (3 телефона/устройства).
        # 5 VLESS — комфортный запас: роутер, Linux/гости/планшет + 1-2 ещё.
        # Раньше было 10 — overkill. 3 — впритык. 5 — sweet spot.
        "awg_slots":      3,
        "vless_slots":    5,
        "wg_slots":       0,
        "speed_mbps":     120,
        "soft_cap_gb":    1000,
        "throttle_mbps":  15,
        "description":    "Семья 3+ человек, стриминг и торренты",
    },

    # ── Multi-period Stars-варианты (скрыты из VISIBLE_PLANS — открываются
    # только через period-chip в PaymentSheet при выборе ⭐ Stars). Те же
    # слоты/скорость что у базового vpn_base/vpn_max, отличается только
    # duration_days + stars (со скидкой за длинный период).
    # Lava/CryptoBot/Cryptomus не поддерживаются для этих ключей (см.
    # webapp_api invoice-endpoints — там фильтр по 1-месячным).
    "vpn_base_3m": {
        "name": "База 3 мес", "stars": 370, "rub": "600", "usd": "6.60",
        "duration_days": 90,
        "awg_slots": 2, "vless_slots": 1, "wg_slots": 0,
        "speed_mbps": 60, "soft_cap_gb": 500, "throttle_mbps": 5,
        "stars_only": True,  # отметка: не показываем для не-Stars методов оплаты
    },
    "vpn_base_6m": {
        "name": "База 6 мес", "stars": 695, "rub": "1140", "usd": "12.50",
        "duration_days": 180,
        "awg_slots": 2, "vless_slots": 1, "wg_slots": 0,
        "speed_mbps": 60, "soft_cap_gb": 500, "throttle_mbps": 5,
        "stars_only": True,
    },
    "vpn_base_12m": {
        "name": "База 1 год", "stars": 1220, "rub": "2000", "usd": "22.00",
        "duration_days": 365,
        "awg_slots": 2, "vless_slots": 1, "wg_slots": 0,
        "speed_mbps": 60, "soft_cap_gb": 500, "throttle_mbps": 5,
        "stars_only": True,
    },
    "vpn_max_3m": {
        "name": "Макс 3 мес", "stars": 920, "rub": "1500", "usd": "16.50",
        "duration_days": 90,
        "awg_slots": 3, "vless_slots": 5, "wg_slots": 0,
        "speed_mbps": 120, "soft_cap_gb": 1000, "throttle_mbps": 15,
        "stars_only": True,
    },
    "vpn_max_6m": {
        "name": "Макс 6 мес", "stars": 1725, "rub": "2850", "usd": "31.50",
        "duration_days": 180,
        "awg_slots": 3, "vless_slots": 5, "wg_slots": 0,
        "speed_mbps": 120, "soft_cap_gb": 1000, "throttle_mbps": 15,
        "stars_only": True,
    },
    "vpn_max_12m": {
        "name": "Макс 1 год", "stars": 3025, "rub": "5000", "usd": "55.00",
        "duration_days": 365,
        "awg_slots": 3, "vless_slots": 5, "wg_slots": 0,
        "speed_mbps": 120, "soft_cap_gb": 1000, "throttle_mbps": 15,
        "stars_only": True,
    },

    # ── Legacy тарифы (для уже-купивших, в новом UI скрыты) ──
    "vpn_start":   {"name": "Старт",      "stars": 128,  "rub": "180",  "usd": "2.00",  "duration_days": 30,  "awg_slots": 1, "vless_slots": 0, "legacy": True},
    "vpn_popular": {"name": "Популярный", "stars": 214,  "rub": "270",  "usd": "3.00",  "duration_days": 30,  "awg_slots": 2, "vless_slots": 0, "legacy": True},
    "vpn_pro":     {"name": "Про",        "stars": 342,  "rub": "450",  "usd": "5.00",  "duration_days": 30,  "awg_slots": 3, "vless_slots": 1, "legacy": True},
    "vpn_family":  {"name": "Семейный",   "stars": 513,  "rub": "640",  "usd": "7.00",  "duration_days": 30,  "awg_slots": 7, "vless_slots": 1, "legacy": True},
    "vpn_1m":      {"name": "1 месяц",    "stars": 299,  "rub": "299",  "usd": "3.50",  "duration_days": 30,  "awg_slots": 1, "vless_slots": 0, "legacy": True},
    "vpn_3m":      {"name": "3 месяца",   "stars": 699,  "rub": "699",  "usd": "8.00",  "duration_days": 90,  "awg_slots": 1, "vless_slots": 0, "legacy": True},
    "vpn_1y":      {"name": "1 год",      "stars": 1990, "rub": "1990", "usd": "22.00", "duration_days": 365, "awg_slots": 1, "vless_slots": 0, "legacy": True},
}


def vless_service_for_plan(plan_key: str) -> str:
    """Возвращает имя `vpnctl`-сервиса для VLESS-провижининга.
    Новые v2-планы маппятся на speed-tier сервисы; legacy / unknown → 'vless'."""
    if plan_key == "vpn_base":
        return "vless-base"
    if plan_key == "vpn_max":
        return "vless-max"
    return "vless"


def vless_slow_service_for_plan(plan_key: str) -> str | None:
    """Throttled-сервис для плана. None для legacy без slow-tier."""
    if plan_key == "vpn_base":
        return "vless-base-slow"
    if plan_key == "vpn_max":
        return "vless-max-slow"
    return None
