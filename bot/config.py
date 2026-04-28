import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

BOT_TOKEN        = os.getenv("BOT_TOKEN", "")
ADMIN_ID         = int(os.getenv("ADMIN_ID") or 0)
WEBAPP_URL       = os.getenv("WEBAPP_URL", "")
API_PORT         = int(os.getenv("API_PORT") or 8080)
DEBUG            = os.getenv("DEBUG", "").lower() == "true"
ESIM_API_KEY     = os.getenv("ESIM_ACCESS_API_KEY", "")
VPN_SERVER_HOST  = os.getenv("VPN_SERVER_HOST", "")
VPN_SERVER_USER  = os.getenv("VPN_SERVER_USER", "root")
VPN_SERVER_KEY   = os.getenv("VPN_SERVER_KEY_PATH", "~/.ssh/id_rsa")
VPN_SERVER_PASS  = os.getenv("VPN_SERVER_PASSWORD", "")
CRYPTOBOT_TOKEN  = os.getenv("CRYPTOBOT_TOKEN", "")
