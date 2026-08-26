"""
Các tính năng phụ của bot, gộp chung 1 file để dễ quản lý (trước đây tách
5 file riêng: achievements.py, quests.py, daily.py, games.py, business.py).

Gồm:
- Thành tựu (Achievements)
- Quest hằng ngày (Quests)
- Daily điểm danh (Daily)
- Minigame: Úp ly chọn kẹo + Wordle (Games)
- Kinh doanh: quán/công ty/nhà trọ/khách sạn (Business)

Các file "lõi" (core) không gộp vào đây: config.py, db.py, economy.py,
ai_chat.py, level_card.py, discord_bot.py, tiktok_client.py, web_server.py.
"""

import asyncio
import random
import re
import string
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import discord

import uuid as _uuid_lib

import db
import economy
from config import (
    log,
    QUEST_COUNT_PER_DAY,
    QUEST_REWARD_MICK,
    QUEST_INVITE_REWARD_MICK,
    QUEST_INVITE_MIN,
    QUEST_INVITE_MAX,
    VN_UTC_OFFSET_HOURS,
    DAILY_BASE_REWARD,
    DAILY_DECAY_RATE,
    DAILY_MIN_REWARD,
    DAILY_WINDOW_HOURS,
    DAILY_REPOST_AFTER_MESSAGES,
    DAILY_REPOST_IDLE_HOURS,
    DAILY_CHALLENGE_CHANCE,
    DAILY_CHALLENGE_BONUS_PERCENT,
    DAILY_STREAK_HISTORY_LEN,
    WORDLE_WIN_REWARD,
    WORDLE_MAX_GUESSES,
    ADMIN_TRIAL_ROLE_ID,
    ADMIN_TRIAL_PRICE,
    ADMIN_TRIAL_HOURS,
    RONALDO_PASTA_PRICE,
    RONALDO_PASTA_HOURS,
    RONALDO_PASTA_EXTRA_GUESSES,
    LA_PEACE_PRICE,
    LA_PEACE_HOURS,
    DELTAX_PRICE,
    DELTAX_HOURS,
    DELTAX_MULT_MIN,
    DELTAX_MULT_MAX,
    GUESS_NUMBER_REWARD,
    GUESS_NUMBER_MAX,
    GUESS_NUMBER_MAX_TRIES,
    RPS_WIN_REWARD,
    CHANLE_WIN_REWARD,
    DOANMAU_WIN_REWARD,
    VONGQUAY_REWARD_TIERS,
    GAME_LOOKUP_TTL_SEC,
    BUSINESS_INCOME_PER_TICK,
    BUSINESS_OPEN_COST,
    BUSINESS_HIRE_COST,
    BUSINESS_MAX_STAFF,
    BUSINESS_TICK_SEC,
    TAIXIU_PAYOUT_MULTIPLIER,
    COINFLIP_PAYOUT_MULTIPLIER,
    XIDACH_PAYOUT_MULTIPLIER,
    XIDACH_BONUS_MULTIPLIER,
    DAILY_TICKET_REWARD,
    TICKET_EMOJI,
    TRIVIA_REWARD_MICK,
    TRIVIA_TIMEOUT_SEC,
    MEMBER_MILESTONE_CODE_MAX_USES,
    MEMBER_MILESTONE_CODE_TTL_SEC,
    MEMBER_MILESTONE_CODE_REWARD_MICK,
)

# ===========================================================================
# Thành tựu (Achievements)
#
# Quy ước thưởng: thành tựu KHÓ thưởng ÍT (<30 MICK), thành tựu DỄ thưởng
# NHIỀU (>30 MICK) - đúng yêu cầu ngược đời của Mango =))
# ===========================================================================

# id: {name, desc, reward, difficulty}
def _field_text(name: str, value: str) -> str:
    return f"**{name}**\n{value}"


def build_container(
    title: str | None = None,
    description: str | None = None,
    color: "discord.Color | None" = None,
    fields: list[tuple[str, str]] | None = None,
    footer: str | None = None,
) -> discord.ui.Container:
    """Dựng discord.ui.Container (Components V2, giao diện Discord 2025+) thay
    cho discord.Embed cũ - giữ layout tương đương (title/description/fields/footer)."""
    container = discord.ui.Container(accent_color=color or discord.Color.blurple())
    header = ""
    if title:
        header += f"### {title}\n"
    if description:
        header += description
    if header:
        container.add_item(discord.ui.TextDisplay(header))
    if fields:
        for name, value in fields:
            container.add_item(discord.ui.TextDisplay(_field_text(name, value)))
    if footer:
        container.add_item(discord.ui.Separator(visible=True))
        container.add_item(discord.ui.TextDisplay(f"-# {footer}"))
    return container


class SimpleContainerLayout(discord.ui.LayoutView):
    """LayoutView dùng 1 lần để bọc 1 Container (Components V2), không có nút -
    dùng cho các thông báo chỉ hiện 1 lần (thành tựu, cập nhật, v.v.)."""

    def __init__(self, container: discord.ui.Container, timeout: float | None = 1):
        super().__init__(timeout=timeout)
        self.add_item(container)


ACHIEVEMENTS: dict[str, dict] = {
    # --- Khó (reward < 30) ---
    "level_50": {"name": "🏔️ Huyền Thoại", "desc": "Đạt Level 50", "reward": 25, "difficulty": "Khó"},
    "mick_10000": {"name": "💎 Đại Gia MICK", "desc": "Sở hữu 10,000 MICK cùng lúc", "reward": 20, "difficulty": "Khó"},
    "wordle_streak": {"name": "🧠 Bậc Thầy Wordle", "desc": "Thắng Wordle 10 lần", "reward": 15, "difficulty": "Khó"},
    "business_tycoon": {"name": "🏢 Trùm Kinh Doanh", "desc": "Sở hữu cả 8 loại hình kinh doanh", "reward": 29, "difficulty": "Khó"},

    # --- Dễ (reward > 30) ---
    "first_message": {"name": "👋 Chào Sân", "desc": "Nhắn tin đầu tiên trong server", "reward": 35, "difficulty": "Dễ"},
    "first_daily": {"name": "🎁 Điểm Danh", "desc": "Nhận Daily lần đầu", "reward": 40, "difficulty": "Dễ"},
    "level_5": {"name": "🌱 Tân Binh", "desc": "Đạt Level 5", "reward": 45, "difficulty": "Dễ"},
    "first_business": {"name": "🏪 Khởi Nghiệp", "desc": "Mở 1 cơ sở kinh doanh đầu tiên", "reward": 50, "difficulty": "Dễ"},
}


# Chặn thông báo lặp lại khi Firestore không lưu được (vd. hết quota): nếu 1
# user vừa "mở khóa" 1 thành tựu nhưng lưu Firestore thất bại, KHÔNG thử/báo
# lại thành tựu đó nữa trong phiên chạy hiện tại (chỉ dev/test - nếu Firestore
# hồi phục, restart bot sẽ tự đồng bộ lại bình thường vì đây chỉ là cache RAM
# tạm, không phải trạng thái đã mở khóa thật).
_unlock_save_failed: set[tuple[int, str]] = set()


async def unlock(user_id: int, achievement_id: str) -> dict | None:
    """Mở khóa thành tựu nếu chưa có. Trả về info thành tựu nếu vừa mở khóa
    VÀ LƯU THÀNH CÔNG vào Firestore, None nếu đã có / không tồn tại / lưu thất
    bại (vd. Firestore hết quota - trước đây bug ở chỗ này: lưu thất bại vẫn
    trả về "đã mở khóa" -> bot cứ báo "Chào Sân" lặp lại mỗi tin nhắn vì thành
    tựu không bao giờ thực sự được ghi nhận là đã có)."""
    if achievement_id not in ACHIEVEMENTS:
        return None

    guard_key = (user_id, achievement_id)
    if guard_key in _unlock_save_failed:
        return None

    user = await db.get_user(user_id)
    unlocked = set(user.get("achievements", []))
    if achievement_id in unlocked:
        return None

    info = ACHIEVEMENTS[achievement_id]
    unlocked.add(achievement_id)
    new_mick = user["mick"] + info["reward"]
    ok = await db.save_user(user_id, {"achievements": list(unlocked), "mick": new_mick})
    if not ok:
        _unlock_save_failed.add(guard_key)
        log.warning(
            "Không lưu được thành tựu %s cho user %s (Firestore lỗi/hết quota) - "
            "tạm hoãn thông báo, sẽ tự thử lại nếu bot restart.",
            achievement_id, user_id,
        )
        return None
    return info


async def check_and_unlock_by_stats(user_id: int) -> list[dict]:
    """Kiểm tra các thành tựu dựa trên level/mick hiện tại, tự mở khóa nếu đạt. Trả về list thành tựu vừa mở."""
    user = await db.get_user(user_id)
    newly_unlocked = []

    checks = [
        ("level_5", user["level"] >= 5),
        ("level_50", user["level"] >= 50),
        ("mick_10000", user["mick"] >= 10000),
        ("wordle_streak", user.get("wordle_wins", 0) >= 10),
    ]
    for aid, condition in checks:
        if condition:
            result = await unlock(user_id, aid)
            if result:
                newly_unlocked.append(result)

    summary = await get_summary(user_id)
    if all(biz.get("opened_at") for biz in summary.values()):
        result = await unlock(user_id, "business_tycoon")
        if result:
            newly_unlocked.append(result)

    return newly_unlocked


def build_list_container(user_achievements: list[str]) -> discord.ui.Container:
    unlocked = set(user_achievements)

    hard = [(aid, a) for aid, a in ACHIEVEMENTS.items() if a["difficulty"] == "Khó"]
    easy = [(aid, a) for aid, a in ACHIEVEMENTS.items() if a["difficulty"] == "Dễ"]

    def render(items):
        lines = []
        for aid, a in items:
            mark = "✅" if aid in unlocked else "🔒"
            lines.append(f"{mark} **{a['name']}** — {a['desc']} (+{a['reward']} MICK)")
        return "\n".join(lines) or "Không có"

    return build_container(
        title="🏆 Danh sách Thành tựu",
        color=discord.Color.gold(),
        fields=[
            ("🔥 Khó (thưởng ít, khoe nhiều)", render(hard)),
            ("🍀 Dễ (thưởng nhiều, cày lẹ)", render(easy)),
        ],
        footer=f"Đã mở khóa {len(unlocked)}/{len(ACHIEVEMENTS)}",
    )


async def announce_unlocks(channel, user: discord.User, unlocked: list[dict]):
    for info in unlocked:
        try:
            container = build_container(
                title="🏆 Mở khóa Thành tựu!",
                description=f"{user.mention} vừa đạt **{info['name']}**\n{info['desc']}\n💰 Bạn đã nhận **{info['reward']} Mick**",
                color=discord.Color.gold(),
            )
            await channel.send(view=SimpleContainerLayout(container))
        except Exception as e:
            log.warning("Gửi thông báo thành tựu lỗi: %s", e)

    if unlocked:
        try:
            finished = await bump_progress(user.id, "achievement_1_3")
            if finished:
                await channel.send(
                    f"✅ {user.mention} hoàn thành quest **{finished['desc']}**! "
                    f"+**{finished['reward']} MICK** (số dư: {finished['new_balance']})"
                )
        except Exception:
            pass


# ===========================================================================
# Quest hằng ngày (Quests)
#
# Mỗi ngày random 3 quest trong danh sách cố định, reset theo giờ VN (dùng
# chung mốc ngày với Daily bên dưới).
# ===========================================================================

# id: {desc, target} - target = số lần cần đạt để hoàn thành
QUEST_POOL: dict[str, dict] = {
    "meow_3": {"desc": "Nói `meow meow` 3 lần", "target": 3},
    "love_tag": {"desc": "Nói `i love @ai đó` (tag ngẫu nhiên 1 người) 1 lần", "target": 1},
    "femboy_3": {"desc": "Nói `i am femboy` 3 lần", "target": 3},
    "play_game_5": {"desc": "Chơi minigame (Wordle/Đoán số/Kéo Búa Bao) 5 lần", "target": 5},
    "level_up": {"desc": "Lên 1 level bất kỳ", "target": 1},
    "achievement_1_3": {"desc": "Hoàn thành 1-3 thành tựu bất kỳ", "target": 1},
    "ai_hoi_3": {"desc": "Nói `ai hỏi` 3 lần", "target": 3},
    "ghet_tomboy": {"desc": "Nói `tôi ghét tomboy` 1 lần", "target": 1},
    "depchai_gay": {
        "desc": "Nói `Btw, i love <@1011257705031274536> because he's is my girlfriend and gay <3` 1 lần",
        "target": 1,
    },
    "nsc_tree": {"desc": "Nói `i love nsc because he crashed into a tree.` 1 lần", "target": 1},
    # target = None: số người cần mời được random riêng cho từng user (xem
    # get_today_quests) trong khoảng QUEST_INVITE_MIN-QUEST_INVITE_MAX, khác
    # với các quest khác (target cố định sẵn trong QUEST_POOL).
    "invite_friends": {"desc": "Mời bạn bè vào server", "target": None},
}

QUEST_IDS = list(QUEST_POOL.keys())

# Quest nào có target random theo từng user (không cố định trong QUEST_POOL)
_DYNAMIC_TARGET_QUESTS = {"invite_friends"}


def quest_target(user: dict, qid: str) -> int:
    """Trả về target thật của 1 quest cho user (áp dụng cho cả quest target
    cố định lẫn quest random target như invite_friends)."""
    target = QUEST_POOL[qid]["target"]
    if target is not None:
        return target
    return user.get("quest_invite_target") or QUEST_INVITE_MAX


