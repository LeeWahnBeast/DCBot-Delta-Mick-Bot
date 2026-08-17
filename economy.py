"""
Hệ thống kinh tế: tiền tệ MICK + level.

- XP cộng dần khi chat (xem discord_bot.py), khi đủ ngưỡng thì lên level.
- Mỗi lần lên 1 level được cộng thẳng LEVEL_UP_MICK_REWARD (15) MICK.
- Công thức ngưỡng XP mỗi level (kiểu MEE6): 5*level^2 + 50*level + 100.
"""

import asyncio
from collections import defaultdict

import db
from config import LEVEL_UP_MICK_REWARD

# ---------------------------------------------------------------------------
# Lock theo user_id: chặn race condition khi cùng 1 user gửi nhiều request
# đọc-sửa-ghi tiền cùng lúc (vd spam nút, double-click, hoặc gọi lệnh 2 lần
# trong lúc lệnh trước còn đang chờ delay). Vì bot chỉ chạy 1 process nên
# asyncio.Lock trong RAM là đủ, không cần lock phân tán (Redis...).
# defaultdict tự tạo Lock mới cho user_id chưa từng thấy.
# ---------------------------------------------------------------------------
_user_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


def user_lock(user_id: int) -> asyncio.Lock:
    return _user_locks[user_id]


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
    """Cộng (hoặc trừ, nếu amount âm) MICK cho user. Trả về số dư mới.
    Có lock theo user_id để 2 request cùng lúc không đọc/ghi đè lên nhau."""
    async with user_lock(user_id):
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
    """Trừ MICK người gửi, cộng cho người nhận. Trả {ok, reason?, from_balance, to_balance}.

    Lock cả 2 user (theo thứ tự id tăng dần, cố định) để:
    - Check số dư + trừ tiền là 1 khối atomic -> spam lệnh nhiều lần không
      thể pass check "đủ tiền" nhiều lần trên cùng 1 số dư cũ (chặn dupe).
    - Thứ tự lock cố định (id nhỏ trước) để 2 transfer ngược chiều nhau
      (A->B và B->A cùng lúc) không bao giờ deadlock chờ nhau.
    """
    if amount <= 0:
        return {"ok": False, "reason": "invalid_amount"}
    if from_id == to_id:
        return {"ok": False, "reason": "self_transfer"}

    first_id, second_id = sorted((from_id, to_id))
    async with user_lock(first_id):
        async with user_lock(second_id):
            sender = await db.get_user(from_id)
            if sender["mick"] < amount:
                return {"ok": False, "reason": "insufficient_funds"}

            new_sender_balance = sender["mick"] - amount
            await db.save_user(from_id, {"mick": new_sender_balance})

            receiver = await db.get_user(to_id)
            new_receiver_balance = receiver["mick"] + amount
            await db.save_user(to_id, {"mick": new_receiver_balance})

    return {"ok": True, "from_balance": new_sender_balance, "to_balance": new_receiver_balance}


# ---------------------------------------------------------------------------
# ATM: giữ MICK hộ, tách khỏi ví tiêu xài (mick)
# ---------------------------------------------------------------------------


async def atm_deposit(user_id: int, amount: int) -> dict:
    """Gửi MICK từ ví vào ATM. Trả {ok, reason?, wallet, atm}."""
    if amount <= 0:
        return {"ok": False, "reason": "invalid_amount"}
    async with user_lock(user_id):
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
    async with user_lock(user_id):
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


# ---------------------------------------------------------------------------
# Cược casino (Tài Xỉu, Xì Dách): trừ tiền cược NGAY LÚC ĐẶT (atomic, có lock)
# để không thể đặt cược vượt số dư bằng cách spam. Thắng thì cộng lại qua
# add_mick() như bình thường (add_mick tự lock riêng, gọi từ ngoài nên không
# deadlock).
# ---------------------------------------------------------------------------


async def place_bet(user_id: int, amount: int) -> dict:
    """Trừ tiền cược ngay khi đặt. Trả {ok, reason?, wallet}."""
    if amount <= 0:
        return {"ok": False, "reason": "invalid_amount"}
    async with user_lock(user_id):
        user = await db.get_user(user_id)
        if user["mick"] < amount:
            return {"ok": False, "reason": "insufficient_funds"}
        wallet = user["mick"] - amount
        await db.save_user(user_id, {"mick": wallet})
        return {"ok": True, "wallet": wallet}
