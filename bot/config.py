import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

BOT_TOKEN        = os.getenv("BOT_TOKEN", "")
ADMIN_ID         = int(os.getenv("ADMIN_ID") or 0)
# Comma-separated list of additional admin Telegram IDs (besides ADMIN_ID)
_extra_ids       = os.getenv("ADMIN_IDS", "")
ADMIN_IDS: set[int] = {ADMIN_ID} | {int(x) for x in _extra_ids.split(",") if x.strip().isdigit()}
WEBAPP_URL       = os.getenv("WEBAPP_URL", "")
# Base URL для VLESS subscription endpoint (/sub/{token}). По умолчанию
# совпадает с прод-доменом; в dev можно переопределить через env.
SUB_URL_BASE     = os.getenv("SUB_URL_BASE", "https://maxvpnesim.com")
API_PORT         = int(os.getenv("API_PORT") or 8080)
DEBUG            = os.getenv("DEBUG", "").lower() == "true"

# ── Production safety: DEBUG=true пропускает initData-проверку как ADMIN.
# Случайно оставленный флаг = открытый бэкдор. Crash на старте если случилось.
# Триггер "production" — наличие WEBAPP_URL (на dev это пусто или localhost).
_is_production = WEBAPP_URL and "localhost" not in WEBAPP_URL and "127.0.0.1" not in WEBAPP_URL
if DEBUG and _is_production:
    sys.stderr.write(
        "FATAL: DEBUG=true on production (WEBAPP_URL=%s). "
        "DEBUG disables initData/HMAC checks and treats every request as ADMIN. "
        "Remove DEBUG=true from .env or set WEBAPP_URL=localhost for dev.\n"
        % WEBAPP_URL
    )
    sys.exit(2)

# Feature flag — спрятать eSIM из UI (бот-меню + Mini App). Когда хочется
# чистого VPN-only продукта без отвлекающего side-product'а. Сам код eSIM
# остаётся (handlers/API эндпоинты), просто не показываем в UI.
# Дефолт `true` — eSIM включён, не ломаем существующих юзеров.
SHOW_ESIM        = os.getenv("SHOW_ESIM", "true").lower() != "false"
ESIM_API_KEY     = os.getenv("ESIM_ACCESS_API_KEY", "")
ESIM_WEBHOOK_SECRET = os.getenv("ESIM_WEBHOOK_SECRET", "")
VPN_SERVER_HOST  = os.getenv("VPN_SERVER_HOST", "")
VPN_SERVER_USER  = os.getenv("VPN_SERVER_USER", "root")
VPN_SERVER_KEY   = os.getenv("VPN_SERVER_KEY_PATH", "~/.ssh/id_rsa")
VPN_SERVER_PASS  = os.getenv("VPN_SERVER_PASSWORD", "")
CRYPTOBOT_TOKEN  = os.getenv("CRYPTOBOT_TOKEN", "")

# Cryptomus — альтернативный крипто-провайдер с прямыми on-chain платежами
# (BTC/ETH/USDT не через @CryptoBot, ниже комиссии, резерв если CryptoBot упадёт).
# Включается двумя env'ами + флагом — все три нужны, иначе endpoint'ы 503.
CRYPTOMUS_MERCHANT_UUID = os.getenv("CRYPTOMUS_MERCHANT_UUID", "")
CRYPTOMUS_PAYMENT_KEY   = os.getenv("CRYPTOMUS_PAYMENT_KEY", "")
CRYPTOMUS_ENABLED = bool(CRYPTOMUS_MERCHANT_UUID and CRYPTOMUS_PAYMENT_KEY) and \
                   os.getenv("CRYPTOMUS_ENABLED", "false").lower() == "true"

# Lava.top — RU-friendly платёжный провайдер с картами + СБП + recurring подпиской.
# У Lava нет meta/payload поля — purchases идентифицируются по email юзера, поэтому
# мы спрашиваем email в Mini App и используем его для tracking. parent_contract_id
# из webhook'а — primary key для будущих recurring-списаний.
LAVATOP_API_KEY        = os.getenv("LAVATOP_API_KEY", "")
# offer_id создаётся в кабинете Lava per план (товар «VPN MAX базовый — 30 дней» и
# «VPN MAX оптимальный — 30 дней»). Один UUID на план.
LAVATOP_OFFER_VPN_BASE = os.getenv("LAVATOP_OFFER_VPN_BASE", "")
LAVATOP_OFFER_VPN_MAX  = os.getenv("LAVATOP_OFFER_VPN_MAX", "")
# Webhook принимает X-Api-Key для аутентификации. По умолчанию используем тот же
# api-key что и для исходящих запросов, но позволяем переопределить — на стороне
# Lava можно задать любой shared secret.
LAVATOP_WEBHOOK_KEY    = os.getenv("LAVATOP_WEBHOOK_KEY", "") or LAVATOP_API_KEY
LAVATOP_ENABLED = bool(LAVATOP_API_KEY and LAVATOP_OFFER_VPN_BASE and LAVATOP_OFFER_VPN_MAX) and \
                  os.getenv("LAVATOP_ENABLED", "false").lower() == "true"

# Map plan_key → Lava offer_id. Когда добавляем новый план — расширяем словарь.
LAVATOP_OFFERS: dict[str, str] = {
    "vpn_base": LAVATOP_OFFER_VPN_BASE,
    "vpn_max":  LAVATOP_OFFER_VPN_MAX,
}

# Shared secret для admin API (Next.js админка → bot REST).
# Админка проксирует write-операции через бота (reply на тикет, etc) чтобы не
# открывать write-доступ к SQLite + чтоб бот мог отправлять сообщения юзерам.
# Без этого секрета такие endpoints возвращают 403.
ADMIN_API_SECRET = os.getenv("ADMIN_API_SECRET", "")
