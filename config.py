"""
Cấu hình bot - đọc toàn bộ từ biến môi trường (set trên Render -> Environment).
"""

import os
import logging

# --- Discord / TikTok cơ bản ---
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
DISCORD_CHANNEL_ID = int(os.environ.get("DISCORD_CHANNEL_ID", "0") or 0)
DISCORD_GUILD_ID = int(os.environ.get("DISCORD_GUILD_ID", "1528554640378171562"))
TIKTOK_USERNAME = os.environ.get("TIKTOK_USERNAME", "tahnuyo_0").lstrip("@")
NOTIFY_MENTION = os.environ.get("NOTIFY_MENTION", "<@&1534358042496335942>")
CHECK_INTERVAL_SEC = int(os.environ.get("CHECK_INTERVAL_SEC", "120"))

# Kênh đăng embed Daily hàng ngày (mặc định dùng chung kênh thông báo TikTok)
DAILY_CHANNEL_ID = int(os.environ.get("DAILY_CHANNEL_ID", "0") or 0) or DISCORD_CHANNEL_ID

# Đồng bộ avatar bot + icon/tên server theo TikTok mỗi 5 tiếng (18000s).
IDENTITY_SYNC_INTERVAL_SEC = int(os.environ.get("IDENTITY_SYNC_INTERVAL_SEC", str(5 * 3600)))
GUILD_NAME_TEMPLATE = os.environ.get("GUILD_NAME_TEMPLATE", "{nickname} Fan Server")

PORT = int(os.environ.get("PORT", "10000"))

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# --- Firestore ---
# Cách 1 (khuyên dùng trên Render): dán nguyên nội dung file JSON service account
# vào biến môi trường FIREBASE_CREDENTIALS_JSON.
# Cách 2: set sẵn GOOGLE_APPLICATION_CREDENTIALS trỏ tới file JSON trên máy/server.
FIREBASE_CREDENTIALS_JSON = os.environ.get("FIREBASE_CREDENTIALS_JSON", "")
FIRESTORE_PROJECT_ID = os.environ.get("FIRESTORE_PROJECT_ID", "") or None

# --- Kinh tế: MICK + Level ---
CURRENCY_NAME = "MICK"
CURRENCY_EMOJI = os.environ.get("CURRENCY_EMOJI", "🪙")
LEVEL_UP_MICK_REWARD = int(os.environ.get("LEVEL_UP_MICK_REWARD", "15"))

XP_MIN_PER_MESSAGE = int(os.environ.get("XP_MIN_PER_MESSAGE", "10"))
XP_MAX_PER_MESSAGE = int(os.environ.get("XP_MAX_PER_MESSAGE", "20"))
XP_MESSAGE_COOLDOWN_SEC = int(os.environ.get("XP_MESSAGE_COOLDOWN_SEC", "60"))

# --- Daily (0h -> 7h sáng giờ VN, UTC+7) ---
VN_UTC_OFFSET_HOURS = 7
DAILY_BASE_REWARD = int(os.environ.get("DAILY_BASE_REWARD", "500"))
DAILY_DECAY_RATE = float(os.environ.get("DAILY_DECAY_RATE", "0.10"))  # giảm 10%/giờ
DAILY_MIN_REWARD = int(os.environ.get("DAILY_MIN_REWARD", "15"))
DAILY_WINDOW_HOURS = int(os.environ.get("DAILY_WINDOW_HOURS", "7"))  # hết hạn lúc 7h sáng

# --- Minigame ---
CUP_GAME_REWARD = int(os.environ.get("CUP_GAME_REWARD", "4"))
CUP_GAME_CUP_COUNT = int(os.environ.get("CUP_GAME_CUP_COUNT", "3"))
WORDLE_WIN_REWARD = int(os.environ.get("WORDLE_WIN_REWARD", "15"))
WORDLE_MAX_GUESSES = int(os.environ.get("WORDLE_MAX_GUESSES", "6"))


def setup_logging() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    return logging.getLogger("tiktok-bot")


log = setup_logging()
