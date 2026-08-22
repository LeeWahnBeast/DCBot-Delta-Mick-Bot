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

# Kênh đăng embed Daily hàng ngày (mặc định kênh riêng, khác kênh thông báo TikTok)
DAILY_CHANNEL_ID = int(os.environ.get("DAILY_CHANNEL_ID", "0") or 0) or 1528590477073584138

# Đồng bộ avatar bot + icon/tên server theo TikTok mỗi 5 tiếng (18000s).
IDENTITY_SYNC_INTERVAL_SEC = int(os.environ.get("IDENTITY_SYNC_INTERVAL_SEC", str(5 * 3600)))
GUILD_NAME_TEMPLATE = os.environ.get("GUILD_NAME_TEMPLATE", "{nickname} Fan Server")

PORT = int(os.environ.get("PORT", "10000"))

# --- Trang web dashboard cho bot ---
BOT_OWNER_NAME = os.environ.get("BOT_OWNER_NAME", "Lee Wahn Beast")
# ID Discord của chủ bot - có MICK/Vé vô hạn (economy.py) + hiển thị owner trên web.
BOT_OWNER_ID = int(os.environ.get("BOT_OWNER_ID", "0") or 0)

# --- Đăng nhập Discord OAuth2 cho trang web (thay cho Google trước đây) ---
# Tạo app tại https://discord.com/developers/applications > OAuth2, thêm
# redirect URI trỏ về "<domain-web>/api/discord-callback".
DISCORD_OAUTH_CLIENT_ID = os.environ.get("DISCORD_OAUTH_CLIENT_ID", "")
DISCORD_OAUTH_CLIENT_SECRET = os.environ.get("DISCORD_OAUTH_CLIENT_SECRET", "")
DISCORD_OAUTH_REDIRECT_URI = os.environ.get("DISCORD_OAUTH_REDIRECT_URI", "")

# --- Thưởng MICK trên trang web ---
# Đánh giá đủ 5 sao (chỉ tính lần đầu/tài khoản) -> +50 MICK.
RATE_5_STAR_REWARD_MICK = int(os.environ.get("RATE_5_STAR_REWARD_MICK", "50"))
# Liên kết tài khoản Discord lần đầu trên web -> +50 MICK.
LINK_DISCORD_REWARD_MICK = int(os.environ.get("LINK_DISCORD_REWARD_MICK", "50"))


UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# --- Firebase Realtime Database ---
# Cách 1 (khuyên dùng trên Render): dán nguyên nội dung file JSON service account
# vào biến môi trường FIREBASE_CREDENTIALS_JSON.
# Cách 2: set sẵn GOOGLE_APPLICATION_CREDENTIALS trỏ tới file JSON trên máy/server.
FIREBASE_CREDENTIALS_JSON = os.environ.get("FIREBASE_CREDENTIALS_JSON", "")
# Bắt buộc: URL database dạng "https://<project-id>-default-rtdb.<region>.firebasedatabase.app"
# (lấy trong Firebase Console > Realtime Database > tab Data, góc trên bên trái).
FIREBASE_DATABASE_URL = os.environ.get("FIREBASE_DATABASE_URL", "").rstrip("/")

# --- Học từ trong chat ---
# Chỉ cho bot "học từ" (đếm tần suất từ lạ vào ai_words) khi server có TỐI
# THIỂU chừng này thành viên - tránh học/ghi DB vô ích ở server nhỏ/test.
AI_LEARN_MIN_MEMBERS = int(os.environ.get("AI_LEARN_MIN_MEMBERS", "15"))

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

# --- Daily (0h -> 12h trưa giờ VN, UTC+7) ---
VN_UTC_OFFSET_HOURS = 7
DAILY_BASE_REWARD = int(os.environ.get("DAILY_BASE_REWARD", "500"))
DAILY_DECAY_RATE = float(os.environ.get("DAILY_DECAY_RATE", "0.10"))  # giảm 10%/giờ
DAILY_MIN_REWARD = int(os.environ.get("DAILY_MIN_REWARD", "15"))
DAILY_WINDOW_HOURS = int(os.environ.get("DAILY_WINDOW_HOURS", "12"))  # hết hạn lúc 12h trưa

