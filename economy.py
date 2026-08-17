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
        "uuid": user.get("uuid", ""),
    }


async def transfer_mick(from_id: int, to_id: int, amount: int) -> dict:
    """Trừ MICK người gửi, cộng cho người nhận. Trả {ok, reason?, from_balance, to_balance}."""
    if amount <= 0:
        return {"ok": False, "reason": "invalid_amount"}
    if from_id == to_id:
        return {"ok": False, "reason": "self_transfer"}

    sender = await db.get_user(from_id)
    if sender["mick"] < amount:
        return {"ok": False, "reason": "insufficient_funds"}

    from_balance = await add_mick(from_id, -amount)
    to_balance = await add_mick(to_id, amount)
    return {"ok": True, "from_balance": from_balance, "to_balance": to_balance}


# ---------------------------------------------------------------------------
# ATM: giữ MICK hộ, tách khỏi ví tiêu xài (mick)
# ---------------------------------------------------------------------------


async def atm_deposit(user_id: int, amount: int) -> dict:
    """Gửi MICK từ ví vào ATM. Trả {ok, reason?, wallet, atm}."""
    if amount <= 0:
        return {"ok": False, "reason": "invalid_amount"}
    user = await db.get_user(user_id)
    if user["mick"] < amount:
        return {"ok": False, "reason": "insufficient_funds"}

    wallet = user["mick"] - amount
    atm_balance = user["atm_balance"] + amount
    await db.save_user(user_id, {"mick": wallet, "atm_balance": atm_balance})
    return {"ok": True, "wallet": wallet, "atm": atm_balance}


async def atm_withdraw(user_id: int, amount: int) -> dict:
    """Rút MICK từ ATM về ví. Trả {ok, reason?, wallet, atm}."""
    if amount <= 0:
        return {"ok": False, "reason": "invalid_amount"}
    user = await db.get_user(user_id)
    if user["atm_balance"] < amount:
        return {"ok": False, "reason": "insufficient_funds"}

    wallet = user["mick"] + amount
    atm_balance = user["atm_balance"] - amount
    await db.save_user(user_id, {"mick": wallet, "atm_balance": atm_balance})
    return {"ok": True, "wallet": wallet, "atm": atm_balance}


async def get_atm_profile(user_id: int) -> dict:
    user = await db.get_user(user_id)
    return {"wallet": user["mick"], "atm": user["atm_balance"]}


def transfer_delay_seconds(amount: int) -> float:
    """Tiền càng cao thì thời gian xử lý chuyển khoản càng lâu."""
    from config import TRANSFER_SECONDS_PER_MICK, TRANSFER_MIN_SECONDS, TRANSFER_MAX_SECONDS

    seconds = amount * TRANSFER_SECONDS_PER_MICK
    return max(TRANSFER_MIN_SECONDS, min(TRANSFER_MAX_SECONDS, seconds))
