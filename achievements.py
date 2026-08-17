"""
Hệ thống thành tựu.

Quy ước thưởng: thành tựu KHÓ thưởng ÍT (<30 MICK), thành tựu DỄ thưởng NHIỀU
(>30 MICK) - đúng yêu cầu ngược đời của Mango =))

Mỗi thành tựu có 1 "check" đơn giản dựa trên field có sẵn trong user doc
(level, mick, xp...) hoặc được mở khóa thủ công/qua sự kiện (achievements.unlock).
"""

import discord

import db
from config import log

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

    # business_tycoon: cần dữ liệu từ collection businesses, kiểm tra riêng
    import business

    summary = await business.get_summary(user_id)
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
            import quests

            finished = await quests.bump_progress(user.id, "achievement_1_3")
            if finished:
                await channel.send(
                    f"✅ {user.mention} hoàn thành quest **{finished['desc']}**! "
                    f"+**{finished['reward']} MICK** (số dư: {finished['new_balance']})"
                )
        except Exception:
            pass