# Đăng lại (bump) tin Daily nếu bị trôi mất giữa dòng chat đông người: sau
# DAILY_REPOST_AFTER_MESSAGES tin nhắn mới trong kênh Daily kể từ lần đăng
# gần nhất, HOẶC nếu đã DAILY_REPOST_IDLE_HOURS giờ trôi qua mà chưa ai bump
# lại (kênh vắng, tin Daily coi như đã bị quên) - miễn còn trong hạn nhận.
DAILY_REPOST_AFTER_MESSAGES = int(os.environ.get("DAILY_REPOST_AFTER_MESSAGES", "10"))
DAILY_REPOST_IDLE_HOURS = float(os.environ.get("DAILY_REPOST_IDLE_HOURS", "3"))

# Xác suất khi nhận Daily sẽ gặp 1 câu hỏi phụ (toán/câu đố dân gian) thay vì
# nhận thẳng - trả lời đúng mới được thưởng. 0 = tắt hẳn tính năng này.
DAILY_CHALLENGE_CHANCE = float(os.environ.get("DAILY_CHALLENGE_CHANCE", "0.35"))
# Trả lời đúng câu hỏi phụ được CỘNG THÊM % này vào phần thưởng Daily gốc.
DAILY_CHALLENGE_BONUS_PERCENT = int(os.environ.get("DAILY_CHALLENGE_BONUS_PERCENT", "20"))
# Số ngày gần nhất hiển thị trên chuỗi Daily (vd. [✓][✓][✓][||][X]).
DAILY_STREAK_HISTORY_LEN = int(os.environ.get("DAILY_STREAK_HISTORY_LEN", "5"))

# --- Minigame ---
WORDLE_WIN_REWARD = int(os.environ.get("WORDLE_WIN_REWARD", "15"))
WORDLE_MAX_GUESSES = int(os.environ.get("WORDLE_MAX_GUESSES", "6"))
GUESS_NUMBER_REWARD = int(os.environ.get("GUESS_NUMBER_REWARD", "12"))
GUESS_NUMBER_MAX = int(os.environ.get("GUESS_NUMBER_MAX", "100"))
GUESS_NUMBER_MAX_TRIES = int(os.environ.get("GUESS_NUMBER_MAX_TRIES", "7"))
RPS_WIN_REWARD = int(os.environ.get("RPS_WIN_REWARD", "6"))
# 3 minigame mới: Chẵn Lẻ, Đoán Màu, Vòng Quay May Mắn
CHANLE_WIN_REWARD = int(os.environ.get("CHANLE_WIN_REWARD", "6"))
DOANMAU_WIN_REWARD = int(os.environ.get("DOANMAU_WIN_REWARD", "10"))
# Vòng Quay May Mắn không có thua - random 1 trong các mốc thưởng này mỗi lượt quay.
VONGQUAY_REWARD_TIERS = [3, 5, 8, 10, 15, 20]
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
# Kênh log cập nhật bot: mỗi lần version bump (xem versioning.py), AI viết
# tóm tắt cập nhật ngắn rồi bot tự đăng vào đây.
UPDATE_LOG_CHANNEL_ID = int(os.environ.get("UPDATE_LOG_CHANNEL_ID", "1539484617663324230"))

# --- Kênh đăng thú tội ẩn danh (/confession, xem CONFESSION_MODAL trong
# discord_bot.py): mọi tin thú tội được đăng ở đây dưới dạng embed đánh số
# thứ tự, KHÔNG hiện tên/avatar người gửi - chỉ hiện 1 mã "ID" đã băm
# (hash) để phân biệt các thú tội của cùng 1 người mà không lộ danh tính
# thật (xem db.get_confession_alias trong db.py) ---
CONFESSION_CHANNEL_ID = int(os.environ.get("CONFESSION_CHANNEL_ID", "0") or 0) or 1539855082210861126
CONFESSION_COOLDOWN_SEC = int(os.environ.get("CONFESSION_COOLDOWN_SEC", "60"))

# --- Thông báo Boost server: nhắn khi 1 member vừa bắt đầu boost (không
# phải tổng số boost của server) - xem on_member_update trong discord_bot.py ---
BOOST_CHANNEL_ID = int(os.environ.get("BOOST_CHANNEL_ID", "0") or 0) or 1531291586909044736