def quest_invite_remaining(user: dict) -> int:
    """Số lượt mời còn thiếu để hoàn thành quest invite_friends hôm nay (tối
    thiểu 1) - dùng làm max_uses khi bot tạo link mời, để Discord TỰ ĐỘNG xoá
    link ngay khi đạt đủ số lượt cần, không cần bot tự dò/xoá thủ công."""
    target = quest_target(user, "invite_friends")
    progress = user.get("quest_progress", {}).get("invite_friends", 0)
    return max(1, target - progress)


def quest_desc(user: dict, qid: str) -> str:
    """Mô tả hiển thị của 1 quest, có nội suy target random (vd. số người cần mời)."""
    if qid == "invite_friends":
        target = quest_target(user, qid)
        return f"Mời {target} người bạn vào server (+{QUEST_INVITE_REWARD_MICK} MICK/người được mời)"
    return QUEST_POOL[qid]["desc"]


async def get_today_quests(user_id: int) -> dict:
    """Trả về (và tự khởi tạo nếu cần) bộ quest hôm nay cho user."""
    user = await db.get_user(user_id)
    today = vn_today_str()

    if user.get("quest_date") == today and user.get("quest_ids"):
        return user

    quest_ids = random.sample(QUEST_IDS, min(QUEST_COUNT_PER_DAY, len(QUEST_IDS)))
    update = {"quest_date": today, "quest_ids": quest_ids, "quest_progress": {}, "quest_done": []}
    if "invite_friends" in quest_ids:
        update["quest_invite_target"] = random.randint(QUEST_INVITE_MIN, QUEST_INVITE_MAX)
    await db.save_user(user_id, update)
    user.update(update)
    return user


async def bump_progress(user_id: int, event_key: str, amount: int = 1) -> dict | None:
    """
    Cộng tiến độ cho quest nào (nếu có trong bộ quest hôm nay của user) khớp event_key.
    event_key phải trùng với id trong QUEST_POOL. Trả về quest info nếu VỪA hoàn thành, None nếu chưa.
    """
    if event_key in _DYNAMIC_TARGET_QUESTS:
        # Quest có target random/luồng thưởng riêng (vd. invite_friends) - dùng
        # bump_invite_progress() tương ứng, không đi qua đường chung này.
        return None

    user = await get_today_quests(user_id)
    if event_key not in user["quest_ids"]:
        return None
    if event_key in user.get("quest_done", []):
        return None

    progress = dict(user.get("quest_progress", {}))
    progress[event_key] = progress.get(event_key, 0) + amount

    quest = QUEST_POOL[event_key]
    if progress[event_key] >= quest["target"]:
        done = list(user.get("quest_done", []))
        done.append(event_key)
        new_balance = await economy.add_mick(user_id, QUEST_REWARD_MICK)
        await db.save_user(user_id, {"quest_progress": progress, "quest_done": done})
        return {"id": event_key, "desc": quest["desc"], "reward": QUEST_REWARD_MICK, "new_balance": new_balance}

    await db.save_user(user_id, {"quest_progress": progress})
    return None


async def bump_invite_progress(inviter_id: int) -> dict | None:
    """Gọi khi 1 thành viên mới join server nhờ link mời của inviter_id.

    Khác với bump_progress(): quest 'invite_friends' thưởng MICK NGAY mỗi lượt
    mời (không đợi hoàn thành), và target là số random 1-10 riêng cho user đó
    (xem get_today_quests). Khi đạt target, quest được đánh dấu hoàn thành
    (biến mất khỏi danh sách quest còn thiếu, giống các quest khác).

    Trả về None nếu user không có quest này hôm nay hoặc đã hoàn thành rồi.
    Ngược lại trả dict thông tin lượt mời (đã thưởng MICK dù đạt target hay chưa).
    """
    qid = "invite_friends"
    user = await get_today_quests(inviter_id)
    if qid not in user.get("quest_ids", []):
        return None
    if qid in user.get("quest_done", []):
        return None

    target = quest_target(user, qid)
    progress = dict(user.get("quest_progress", {}))
    progress[qid] = progress.get(qid, 0) + 1
    invited_count = progress[qid]

    # Thưởng ngay mỗi lượt mời, bất kể đã đạt target hay chưa.
    new_balance = await economy.add_mick(inviter_id, QUEST_INVITE_REWARD_MICK)

    result = {
        "id": qid,
        "invited_count": invited_count,
        "target": target,
        "reward": QUEST_INVITE_REWARD_MICK,
        "new_balance": new_balance,
        "completed": False,
    }

    if invited_count >= target:
        done = list(user.get("quest_done", []))
        done.append(qid)
        code = user.get("quest_invite_code") or ""
        await db.save_user(
            inviter_id,
            {"quest_progress": progress, "quest_done": done, "quest_invite_code": "", "quest_invite_code_date": ""},
        )
        if code:
            await db.delete_invite_owner(code)
        result["completed"] = True
        result["invite_code"] = code
    else:
        await db.save_user(inviter_id, {"quest_progress": progress})

    return result


def build_quest_container(user: dict, display_name: str, invite_link: str | None = None) -> discord.ui.Container:
    done = set(user.get("quest_done", []))
    progress = user.get("quest_progress", {})

    lines = []
    for qid in user.get("quest_ids", []):
        target = quest_target(user, qid)
        mark = "✅" if qid in done else "⬜"
        cur = progress.get(qid, 0)
        line = f"{mark} {quest_desc(user, qid)} — `{min(cur, target)}/{target}`"
        if qid == "invite_friends" and qid not in done and invite_link:
            line += f"\n> 🔗 Link mời của bạn: {invite_link}"
        lines.append(line)

    description = "\n".join(lines) if lines else "Chưa có quest, gõ lại lệnh để random."
    return build_container(
        title=f"📜 Quest hằng ngày của {display_name}",
        description=description,
        color=discord.Color.teal(),
        footer=f"Mỗi quest hoàn thành: +{QUEST_REWARD_MICK} MICK · Reset 0h giờ VN",
    )


# ===========================================================================
# Daily điểm danh (Daily)
#
# Đúng 0h sáng giờ VN (UTC+7) đăng embed có nút "Nhận Daily". Nhận càng trễ
# thì MICK càng ít (giảm DAILY_DECAY_RATE mỗi giờ), sàn DAILY_MIN_REWARD.
# Hết hạn đúng DAILY_WINDOW_HOURS giờ (mặc định 12h trưa).
#
# Ngoài bấm nút trên embed, còn có thể gõ lệnh /daily để nhận trực tiếp -
# phòng trường hợp embed Daily bị trôi mất giữa dòng chat đông người.
#
# Random 1 phần trong DAILY_CHALLENGE_CHANCE lượt nhận sẽ hiện 1 câu hỏi phụ
# (toán nhanh hoặc câu đố dân gian kiểu xưa) - phải trả lời đúng mới nhận
# được MICK, nhưng trả lời đúng thì được CỘNG THÊM DAILY_CHALLENGE_BONUS_PERCENT%.
#
# Chuỗi Daily (daily_streak/daily_history): mỗi ngày lúc 0h (ngay trước khi
# đăng embed Daily mới), finalize_daily_streaks() chốt trạng thái NGÀY HÔM
# QUA cho từng user, dùng last_active_date (ngày gần nhất có nhắn tin) để
# phân biệt "quên điểm danh" (bị reset chuỗi) với "cả ngày không online"
# (giữ nguyên chuỗi, không phạt):
#   ✓ (done)   - đã nhận Daily hôm đó
#   || (paused)- không hề nhắn tin/online cả ngày hôm đó -> không tính là bỏ lỡ
#   X (missed) - có online/nhắn tin nhưng KHÔNG điểm danh -> chuỗi về 0
# ===========================================================================

VN_TZ = timezone(timedelta(hours=VN_UTC_OFFSET_HOURS))
DAILY_CLAIM_CUSTOM_ID = "daily_claim_btn"
_STREAK_SYMBOLS = {"done": "✓", "missed": "X", "paused": "||"}


def vn_now() -> datetime:
    return datetime.now(VN_TZ)


def vn_today_str() -> str:
    return vn_now().strftime("%Y-%m-%d")


def compute_daily_reward(hours_elapsed: int) -> int:
    hours_elapsed = max(0, hours_elapsed)
    amount = DAILY_BASE_REWARD * ((1 - DAILY_DECAY_RATE) ** hours_elapsed)
    return max(DAILY_MIN_REWARD, round(amount))


def build_daily_container() -> discord.ui.Container:
    ts = int(vn_now().astimezone(timezone.utc).timestamp())
    return build_container(
        title="🎁 Daily hàng ngày",
        description=(
            f"Bấm nút bên dưới (hoặc gõ `/daily` nếu tin nhắn này bị trôi) để nhận MICK miễn phí!\n"
            f"Nhận ngay lúc 0h: **{DAILY_BASE_REWARD} MICK**. "
            f"Càng nhận trễ, MICK càng giảm {int(DAILY_DECAY_RATE * 100)}%/giờ "
            f"(tối thiểu **{DAILY_MIN_REWARD} MICK**).\n"
            f"⏰ Hết hạn lúc **{DAILY_WINDOW_HOURS}:00 trưa**.\n"
            f"🧩 Ngẫu nhiên có thể gặp 1 câu hỏi phụ (toán nhanh/câu đố dân gian) - "
            f"trả lời đúng được **+{DAILY_CHALLENGE_BONUS_PERCENT}%** thưởng!"
        ),
        color=discord.Color.gold(),
        footer=f"<t:{ts}:f>",
    )


class DailyClaimView(discord.ui.LayoutView):
    """Persistent view Components V2 (timeout=None, custom_id cố định) để sống
    sót qua restart bot - gộp luôn Container nội dung + nút Nhận Daily."""

    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(build_daily_container())
        row = discord.ui.ActionRow()
        button = discord.ui.Button(
            label="Nhận Daily", emoji="🎁", style=discord.ButtonStyle.success, custom_id=DAILY_CLAIM_CUSTOM_ID
        )
        button.callback = self._claim
        row.add_item(button)
        self.add_item(row)

    async def _claim(self, interaction: discord.Interaction):
        await _handle_claim(interaction)


# --- Câu hỏi phụ: toán nhanh + câu đố dân gian kiểu xưa ---------------------


def _normalize_answer(text: str) -> str:
    """Chuẩn hoá đáp án để so khớp khoan dung hơn: bỏ khoảng trắng thừa, viết
    thường, bỏ dấu tiếng Việt (cho phép người chơi gõ không dấu)."""
    text = (text or "").strip().lower()
    text = text.replace("đ", "d")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.split())


def _gen_math_challenge() -> tuple[str, set[str]]:
    op = random.choice(["+", "-", "x"])
    if op == "+":
        a, b = random.randint(10, 99), random.randint(10, 99)
        answer = a + b
    elif op == "-":
        a, b = random.randint(30, 99), random.randint(1, 29)
        answer = a - b
    else:
        a, b = random.randint(2, 12), random.randint(2, 12)
        answer = a * b
    question = f"🧮 {a} {op} {b} = ?"
    return question, {str(answer)}


# Câu đố dân gian kiểu xưa - mỗi câu kèm sẵn vài cách trả lời được chấp nhận
# (không dấu, viết tắt...) vì đây chỉ là câu hỏi vui, không cần chấm quá khắt khe.
_FOLK_RIDDLES: list[tuple[str, set[str]]] = [
    (
        "📜 Câu đố dân gian: Con gì sáng đi 4 chân, trưa đi 2 chân, chiều tối đi 3 chân?",
        {"con nguoi", "nguoi"},
    ),
    (
        "📜 Câu đố dân gian: Mỗi năm chỉ ghé thăm 1 lần, mang theo bánh chưng bánh tét, lì xì đầu năm?",
        {"tet", "tet nguyen dan", "ngay tet"},
    ),
    (
        "📜 Câu đố dân gian: Càng gọt càng ngắn, càng dùng càng cùn, dùng để viết chữ?",
        {"but chi", "cay but chi"},
    ),
    (
        "📜 Câu đố dân gian: Không chân mà chạy khắp làng trên xóm dưới, không miệng mà ai cũng nghe thấy?",
        {"tin don"},
    ),
    (
        "📜 Câu đố dân gian: Sáng mọc đằng đông, tối lặn đằng tây, ngày nào cũng đi làm đúng giờ?",
        {"mat troi"},
    ),
]


def _random_daily_challenge() -> tuple[str, set[str]]:
    if random.random() < 0.5:
        return _gen_math_challenge()
    question, answers = random.choice(_FOLK_RIDDLES)
    return question, answers


class DailyChallengeModal(discord.ui.Modal):
    """Modal hỏi 1 câu (toán/câu đố) trước khi cho nhận Daily. Đáp án đưa vào
    placeholder (giới hạn label của Discord chỉ 45 ký tự, không đủ chứa câu
    đố dài) để người chơi vẫn thấy rõ câu hỏi ngay trong ô nhập."""

    def __init__(self, question: str, accepted_answers: set[str]):
        super().__init__(title="🧩 Câu hỏi Daily", timeout=120)
        self.question = question
        self.accepted_answers = accepted_answers
        self.answer_input = discord.ui.TextInput(
            label="Đáp án của bạn",
            placeholder=question[:100],
            max_length=100,
            required=True,
        )
        self.add_item(self.answer_input)

    async def on_submit(self, interaction: discord.Interaction):
        given = _normalize_answer(self.answer_input.value)
        if given not in self.accepted_answers:
            await interaction.response.send_message(
                f"❌ Chưa đúng rồi! Câu hỏi vừa rồi: {self.question}\n"
                f"Vẫn còn hạn Daily hôm nay (trước {DAILY_WINDOW_HOURS}h trưa) - "
                f"bấm nhận Daily lần nữa để thử câu khác nhé!",
                ephemeral=True,
            )
            return
        await _grant_daily(interaction, vn_today_str(), challenge_bonus=True)


