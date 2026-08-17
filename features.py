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
    CUP_GAME_REWARD,
    CUP_GAME_CUP_COUNT,
    WORDLE_WIN_REWARD,
    WORDLE_MAX_GUESSES,
    BUSINESS_INCOME_PER_TICK,
    BUSINESS_OPEN_COST,
    BUSINESS_HIRE_COST,
    BUSINESS_MAX_STAFF,
    BUSINESS_TICK_SEC,
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
    "business_tycoon": {"name": "🏢 Trùm Kinh Doanh", "desc": "Sở hữu cả 4 loại hình kinh doanh", "reward": 29, "difficulty": "Khó"},

    # --- Dễ (reward > 30) ---
    "first_message": {"name": "👋 Chào Sân", "desc": "Nhắn tin đầu tiên trong server", "reward": 35, "difficulty": "Dễ"},
    "first_daily": {"name": "🎁 Điểm Danh", "desc": "Nhận Daily lần đầu", "reward": 40, "difficulty": "Dễ"},
    "level_5": {"name": "🌱 Tân Binh", "desc": "Đạt Level 5", "reward": 45, "difficulty": "Dễ"},
    "first_business": {"name": "🏪 Khởi Nghiệp", "desc": "Mở 1 cơ sở kinh doanh đầu tiên", "reward": 50, "difficulty": "Dễ"},
}


async def unlock(user_id: int, achievement_id: str) -> dict | None:
    """Mở khóa thành tựu nếu chưa có. Trả về info thành tựu nếu vừa mở khóa, None nếu đã có / không tồn tại."""
    if achievement_id not in ACHIEVEMENTS:
        return None

    user = await db.get_user(user_id)
    unlocked = set(user.get("achievements", []))
    if achievement_id in unlocked:
        return None

    info = ACHIEVEMENTS[achievement_id]
    unlocked.add(achievement_id)
    new_mick = user["mick"] + info["reward"]
    await db.save_user(user_id, {"achievements": list(unlocked), "mick": new_mick})
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
                description=f"{user.mention} vừa đạt **{info['name']}**\n{info['desc']}\n+**{info['reward']} MICK**",
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
    "play_game_5": {"desc": "Chơi minigame (cup/wordle) 5 lần", "target": 5},
    "level_up": {"desc": "Lên 1 level bất kỳ", "target": 1},
    "achievement_1_3": {"desc": "Hoàn thành 1-3 thành tựu bất kỳ", "target": 1},
    "ai_hoi_3": {"desc": "Nói `ai hỏi` 3 lần", "target": 3},
    "ghet_tomboy": {"desc": "Nói `tôi ghét tomboy` 1 lần", "target": 1},
    "depchai_gay": {"desc": "Nói `btw i love depchai because he's gay` 1 lần", "target": 1},
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

    new_balance = await economy.add_mick(user_id, reward)
    await db.save_user(user_id, {"last_daily_date": today})

    await interaction.response.send_message(
        f"🎁 Bạn nhận được **{reward} MICK**! (Số dư hiện tại: **{new_balance} MICK**)", ephemeral=True
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
# Minigame: Úp ly chọn kẹo + Wordle (Games)
# ===========================================================================

CUP_EMOJI = "🥤"
CANDY_EMOJI = "🍬"


class CupGameView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=30)
        self.owner_id = owner_id
        self.winning_index = random.randrange(CUP_GAME_CUP_COUNT)
        self.done = False

        for i in range(CUP_GAME_CUP_COUNT):
            self.add_item(self._make_button(i))

    def _make_button(self, index: int) -> discord.ui.Button:
        button = discord.ui.Button(label=f"Ly {index + 1}", emoji=CUP_EMOJI, style=discord.ButtonStyle.secondary)

        async def callback(interaction: discord.Interaction):
            await self._on_pick(interaction, index)

        button.callback = callback
        return button

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Đây không phải lượt chơi của bạn!", ephemeral=True)
            return False
        return True

    async def _on_pick(self, interaction: discord.Interaction, index: int):
        if self.done:
            return
        self.done = True
        for child in self.children:
            child.disabled = True

        won = index == self.winning_index
        reveal = " ".join(
            CANDY_EMOJI if i == self.winning_index else "🚫" for i in range(CUP_GAME_CUP_COUNT)
        )

        try:
            finished = await bump_progress(self.owner_id, "play_game_5")
            if finished:
                await interaction.channel.send(
                    f"✅ <@{self.owner_id}> hoàn thành quest **{finished['desc']}**! "
                    f"+**{finished['reward']} MICK** (số dư: {finished['new_balance']})"
                )
        except Exception:
            pass

        if won:
            new_balance = await economy.add_mick(self.owner_id, CUP_GAME_REWARD)
            desc = (
                f"{CANDY_EMOJI} Chính xác! Bạn nhận được **{CUP_GAME_REWARD} MICK**.\n"
                f"Số dư hiện tại: **{new_balance} MICK**."
            )
            color = discord.Color.green()
        else:
            desc = f"Tiếc quá, ly có kẹo là **Ly {self.winning_index + 1}**. Chúc may mắn lần sau!"
            color = discord.Color.red()

        embed = discord.Embed(title="🥤 Úp ly chọn kẹo - Kết quả", description=f"{reveal}\n\n{desc}", color=color)
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


