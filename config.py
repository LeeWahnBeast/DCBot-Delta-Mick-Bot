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

# --- Trang web dạng "Google Play Store" cho bot ---
BOT_OWNER_NAME = os.environ.get("BOT_OWNER_NAME", "Lee Wahn Beast")
# Client ID của Google OAuth (Google Cloud Console > APIs & Services > Credentials
# > OAuth 2.0 Client IDs > Web application). Dùng cho nút "Đăng nhập Google" để
# xác thực người đánh giá, chặn 1 người vote nhiều lần bằng tài khoản ảo/xoá cookie.
GOOGLE_OAUTH_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")


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

# --- XP theo Voice Chat (chỉ tính khi không mute/deaf và có >=2 người trong kênh) ---
VOICE_XP_TICK_SEC = int(os.environ.get("VOICE_XP_TICK_SEC", "60"))  # kiểm tra mỗi 60s
VOICE_XP_MIN_PER_TICK = int(os.environ.get("VOICE_XP_MIN_PER_TICK", "8"))
VOICE_XP_MAX_PER_TICK = int(os.environ.get("VOICE_XP_MAX_PER_TICK", "15"))
VOICE_XP_MIN_MEMBERS = int(os.environ.get("VOICE_XP_MIN_MEMBERS", "2"))  # tối thiểu bao nhiêu người trong kênh mới tính XP

# --- Daily (0h -> 7h sáng giờ VN, UTC+7) ---
VN_UTC_OFFSET_HOURS = 7
DAILY_BASE_REWARD = int(os.environ.get("DAILY_BASE_REWARD", "500"))
DAILY_DECAY_RATE = float(os.environ.get("DAILY_DECAY_RATE", "0.10"))  # giảm 10%/giờ
DAILY_MIN_REWARD = int(os.environ.get("DAILY_MIN_REWARD", "15"))
DAILY_WINDOW_HOURS = int(os.environ.get("DAILY_WINDOW_HOURS", "7"))  # hết hạn lúc 7h sáng

# --- Minigame ---
WORDLE_WIN_REWARD = int(os.environ.get("WORDLE_WIN_REWARD", "15"))
WORDLE_MAX_GUESSES = int(os.environ.get("WORDLE_MAX_GUESSES", "6"))
GUESS_NUMBER_REWARD = int(os.environ.get("GUESS_NUMBER_REWARD", "12"))
GUESS_NUMBER_MAX = int(os.environ.get("GUESS_NUMBER_MAX", "100"))
GUESS_NUMBER_MAX_TRIES = int(os.environ.get("GUESS_NUMBER_MAX_TRIES", "7"))
RPS_WIN_REWARD = int(os.environ.get("RPS_WIN_REWARD", "6"))
# Ván minigame đã kết thúc vẫn tra được bằng ID trong khoảng thời gian này (giây)
GAME_LOOKUP_TTL_SEC = int(os.environ.get("GAME_LOOKUP_TTL_SEC", "600"))

# --- Casino: Tài Xỉu (thắng ăn x2 tiền cược) ---
TAIXIU_PAYOUT_MULTIPLIER = float(os.environ.get("TAIXIU_PAYOUT_MULTIPLIER", "2.0"))

# --- Casino: Xì Dách (thắng ăn x2, Xì Bàng/Ngũ Linh ăn x3) ---
XIDACH_PAYOUT_MULTIPLIER = float(os.environ.get("XIDACH_PAYOUT_MULTIPLIER", "2.0"))
XIDACH_BONUS_MULTIPLIER = float(os.environ.get("XIDACH_BONUS_MULTIPLIER", "3.0"))

# --- Trivia đố vui (không cược, thưởng cố định) ---
TRIVIA_REWARD_MICK = int(os.environ.get("TRIVIA_REWARD_MICK", "10"))
TRIVIA_TIMEOUT_SEC = int(os.environ.get("TRIVIA_TIMEOUT_SEC", "30"))

# --- OTP xác minh chuyển tiền qua DM ---
TRANSFER_OTP_ENABLED = True
TRANSFER_OTP_LENGTH = int(os.environ.get("TRANSFER_OTP_LENGTH", "6"))
TRANSFER_OTP_TTL_SEC = int(os.environ.get("TRANSFER_OTP_TTL_SEC", "120"))
TRANSFER_OTP_MAX_ATTEMPTS = int(os.environ.get("TRANSFER_OTP_MAX_ATTEMPTS", "3"))