# --- Thông báo mốc thành viên (tròn chục/trăm, vd 50/100/150...) kèm code
# quà tặng giới hạn số lượt nhập + tự hết hạn - xem on_member_join trong
# discord_bot.py và create_milestone_code()/redeem_milestone_code() trong
# features.py ---
MEMBER_MILESTONE_CHANNEL_ID = int(os.environ.get("MEMBER_MILESTONE_CHANNEL_ID", "0") or 0) or 1528556249178706071
WELCOME_CHANNEL_ID = int(os.environ.get("WELCOME_CHANNEL_ID", "0") or 0) or 1528556700653719734
BOT_ROLE_ID = int(os.environ.get("BOT_ROLE_ID", "0") or 0) or 1528556092139896832
MEMBER_MILESTONE_STEP = int(os.environ.get("MEMBER_MILESTONE_STEP", "50"))  # mốc mỗi 50 member
MEMBER_MILESTONE_CODE_MAX_USES = int(os.environ.get("MEMBER_MILESTONE_CODE_MAX_USES", "50"))
MEMBER_MILESTONE_CODE_TTL_SEC = int(os.environ.get("MEMBER_MILESTONE_CODE_TTL_SEC", str(10 * 3600)))
MEMBER_MILESTONE_CODE_REWARD_MICK = int(os.environ.get("MEMBER_MILESTONE_CODE_REWARD_MICK", "50"))

# --- /mick-shop: 4 item mua bằng MICK, mỗi item có hạn dùng riêng, hết hạn
# tự dọn (xem shop_expiry_loop trong discord_bot.py). Gia hạn qua tin nhắn
# thường "GH {tên sản phẩm}" (xem on_message), giá gia hạn = giá mua mới. ---
ADMIN_TRIAL_ROLE_ID = int(os.environ.get("ADMIN_TRIAL_ROLE_ID", "0") or 0) or 1537212897904558200
ADMIN_TRIAL_PRICE = int(os.environ.get("ADMIN_TRIAL_PRICE", "9500"))
ADMIN_TRIAL_HOURS = int(os.environ.get("ADMIN_TRIAL_HOURS", "1"))
RONALDO_PASTA_PRICE = int(os.environ.get("RONALDO_PASTA_PRICE", "9500"))
RONALDO_PASTA_HOURS = int(os.environ.get("RONALDO_PASTA_HOURS", "50"))
RONALDO_PASTA_EXTRA_GUESSES = int(os.environ.get("RONALDO_PASTA_EXTRA_GUESSES", "2"))  # 6 -> 8 lượt
LA_PEACE_PRICE = int(os.environ.get("LA_PEACE_PRICE", "9500"))
LA_PEACE_HOURS = int(os.environ.get("LA_PEACE_HOURS", "24"))
DELTAX_PRICE = int(os.environ.get("DELTAX_PRICE", "9500"))
DELTAX_HOURS = int(os.environ.get("DELTAX_HOURS", "5"))
DELTAX_MULT_MIN = float(os.environ.get("DELTAX_MULT_MIN", "1.1"))
DELTAX_MULT_MAX = float(os.environ.get("DELTAX_MULT_MAX", "2.0"))
SHOP_EXPIRY_CHECK_SEC = int(os.environ.get("SHOP_EXPIRY_CHECK_SEC", "60"))

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

# --- Quest mời bạn bè: số người cần mời random 1-10, mỗi người mời được
# thưởng ngay QUEST_INVITE_REWARD_MICK (khác các quest khác chỉ thưởng khi
# hoàn thành) ---
QUEST_INVITE_REWARD_MICK = int(os.environ.get("QUEST_INVITE_REWARD_MICK", "89"))
QUEST_INVITE_MIN = int(os.environ.get("QUEST_INVITE_MIN", "1"))
QUEST_INVITE_MAX = int(os.environ.get("QUEST_INVITE_MAX", "10"))

# --- AI Chat (Groq) ---
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")