def format_streak_line(streak: int, history: list[str], just_claimed: bool = False) -> str:
    """Vẽ chuỗi Daily kiểu [✓][✓][✓][||][X]. Công khai để discord_bot.py (vd.
    /profile) dùng lại được, không chỉ nội bộ module này."""
    boxes = [f"[{_STREAK_SYMBOLS.get(s, '?')}]" for s in history[-DAILY_STREAK_HISTORY_LEN:]]
    if just_claimed:
        boxes.append("[✓]")
        boxes = boxes[-DAILY_STREAK_HISTORY_LEN:]
    boxes_text = " ".join(boxes) if boxes else "(chưa có dữ liệu)"
    return f"🔥 Chuỗi Daily: **{streak} ngày** · {boxes_text}"


_format_streak_line = format_streak_line  # tương thích ngược cho code nội bộ đã gọi tên cũ


async def _grant_daily(interaction: discord.Interaction, today: str, challenge_bonus: bool = False):
    """Cấp thưởng Daily thật sự (sau khi đã qua mọi kiểm tra/câu hỏi phụ nếu
    có). Tách riêng khỏi _handle_claim vì đường "trả lời đúng câu hỏi" đến từ
    1 Interaction KHÁC (của Modal) nên cần gọi lại độc lập."""
    user_id = interaction.user.id

    async with economy.user_lock(user_id):
        user = await db.get_user(user_id)
        if user.get("last_daily_date") == today:
            await interaction.response.send_message("✅ Bạn đã nhận Daily hôm nay rồi!", ephemeral=True)
            return

        daily_state = await db.get_daily_state()
        reset_epoch = daily_state.get("reset_at_epoch")
        if not reset_epoch or daily_state.get("date") != today:
            # Phòng trường hợp bot restart lệch nhịp và chưa có mốc reset hôm nay.
            reset_epoch = int(vn_now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())

        hours_elapsed = int((time.time() - reset_epoch) // 3600)
        reward = compute_daily_reward(hours_elapsed)
        if challenge_bonus:
            reward = round(reward * (1 + DAILY_CHALLENGE_BONUS_PERCENT / 100))

        # Ghi thẳng ở đây (không gọi economy.add_mick/add_ve) vì đang ở trong
        # user_lock rồi -> asyncio.Lock không reentrant, gọi lại sẽ deadlock.
        new_balance = max(0, user["mick"] + reward)
        is_owner = economy.is_owner(user_id)
        new_ve = user.get("ve", 0) if is_owner else user.get("ve", 0) + DAILY_TICKET_REWARD
        update = {"last_daily_date": today, "mick": new_balance}
        if not is_owner:
            update["ve"] = new_ve
        await db.save_user(user_id, update)

        streak = user.get("daily_streak", 0)
        history = list(user.get("daily_history", []))

    ve_display = "∞" if is_owner else str(new_ve)
    bonus_note = f" (🧩 +{DAILY_CHALLENGE_BONUS_PERCENT}% vì trả lời đúng câu hỏi!)" if challenge_bonus else ""
    await interaction.response.send_message(
        f"🎁 Bạn đã nhận **{reward} Mick**{bonus_note} + {TICKET_EMOJI} **{DAILY_TICKET_REWARD} Vé**! "
        f"(Số dư hiện tại: **{new_balance} MICK** · Vé: **{ve_display}**)\n"
        f"{_format_streak_line(streak, history, just_claimed=True)}",
        ephemeral=True,
    )

    try:
        unlocked = await unlock(user_id, "first_daily")
        if unlocked and interaction.channel is not None:
            await announce_unlocks(interaction.channel, interaction.user, [unlocked])
    except Exception:
        pass


async def _handle_claim(interaction: discord.Interaction):
    """Điểm vào chung cho cả nút 'Nhận Daily' trên embed VÀ lệnh /daily -
    hành vi giống hệt nhau, chỉ khác nguồn gọi."""
    now = vn_now()
    today = now.strftime("%Y-%m-%d")

    if now.hour >= DAILY_WINDOW_HOURS:
        await interaction.response.send_message(
            f"⏰ Daily hôm nay đã hết hạn (quá {DAILY_WINDOW_HOURS}h trưa). Chờ 0h mai nhé!", ephemeral=True
        )
        return

    user = await db.get_user(interaction.user.id)
    if user.get("last_daily_date") == today:
        await interaction.response.send_message(
            f"✅ Bạn đã nhận Daily hôm nay rồi!\n"
            f"{_format_streak_line(user.get('daily_streak', 0), user.get('daily_history', []), just_claimed=True)}",
            ephemeral=True,
        )
        return

    if DAILY_CHALLENGE_CHANCE > 0 and random.random() < DAILY_CHALLENGE_CHANCE:
        question, accepted = _random_daily_challenge()
        await interaction.response.send_modal(DailyChallengeModal(question, accepted))
        return

    await _grant_daily(interaction, today)


# Alias công khai - dùng cho lệnh /daily (xem discord_bot.py), tách biệt
# tên khỏi hàm nội bộ _handle_claim để module khác không cần đụng tới hàm "_".
claim_daily = _handle_claim


async def finalize_daily_streaks() -> None:
    """Chạy 1 lần/ngày, ngay TRƯỚC khi đăng embed Daily mới (xem
    maybe_post_daily) - chốt trạng thái NGÀY HÔM QUA cho từng user đã từng
    xuất hiện trong DB (không quét toàn bộ member server, chỉ user có dữ liệu
    sẵn - đỡ tốn băng thông Firebase)."""
    yesterday = (vn_now() - timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        users = await db.get_all_users(use_cache=False)
    except Exception as e:
        log.warning("Không đọc được danh sách user để chốt chuỗi Daily: %s", e)
        return

    for user_id_str, data in users:
        last_daily = data.get("last_daily_date", "")
        last_active = data.get("last_active_date", "")
        streak = data.get("daily_streak", 0)
        history = list(data.get("daily_history", []))

        if last_daily == yesterday:
            status = "done"
            streak += 1
        elif last_active == yesterday:
            status = "missed"
            streak = 0
        else:
            # Không hề nhắn tin/online cả ngày hôm qua -> không tính là bỏ
            # lỡ, giữ nguyên chuỗi (chỉ "tạm ngưng").
            status = "paused"

        history.append(status)
        history = history[-DAILY_STREAK_HISTORY_LEN:]

        try:
            await db.save_user(int(user_id_str), {"daily_streak": streak, "daily_history": history})
        except Exception as e:
            log.warning("Chốt chuỗi Daily cho user %s lỗi: %s", user_id_str, e)


_daily_messages_since_post = 0
_daily_repost_lock = asyncio.Lock()


async def maybe_post_daily(client: discord.Client, channel_id: int) -> None:
    """Gọi mỗi phút từ vòng lặp; tự đăng embed đúng lúc 0h VN nếu chưa đăng hôm nay."""
    global _daily_messages_since_post
    now = vn_now()
    today = now.strftime("%Y-%m-%d")

    if now.hour != 0:
        return

    daily_state = await db.get_daily_state()
    if daily_state.get("date") == today:
        return  # đã đăng hôm nay rồi

    channel = client.get_channel(channel_id)
    if channel is None:
        try:
            channel = await client.fetch_channel(channel_id)
        except Exception:
            return

    await finalize_daily_streaks()
    msg = await channel.send(view=DailyClaimView())
    _daily_messages_since_post = 0
    await db.save_daily_state({
        "date": today,
        "reset_at_epoch": int(time.time()),
        "message_id": msg.id,
        "last_post_ts": time.time(),
    })


async def _repost_daily(client: discord.Client, channel_id: int, daily_state: dict) -> None:
    """Đăng lại (bump) tin Daily lên cuối kênh - xoá tin cũ nếu còn xoá được,
    rồi gửi tin mới + cập nhật message_id/last_post_ts. Dùng khi tin Daily cũ
    bị trôi mất giữa dòng chat đông người, hoặc kênh im ắng quá lâu."""
    global _daily_messages_since_post
    channel = client.get_channel(channel_id)
    if channel is None:
        try:
            channel = await client.fetch_channel(channel_id)
        except Exception:
            return

    old_msg_id = daily_state.get("message_id")
    if old_msg_id:
        try:
            old_msg = await channel.fetch_message(int(old_msg_id))
            await old_msg.delete()
        except Exception:
            pass  # tin cũ có thể đã bị xoá tay/trôi quá xa - bỏ qua, không quan trọng

    try:
        new_msg = await channel.send(view=DailyClaimView())
    except Exception as e:
        log.warning("Đăng lại Daily lỗi: %s", e)
        return

    _daily_messages_since_post = 0
    await db.save_daily_state({"message_id": new_msg.id, "last_post_ts": time.time()})


async def on_daily_channel_message(client: discord.Client, channel_id: int) -> None:
    """Gọi từ on_message mỗi khi có tin nhắn mới TRONG kênh Daily - đếm dồn,
    hễ đủ DAILY_REPOST_AFTER_MESSAGES tin thì bump lại Daily ngay (miễn còn
    trong hạn nhận và Daily hôm nay đã đăng)."""
    global _daily_messages_since_post
    now = vn_now()
    if now.hour >= DAILY_WINDOW_HOURS:
        return  # đã hết hạn nhận Daily hôm nay, bump làm gì nữa

    daily_state = await db.get_daily_state()
    if daily_state.get("date") != vn_today_str():
        return  # Daily hôm nay chưa đăng - không có gì để bump

    _daily_messages_since_post += 1
    if _daily_messages_since_post < DAILY_REPOST_AFTER_MESSAGES:
        return

    async with _daily_repost_lock:
        if _daily_messages_since_post < DAILY_REPOST_AFTER_MESSAGES:
            return  # 1 tin nhắn khác vừa bump rồi, khỏi bump trùng
        await _repost_daily(client, channel_id, daily_state)


async def maybe_repost_daily_idle(client: discord.Client, channel_id: int) -> None:
    """Gọi mỗi phút từ vòng lặp (cùng chỗ với maybe_post_daily) - nếu kênh
    quá im ắng (không đủ tin để on_daily_channel_message tự bump) mà đã
    DAILY_REPOST_IDLE_HOURS giờ trôi qua kể từ lần đăng/bump gần nhất, tự
    đăng lại 1 lần để nhắc, tránh Daily bị quên lãng cả buổi."""
    now = vn_now()
    if now.hour >= DAILY_WINDOW_HOURS:
        return

    daily_state = await db.get_daily_state()
    if daily_state.get("date") != vn_today_str():
        return

    last_post_ts = daily_state.get("last_post_ts", 0)
    if time.time() - last_post_ts < DAILY_REPOST_IDLE_HOURS * 3600:
        return

    async with _daily_repost_lock:
        daily_state = await db.get_daily_state()  # đọc lại phòng vừa bị bump ở nhánh khác
        last_post_ts = daily_state.get("last_post_ts", 0)
        if time.time() - last_post_ts < DAILY_REPOST_IDLE_HOURS * 3600:
            return
        await _repost_daily(client, channel_id, daily_state)


# ===========================================================================
# Minigame (Games): Wordle · Đoán số · Kéo Búa Bao
#
# Mỗi ván có 1 game_id riêng (uuid ngắn, tra được qua lệnh /tra-game), lưu
# trong RAM (đủ nhanh, không cần bền qua restart vì ván chơi chỉ ngắn hạn).
# Nhập liệu (đoán từ/số) qua Modal thay vì gõ thẳng vào kênh chat, để tránh
# người khác lỡ gõ giùm/gõ nhầm và để nhiều ván chạy song song không đụng nhau.
# ===========================================================================

GAME_TYPE_LABELS = {
    "wordle": "🟩 Wordle",
    "guess_number": "🔢 Đoán số",
    "rps": "✊ Kéo Búa Bao",
    "taixiu": "🎲 Tài Xỉu",
    "xidach": "🃏 Xì Dách",
    "coinflip": "🪙 Lật Đồng Xu",
    "trivia": "🧠 Trivia",
    "chanle": "🎲 Chẵn Lẻ",
    "doanmau": "🎨 Đoán Màu",
    "vongquay": "🎡 Vòng Quay May Mắn",
}

_GAME_STATUS_LABELS = {
    "playing": "🟡 Đang chơi",
    "won": "🟢 Thắng",
    "lost": "🔴 Thua",
    "draw": "⚪ Hòa",
}

# game_id (str, 8 ký tự) -> {"type", "owner_id", "status", "created_at", ...}
_active_games: dict[str, dict] = {}


async def stop_game(game_id: str, user_id: int) -> tuple[bool, int | None]:
    """Dừng thủ công 1 ván đang chơi (nút Stop). Chỉ owner_id của ván mới dừng được.

    Trả (ok, refunded_amount). refunded_amount khác None nếu ván có cược tiền
    (Tài Xỉu/Xì Dách/Lật Đồng Xu) và tiền cược được hoàn lại vì ván chưa có kết quả.
    Ván bị dừng được XOÁ NGAY khỏi _active_games (giải phóng Game ID ngay lập
    tức) thay vì chờ GC theo TTL như ván đã có kết quả tự nhiên.
    """
    game = _active_games.get(game_id)
    if not game or game["owner_id"] != user_id or game["status"] != "playing":
        return False, None

    refunded = None
    if game["type"] in ("taixiu", "xidach", "coinflip") and game.get("bet"):
        refunded = await economy.add_mick(user_id, game["bet"])

    _active_games.pop(game_id, None)
    return True, refunded


def _new_game_id() -> str:
    gid = _uuid_lib.uuid4().hex[:8]
    while gid in _active_games:
        gid = _uuid_lib.uuid4().hex[:8]
    return gid


# Ván "playing" bị BỎ DỞ (user thoát ngang, không bấm Stop/không chơi tiếp)
# thì KHÔNG BAO GIỜ tự chuyển status khác "playing" -> _gc_games() cũ chỉ dọn
# ván đã xong (status != "playing") nên các ván bỏ dở này ở lại RAM MÃI MÃI,
# đây là 1 nguồn rò rỉ bộ nhớ thật sự nếu server chơi minigame nhiều mà
# không phải ai cũng bấm Stop. Dọn luôn ván "playing" quá cũ theo created_at.
_ABANDONED_GAME_TTL_SEC = 6 * 3600  # 6 giờ không động tới coi như bỏ dở


def _gc_games() -> None:
    """Dọn RAM: xoá ván đã kết thúc quá GAME_LOOKUP_TTL_SEC, VÀ xoá ván
    "playing" bị bỏ dở quá _ABANDONED_GAME_TTL_SEC (tránh rò rỉ bộ nhớ)."""
    now = time.time()
    stale = [
        gid
        for gid, g in _active_games.items()
        if (
            (g.get("status") != "playing" and now - g.get("finished_at", now) > GAME_LOOKUP_TTL_SEC)
            or (g.get("status") == "playing" and now - g.get("created_at", now) > _ABANDONED_GAME_TTL_SEC)
        )
    ]
    for gid in stale:
        _active_games.pop(gid, None)


def cleanup_stale_games() -> int:
    """Wrapper public cho _gc_games() - gọi định kỳ từ 1 tasks.loop trong
    discord_bot.py (KHÔNG chỉ dọn khi có người /tra-game như trước, vì ván
    bị bỏ dở có thể không ai tra tới -> nằm lại RAM mãi mãi). Trả về số
    lượng RAM entry hiện có SAU khi dọn (để log theo dõi)."""
    _gc_games()
    return len(_active_games)


def lookup_game(game_id: str) -> discord.ui.Container | None:
    """Tra thông tin 1 ván theo ID - chỉ đọc, dùng cho lệnh /tra-game."""
    _gc_games()
    game = _active_games.get(game_id.strip().lower())
    if not game:
        return None

    fields = [
        ("Loại game", GAME_TYPE_LABELS.get(game["type"], game["type"])),
        ("Người chơi", f"<@{game['owner_id']}>"),
        ("Trạng thái", _GAME_STATUS_LABELS.get(game.get("status"), "?")),
    ]
    if game.get("summary"):
        fields.append(("Chi tiết", game["summary"]))
    return build_container(
        title=f"🔎 Tra ván #{game_id}",
        color=discord.Color.blurple(),
        fields=fields,
        footer=f"ID ván: {game_id}",
    )


def user_active_wordle_id(user_id: int) -> str | None:
    """Trả về game_id ván Wordle đang chơi dở của user (nếu có), để chặn mở ván 2."""
    for gid, g in _active_games.items():
        if g["type"] == "wordle" and g["owner_id"] == user_id and g["status"] == "playing":
            return gid
    return None


# --- Wordle ---------------------------------------------------------------

_WORDLE_WORDS = [
    "apple", "table", "chair", "house", "mouse", "plane", "train", "brick", "cloud", "storm",
    "light", "night", "water", "fruit", "grape", "bread", "sugar", "honey", "spice", "sound",
    "plant", "trees", "grass", "stone", "sandy", "beach", "ocean", "river", "magic", "dream",
    "happy", "smile", "heart", "brain", "music", "dance", "paint", "write", "radio", "phone",
    "video", "movie", "actor", "drama", "novel", "story", "poems", "songs", "piano", "drums",
    "flute", "angle", "shape", "color", "black", "white", "green", "brown", "coral", "ivory",
    "amber", "olive", "lemon", "mango", "peach", "melon", "berry", "robot", "laser", "pixel",
    "cyber", "space", "earth", "venus",
]


def _feedback_row(answer: str, guess: str) -> str:
    """🟩 đúng vị trí, 🟨 đúng chữ sai vị trí, ⬛ không có trong từ (xử lý đúng chữ trùng lặp)."""
    result = ["⬛"] * 5
    answer_chars = list(answer)

    for i in range(5):
        if guess[i] == answer[i]:
            result[i] = "🟩"
            answer_chars[i] = None

    for i in range(5):
        if result[i] == "🟩":
            continue
        if guess[i] in answer_chars:
            result[i] = "🟨"
            answer_chars[answer_chars.index(guess[i])] = None

    return "".join(result)


def _render_board(game: dict) -> str:
    lines = []
    for guess, fb in zip(game["guesses"], game["feedback"]):
        lines.append(f"{fb}   `{guess.upper()}`")
    max_guesses = game.get("max_guesses", WORDLE_MAX_GUESSES)
    remaining = max_guesses - len(game["guesses"])
    lines.append(f"\nCòn **{remaining}** lượt đoán. Bấm nút \"Nhập từ\" bên dưới để đoán.")
    return "\n".join(lines) if game["guesses"] else "Bấm nút \"Nhập từ\" bên dưới để bắt đầu đoán 1 từ tiếng Anh 5 chữ!"


async def start_wordle(user_id: int) -> tuple[str, discord.ui.Container]:
    gid = _new_game_id()
    answer = random.choice(_WORDLE_WORDS)
    # Ronaldo Pasta (/mick-shop): +RONALDO_PASTA_EXTRA_GUESSES lượt đoán nếu
    # user đang sở hữu item còn hạn (xem get_active_shop_effect).
    max_guesses = WORDLE_MAX_GUESSES
    pasta = await get_active_shop_effect(user_id, "ronaldo_pasta")
    if pasta:
        max_guesses += RONALDO_PASTA_EXTRA_GUESSES
    _active_games[gid] = {
        "type": "wordle",
        "owner_id": user_id,
        "status": "playing",
        "created_at": time.time(),
        "answer": answer,
        "guesses": [],
        "feedback": [],
        "max_guesses": max_guesses,
    }
    container = build_container(
        title=f"🟩 Wordle · #{gid}",
        description=_render_board(_active_games[gid]),
        color=discord.Color.blurple(),
        footer=f"Đoán đúng nhận {WORDLE_WIN_REWARD} MICK · Tối đa {max_guesses} lượt",
    )
    return gid, container


def is_valid_guess(text: str) -> bool:
    return len(text) == 5 and all(c in string.ascii_letters for c in text)


async def process_wordle_guess(game_id: str, guess: str) -> tuple[discord.ui.Container, bool] | None:
    """Trả về (embed, finished) hoặc None nếu ván không tồn tại/đã kết thúc.
    finished=True nghĩa là ván đã kết thúc (thắng/thua) - ván vẫn tra được qua ID sau đó."""
    game = _active_games.get(game_id)
    if not game or game["type"] != "wordle" or game["status"] != "playing":
        return None

    guess = guess.lower()
    answer = game["answer"]
    user_id = game["owner_id"]

    fb = _feedback_row(answer, guess)
    game["guesses"].append(guess)
    game["feedback"].append(fb)

    won = guess == answer
    max_guesses = game.get("max_guesses", WORDLE_MAX_GUESSES)
    out_of_tries = len(game["guesses"]) >= max_guesses

    if won:
        new_balance = await economy.add_mick(user_id, WORDLE_WIN_REWARD)
        user = await db.get_user(user_id)
        wordle_wins = user.get("wordle_wins", 0) + 1
        await db.save_user(user_id, {"wordle_wins": wordle_wins})
        container = build_container(
            title=f"🎉 Wordle - Thắng! · #{game_id}",
            description=(
                f"{_render_board(game)}\n\n"
                f"Chính xác là **{answer.upper()}**! 💰 Bạn đã nhận **{WORDLE_WIN_REWARD} Mick**.\n"
                f"Số dư hiện tại: **{new_balance} MICK**."
            ),
            color=discord.Color.green(),
        )
        game["status"] = "won"
        game["finished_at"] = time.time()
        game["summary"] = f"Thắng sau {len(game['guesses'])} lượt · từ đúng **{answer.upper()}**"
        return container, True

    if out_of_tries:
        container = build_container(
            title=f"💀 Wordle - Hết lượt · #{game_id}",
            description=f"{_render_board(game)}\n\nTừ đúng là **{answer.upper()}**. Chúc may mắn lần sau!",
            color=discord.Color.red(),
        )
        game["status"] = "lost"
        game["finished_at"] = time.time()
        game["summary"] = f"Thua · từ đúng **{answer.upper()}**"
        return container, True

    container = build_container(
        title=f"🟩 Wordle · #{game_id}",
        description=_render_board(game),
        color=discord.Color.blurple(),
        footer=f"Đoán đúng nhận {WORDLE_WIN_REWARD} MICK · Tối đa {max_guesses} lượt",
    )
    return container, False


# --- Đoán số ----------------------------------------------------------------


def start_guess_number(user_id: int) -> tuple[str, discord.ui.Container]:
    gid = _new_game_id()
    secret = random.randint(1, GUESS_NUMBER_MAX)
    _active_games[gid] = {
        "type": "guess_number",
        "owner_id": user_id,
        "status": "playing",
        "created_at": time.time(),
        "secret": secret,
        "tries": 0,
        "history": [],
    }
    return gid, _guess_number_container(gid, _active_games[gid])


def _guess_number_container(gid: str, game: dict) -> discord.ui.Container:
    remaining = GUESS_NUMBER_MAX_TRIES - game["tries"]
    lines = [f"`{g}` → {hint}" for g, hint in game["history"]]
    desc = "\n".join(lines) if lines else "Chưa đoán lần nào."
    return build_container(
        title=f"🔢 Đoán số (1-{GUESS_NUMBER_MAX}) · #{gid}",
        description=f"{desc}\n\nCòn **{remaining}** lượt đoán. Bấm nút \"Nhập số\" bên dưới.",
        color=discord.Color.blurple(),
        footer=f"Đoán đúng nhận {GUESS_NUMBER_REWARD} MICK · Tối đa {GUESS_NUMBER_MAX_TRIES} lượt",
    )


async def process_guess_number(game_id: str, guess: int) -> tuple[discord.ui.Container, bool] | None:
    game = _active_games.get(game_id)
    if not game or game["type"] != "guess_number" or game["status"] != "playing":
        return None

    secret = game["secret"]
    user_id = game["owner_id"]
    game["tries"] += 1

    if guess == secret:
        new_balance = await economy.add_mick(user_id, GUESS_NUMBER_REWARD)
        game["history"].append((guess, "🎯 Chính xác!"))
        game["status"] = "won"
        game["finished_at"] = time.time()
        game["summary"] = f"Đoán đúng số **{secret}** sau {game['tries']} lượt"
        container = build_container(
            title=f"🎉 Đoán số - Thắng! · #{game_id}",
            description=(
                f"Số bí mật là **{secret}**! 💰 Bạn đã nhận **{GUESS_NUMBER_REWARD} Mick**.\n"
                f"Số dư hiện tại: **{new_balance} MICK**."
            ),
            color=discord.Color.green(),
        )
        return container, True

    hint = "⬆️ Lớn hơn số này" if guess < secret else "⬇️ Nhỏ hơn số này"
    game["history"].append((guess, hint))

    if game["tries"] >= GUESS_NUMBER_MAX_TRIES:
        game["status"] = "lost"
        game["finished_at"] = time.time()
        game["summary"] = f"Hết lượt · số đúng là **{secret}**"
        container = build_container(
            title=f"💀 Đoán số - Hết lượt · #{game_id}",
            description=f"Số bí mật là **{secret}**. Chúc may mắn lần sau!",
            color=discord.Color.red(),
        )
        return container, True

    container = _guess_number_container(game_id, game)
    return container, False


# --- Kéo Búa Bao --------------------------------------------------------------

RPS_CHOICE_LABELS = {"keo": "✂️ Kéo", "bua": "🪨 Búa", "bao": "📄 Bao"}
_RPS_BEATS = {"keo": "bao", "bua": "keo", "bao": "bua"}  # key thắng value


def start_rps(user_id: int) -> tuple[str, discord.ui.Container]:
    gid = _new_game_id()
    _active_games[gid] = {"type": "rps", "owner_id": user_id, "status": "playing", "created_at": time.time()}
    container = build_container(
        title=f"✊ Kéo Búa Bao · #{gid}",
        description="Chọn 1 trong 3 bên dưới để đấu với bot!",
        color=discord.Color.gold(),
        footer=f"Thắng nhận {RPS_WIN_REWARD} MICK",
    )
    return gid, container


async def process_rps(game_id: str, player_choice: str) -> discord.ui.Container | None:
    game = _active_games.get(game_id)
    if not game or game["type"] != "rps" or game["status"] != "playing":
        return None

    bot_choice = random.choice(list(RPS_CHOICE_LABELS.keys()))
    user_id = game["owner_id"]

    if player_choice == bot_choice:
        result_text, color, status = "🤝 Hòa! Không ai nhận MICK.", discord.Color.greyple(), "draw"
    elif _RPS_BEATS[player_choice] == bot_choice:
        new_balance = await economy.add_mick(user_id, RPS_WIN_REWARD)
        result_text = f"🎉 Bạn thắng! 💰 Bạn đã nhận **{RPS_WIN_REWARD} Mick** (số dư: {new_balance})"
        color, status = discord.Color.green(), "won"
    else:
        result_text, color, status = "😵 Bạn thua! Chúc may mắn lần sau.", discord.Color.red(), "lost"

    game["status"] = status
    game["finished_at"] = time.time()
    game["summary"] = f"Bạn: {RPS_CHOICE_LABELS[player_choice]} · Bot: {RPS_CHOICE_LABELS[bot_choice]} → {result_text}"

    container = build_container(
        title=f"✊ Kéo Búa Bao - Kết quả · #{game_id}",
        description=(
            f"Bạn chọn: {RPS_CHOICE_LABELS[player_choice]}\nBot chọn: {RPS_CHOICE_LABELS[bot_choice]}\n\n{result_text}"
        ),
        color=color,
    )
    return container


# --- Chẵn Lẻ (lắc 1 xúc xắc, đoán chẵn hay lẻ) -----------------------------


def start_chanle(user_id: int) -> tuple[str, discord.ui.Container]:
    gid = _new_game_id()
    _active_games[gid] = {"type": "chanle", "owner_id": user_id, "status": "playing", "created_at": time.time()}
    container = build_container(
        title=f"🎲 Chẵn Lẻ · #{gid}",
        description="Bot sẽ lắc 1 viên xúc xắc (1-6). Đoán xem kết quả là Chẵn hay Lẻ!",
        color=discord.Color.gold(),
        footer=f"Đoán đúng nhận {CHANLE_WIN_REWARD} MICK",
    )
    return gid, container


async def process_chanle(game_id: str, choice: str) -> discord.ui.Container | None:
    """choice: 'chan' hoặc 'le'."""
    game = _active_games.get(game_id)
    if not game or game["type"] != "chanle" or game["status"] != "playing":
        return None

    roll = random.randint(1, 6)
    is_even = roll % 2 == 0
    guessed_even = choice == "chan"
    user_id = game["owner_id"]
    won = is_even == guessed_even

    if won:
        new_balance = await economy.add_mick(user_id, CHANLE_WIN_REWARD)
        result_text = f"🎉 Chính xác! 💰 Bạn đã nhận **{CHANLE_WIN_REWARD} Mick** (số dư: {new_balance})"
        color, status = discord.Color.green(), "won"
    else:
        result_text, color, status = "😵 Đoán sai rồi! Chúc may mắn lần sau.", discord.Color.red(), "lost"

    game["status"] = status
    game["finished_at"] = time.time()
    parity_text = "Chẵn" if is_even else "Lẻ"
    game["summary"] = f"Xúc xắc ra **{roll}** ({parity_text}) · Bạn đoán **{'Chẵn' if guessed_even else 'Lẻ'}** → {result_text}"

    container = build_container(
        title=f"🎲 Chẵn Lẻ - Kết quả · #{game_id}",
        description=f"Xúc xắc ra: **{roll}** ({parity_text})\nBạn đoán: **{'Chẵn' if guessed_even else 'Lẻ'}**\n\n{result_text}",
        color=color,
    )
    return container


# --- Đoán Màu (chọn 1 trong 4 màu, khớp màu bot random thì thắng) ----------

DOANMAU_LABELS = {"do": "🔴 Đỏ", "xanh": "🔵 Xanh", "vang": "🟡 Vàng", "tim": "🟣 Tím"}


def start_doanmau(user_id: int) -> tuple[str, discord.ui.Container]:
    gid = _new_game_id()
    _active_games[gid] = {"type": "doanmau", "owner_id": user_id, "status": "playing", "created_at": time.time()}
    container = build_container(
        title=f"🎨 Đoán Màu · #{gid}",
        description="Bot sẽ random 1 trong 4 màu. Chọn đúng màu để thắng!",
        color=discord.Color.gold(),
        footer=f"Đoán đúng nhận {DOANMAU_WIN_REWARD} MICK (tỉ lệ 1/4)",
    )
    return gid, container


async def process_doanmau(game_id: str, choice: str) -> discord.ui.Container | None:
    game = _active_games.get(game_id)
    if not game or game["type"] != "doanmau" or game["status"] != "playing":
        return None

    bot_choice = random.choice(list(DOANMAU_LABELS.keys()))
    user_id = game["owner_id"]
    won = choice == bot_choice

    if won:
        new_balance = await economy.add_mick(user_id, DOANMAU_WIN_REWARD)
        result_text = f"🎉 Trúng màu! 💰 Bạn đã nhận **{DOANMAU_WIN_REWARD} Mick** (số dư: {new_balance})"
        color, status = discord.Color.green(), "won"
    else:
        result_text, color, status = "😵 Không trùng màu bot chọn. Chúc may mắn lần sau!", discord.Color.red(), "lost"

    game["status"] = status
    game["finished_at"] = time.time()
    game["summary"] = f"Bạn: {DOANMAU_LABELS[choice]} · Bot: {DOANMAU_LABELS[bot_choice]} → {result_text}"

    container = build_container(
        title=f"🎨 Đoán Màu - Kết quả · #{game_id}",
        description=f"Bạn chọn: {DOANMAU_LABELS[choice]}\nBot chọn: {DOANMAU_LABELS[bot_choice]}\n\n{result_text}",
        color=color,
    )
    return container


# --- Vòng Quay May Mắn (không thua, chỉ random mức thưởng) ----------------


def start_vongquay(user_id: int) -> tuple[str, discord.ui.Container]:
    gid = _new_game_id()
    _active_games[gid] = {"type": "vongquay", "owner_id": user_id, "status": "playing", "created_at": time.time()}
    container = build_container(
        title=f"🎡 Vòng Quay May Mắn · #{gid}",
        description="Bấm \"Quay\" để nhận ngay 1 mức thưởng MICK ngẫu nhiên - không bao giờ thua!",
        color=discord.Color.gold(),
        footer=f"Mức thưởng có thể ra: {', '.join(str(v) for v in VONGQUAY_REWARD_TIERS)} MICK",
    )
    return gid, container


async def process_vongquay(game_id: str) -> discord.ui.Container | None:
    game = _active_games.get(game_id)
    if not game or game["type"] != "vongquay" or game["status"] != "playing":
        return None

    reward = random.choice(VONGQUAY_REWARD_TIERS)
    user_id = game["owner_id"]
    new_balance = await economy.add_mick(user_id, reward)

    game["status"] = "won"
    game["finished_at"] = time.time()
    game["summary"] = f"Vòng quay ra **{reward} MICK**"

    container = build_container(
        title=f"🎡 Vòng Quay May Mắn - Kết quả · #{game_id}",
        description=f"🎉 Vòng quay dừng ở **{reward} MICK**! 💰 Số dư hiện tại: **{new_balance} MICK**.",
        color=discord.Color.green(),
    )
    return container


# ===========================================================================
# Casino: Tài Xỉu (lắc 3 xúc xắc, tổng 11-18 = Tài, 3-10 = Xỉu)
#
# Khác với Wordle/Đoán số/RPS (không mất tiền để chơi), 2 game casino này ăn
# thua MICK thật -> tiền cược bị trừ NGAY LÚC ĐẶT qua economy.place_bet()
# (atomic, có lock user_id) để không thể lách cược vượt số dư bằng cách spam
# nhiều lệnh cùng lúc - học từ bug dupe MICK ở /chuyển-tiền trước đó.
# ===========================================================================


def start_taixiu(user_id: int, bet: int, wallet_after_bet: int) -> tuple[str, discord.ui.Container]:
    gid = _new_game_id()
    _active_games[gid] = {
        "type": "taixiu",
        "owner_id": user_id,
        "status": "playing",
        "created_at": time.time(),
        "bet": bet,
    }
    container = build_container(
        title=f"🎲 Tài Xỉu · #{gid}",
        description=(
            f"Cược **{bet} MICK** đã bị trừ (số dư còn: **{wallet_after_bet}**).\n"
            f"Chọn **Tài** (11-18) hoặc **Xỉu** (3-10) bên dưới rồi bot lắc 3 xúc xắc!"
        ),
        color=discord.Color.gold(),
        footer=f"Thắng ăn x{TAIXIU_PAYOUT_MULTIPLIER:.0f} tiền cược",
    )
    return gid, container


async def process_taixiu(game_id: str, choice: str) -> discord.ui.Container | None:
    """choice: 'tai' hoặc 'xiu'. Trả None nếu ván không hợp lệ."""
    game = _active_games.get(game_id)
    if not game or game["type"] != "taixiu" or game["status"] != "playing":
        return None

    dice = [random.randint(1, 6) for _ in range(3)]
    total = sum(dice)
    result = "tai" if total >= 11 else "xiu"
    user_id = game["owner_id"]
    bet = game["bet"]
    dice_text = " ".join(f"🎲{d}" for d in dice)

    won = choice == result
    if won:
        payout = round(bet * TAIXIU_PAYOUT_MULTIPLIER)
        new_balance = await economy.add_mick(user_id, payout)
        desc = (
            f"{dice_text} = **{total}** → **{'Tài' if result == 'tai' else 'Xỉu'}**\n\n"
            f"🎉 Bạn thắng! 💰 Bạn đã nhận **{payout} Mick** (số dư: {new_balance})"
        )
        color, status = discord.Color.green(), "won"
    else:
        desc = (
            f"{dice_text} = **{total}** → **{'Tài' if result == 'tai' else 'Xỉu'}**\n\n"
            f"😵 Bạn thua, mất **{bet} MICK** đã cược."
        )
        color, status = discord.Color.red(), "lost"

    game["status"] = status
    game["finished_at"] = time.time()
    game["summary"] = f"Cược {bet} MICK vào {choice} · Kết quả xúc xắc: {total} ({result}) → {status}"

    container = build_container(title=f"🎲 Tài Xỉu - Kết quả · #{game_id}", description=desc, color=color)
    return container


# ===========================================================================
# Casino: Lật Đồng Xu (Coinflip) - 50/50 đơn giản, chọn Ngửa/Sấp. Cùng cơ chế
# trừ tiền ngay lúc đặt (place_bet) như Tài Xỉu/Xì Dách. Trả thấp hơn x2 một
# chút (COINFLIP_PAYOUT_MULTIPLIER, mặc định x1.9) để nhà cái giữ biên nhẹ,
# tương tự cách các casino thật vận hành coinflip 50/50.
# ===========================================================================


def start_coinflip(user_id: int, bet: int, wallet_after_bet: int) -> tuple[str, discord.ui.Container]:
    gid = _new_game_id()
    _active_games[gid] = {
        "type": "coinflip",
        "owner_id": user_id,
        "status": "playing",
        "created_at": time.time(),
        "bet": bet,
    }
    container = build_container(
        title=f"🪙 Lật Đồng Xu · #{gid}",
        description=(
            f"Cược **{bet} MICK** đã bị trừ (số dư còn: **{wallet_after_bet}**).\n"
            f"Chọn **Ngửa** hoặc **Sấp** bên dưới rồi bot tung đồng xu!"
        ),
        color=discord.Color.gold(),
        footer=f"Thắng ăn x{COINFLIP_PAYOUT_MULTIPLIER:.1f} tiền cược",
    )
    return gid, container


async def process_coinflip(game_id: str, choice: str) -> discord.ui.Container | None:
    """choice: 'ngua' hoặc 'sap'. Trả None nếu ván không hợp lệ."""
    game = _active_games.get(game_id)
    if not game or game["type"] != "coinflip" or game["status"] != "playing":
        return None

    result = random.choice(("ngua", "sap"))
    user_id = game["owner_id"]
    bet = game["bet"]
    result_text = "Ngửa 🌕" if result == "ngua" else "Sấp 🌑"

    won = choice == result
    if won:
        payout = round(bet * COINFLIP_PAYOUT_MULTIPLIER)
        new_balance = await economy.add_mick(user_id, payout)
        desc = (
            f"🪙 Đồng xu ra: **{result_text}**\n\n"
            f"🎉 Bạn thắng! 💰 Bạn đã nhận **{payout} Mick** (số dư: {new_balance})"
        )
        color, status = discord.Color.green(), "won"
    else:
        desc = (
            f"🪙 Đồng xu ra: **{result_text}**\n\n"
            f"😵 Bạn thua, mất **{bet} MICK** đã cược."
        )
        color, status = discord.Color.red(), "lost"

    game["status"] = status
    game["finished_at"] = time.time()
    game["summary"] = f"Cược {bet} MICK vào {choice} · Đồng xu ra {result} → {status}"

    container = build_container(title=f"🪙 Lật Đồng Xu - Kết quả · #{game_id}", description=desc, color=color)
    return container


# ===========================================================================
# Casino: Xì Dách (Blackjack rút gọn) - rút bài đấu bot, gần 21 nhất thắng,
# quá 21 (quắc) thua luôn. Xì Bàng (2 lá = 21, có Át) hoặc Ngũ Linh (5 lá
# không quắc) ăn x3, thắng thường ăn x2.
# ===========================================================================

_CARD_RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]


def _card_value(rank: str) -> int:
    if rank == "A":
        return 11  # tính đơn giản: Át = 11, cộng thêm bù trừ ở _hand_value
    if rank in ("J", "Q", "K"):
        return 10
    return int(rank)


def _hand_value(hand: list[str]) -> int:
    total = sum(_card_value(r) for r in hand)
    aces = hand.count("A")
    while total > 21 and aces > 0:
        total -= 10  # Át tính lại thành 1 thay vì 11 để đỡ quắc
        aces -= 1
    return total


def _draw_card() -> str:
    return random.choice(_CARD_RANKS)


def _hand_text(hand: list[str]) -> str:
    return " ".join(hand) + f" (= {_hand_value(hand)})"


def start_xidach(user_id: int, bet: int, wallet_after_bet: int) -> tuple[str, discord.ui.Container]:
    gid = _new_game_id()
    player = [_draw_card(), _draw_card()]
    bot_hand = [_draw_card(), _draw_card()]
    _active_games[gid] = {
        "type": "xidach",
        "owner_id": user_id,
        "status": "playing",
        "created_at": time.time(),
        "bet": bet,
        "player": player,
        "bot": bot_hand,
    }

    player_val = _hand_value(player)
    desc = (
        f"Cược **{bet} MICK** đã bị trừ (số dư còn: **{wallet_after_bet}**).\n\n"
        f"Bài của bạn: {_hand_text(player)}\n"
        f"Bài bot: {bot_hand[0]} ❓\n\n"
        f"Bấm **Rút thêm** để lấy 1 lá, hoặc **Dằn bài** để dừng và so bài với bot."
    )
    if player_val == 21:
        desc += "\n\n✨ **Xì Bàng!** (21 điểm với 2 lá) — dằn bài để ăn x3!"

    container = build_container(
        title=f"🃏 Xì Dách · #{gid}",
        description=desc,
        color=discord.Color.gold(),
        footer=f"Thắng ăn x{XIDACH_PAYOUT_MULTIPLIER:.0f} · Xì Bàng/Ngũ Linh ăn x{XIDACH_BONUS_MULTIPLIER:.0f}",
    )
    return gid, container


def xidach_draw(game_id: str) -> tuple[discord.ui.Container, bool] | None:
    """Người chơi rút thêm 1 lá. Trả (embed, finished). finished=True nếu quắc (>21)."""
    game = _active_games.get(game_id)
    if not game or game["type"] != "xidach" or game["status"] != "playing":
        return None

    game["player"].append(_draw_card())
    player_val = _hand_value(game["player"])

    if player_val > 21:
        bet = game["bet"]
        game["status"] = "lost"
        game["finished_at"] = time.time()
        game["summary"] = f"Cược {bet} MICK · Quắc {player_val} điểm → thua"
        container = build_container(
            title=f"🃏 Xì Dách - Quắc rồi! · #{game_id}",
            description=(
                f"Bài của bạn: {_hand_text(game['player'])}\n\n"
                f"💥 Quắc! Quá 21 điểm, bạn mất **{bet} MICK** đã cược."
            ),
            color=discord.Color.red(),
        )
        return container, True

    container = build_container(
        title=f"🃏 Xì Dách · #{game_id}",
        description=(
            f"Bài của bạn: {_hand_text(game['player'])}\n"
            f"Bài bot: {game['bot'][0]} ❓\n\n"
            f"Bấm **Rút thêm** để lấy thêm 1 lá, hoặc **Dằn bài** để dừng."
        ),
        color=discord.Color.gold(),
    )
    return container, False


async def xidach_stand(game_id: str) -> discord.ui.Container | None:
    """Người chơi dằn bài: bot tự rút đến khi >=17 điểm, rồi so bài."""
    game = _active_games.get(game_id)
    if not game or game["type"] != "xidach" or game["status"] != "playing":
        return None

    user_id = game["owner_id"]
    bet = game["bet"]
    player = game["player"]
    bot_hand = game["bot"]

    while _hand_value(bot_hand) < 17:
        bot_hand.append(_draw_card())

    player_val = _hand_value(player)
    bot_val = _hand_value(bot_hand)
    bot_bust = bot_val > 21

    # Xì Bàng: đúng 21 với 2 lá đầu (kèm Át). Ngũ Linh: 5 lá mà không quắc.
    is_xi_bang = player_val == 21 and len(player) == 2
    is_ngu_linh = len(player) >= 5 and player_val <= 21
    is_bonus = is_xi_bang or is_ngu_linh

    if bot_bust or player_val > bot_val:
        multiplier = XIDACH_BONUS_MULTIPLIER if is_bonus else XIDACH_PAYOUT_MULTIPLIER
        payout = round(bet * multiplier)
        new_balance = await economy.add_mick(user_id, payout)
        bonus_text = " ✨ (Xì Bàng/Ngũ Linh, ăn x{:.0f}!)".format(XIDACH_BONUS_MULTIPLIER) if is_bonus else ""
        desc = (
            f"Bài của bạn: {_hand_text(player)}\nBài bot: {_hand_text(bot_hand)}"
            f"{' (quắc!)' if bot_bust else ''}\n\n"
            f"🎉 Bạn thắng!{bonus_text} 💰 Bạn đã nhận **{payout} Mick** (số dư: {new_balance})"
        )
        color, status = discord.Color.green(), "won"
    elif player_val == bot_val:
        new_balance = await economy.add_mick(user_id, bet)  # hòa: trả lại tiền cược
        desc = (
            f"Bài của bạn: {_hand_text(player)}\nBài bot: {_hand_text(bot_hand)}\n\n"
            f"🤝 Hòa! Bạn được trả lại **{bet} MICK** đã cược (số dư: {new_balance})"
        )
        color, status = discord.Color.greyple(), "draw"
    else:
        desc = (
            f"Bài của bạn: {_hand_text(player)}\nBài bot: {_hand_text(bot_hand)}\n\n"
            f"😵 Bạn thua, mất **{bet} MICK** đã cược."
        )
        color, status = discord.Color.red(), "lost"

    game["status"] = status
    game["finished_at"] = time.time()
    game["summary"] = f"Cược {bet} MICK · Bạn {player_val} vs Bot {bot_val} → {status}"

    return build_container(title=f"🃏 Xì Dách - Kết quả · #{game_id}", description=desc, color=color)


# ===========================================================================
# Trivia đố vui: không cược tiền, trả lời đúng nhận thưởng cố định
# (TRIVIA_REWARD_MICK). Câu hỏi lấy ngẫu nhiên từ bộ có sẵn.
# ===========================================================================

_TRIVIA_QUESTIONS = [
    {"q": "Thủ đô của Việt Nam là gì?", "options": ["Hà Nội", "TP.HCM", "Đà Nẵng", "Huế"], "answer": 0},
    {"q": "Hành tinh nào gần Mặt Trời nhất?", "options": ["Sao Kim", "Sao Thủy", "Trái Đất", "Sao Hỏa"], "answer": 1},
    {"q": "1 giờ có bao nhiêu phút?", "options": ["100", "24", "60", "30"], "answer": 2},
    {"q": "Ngôn ngữ lập trình nào dùng để viết Minecraft Bedrock addon?", "options": ["Python", "JavaScript", "Java thuần", "C#"], "answer": 1},
    {"q": "Con vật nào được coi là 'vua rừng xanh'?", "options": ["Voi", "Hổ", "Sư tử", "Gấu"], "answer": 2},
    {"q": "Nước nào có diện tích lớn nhất thế giới?", "options": ["Trung Quốc", "Mỹ", "Canada", "Nga"], "answer": 3},
    {"q": "Đơn vị đo tốc độ khung hình trong game thường gọi là gì?", "options": ["FPS", "MPH", "RPM", "GHz"], "answer": 0},
    {"q": "Số nào là số nguyên tố?", "options": ["9", "15", "17", "21"], "answer": 2},
    {"q": "Việt Nam có bao nhiêu tỉnh/thành giáp biển?", "options": ["18", "28", "35", "40"], "answer": 1},
    {"q": "Sông nào dài nhất Việt Nam?", "options": ["Sông Hồng", "Sông Mê Kông", "Sông Đồng Nai", "Sông Mã"], "answer": 1},
    {"q": "1 năm ánh sáng dùng để đo đại lượng nào?", "options": ["Thời gian", "Khối lượng", "Khoảng cách", "Nhiệt độ"], "answer": 2},
    {"q": "Kim loại nào nhẹ nhất trong các kim loại phổ biến?", "options": ["Nhôm", "Sắt", "Liti", "Đồng"], "answer": 2},
    {"q": "Ai được coi là cha đẻ của thuyết tương đối?", "options": ["Newton", "Einstein", "Tesla", "Bohr"], "answer": 1},
    {"q": "Trong bóng đá, 1 đội có tối đa bao nhiêu cầu thủ trên sân (kể cả thủ môn)?", "options": ["9", "10", "11", "12"], "answer": 2},
    {"q": "HTML là viết tắt của gì?", "options": ["HyperText Markup Language", "HighText Machine Language", "HyperTransfer Markup Language", "HyperText Making Language"], "answer": 0},
    {"q": "Núi nào cao nhất thế giới?", "options": ["K2", "Everest", "Fansipan", "Kilimanjaro"], "answer": 1},
    {"q": "Đại dương nào lớn nhất thế giới?", "options": ["Đại Tây Dương", "Ấn Độ Dương", "Bắc Băng Dương", "Thái Bình Dương"], "answer": 3},
    {"q": "1 tuần có bao nhiêu giờ?", "options": ["148", "156", "168", "172"], "answer": 2},
    {"q": "Loài động vật nào lớn nhất hành tinh?", "options": ["Voi châu Phi", "Cá voi xanh", "Cá mập trắng", "Hươu cao cổ"], "answer": 1},
    {"q": "GPU thường được dùng chủ yếu để làm gì trong máy tính?", "options": ["Lưu trữ dữ liệu", "Xử lý đồ họa", "Kết nối mạng", "Tản nhiệt"], "answer": 1},
    {"q": "Ai là tác giả của 'Truyện Kiều'?", "options": ["Nguyễn Trãi", "Nguyễn Du", "Hồ Xuân Hương", "Nguyễn Đình Chiểu"], "answer": 1},
    {"q": "Màu nào không có trong cầu vồng 7 sắc?", "options": ["Chàm", "Lục", "Hồng", "Cam"], "answer": 2},
    {"q": "Đơn vị tiền tệ chính thức của Nhật Bản là gì?", "options": ["Yên", "Won", "Nhân dân tệ", "Baht"], "answer": 0},
    {"q": "Trong 1 bộ bài Tây tiêu chuẩn có bao nhiêu lá?", "options": ["48", "52", "54", "56"], "answer": 1},
    {"q": "Loài chim nào không biết bay?", "options": ["Đại bàng", "Chim cánh cụt", "Chim ưng", "Chim én"], "answer": 1},
    {"q": "1 byte bằng bao nhiêu bit?", "options": ["4", "8", "16", "32"], "answer": 1},
    {"q": "Kỳ quan nào trong 7 kỳ quan cổ đại còn tồn tại đến ngày nay?", "options": ["Vườn treo Babylon", "Đại kim tự tháp Giza", "Tượng thần Zeus", "Đền Artemis"], "answer": 1},
    {"q": "Việt Nam giành huy chương vàng Olympic đầu tiên ở môn thể thao nào?", "options": ["Cử tạ", "Bắn súng", "Taekwondo", "Boxing"], "answer": 1},
    {"q": "Quốc gia nào có dân số đông nhất thế giới hiện nay?", "options": ["Trung Quốc", "Mỹ", "Ấn Độ", "Indonesia"], "answer": 2},
    {"q": "Trong hệ Mặt Trời, hành tinh nào được mệnh danh 'hành tinh đỏ'?", "options": ["Sao Kim", "Sao Hỏa", "Sao Thổ", "Sao Mộc"], "answer": 1},
]


def start_trivia(user_id: int) -> tuple[str, discord.ui.Container, list[str]]:
    gid = _new_game_id()
    q = random.choice(_TRIVIA_QUESTIONS)
    _active_games[gid] = {
        "type": "trivia",
        "owner_id": user_id,
        "status": "playing",
        "created_at": time.time(),
        "question": q["q"],
        "options": q["options"],
        "answer_index": q["answer"],
    }
    container = build_container(
        title=f"🧠 Trivia · #{gid}",
        description=q["q"],
        color=discord.Color.blurple(),
        footer=f"Trả lời đúng nhận {TRIVIA_REWARD_MICK} MICK · {TRIVIA_TIMEOUT_SEC}s để trả lời",
    )
    return gid, container, q["options"]


async def process_trivia(game_id: str, chosen_index: int) -> discord.ui.Container | None:
    game = _active_games.get(game_id)
    if not game or game["type"] != "trivia" or game["status"] != "playing":
        return None

    correct_index = game["answer_index"]
    options = game["options"]
    user_id = game["owner_id"]
    won = chosen_index == correct_index

    if won:
        new_balance = await economy.add_mick(user_id, TRIVIA_REWARD_MICK)
        desc = f"✅ Chính xác! Đáp án là **{options[correct_index]}**.\n💰 Bạn đã nhận **{TRIVIA_REWARD_MICK} Mick** (số dư: {new_balance})"
        color, status = discord.Color.green(), "won"
    else:
        desc = f"❌ Sai rồi! Đáp án đúng là **{options[correct_index]}**."
        color, status = discord.Color.red(), "lost"

    game["status"] = status
    game["finished_at"] = time.time()
    game["summary"] = f"Câu hỏi: {game['question']} · Chọn: {options[chosen_index]} → {status}"

    return build_container(title=f"🧠 Trivia - Kết quả · #{game_id}", description=desc, color=color)


async def trivia_timeout(game_id: str) -> None:
    """Gọi khi hết giờ mà chưa trả lời - đánh dấu ván kết thúc (thua, không cộng gì)."""
    game = _active_games.get(game_id)
    if not game or game["type"] != "trivia" or game["status"] != "playing":
        return
    game["status"] = "lost"
    game["finished_at"] = time.time()
    game["summary"] = f"Câu hỏi: {game['question']} · Hết giờ, không trả lời"


# ===========================================================================
# OTP xác minh /chuyển-tiền qua DM
#
# Lưu trong RAM (không cần bền qua restart, OTP chỉ sống TRANSFER_OTP_TTL_SEC
# giây). Key theo user_id vì 1 người chỉ nên có 1 giao dịch chờ xác minh tại
# 1 thời điểm - tạo OTP mới sẽ ghi đè OTP cũ của chính họ (huỷ giao dịch cũ).
# ===========================================================================

# user_id -> {"code", "to_id", "amount", "expires_at", "attempts"}
_pending_transfers: dict[int, dict] = {}


def create_transfer_otp(user_id: int, to_id: int, amount: int) -> str:
    from config import TRANSFER_OTP_LENGTH, TRANSFER_OTP_TTL_SEC

    code = "".join(random.choices(string.digits, k=TRANSFER_OTP_LENGTH))
    _pending_transfers[user_id] = {
        "code": code,
        "to_id": to_id,
        "amount": amount,
        "expires_at": time.time() + TRANSFER_OTP_TTL_SEC,
        "attempts": 0,
    }
    return code


def verify_transfer_otp(user_id: int, entered_code: str) -> dict:
    """Trả {ok, reason?, to_id?, amount?}. reason: expired/not_found/wrong_code/too_many_attempts."""
    from config import TRANSFER_OTP_MAX_ATTEMPTS

    pending = _pending_transfers.get(user_id)
    if not pending:
        return {"ok": False, "reason": "not_found"}

    if time.time() > pending["expires_at"]:
        _pending_transfers.pop(user_id, None)
        return {"ok": False, "reason": "expired"}

    if pending["attempts"] >= TRANSFER_OTP_MAX_ATTEMPTS:
        _pending_transfers.pop(user_id, None)
        return {"ok": False, "reason": "too_many_attempts"}

    if entered_code.strip() != pending["code"]:
        pending["attempts"] += 1
        return {"ok": False, "reason": "wrong_code"}

    _pending_transfers.pop(user_id, None)  # dùng 1 lần, xong xoá luôn
    return {"ok": True, "to_id": pending["to_id"], "amount": pending["amount"]}


# ===========================================================================
# Code quà mốc thành viên (member milestone)
#
# Khi server đạt mốc tròn chục/trăm member (xem on_member_join trong
# discord_bot.py), bot tự tạo 1 code kiểu "ab12cd-MemberUp3456", giới hạn
# MEMBER_MILESTONE_CODE_MAX_USES lượt nhập và tự hết hạn sau
# MEMBER_MILESTONE_CODE_TTL_SEC giây. Ai nhập trước (qua lệnh /nhap-code)
# và còn hạn/còn lượt thì được cộng thẳng MEMBER_MILESTONE_CODE_REWARD_MICK
# MICK, mỗi người chỉ nhập được 1 lần/code.
#
# Lock riêng theo code (không dùng chung economy.user_lock vì đây là ghi
# đè list "used_by" của DOCUMENT CODE, không phải ví của 1 user) để 2 người
# bấm nhập cùng lúc không bị mất lượt do đọc-sửa-ghi chồng nhau.
# ===========================================================================

_milestone_code_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def generate_milestone_code() -> str:
    prefix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    suffix = "".join(random.choices(string.digits, k=4))
    return f"{prefix}-MemberUp{suffix}"


async def create_milestone_code(guild_id: int, member_count: int) -> dict:
    """Tạo + lưu 1 code quà mốc thành viên mới, trả về dict thông tin code
    (kể cả khi lưu DB thất bại - caller vẫn có thể thông báo code, chỉ là
    nó sẽ không redeem được nếu DB thật sự lỗi)."""
    code = generate_milestone_code()
    now = time.time()
    data = {
        "guild_id": guild_id,
        "member_count": member_count,
        "reward": MEMBER_MILESTONE_CODE_REWARD_MICK,
        "max_uses": MEMBER_MILESTONE_CODE_MAX_USES,
        "used_by": [],
        "created_at": now,
        "expires_at": now + MEMBER_MILESTONE_CODE_TTL_SEC,
    }
    await db.save_milestone_code(code, data)
    return {"code": code, **data}


async def redeem_milestone_code(user_id: int, code: str) -> dict:
    """Trả {ok, reward, new_balance, remaining} hoặc
    {ok: False, reason: not_found/expired/already_used/full}."""
    code = code.strip()
    async with _milestone_code_locks[code]:
        doc = await db.get_milestone_code(code)
        if not doc:
            return {"ok": False, "reason": "not_found"}
        if time.time() > doc.get("expires_at", 0):
            return {"ok": False, "reason": "expired"}

        used_by = list(doc.get("used_by") or [])
        if user_id in used_by:
            return {"ok": False, "reason": "already_used"}
        if len(used_by) >= doc.get("max_uses", 0):
            return {"ok": False, "reason": "full"}

        used_by.append(user_id)
        await db.save_milestone_code(code, {"used_by": used_by})
        reward = doc.get("reward", 0)

    new_balance = await economy.add_mick(user_id, reward)
    return {
        "ok": True,
        "reward": reward,
        "new_balance": new_balance,
        "remaining": doc.get("max_uses", 0) - len(used_by),
    }


# ===========================================================================
# Kỉ niệm N tháng thành lập server + thống kê emoji dùng nhiều nhất tháng
#
# - Mỗi tin nhắn/reaction có emoji được cộng dồn vào RAM (giống cơ chế "học
#   từ" ở ai_chat.py), rồi 1 tasks.loop trong discord_bot.py flush theo batch
#   lên Firebase (đỡ tốn quota so với ghi từng emoji 1 lần).
# - Mỗi ngày, maybe_post_anniversary() kiểm tra: nếu hôm nay đúng "ngày sinh"
#   của server (theo guild.created_at) và THÁNG NÀY chưa đăng, thì đăng
#   "Kỉ niệm N tháng" kèm top emoji của tháng qua, rồi reset bộ đếm.
# ===========================================================================

_CUSTOM_EMOJI_RE = re.compile(r"<a?:\w+:\d+>")
_UNICODE_EMOJI_CHAR_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000026FF"
    "\U00002700-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "\U00002B00-\U00002BFF"
    "\U0001F900-\U0001F9FF"
    "]"
)

