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

import random
import string
import time
from datetime import datetime, timedelta, timezone

import discord

import uuid as _uuid_lib

import db
import economy
from config import (
    log,
    QUEST_COUNT_PER_DAY,
    QUEST_REWARD_MICK,
    VN_UTC_OFFSET_HOURS,
    DAILY_BASE_REWARD,
    DAILY_DECAY_RATE,
    DAILY_MIN_REWARD,
    DAILY_WINDOW_HOURS,
    WORDLE_WIN_REWARD,
    WORDLE_MAX_GUESSES,
    GUESS_NUMBER_REWARD,
    GUESS_NUMBER_MAX,
    GUESS_NUMBER_MAX_TRIES,
    RPS_WIN_REWARD,
    GAME_LOOKUP_TTL_SEC,
    BUSINESS_INCOME_PER_TICK,
    BUSINESS_OPEN_COST,
    BUSINESS_HIRE_COST,
    BUSINESS_MAX_STAFF,
    BUSINESS_TICK_SEC,
    TAIXIU_PAYOUT_MULTIPLIER,
    XIDACH_PAYOUT_MULTIPLIER,
    XIDACH_BONUS_MULTIPLIER,
    DAILY_TICKET_REWARD,
    TICKET_EMOJI,
    TRIVIA_REWARD_MICK,
    TRIVIA_TIMEOUT_SEC,
)

# ===========================================================================
# Thành tựu (Achievements)
#
# Quy ước thưởng: thành tựu KHÓ thưởng ÍT (<30 MICK), thành tựu DỄ thưởng
# NHIỀU (>30 MICK) - đúng yêu cầu ngược đời của Mango =))
# ===========================================================================

# id: {name, desc, reward, difficulty}
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


def build_list_embed(user_achievements: list[str]) -> discord.Embed:
    unlocked = set(user_achievements)
    embed = discord.Embed(title="🏆 Danh sách Thành tựu", color=discord.Color.gold())

    hard = [(aid, a) for aid, a in ACHIEVEMENTS.items() if a["difficulty"] == "Khó"]
    easy = [(aid, a) for aid, a in ACHIEVEMENTS.items() if a["difficulty"] == "Dễ"]

    def render(items):
        lines = []
        for aid, a in items:
            mark = "✅" if aid in unlocked else "🔒"
            lines.append(f"{mark} **{a['name']}** — {a['desc']} (+{a['reward']} MICK)")
        return "\n".join(lines) or "Không có"

    embed.add_field(name="🔥 Khó (thưởng ít, khoe nhiều)", value=render(hard), inline=False)
    embed.add_field(name="🍀 Dễ (thưởng nhiều, cày lẹ)", value=render(easy), inline=False)
    embed.set_footer(text=f"Đã mở khóa {len(unlocked)}/{len(ACHIEVEMENTS)}")
    return embed


async def announce_unlocks(channel, user: discord.User, unlocked: list[dict]):
    for info in unlocked:
        try:
            embed = discord.Embed(
                title="🏆 Mở khóa Thành tựu!",
                description=f"{user.mention} vừa đạt **{info['name']}**\n{info['desc']}\n💰 Bạn đã nhận **{info['reward']} Mick**",
                color=discord.Color.gold(),
            )
            await channel.send(embed=embed)
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
}

QUEST_IDS = list(QUEST_POOL.keys())


async def get_today_quests(user_id: int) -> dict:
    """Trả về (và tự khởi tạo nếu cần) bộ quest hôm nay cho user."""
    user = await db.get_user(user_id)
    today = vn_today_str()

    if user.get("quest_date") == today and user.get("quest_ids"):
        return user

    quest_ids = random.sample(QUEST_IDS, min(QUEST_COUNT_PER_DAY, len(QUEST_IDS)))
    update = {"quest_date": today, "quest_ids": quest_ids, "quest_progress": {}, "quest_done": []}
    await db.save_user(user_id, update)
    user.update(update)
    return user