def build_cup_game_embed() -> discord.Embed:
    return discord.Embed(
        title="🥤 Úp ly chọn kẹo",
        description=f"Có {CUP_GAME_CUP_COUNT} ly, 1 ly giấu {CANDY_EMOJI}. Chọn đúng nhận **{CUP_GAME_REWARD} MICK**!",
        color=discord.Color.gold(),
    )


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

# user_id -> {"answer": str, "guesses": [str], "feedback": [str]}
_active_games: dict[int, dict] = {}


def has_active_wordle(user_id: int) -> bool:
    return user_id in _active_games


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
    lines.append(f"\nCòn **{remaining}** lượt đoán. Gõ thẳng 1 từ 5 chữ vào kênh để đoán.")
    return "\n".join(lines) if game["guesses"] else "Gõ thẳng 1 từ tiếng Anh 5 chữ vào kênh để bắt đầu đoán!"


def start_wordle(user_id: int) -> discord.Embed:
    answer = random.choice(_WORDLE_WORDS)
    _active_games[user_id] = {"answer": answer, "guesses": [], "feedback": []}
    embed = discord.Embed(
        title="🟩 Wordle",
        description=_render_board(_active_games[user_id]),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text=f"Đoán đúng nhận {WORDLE_WIN_REWARD} MICK · Tối đa {WORDLE_MAX_GUESSES} lượt")
    return embed


def is_valid_guess(text: str) -> bool:
    return len(text) == 5 and all(c in string.ascii_letters for c in text)


async def process_guess(user_id: int, guess: str) -> tuple[discord.Embed, bool]:
    """Trả về (embed, finished). finished=True nghĩa là ván đã kết thúc (thắng/thua)."""
    game = _active_games[user_id]
    guess = guess.lower()
    answer = game["answer"]

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
            title="🎉 Wordle - Thắng!",
            description=(
                f"{_render_board(game)}\n\n"
                f"Chính xác là **{answer.upper()}**! Bạn nhận **{WORDLE_WIN_REWARD} MICK**.\n"
                f"Số dư hiện tại: **{new_balance} MICK**."
            ),
            color=discord.Color.green(),
        )
        del _active_games[user_id]
        return embed, True

    if out_of_tries:
        embed = discord.Embed(
            title="💀 Wordle - Hết lượt",
            description=f"{_render_board(game)}\n\nTừ đúng là **{answer.upper()}**. Chúc may mắn lần sau!",
            color=discord.Color.red(),
        )
        del _active_games[user_id]
        return embed, True

    embed = discord.Embed(title="🟩 Wordle", description=_render_board(game), color=discord.Color.blurple())
    embed.set_footer(text=f"Đoán đúng nhận {WORDLE_WIN_REWARD} MICK · Tối đa {WORDLE_MAX_GUESSES} lượt")
    return embed, False


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