# Tìm kiếm web thật (xem ai_chat._tavily_search_answer) dùng Tavily lấy kết
# quả search rồi nhờ Groq tóm tắt lại - Groq không có tool search đủ ổn
# định/miễn phí nên tách hẳn ra provider riêng cho phần này.
# Free tier Tavily: ~1000 credit/tháng. Lấy key tại https://app.tavily.com
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
AI_AUTO_CHAT_INTERVAL_SEC = int(os.environ.get("AI_AUTO_CHAT_INTERVAL_SEC", str(15 * 60)))
# Chỉ tự nhắn nếu có người thật (không phải bot) chat trong kênh trong khoảng thời gian này
AI_AUTO_CHAT_REQUIRE_ACTIVITY_SEC = int(os.environ.get("AI_AUTO_CHAT_REQUIRE_ACTIVITY_SEC", str(60 * 60)))
AI_LEARN_MIN_WORD_LEN = int(os.environ.get("AI_LEARN_MIN_WORD_LEN", "3"))
# Định kỳ nhờ AI đoán nghĩa hàng loạt cho top từ đã học nhiều lần nhưng chưa
# có nghĩa (xem ai_chat.guess_meanings_for_top_words). Mặc định 6 tiếng/lần,
# mỗi lần tối đa 20 từ/1 call Groq, chỉ đoán từ đã gặp >= 3 lần (lọc bớt từ
# gõ nhầm/ngẫu nhiên chỉ xuất hiện 1-2 lần, không đáng tốn call để đoán).
AI_GUESS_MEANING_INTERVAL_SEC = int(os.environ.get("AI_GUESS_MEANING_INTERVAL_SEC", str(6 * 3600)))
AI_GUESS_MEANING_BATCH_SIZE = int(os.environ.get("AI_GUESS_MEANING_BATCH_SIZE", "20"))
AI_GUESS_MEANING_MIN_COUNT = int(os.environ.get("AI_GUESS_MEANING_MIN_COUNT", "3"))
# Khung giờ "ngủ" - bot KHÔNG tự nhắn trong khung này (giờ VN, 0-23). Mặc định
# 0h -> 3h sáng, tránh spam lúc đêm khuya không ai đọc.
AI_AUTO_CHAT_QUIET_START_HOUR = int(os.environ.get("AI_AUTO_CHAT_QUIET_START_HOUR", "0"))
AI_AUTO_CHAT_QUIET_END_HOUR = int(os.environ.get("AI_AUTO_CHAT_QUIET_END_HOUR", "3"))

# --- ATM: giữ tiền hộ, tách khỏi ví tiêu xài ---
ATM_ENABLED = True

# --- Vé: tốn 1 Vé mỗi lần chơi minigame (Wordle/Đoán số/Kéo Búa Bao/Tài Xỉu/
# Xì Dách/Trivia). Nhận thêm Vé qua /daily (Daily). Chủ bot (BOT_OWNER_ID)
# có Vé vô hạn, không bao giờ bị trừ. ---
TICKET_EMOJI = os.environ.get("TICKET_EMOJI", "🎟️")
GAME_TICKET_COST = int(os.environ.get("GAME_TICKET_COST", "1"))
STARTER_TICKETS = int(os.environ.get("STARTER_TICKETS", "3"))
DAILY_TICKET_REWARD = int(os.environ.get("DAILY_TICKET_REWARD", "2"))

# --- Minigame kinh doanh (quán / công ty / nhà trọ / khách sạn) ---
# income mỗi 30 phút cho MỖI nhân viên đã thuê, theo loại hình
BUSINESS_INCOME_PER_TICK = {
    "quan": int(os.environ.get("BIZ_INCOME_QUAN", "10")),
    "congty": int(os.environ.get("BIZ_INCOME_CONGTY", "18")),
    "nhatro": int(os.environ.get("BIZ_INCOME_NHATRO", "14")),
    "khachsan": int(os.environ.get("BIZ_INCOME_KHACHSAN", "25")),
    "hotoc": int(os.environ.get("BIZ_INCOME_HOTOC", "8")),
    "taphoa": int(os.environ.get("BIZ_INCOME_TAPHOA", "9")),
    "gym": int(os.environ.get("BIZ_INCOME_GYM", "16")),
    "chebien": int(os.environ.get("BIZ_INCOME_CHEBIEN", "20")),
}
BUSINESS_OPEN_COST = {
    "quan": int(os.environ.get("BIZ_COST_QUAN", "200")),
    "congty": int(os.environ.get("BIZ_COST_CONGTY", "500")),
    "nhatro": int(os.environ.get("BIZ_COST_NHATRO", "350")),
    "khachsan": int(os.environ.get("BIZ_COST_KHACHSAN", "800")),
    "hotoc": int(os.environ.get("BIZ_COST_HOTOC", "150")),
    "taphoa": int(os.environ.get("BIZ_COST_TAPHOA", "180")),
    "gym": int(os.environ.get("BIZ_COST_GYM", "450")),
    "chebien": int(os.environ.get("BIZ_COST_CHEBIEN", "600")),
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
