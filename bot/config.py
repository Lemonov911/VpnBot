import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

BOT_TOKEN        = os.getenv("BOT_TOKEN", "")
ADMIN_ID         = int(os.getenv("ADMIN_ID") or 0)
# Comma-separated list of additional admin Telegram IDs (besides ADMIN_ID)
_extra_ids       = os.getenv("ADMIN_IDS", "")
ADMIN_IDS: set[int] = {ADMIN_ID} | {int(x) for x in _extra_ids.split(",") if x.strip().isdigit()}
WEBAPP_URL       = os.getenv("WEBAPP_URL", "")
API_PORT         = int(os.getenv("API_PORT") or 8080)
DEBUG            = os.getenv("DEBUG", "").lower() == "true"

# vpn-only branch — eSIM physically removed, SHOW_ESIM flag не нужен.
SHOW_ESIM        = False
# eSIM env vars удалены в vpn-only бранче.
VPN_SERVER_HOST  = os.getenv("VPN_SERVER_HOST", "")
VPN_SERVER_USER  = os.getenv("VPN_SERVER_USER", "root")
VPN_SERVER_KEY   = os.getenv("VPN_SERVER_KEY_PATH", "~/.ssh/id_rsa")
VPN_SERVER_PASS  = os.getenv("VPN_SERVER_PASSWORD", "")
CRYPTOBOT_TOKEN  = os.getenv("CRYPTOBOT_TOKEN", "")

# Shared secret для admin API (Next.js админка → bot REST).
# Админка проксирует write-операции через бота (reply на тикет, etc) чтобы не
# открывать write-доступ к SQLite + чтоб бот мог отправлять сообщения юзерам.
# Без этого секрета такие endpoints возвращают 403.
ADMIN_API_SECRET = os.getenv("ADMIN_API_SECRET", "")