_pending_emoji_counts: dict[str, int] = {}
_PENDING_EMOJI_CAP = 1000  # chặn RAM phình nếu chat cực đông mà chưa kịp flush


def track_emojis_in_text(content: str) -> None:
    """Cộng dồn (RAM) số lần xuất hiện của mỗi emoji (custom + unicode) trong
    1 đoạn text. Chỉ cộng RAM, xem flush_emoji_counts() để biết khi nào thật
    sự ghi lên Firebase - gọi từ on_message trong discord_bot.py."""
    if not content or len(_pending_emoji_counts) >= _PENDING_EMOJI_CAP:
        return
    found = _CUSTOM_EMOJI_RE.findall(content) + _UNICODE_EMOJI_CHAR_RE.findall(content)
    for emoji in found:
        _pending_emoji_counts[emoji] = _pending_emoji_counts.get(emoji, 0) + 1


def track_emoji_reaction(emoji_display: str) -> None:
    """Ghi nhận 1 lượt reaction bằng emoji - gọi từ on_reaction_add trong discord_bot.py."""
    if not emoji_display or len(_pending_emoji_counts) >= _PENDING_EMOJI_CAP:
        return
    _pending_emoji_counts[emoji_display] = _pending_emoji_counts.get(emoji_display, 0) + 1


