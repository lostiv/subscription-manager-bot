import os
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

load_dotenv()  # 自动加载 .env

DB_PATH = os.getenv("DB_PATH", "/data/subscriptions.db").strip()

# fix #2: 所有凭据统一在配置模块读取并清理空白
BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()
TG_USER_ID_TEXT = os.getenv("TG_USER_ID", "").strip()

if not BOT_TOKEN or not TG_USER_ID_TEXT:
    raise ValueError("❌ 请在 .env 文件中正确设置 TG_BOT_TOKEN 和 TG_USER_ID")

try:
    TG_USER_ID = int(TG_USER_ID_TEXT)
except ValueError:
    raise ValueError("❌ TG_USER_ID 必须是整数")

BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/"
# fix #6: 明确使用用户所在的 IANA 时区，避免服务器时区影响日期语义
TIMEZONE = ZoneInfo("Asia/Shanghai")