# --- Emoji / kênh chung ---
MICKCOIN_EMOJI = os.environ.get("MICKCOIN_EMOJI", "<:mickcoin:1538841771935531038>")
QUEST_CHANNEL_ID = int(os.environ.get("QUEST_CHANNEL_ID", "1528590477073584138"))
AI_CHAT_CHANNEL_ID = int(os.environ.get("AI_CHAT_CHANNEL_ID", "1528590477073584138"))

# --- Chuyển MICK (transfer): tiền càng cao, thời gian xử lý càng lâu ---
TRANSFER_SECONDS_PER_MICK = float(os.environ.get("TRANSFER_SECONDS_PER_MICK", "0.05"))  # 0.05s/1 MICK
TRANSFER_MIN_SECONDS = float(os.environ.get("TRANSFER_MIN_SECONDS", "2"))
TRANSFER_MAX_SECONDS = float(os.environ.get("TRANSFER_MAX_SECONDS", "600"))  # trần 10 phút

# --- Thành tựu: khó <30 MICK, dễ >30 MICK (độ khó tỉ lệ NGHỊCH với thưởng) ---
ACHIEVEMENT_HARD_REWARD_MAX = int(os.environ.get("ACHIEVEMENT_HARD_REWARD_MAX", "29"))
ACHIEVEMENT_EASY_REWARD_MIN = int(os.environ.get("ACHIEVEMENT_EASY_REWARD_MIN", "31"))

# --- Quest hằng ngày ---
QUEST_COUNT_PER_DAY = int(os.environ.get("QUEST_COUNT_PER_DAY", "3"))
QUEST_REWARD_MICK = int(os.environ.get("QUEST_REWARD_MICK", "20"))

# --- AI Chat (Groq) ---
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
AI_AUTO_CHAT_INTERVAL_SEC = int(os.environ.get("AI_AUTO_CHAT_INTERVAL_SEC", str(15 * 60)))
# Chỉ tự nhắn nếu có người thật (không phải bot) chat trong kênh trong khoảng thời gian này
AI_AUTO_CHAT_REQUIRE_ACTIVITY_SEC = int(os.environ.get("AI_AUTO_CHAT_REQUIRE_ACTIVITY_SEC", str(60 * 60)))
AI_LEARN_MIN_WORD_LEN = int(os.environ.get("AI_LEARN_MIN_WORD_LEN", "3"))
# Khung giờ "ngủ" - bot KHÔNG tự nhắn trong khung này (giờ VN, 0-23). Mặc định
# 0h -> 3h sáng, tránh spam lúc đêm khuya không ai đọc.
AI_AUTO_CHAT_QUIET_START_HOUR = int(os.environ.get("AI_AUTO_CHAT_QUIET_START_HOUR", "0"))
AI_AUTO_CHAT_QUIET_END_HOUR = int(os.environ.get("AI_AUTO_CHAT_QUIET_END_HOUR", "3"))

# --- ATM: giữ tiền hộ, tách khỏi ví tiêu xài ---
ATM_ENABLED = True

# --- Minigame kinh doanh (quán / công ty / nhà trọ / khách sạn) ---
# income mỗi 30 phút cho MỖI nhân viên đã thuê, theo loại hình
BUSINESS_INCOME_PER_TICK = {
    "quan": int(os.environ.get("BIZ_INCOME_QUAN", "10")),
    "congty": int(os.environ.get("BIZ_INCOME_CONGTY", "18")),
    "nhatro": int(os.environ.get("BIZ_INCOME_NHATRO", "14")),
    "khachsan": int(os.environ.get("BIZ_INCOME_KHACHSAN", "25")),
}
BUSINESS_OPEN_COST = {
    "quan": int(os.environ.get("BIZ_COST_QUAN", "200")),
    "congty": int(os.environ.get("BIZ_COST_CONGTY", "500")),
    "nhatro": int(os.environ.get("BIZ_COST_NHATRO", "350")),
    "khachsan": int(os.environ.get("BIZ_COST_KHACHSAN", "800")),
}
BUSINESS_HIRE_COST = int(os.environ.get("BIZ_HIRE_COST", "100"))
BUSINESS_MAX_STAFF = int(os.environ.get("BIZ_MAX_STAFF", "5"))
BUSINESS_TICK_SEC = int(os.environ.get("BIZ_TICK_SEC", str(30 * 60)))  # trả lương/thu nhập mỗi 30p


def setup_logging() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    return logging.getLogger("tiktok-bot")


log = setup_logging()