async def flush_emoji_counts() -> None:
    """Đẩy toàn bộ số đếm emoji đang chờ trong RAM lên Firebase theo batch.
    Gọi định kỳ (vài phút/lần) từ 1 tasks.loop - KHÔNG gọi trực tiếp mỗi tin nhắn."""
    if not _pending_emoji_counts:
        return
    counts = dict(_pending_emoji_counts)
    _pending_emoji_counts.clear()
    try:
        await db.bump_emoji_counts(counts)
    except Exception as e:
        log.warning("Flush %d emoji lên Firebase lỗi (có thể mất lượt đếm đợt này): %s", len(counts), e)


async def build_monthly_emoji_summary(top_n: int = 20) -> str:
    """Trả text liệt kê top emoji được dùng nhiều nhất kể từ lần reset gần
    nhất (dùng thẳng vào nội dung thông báo kỉ niệm tháng)."""
    stats = await db.get_emoji_stats()
    rows = [
        (emoji, data.get("count", 0) if isinstance(data, dict) else 0)
        for emoji, data in stats.items()
    ]
    rows = [r for r in rows if r[1] > 0]
    if not rows:
        return "_(chưa có emoji nào được ghi nhận trong tháng qua 🥲)_"

    rows.sort(key=lambda r: r[1], reverse=True)
    return "\n".join(f"{i}. {emoji} — **{count}** lượt" for i, (emoji, count) in enumerate(rows[:top_n], start=1))


