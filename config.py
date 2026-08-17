"""
Cấu hình bot - đọc toàn bộ từ biến môi trường (set trên Render -> Environment).
"""

import os
import logging

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
DISCORD_CHANNEL_ID = int(os.environ.get("DISCORD_CHANNEL_ID", "0") or 0)
DISCORD_GUILD_ID = int(os.environ.get("DISCORD_GUILD_ID", "1528554640378171562"))
TIKTOK_USERNAME = os.environ.get("TIKTOK_USERNAME", "tahnuyo_0").lstrip("@")
NOTIFY_MENTION = os.environ.get("NOTIFY_MENTION", "<@&1534358042496335942>")
CHECK_INTERVAL_SEC = int(os.environ.get("CHECK_INTERVAL_SEC", "120"))
DATA_FILE = os.environ.get("DATA_FILE", "data.json")
PORT = int(os.environ.get("PORT", "10000"))

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def setup_logging() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    return logging.getLogger("tiktok-bot")


log = setup_logging()