async def bump_progress(user_id: int, event_key: str, amount: int = 1) -> dict | None:
    """
    Cộng tiến độ cho quest nào (nếu có trong bộ quest hôm nay của user) khớp event_key.
    event_key phải trùng với id trong QUEST_POOL. Trả về quest info nếu VỪA hoàn thành, None nếu chưa.
    """
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


def build_quest_embed(user: dict, display_name: str) -> discord.Embed:
    embed = discord.Embed(title=f"📜 Quest hằng ngày của {display_name}", color=discord.Color.teal())
    done = set(user.get("quest_done", []))
    progress = user.get("quest_progress", {})

    lines = []
    for qid in user.get("quest_ids", []):
        quest = QUEST_POOL[qid]
        mark = "✅" if qid in done else "⬜"
        cur = progress.get(qid, 0)
        lines.append(f"{mark} {quest['desc']} — `{min(cur, quest['target'])}/{quest['target']}`")

    embed.description = "\n".join(lines) if lines else "Chưa có quest, gõ lại lệnh để random."
    embed.set_footer(text=f"Mỗi quest hoàn thành: +{QUEST_REWARD_MICK} MICK · Reset 0h giờ VN")
    return embed


# ===========================================================================
# Daily điểm danh (Daily)
#
# Đúng 0h sáng giờ VN (UTC+7) đăng embed có nút "Nhận Daily". Nhận càng trễ
# thì MICK càng ít (giảm DAILY_DECAY_RATE mỗi giờ), sàn DAILY_MIN_REWARD.
# Hết hạn đúng DAILY_WINDOW_HOURS giờ sáng (mặc định 7h).
# ===========================================================================

VN_TZ = timezone(timedelta(hours=VN_UTC_OFFSET_HOURS))
DAILY_CLAIM_CUSTOM_ID = "daily_claim_btn"


def vn_now() -> datetime:
    return datetime.now(VN_TZ)


def vn_today_str() -> str:
    return vn_now().strftime("%Y-%m-%d")


def compute_daily_reward(hours_elapsed: int) -> int:
    hours_elapsed = max(0, hours_elapsed)
    amount = DAILY_BASE_REWARD * ((1 - DAILY_DECAY_RATE) ** hours_elapsed)
    return max(DAILY_MIN_REWARD, round(amount))


def build_daily_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🎁 Daily hàng ngày",
        description=(
            f"Bấm nút bên dưới để nhận MICK miễn phí!\n"
            f"Nhận ngay lúc 0h: **{DAILY_BASE_REWARD} MICK**. "
            f"Càng nhận trễ, MICK càng giảm {int(DAILY_DECAY_RATE * 100)}%/giờ "
            f"(tối thiểu **{DAILY_MIN_REWARD} MICK**).\n"
            f"⏰ Hết hạn lúc **{DAILY_WINDOW_HOURS}:00 sáng**."
        ),
        color=discord.Color.gold(),
        timestamp=vn_now().astimezone(timezone.utc),
    )
    return embed