async def maybe_post_anniversary(client: discord.Client, channel_id: int, guild: discord.Guild) -> None:
    """Gọi định kỳ (vd 1 lần/ngày) từ 1 tasks.loop. Nếu hôm nay đúng ngày
    "sinh nhật" server (cùng ngày-trong-tháng với guild.created_at) và tháng
    này CHƯA đăng, thì đăng thông báo kỉ niệm N tháng kèm top emoji dùng
    nhiều nhất tháng qua vào channel_id, rồi reset bộ đếm emoji cho chu kỳ tiếp theo."""
    now = vn_now()
    created = guild.created_at
    if now.day != created.day:
        return

    month_key = now.strftime("%Y-%m")
    state = await db.get_anniversary_state()
    if state.get("last_posted_month") == month_key:
        return  # tháng này đăng rồi

    months = (now.year - created.year) * 12 + (now.month - created.month)
    if months <= 0:
        return  # server chưa tròn tháng nào (vd mới lập trong tháng này)

    channel = client.get_channel(channel_id)
    if channel is None:
        try:
            channel = await client.fetch_channel(channel_id)
        except Exception:
            return

    await flush_emoji_counts()  # đẩy nốt phần đang chờ trong RAM trước khi tổng kết
    summary = await build_monthly_emoji_summary()

    try:
        await channel.send(
            f"🎉 Kỉ niệm {months} tháng thành lập server Delta Mick\n\n"
            f"**Top emoji được dùng nhiều nhất trong tháng qua:**\n{summary}"
        )
    except Exception as e:
        log.warning("Đăng kỉ niệm tháng lỗi: %s", e)
        return

    await db.save_anniversary_state({"last_posted_month": month_key})
    await db.reset_emoji_stats()


