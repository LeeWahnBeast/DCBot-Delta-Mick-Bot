"""
Hệ thống kinh tế: tiền tệ MICK + level.

- XP cộng dần khi chat (xem discord_bot.py), khi đủ ngưỡng thì lên level.
- Mỗi lần lên 1 level được cộng thẳng LEVEL_UP_MICK_REWARD (15) MICK.
- Công thức ngưỡng XP mỗi level (kiểu MEE6): 5*level^2 + 50*level + 100.
"""

import db
from config import LEVEL_UP_MICK_REWARD


def xp_needed_for_level(level: int) -> int:
    return 5 * (level ** 2) + 50 * level + 100


async def add_xp(user_id: int, amount: int) -> dict:
    """
    Cộng XP cho user, tự lên level nếu đủ (có thể lên nhiều level cùng lúc).
    Trả về {level, levels_gained, mick_awarded, xp, mick}.
    """
    user = await db.get_user(user_id)
    xp = user["xp"] + amount
    level = user["level"]
    mick = user["mick"]

    levels_gained = 0
    while xp >= xp_needed_for_level(level):
        xp -= xp_needed_for_level(level)
        level += 1
        levels_gained += 1

    mick_awarded = levels_gained * LEVEL_UP_MICK_REWARD
    mick += mick_awarded

    await db.save_user(user_id, {"xp": xp, "level": level, "mick": mick})
    return {"level": level, "levels_gained": levels_gained, "mick_awarded": mick_awarded, "xp": xp, "mick": mick}


async def add_mick(user_id: int, amount: int) -> int:
    """Cộng (hoặc trừ, nếu amount âm) MICK cho user. Trả về số dư mới."""
    user = await db.get_user(user_id)
    new_balance = max(0, user["mick"] + amount)
    await db.save_user(user_id, {"mick": new_balance})
    return new_balance


async def get_profile(user_id: int) -> dict:
    user = await db.get_user(user_id)
    needed = xp_needed_for_level(user["level"])
    return {
        "mick": user["mick"],
        "level": user["level"],
        "xp": user["xp"],
        "xp_needed": needed,
    }