class DailyClaimView(discord.ui.View):
    """Persistent view (timeout=None, custom_id cố định) để sống sót qua restart bot."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Nhận Daily", emoji="🎁", style=discord.ButtonStyle.success, custom_id=DAILY_CLAIM_CUSTOM_ID)
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _handle_claim(interaction)


async def _handle_claim(interaction: discord.Interaction):
    now = vn_now()
    today = now.strftime("%Y-%m-%d")

    if now.hour >= DAILY_WINDOW_HOURS:
        await interaction.response.send_message(
            f"⏰ Daily hôm nay đã hết hạn (quá {DAILY_WINDOW_HOURS}h sáng). Chờ 0h mai nhé!", ephemeral=True
        )
        return

    user_id = interaction.user.id

    # Lock theo user_id: chặn double-claim khi bấm nút 2 lần liền/lag mạng
    # (2 request đọc "last_daily_date" cũ cùng lúc trước khi cái đầu ghi xong).
    async with economy.user_lock(user_id):
        user = await db.get_user(user_id)
        if user.get("last_daily_date") == today:
            await interaction.response.send_message("✅ Bạn đã nhận Daily hôm nay rồi!", ephemeral=True)
            return

        daily_state = await db.get_daily_state()
        reset_epoch = daily_state.get("reset_at_epoch")
        if not reset_epoch or daily_state.get("date") != today:
            # Phòng trường hợp bot restart lệch nhịp và chưa có mốc reset hôm nay.
            reset_epoch = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())

        hours_elapsed = int((time.time() - reset_epoch) // 3600)
        reward = compute_daily_reward(hours_elapsed)

        # Ghi thẳng ở đây (không gọi economy.add_mick/add_ve) vì đang ở trong
        # user_lock rồi -> asyncio.Lock không reentrant, gọi lại sẽ deadlock.
        new_balance = max(0, user["mick"] + reward)
        is_owner = economy.is_owner(user_id)
        new_ve = user.get("ve", 0) if is_owner else user.get("ve", 0) + DAILY_TICKET_REWARD
        update = {"last_daily_date": today, "mick": new_balance}
        if not is_owner:
            update["ve"] = new_ve
        await db.save_user(user_id, update)

    ve_display = "∞" if is_owner else str(new_ve)
    await interaction.response.send_message(
        f"🎁 Bạn đã nhận **{reward} Mick** + {TICKET_EMOJI} **{DAILY_TICKET_REWARD} Vé**! "
        f"(Số dư hiện tại: **{new_balance} MICK** · Vé: **{ve_display}**)",
        ephemeral=True,
    )

    try:
        unlocked = await unlock(user_id, "first_daily")
        if unlocked and interaction.channel is not None:
            await announce_unlocks(interaction.channel, interaction.user, [unlocked])
    except Exception:
        pass


async def maybe_post_daily(client: discord.Client, channel_id: int) -> None:
    """Gọi mỗi phút từ vòng lặp; tự đăng embed đúng lúc 0h VN nếu chưa đăng hôm nay."""
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

    await channel.send(embed=build_daily_embed(), view=DailyClaimView())
    await db.save_daily_state({"date": today, "reset_at_epoch": int(time.time())})


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
    "trivia": "🧠 Trivia",
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
    (Tài Xỉu/Xì Dách) và tiền cược được hoàn lại vì ván chưa có kết quả.
    Ván bị dừng được XOÁ NGAY khỏi _active_games (giải phóng Game ID ngay lập
    tức) thay vì chờ GC theo TTL như ván đã có kết quả tự nhiên.
    """
    game = _active_games.get(game_id)
    if not game or game["owner_id"] != user_id or game["status"] != "playing":
        return False, None

    refunded = None
    if game["type"] in ("taixiu", "xidach") and game.get("bet"):
        refunded = await economy.add_mick(user_id, game["bet"])

    _active_games.pop(game_id, None)
    return True, refunded


def _new_game_id() -> str:
    gid = _uuid_lib.uuid4().hex[:8]
    while gid in _active_games:
        gid = _uuid_lib.uuid4().hex[:8]
    return gid


def _gc_games() -> None:
    """Dọn RAM: xoá ván đã kết thúc quá GAME_LOOKUP_TTL_SEC (tránh rò rỉ bộ nhớ)."""
    now = time.time()
    stale = [
        gid
        for gid, g in _active_games.items()
        if g.get("status") != "playing" and now - g.get("finished_at", now) > GAME_LOOKUP_TTL_SEC
    ]
    for gid in stale:
        _active_games.pop(gid, None)