def cancel_transfer_otp(user_id: int) -> None:
    _pending_transfers.pop(user_id, None)


# ===========================================================================
# Kinh doanh: quán / công ty / nhà trọ / khách sạn (Business)
#
# Mở cơ sở tốn 1 lần MICK, sau đó thuê nhân viên (mỗi nhân viên tốn MICK cố
# định). Mỗi BUSINESS_TICK_SEC (mặc định 30 phút), mỗi nhân viên đang thuê
# tạo ra thu nhập cho chủ - kể cả khi chủ offline, vì tick chạy nền trong
# loop của bot (không phụ thuộc user đang online).
# ===========================================================================

BUSINESS_NAMES = {
    "quan": "🍜 Quán ăn",
    "congty": "🏢 Công ty",
    "nhatro": "🏠 Nhà trọ",
    "khachsan": "🏨 Khách sạn",
    "hotoc": "💈 Tiệm hớt tóc",
    "taphoa": "🏪 Tiệm tạp hoá",
    "gym": "🏋️ Phòng gym",
    "chebien": "🍱 Xưởng chế biến đồ ăn",
}
BUSINESS_KINDS = list(BUSINESS_NAMES.keys())

DEFAULT_BUSINESS = {"staff": 0, "opened_at": 0, "last_tick": 0, "total_earned": 0}


def _label(kind: str) -> str:
    return BUSINESS_NAMES.get(kind, kind)


async def open_business(user_id: int, kind: str) -> dict:
    """Mở cơ sở kinh doanh mới. Trả {ok, reason?, cost?}."""
    if kind not in BUSINESS_KINDS:
        return {"ok": False, "reason": "invalid_kind"}

    existing = await db.get_business(user_id, kind)
    if existing.get("opened_at"):
        return {"ok": False, "reason": "already_open"}

    cost = BUSINESS_OPEN_COST[kind]
    if not economy.is_owner(user_id):
        user = await db.get_user(user_id)
        if user["mick"] < cost:
            return {"ok": False, "reason": "insufficient_funds", "cost": cost}

    await economy.add_mick(user_id, -cost)
    now = int(time.time())
    await db.save_business(user_id, kind, {"staff": 0, "opened_at": now, "last_tick": now, "total_earned": 0})
    return {"ok": True, "cost": cost}


async def hire_staff(user_id: int, kind: str) -> dict:
    """Thuê thêm 1 nhân viên cho cơ sở đã mở. Trả {ok, reason?, staff?, cost?}."""
    if kind not in BUSINESS_KINDS:
        return {"ok": False, "reason": "invalid_kind"}

    biz = await db.get_business(user_id, kind)
    if not biz.get("opened_at"):
        return {"ok": False, "reason": "not_opened"}
    if biz.get("staff", 0) >= BUSINESS_MAX_STAFF:
        return {"ok": False, "reason": "max_staff"}

    if not economy.is_owner(user_id):
        user = await db.get_user(user_id)
        if user["mick"] < BUSINESS_HIRE_COST:
            return {"ok": False, "reason": "insufficient_funds", "cost": BUSINESS_HIRE_COST}

    await economy.add_mick(user_id, -BUSINESS_HIRE_COST)
    new_staff = biz.get("staff", 0) + 1
    await db.save_business(user_id, kind, {"staff": new_staff})
    return {"ok": True, "staff": new_staff, "cost": BUSINESS_HIRE_COST}


