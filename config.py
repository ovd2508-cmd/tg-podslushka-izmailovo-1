import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", encoding="utf-8-sig")
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "-1006075820573"))
CHANNEL_URL = os.getenv("CHANNEL_URL", "").strip() or "https://t.me/podslushano_izmayolovo"
CHANNEL_LINK = CHANNEL_URL
MINI_APP_URL = os.getenv("MINI_APP_URL", "").strip()
WEB_HOST = "0.0.0.0"
WEB_PORT = int(os.getenv("PORT") or os.getenv("WEB_PORT") or "8081")
DATA_DIR = Path(os.getenv("DATA_DIR") or BASE_DIR)
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "bot.db"
BOT_DB_PATH = BASE_DIR / "inbox.sqlite3"
FLOOD_SECONDS = 30
FLOOD_INTERVAL_SECONDS = FLOOD_SECONDS
MINI_APP_AUTH_MAX_AGE = 3600
if not BOT_TOKEN:
    raise RuntimeError("Добавь BOT_TOKEN в .env или переменные окружения")