def lookup_game(game_id: str) -> discord.Embed | None:
    """Tra thông tin 1 ván theo ID - chỉ đọc, dùng cho lệnh /tra-game."""
    _gc_games()
    game = _active_games.get(game_id.strip().lower())
    if not game:
        return None

    embed = discord.Embed(title=f"🔎 Tra ván #{game_id}", color=discord.Color.blurple())
    embed.add_field(name="Loại game", value=GAME_TYPE_LABELS.get(game["type"], game["type"]), inline=True)
    embed.add_field(name="Người chơi", value=f"<@{game['owner_id']}>", inline=True)
    embed.add_field(name="Trạng thái", value=_GAME_STATUS_LABELS.get(game.get("status"), "?"), inline=True)
    if game.get("summary"):
        embed.add_field(name="Chi tiết", value=game["summary"], inline=False)
    embed.set_footer(text=f"ID ván: {game_id}")
    return embed


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
    remaining = WORDLE_MAX_GUESSES - len(game["guesses"])
    lines.append(f"\nCòn **{remaining}** lượt đoán. Bấm nút \"Nhập từ\" bên dưới để đoán.")
    return "\n".join(lines) if game["guesses"] else "Bấm nút \"Nhập từ\" bên dưới để bắt đầu đoán 1 từ tiếng Anh 5 chữ!"


def start_wordle(user_id: int) -> tuple[str, discord.Embed]:
    gid = _new_game_id()
    answer = random.choice(_WORDLE_WORDS)
    _active_games[gid] = {
        "type": "wordle",
        "owner_id": user_id,
        "status": "playing",
        "created_at": time.time(),
        "answer": answer,
        "guesses": [],
        "feedback": [],
    }
    embed = discord.Embed(
        title=f"🟩 Wordle · #{gid}",
        description=_render_board(_active_games[gid]),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text=f"Đoán đúng nhận {WORDLE_WIN_REWARD} MICK · Tối đa {WORDLE_MAX_GUESSES} lượt")
    return gid, embed


def is_valid_guess(text: str) -> bool:
    return len(text) == 5 and all(c in string.ascii_letters for c in text)