async def get_summary(user_id: int) -> dict:
    """Trả về {kind: biz_data} cho tất cả cơ sở user đã mở (kể cả chưa mở, staff=0)."""
    out = {}
    for kind in BUSINESS_KINDS:
        biz = await db.get_business(user_id, kind)
        merged = dict(DEFAULT_BUSINESS)
        merged.update(biz)
        out[kind] = merged
    return out


def build_summary_container(display_name: str, summary: dict) -> discord.ui.Container:
    fields = []
    for kind in BUSINESS_KINDS:
        biz = summary[kind]
        if not biz.get("opened_at"):
            fields.append((_label(kind), f"Chưa mở · Mở tốn **{BUSINESS_OPEN_COST[kind]} MICK**"))
        else:
            income_per_tick = biz["staff"] * BUSINESS_INCOME_PER_TICK[kind]
            fields.append((
                _label(kind),
                f"👥 {biz['staff']}/{BUSINESS_MAX_STAFF} nhân viên · "
                f"💰 +{income_per_tick} MICK/{BUSINESS_TICK_SEC // 60} phút · "
                f"Tổng đã kiếm: **{biz.get('total_earned', 0)} MICK**",
            ))
    return build_container(
        title=f"💼 Cơ ngơi kinh doanh của {display_name}",
        color=discord.Color.dark_gold(),
        fields=fields,
        footer=f"Thuê thêm nhân viên: {BUSINESS_HIRE_COST} MICK/người · Vẫn chạy khi bạn offline",
    )


async def run_income_tick() -> int:
    """Duyệt toàn bộ business, cộng thu nhập nếu đã đủ BUSINESS_TICK_SEC kể từ last_tick.
    Trả về số cơ sở vừa được trả lương."""
    now = int(time.time())
    paid = 0

    for doc_id, biz in await db.get_all_businesses():
        staff = biz.get("staff", 0)
        if staff <= 0:
            continue
        last_tick = biz.get("last_tick", 0)
        if now - last_tick < BUSINESS_TICK_SEC:
            continue

        try:
            user_id_str, kind = doc_id.rsplit("_", 1)
            user_id = int(user_id_str)
        except Exception:
            continue
        if kind not in BUSINESS_INCOME_PER_TICK:
            continue

        income = staff * BUSINESS_INCOME_PER_TICK[kind]
        await economy.add_mick(user_id, income)
        total_earned = biz.get("total_earned", 0) + income
        await db.save_business(user_id, kind, {"last_tick": now, "total_earned": total_earned})
        paid += 1

    if paid:
        log.info("Business tick: đã trả thu nhập cho %s cơ sở.", paid)
    return paid


# ===========================================================================
# /mick-shop: 4 item mua bằng MICK, mỗi item có hạn dùng riêng
#
#   1. Admin Trial (ADMIN_TRIAL_ROLE_ID, 1h) - gán role admin tạm thời.
#   2. Ronaldo Pasta (50h) - +RONALDO_PASTA_EXTRA_GUESSES lượt đoán Wordle
#      (xem start_wordle/process_wordle_guess ở trên).
#   3. La Peace (24h) - troll thuần, không ảnh hưởng gameplay thật, chỉ hiện
#      trong /mick-shop của user.
#   4. DeltaX (5h) - nhân MICK kiếm được x{random 1.1-2.0}, hệ số chốt 1 lần
#      lúc mua/gia hạn (xem economy._apply_deltax_multiplier).
#
# Gia hạn qua tin nhắn thường "GH {tên sản phẩm}" (xem on_message trong
# discord_bot.py) - bot hỏi lại Y/N trước khi trừ tiền, giá gia hạn = giá
# mua mới. Hết hạn tự dọn (gỡ role nếu có) qua shop_expiry_loop.
# ===========================================================================

SHOP_ITEMS: dict[str, dict] = {
    "admin_trial": {
        "name": "Admin Trial",
        "emoji": "🛡️",
        "price": ADMIN_TRIAL_PRICE,
        "hours": ADMIN_TRIAL_HOURS,
        "desc": f"Cấp role <@&{ADMIN_TRIAL_ROLE_ID}> tạm thời trong {ADMIN_TRIAL_HOURS}H.",
    },
    "ronaldo_pasta": {
        "name": "Ronaldo Pasta",
        "emoji": "🍝",
        "price": RONALDO_PASTA_PRICE,
        "hours": RONALDO_PASTA_HOURS,
        "desc": f"Mì ống Ronaldo giúp bạn khó thua ở Wordle hơn (+{RONALDO_PASTA_EXTRA_GUESSES} lượt đoán) trong {RONALDO_PASTA_HOURS}H.",
    },
    "la_peace": {
        "name": "La Peace",
        "emoji": "☮️",
        "price": LA_PEACE_PRICE,
        "hours": LA_PEACE_HOURS,
        "desc": "???",
    },
    "deltax": {
        "name": "DeltaX",
        "emoji": "✖️",
        "price": DELTAX_PRICE,
        "hours": DELTAX_HOURS,
        "desc": f"Tăng MICK kiếm được x(random {DELTAX_MULT_MIN}-{DELTAX_MULT_MAX}) trong {DELTAX_HOURS}H.",
    },
}

# Alias để nhận diện tên sản phẩm gõ tay trong "GH {tên}" - không phân biệt
# hoa/thường, chấp nhận cả tên tiếng Việt lẫn key nội bộ.
_SHOP_NAME_ALIASES: dict[str, str] = {}
for _key, _item in SHOP_ITEMS.items():
    _SHOP_NAME_ALIASES[_item["name"].lower()] = _key
    _SHOP_NAME_ALIASES[_key.lower()] = _key
_SHOP_NAME_ALIASES["admin trial"] = "admin_trial"
_SHOP_NAME_ALIASES["ronaldo pasta"] = "ronaldo_pasta"
_SHOP_NAME_ALIASES["mì ronaldo"] = "ronaldo_pasta"
_SHOP_NAME_ALIASES["la peace"] = "la_peace"
del _key, _item


def resolve_shop_item_key(text: str) -> str | None:
    return _SHOP_NAME_ALIASES.get(text.strip().lower())


async def get_active_shop_effect(user_id: int, item_key: str) -> dict | None:
    """Trả về data purchase nếu user đang sở hữu item còn hạn, None nếu
    không có/đã hết hạn (không tự xoá ở đây - shop_expiry_loop lo việc đó,
    hàm này chỉ ĐỌC để các chỗ khác - Wordle, DeltaX... - biết mà áp dụng)."""
    purchase = await db.get_shop_purchase(user_id, item_key)
    if not purchase or purchase.get("expires_at", 0) <= time.time():
        return None
    return purchase


async def get_user_shop_status(user_id: int) -> list[dict]:
    """Trả về danh sách item user ĐANG sở hữu (còn hạn), kèm remaining_sec -
    dùng để hiện trong /mick-shop."""
    out = []
    now = time.time()
    for key, item in SHOP_ITEMS.items():
        purchase = await db.get_shop_purchase(user_id, key)
        if purchase and purchase.get("expires_at", 0) > now:
            out.append({
                "key": key,
                **item,
                "remaining_sec": int(purchase["expires_at"] - now),
            })
    return out


async def buy_shop_item(user_id: int, item_key: str) -> dict:
    """Mua 1 item mới (chỉ khi CHƯA sở hữu - dùng gia hạn qua "GH {tên}" nếu
    đã có). Trả {ok, reason} hoặc {ok: True, item, expires_at}."""
    item = SHOP_ITEMS.get(item_key)
    if not item:
        return {"ok": False, "reason": "not_found"}

    existing = await get_active_shop_effect(user_id, item_key)
    if existing:
        return {"ok": False, "reason": "already_owned", "expires_at": existing["expires_at"]}

    return await _do_purchase(user_id, item_key, item)


async def _do_purchase(user_id: int, item_key: str, item: dict) -> dict:
    async with economy.user_lock(user_id):
        user = await db.get_user(user_id)
        current = user.get("mick", 0)
        if not economy.is_owner(user_id) and current < item["price"]:
            return {"ok": False, "reason": "insufficient_funds", "have": current, "need": item["price"]}
        if not economy.is_owner(user_id):
            new_balance = current - item["price"]
            await db.save_user(user_id, {"mick": new_balance})

    now = time.time()
    expires_at = now + item["hours"] * 3600
    data = {
        "user_id": user_id,
        "item_key": item_key,
        "expires_at": expires_at,
        "created_at": now,
    }
    if item_key == "deltax":
        data["multiplier"] = round(random.uniform(DELTAX_MULT_MIN, DELTAX_MULT_MAX), 2)
    await db.save_shop_purchase(user_id, item_key, data)
    return {"ok": True, "item": item, "expires_at": expires_at, "data": data}


async def extend_shop_item(user_id: int, item_key: str) -> dict:
    """Gia hạn 1 item ĐANG sở hữu (còn hạn) - cộng thêm item['hours'] giờ
    vào expires_at hiện tại (không reset về mốc mới, để không mất thời gian
    còn lại), giá = giá mua mới. Trả {ok, reason} hoặc {ok: True, expires_at}."""
    item = SHOP_ITEMS.get(item_key)
    if not item:
        return {"ok": False, "reason": "not_found"}

    existing = await get_active_shop_effect(user_id, item_key)
    if not existing:
        return {"ok": False, "reason": "not_owned"}

    async with economy.user_lock(user_id):
        user = await db.get_user(user_id)
        current = user.get("mick", 0)
        if not economy.is_owner(user_id) and current < item["price"]:
            return {"ok": False, "reason": "insufficient_funds", "have": current, "need": item["price"]}
        if not economy.is_owner(user_id):
            new_balance = current - item["price"]
            await db.save_user(user_id, {"mick": new_balance})

    new_expires_at = existing["expires_at"] + item["hours"] * 3600
    update = {"expires_at": new_expires_at}
    if item_key == "deltax":
        # Gia hạn DeltaX chốt lại hệ số mới luôn (không giữ hệ số cũ), cùng
        # tinh thần "mua lại 1 lượt mới" như các item khác.
        update["multiplier"] = round(random.uniform(DELTAX_MULT_MIN, DELTAX_MULT_MAX), 2)
    await db.save_shop_purchase(user_id, item_key, {**existing, **update})
    return {"ok": True, "expires_at": new_expires_at}


def build_shop_container(owned: list[dict]) -> discord.ui.Container:
    lines = []
    for key, item in SHOP_ITEMS.items():
        lines.append(f"{item['emoji']} **{item['name']}** — {item['price']} MICK ({item['hours']}H)\n{item['desc']}")
    body = "\n\n".join(lines)

    owned_text = "Bạn chưa sở hữu item nào."
    if owned:
        owned_lines = []
        for o in owned:
            h = o["remaining_sec"] // 3600
            m = (o["remaining_sec"] % 3600) // 60
            owned_lines.append(f"{o['emoji']} **{o['name']}** — còn **{h}H{m}p**")
        owned_text = "\n".join(owned_lines)

    return build_container(
        title="🛒 Mick Shop",
        description=f"{body}\n\n### 📦 Đang sở hữu\n{owned_text}",
        color=discord.Color.gold(),
        footer="Mua bằng nút bên dưới · Gia hạn bằng tin nhắn \"GH {tên sản phẩm}\"",
    )


async def apply_shop_purchase_effects(guild: "discord.Guild | None", user_id: int, item_key: str) -> None:
    """Xử lý side-effect NGAY LÚC MUA (hiện chỉ có admin_trial cần gán role
    ngay - các item khác chỉ cần lưu DB, hiệu ứng đọc lại lúc dùng)."""
    if item_key != "admin_trial" or guild is None:
        return
    member = guild.get_member(user_id)
    if member is None:
        return
    role = guild.get_role(ADMIN_TRIAL_ROLE_ID)
    if role is None:
        log.warning("apply_shop_purchase_effects: không tìm thấy role %s trong guild", ADMIN_TRIAL_ROLE_ID)
        return
    try:
        await member.add_roles(role, reason="Mua Admin Trial qua /mick-shop")
    except Exception as e:
        log.warning("Gán role Admin Trial lỗi cho %s: %s", user_id, e)


async def run_shop_expiry_tick(client: "discord.Client") -> int:
    """Duyệt toàn bộ shop_purchases, xoá + xử lý dọn dẹp (gỡ role) cho item
    đã hết hạn - gọi định kỳ từ shop_expiry_loop (discord_bot.py). Trả về
    số item vừa hết hạn được xử lý."""
    now = time.time()
    expired_count = 0
    for doc_id, data in await db.get_all_shop_purchases():
        expires_at = data.get("expires_at", 0)
        if expires_at > now:
            continue
        try:
            user_id_str, item_key = doc_id.rsplit("_", 1)
            user_id = int(user_id_str)
        except Exception:
            continue

        if item_key == "admin_trial":
            for guild in client.guilds:
                member = guild.get_member(user_id)
                if member is None:
                    continue
                role = guild.get_role(ADMIN_TRIAL_ROLE_ID)
                if role is None or role not in member.roles:
                    continue
                try:
                    await member.remove_roles(role, reason="Admin Trial hết hạn (/mick-shop)")
                except Exception as e:
                    log.warning("Gỡ role Admin Trial lỗi cho %s: %s", user_id, e)

        await db.delete_shop_purchase(user_id, item_key)
        expired_count += 1

    return expired_count