async def process_wordle_guess(game_id: str, guess: str) -> tuple[discord.Embed, bool] | None:
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
    out_of_tries = len(game["guesses"]) >= WORDLE_MAX_GUESSES

    if won:
        new_balance = await economy.add_mick(user_id, WORDLE_WIN_REWARD)
        user = await db.get_user(user_id)
        wordle_wins = user.get("wordle_wins", 0) + 1
        await db.save_user(user_id, {"wordle_wins": wordle_wins})
        embed = discord.Embed(
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
        return embed, True

    if out_of_tries:
        embed = discord.Embed(
            title=f"💀 Wordle - Hết lượt · #{game_id}",
            description=f"{_render_board(game)}\n\nTừ đúng là **{answer.upper()}**. Chúc may mắn lần sau!",
            color=discord.Color.red(),
        )
        game["status"] = "lost"
        game["finished_at"] = time.time()
        game["summary"] = f"Thua · từ đúng **{answer.upper()}**"
        return embed, True

    embed = discord.Embed(title=f"🟩 Wordle · #{game_id}", description=_render_board(game), color=discord.Color.blurple())
    embed.set_footer(text=f"Đoán đúng nhận {WORDLE_WIN_REWARD} MICK · Tối đa {WORDLE_MAX_GUESSES} lượt")
    return embed, False


# --- Đoán số ----------------------------------------------------------------


def start_guess_number(user_id: int) -> tuple[str, discord.Embed]:
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
    return gid, _guess_number_embed(gid, _active_games[gid])


def _guess_number_embed(gid: str, game: dict) -> discord.Embed:
    remaining = GUESS_NUMBER_MAX_TRIES - game["tries"]
    lines = [f"`{g}` → {hint}" for g, hint in game["history"]]
    desc = "\n".join(lines) if lines else "Chưa đoán lần nào."
    embed = discord.Embed(
        title=f"🔢 Đoán số (1-{GUESS_NUMBER_MAX}) · #{gid}",
        description=f"{desc}\n\nCòn **{remaining}** lượt đoán. Bấm nút \"Nhập số\" bên dưới.",
        color=discord.Color.blurple(),
    )
    embed.set_footer(text=f"Đoán đúng nhận {GUESS_NUMBER_REWARD} MICK · Tối đa {GUESS_NUMBER_MAX_TRIES} lượt")
    return embed


async def process_guess_number(game_id: str, guess: int) -> tuple[discord.Embed, bool] | None:
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
        embed = discord.Embed(
            title=f"🎉 Đoán số - Thắng! · #{game_id}",
            description=(
                f"Số bí mật là **{secret}**! 💰 Bạn đã nhận **{GUESS_NUMBER_REWARD} Mick**.\n"
                f"Số dư hiện tại: **{new_balance} MICK**."
            ),
            color=discord.Color.green(),
        )
        return embed, True

    hint = "⬆️ Lớn hơn số này" if guess < secret else "⬇️ Nhỏ hơn số này"
    game["history"].append((guess, hint))

    if game["tries"] >= GUESS_NUMBER_MAX_TRIES:
        game["status"] = "lost"
        game["finished_at"] = time.time()
        game["summary"] = f"Hết lượt · số đúng là **{secret}**"
        embed = discord.Embed(
            title=f"💀 Đoán số - Hết lượt · #{game_id}",
            description=f"Số bí mật là **{secret}**. Chúc may mắn lần sau!",
            color=discord.Color.red(),
        )
        return embed, True

    embed = _guess_number_embed(game_id, game)
    return embed, False


# --- Kéo Búa Bao --------------------------------------------------------------

RPS_CHOICE_LABELS = {"keo": "✂️ Kéo", "bua": "🪨 Búa", "bao": "📄 Bao"}
_RPS_BEATS = {"keo": "bao", "bua": "keo", "bao": "bua"}  # key thắng value


def start_rps(user_id: int) -> tuple[str, discord.Embed]:
    gid = _new_game_id()
    _active_games[gid] = {"type": "rps", "owner_id": user_id, "status": "playing", "created_at": time.time()}
    embed = discord.Embed(
        title=f"✊ Kéo Búa Bao · #{gid}",
        description="Chọn 1 trong 3 bên dưới để đấu với bot!",
        color=discord.Color.gold(),
    )
    embed.set_footer(text=f"Thắng nhận {RPS_WIN_REWARD} MICK")
    return gid, embed


async def process_rps(game_id: str, player_choice: str) -> discord.Embed | None:
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

    embed = discord.Embed(
        title=f"✊ Kéo Búa Bao - Kết quả · #{game_id}",
        description=(
            f"Bạn chọn: {RPS_CHOICE_LABELS[player_choice]}\nBot chọn: {RPS_CHOICE_LABELS[bot_choice]}\n\n{result_text}"
        ),
        color=color,
    )
    return embed


# ===========================================================================
# Casino: Tài Xỉu (lắc 3 xúc xắc, tổng 11-18 = Tài, 3-10 = Xỉu)
#
# Khác với Wordle/Đoán số/RPS (không mất tiền để chơi), 2 game casino này ăn
# thua MICK thật -> tiền cược bị trừ NGAY LÚC ĐẶT qua economy.place_bet()
# (atomic, có lock user_id) để không thể lách cược vượt số dư bằng cách spam
# nhiều lệnh cùng lúc - học từ bug dupe MICK ở /chuyển-tiền trước đó.
# ===========================================================================


def start_taixiu(user_id: int, bet: int, wallet_after_bet: int) -> tuple[str, discord.Embed]:
    gid = _new_game_id()
    _active_games[gid] = {
        "type": "taixiu",
        "owner_id": user_id,
        "status": "playing",
        "created_at": time.time(),
        "bet": bet,
    }
    embed = discord.Embed(
        title=f"🎲 Tài Xỉu · #{gid}",
        description=(
            f"Cược **{bet} MICK** đã bị trừ (số dư còn: **{wallet_after_bet}**).\n"
            f"Chọn **Tài** (11-18) hoặc **Xỉu** (3-10) bên dưới rồi bot lắc 3 xúc xắc!"
        ),
        color=discord.Color.gold(),
    )
    embed.set_footer(text=f"Thắng ăn x{TAIXIU_PAYOUT_MULTIPLIER:.0f} tiền cược")
    return gid, embed


async def process_taixiu(game_id: str, choice: str) -> discord.Embed | None:
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

    embed = discord.Embed(title=f"🎲 Tài Xỉu - Kết quả · #{game_id}", description=desc, color=color)
    return embed


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


def start_xidach(user_id: int, bet: int, wallet_after_bet: int) -> tuple[str, discord.Embed]:
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

    embed = discord.Embed(title=f"🃏 Xì Dách · #{gid}", description=desc, color=discord.Color.gold())
    embed.set_footer(text=f"Thắng ăn x{XIDACH_PAYOUT_MULTIPLIER:.0f} · Xì Bàng/Ngũ Linh ăn x{XIDACH_BONUS_MULTIPLIER:.0f}")
    return gid, embed


def xidach_draw(game_id: str) -> tuple[discord.Embed, bool] | None:
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
        embed = discord.Embed(
            title=f"🃏 Xì Dách - Quắc rồi! · #{game_id}",
            description=(
                f"Bài của bạn: {_hand_text(game['player'])}\n\n"
                f"💥 Quắc! Quá 21 điểm, bạn mất **{bet} MICK** đã cược."
            ),
            color=discord.Color.red(),
        )
        return embed, True

    embed = discord.Embed(
        title=f"🃏 Xì Dách · #{game_id}",
        description=(
            f"Bài của bạn: {_hand_text(game['player'])}\n"
            f"Bài bot: {game['bot'][0]} ❓\n\n"
            f"Bấm **Rút thêm** để lấy thêm 1 lá, hoặc **Dằn bài** để dừng."
        ),
        color=discord.Color.gold(),
    )
    return embed, False


async def xidach_stand(game_id: str) -> discord.Embed | None:
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

    return discord.Embed(title=f"🃏 Xì Dách - Kết quả · #{game_id}", description=desc, color=color)


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
]


def start_trivia(user_id: int) -> tuple[str, discord.Embed, list[str]]:
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
    embed = discord.Embed(
        title=f"🧠 Trivia · #{gid}",
        description=q["q"],
        color=discord.Color.blurple(),
    )
    embed.set_footer(text=f"Trả lời đúng nhận {TRIVIA_REWARD_MICK} MICK · {TRIVIA_TIMEOUT_SEC}s để trả lời")
    return gid, embed, q["options"]


async def process_trivia(game_id: str, chosen_index: int) -> discord.Embed | None:
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

    return discord.Embed(title=f"🧠 Trivia - Kết quả · #{game_id}", description=desc, color=color)


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


def build_summary_embed(display_name: str, summary: dict) -> discord.Embed:
    embed = discord.Embed(title=f"💼 Cơ ngơi kinh doanh của {display_name}", color=discord.Color.dark_gold())
    for kind in BUSINESS_KINDS:
        biz = summary[kind]
        if not biz.get("opened_at"):
            embed.add_field(
                name=_label(kind),
                value=f"Chưa mở · Mở tốn **{BUSINESS_OPEN_COST[kind]} MICK**",
                inline=False,
            )
        else:
            income_per_tick = biz["staff"] * BUSINESS_INCOME_PER_TICK[kind]
            embed.add_field(
                name=_label(kind),
                value=(
                    f"👥 {biz['staff']}/{BUSINESS_MAX_STAFF} nhân viên · "
                    f"💰 +{income_per_tick} MICK/{BUSINESS_TICK_SEC // 60} phút · "
                    f"Tổng đã kiếm: **{biz.get('total_earned', 0)} MICK**"
                ),
                inline=False,
            )
    embed.set_footer(text=f"Thuê thêm nhân viên: {BUSINESS_HIRE_COST} MICK/người · Vẫn chạy khi bạn offline")
    return embed


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
