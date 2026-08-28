"""
Hệ thống kinh tế: tiền tệ MICK + level.

- XP cộng dần khi chat (xem discord_bot.py), khi đủ ngưỡng thì lên level.
- Mỗi lần lên 1 level được cộng thẳng LEVEL_UP_MICK_REWARD (15) MICK.
- Công thức ngưỡng XP mỗi level (kiểu MEE6): 5*level^2 + 50*level + 100.
"""

import asyncio
import time
from collections import defaultdict

import db
from config import (
    LEVEL_UP_MICK_REWARD,
    BOT_OWNER_ID,
    GAME_TICKET_COST,
    TRANSFER_FEE_PERCENT,
    ATM_INTEREST_RATE_PER_DAY,
)

# Giá trị hiển thị cho MICK/Vé của chủ bot - không lưu số này xuống DB, chỉ
# override lúc đọc/trừ để owner luôn thấy/dùng được "vô hạn" (∞).
INFINITE = float("inf")


def is_owner(user_id: int) -> bool:
    return bool(BOT_OWNER_ID) and user_id == BOT_OWNER_ID

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


# ---------------------------------------------------------------------------
# Lịch sử giao dịch: tối đa TX_HISTORY_MAX dòng gần nhất/user, lưu ngay trong
# doc user (field tx_history) để khỏi tốn thêm collection/query riêng. Owner
# (MICK vô hạn) không cần lưu lịch sử.
# ---------------------------------------------------------------------------

TX_HISTORY_MAX = 15

TX_TYPE_LABELS = {
    "transfer_out": "📤 Chuyển đi",
    "transfer_in": "📥 Nhận được",
    "atm_deposit": "🏧 Gửi ATM",
    "atm_withdraw": "🏧 Rút ATM",
    "atm_interest": "📈 Lãi ATM",
    "bet": "🎲 Đặt cược",
}


def _append_tx(user: dict, tx_type: str, amount, balance_after, note: str = "") -> list:
    """Trả về list tx_history MỚI (đã thêm dòng này + cắt còn tối đa
    TX_HISTORY_MAX dòng) - gọi trong cùng 1 db.save_user() với thay đổi số
    dư để khỏi tốn thêm round-trip Firebase / tránh lệch dữ liệu do race."""
    history = list(user.get("tx_history", []))
    history.append({
        "type": tx_type,
        "amount": amount,
        "balance_after": balance_after,
        "note": note,
        "at": int(time.time()),
    })
    return history[-TX_HISTORY_MAX:]


async def get_tx_history(user_id: int) -> list:
    user = await db.get_user(user_id)
    return list(reversed(user.get("tx_history", [])))


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


async def add_mick(user_id: int, amount: int) -> int | float:
    """Cộng (hoặc trừ, nếu amount âm) MICK cho user. Trả về số dư mới.
    Có lock theo user_id để 2 request cùng lúc không đọc/ghi đè lên nhau.

    Chủ bot (BOT_OWNER_ID) có MICK vô hạn: không bao giờ bị trừ hết, trả về
    INFINITE (hiển thị "∞" ở embed/web) mà không cần ghi DB.

    DeltaX (/mick-shop): nếu user đang sở hữu item còn hạn và amount > 0
    (chỉ nhân MICK KIẾM ĐƯỢC, không nhân khi bị trừ tiền vd cược thua/chuyển
    khoản), nhân thêm hệ số ngẫu nhiên đã chốt lúc mua (multiplier lưu sẵn
    trong shop_purchases, không random lại mỗi lần cộng)."""
    if is_owner(user_id):
        return INFINITE
    if amount > 0:
        amount = await _apply_deltax_multiplier(user_id, amount)
    async with user_lock(user_id):
        user = await db.get_user(user_id)
        new_balance = max(0, user["mick"] + amount)
        await db.save_user(user_id, {"mick": new_balance})
        return new_balance


async def _apply_deltax_multiplier(user_id: int, amount: int) -> int:
    import time as _time
    try:
        purchase = await db.get_shop_purchase(user_id, "deltax")
        if not purchase or purchase.get("expires_at", 0) <= _time.time():
            return amount
        mult = purchase.get("multiplier", 1.0)
        return max(1, round(amount * mult))
    except Exception:
        return amount


# ---------------------------------------------------------------------------
# Vé: dùng để chơi minigame (Wordle/Đoán số/Kéo Búa Bao/Tài Xỉu/Xì Dách/
# Trivia...). Chủ bot có Vé vô hạn.
# ---------------------------------------------------------------------------


async def get_ve(user_id: int) -> int | float:
    if is_owner(user_id):
        return INFINITE
    user = await db.get_user(user_id)
    return user.get("ve", 0)


async def add_ve(user_id: int, amount: int) -> int | float:
    """Cộng (hoặc trừ) Vé. Owner luôn vô hạn, không ghi DB."""
    if is_owner(user_id):
        return INFINITE
    async with user_lock(user_id):
        user = await db.get_user(user_id)
        new_balance = max(0, user.get("ve", 0) + amount)
        await db.save_user(user_id, {"ve": new_balance})
        return new_balance


async def spend_game_ticket(user_id: int) -> dict:
    """Trừ GAME_TICKET_COST Vé để bắt đầu 1 ván minigame. Trả {ok, ve, reason?}.
    Owner luôn ok, Vé hiển thị "∞"."""
    if is_owner(user_id):
        return {"ok": True, "ve": INFINITE}
    async with user_lock(user_id):
        user = await db.get_user(user_id)
        current = user.get("ve", 0)
        if current < GAME_TICKET_COST:
            return {"ok": False, "reason": "insufficient_tickets", "ve": current}
        new_balance = current - GAME_TICKET_COST
        await db.save_user(user_id, {"ve": new_balance})
        return {"ok": True, "ve": new_balance}


# Số hiển thị cho owner thay vì ký hiệu "∞" - CHỈ là số hiển thị (cosmetic),
# số dư thật trong Firebase của owner vẫn chỉ có vài MICK/Vé như bình thường
# (add_mick/add_ve/spend_game_ticket ở trên không ghi INFINITE xuống DB).
OWNER_DISPLAY_AMOUNT = 9999999999


def format_ve(ve: int | float) -> str:
    return str(OWNER_DISPLAY_AMOUNT) if ve == INFINITE else str(ve)


def format_mick(mick: int | float) -> str:
    return str(OWNER_DISPLAY_AMOUNT) if mick == INFINITE else str(mick)


async def get_profile(user_id: int) -> dict:
    user = await db.get_user(user_id)
    needed = xp_needed_for_level(user["level"])
    owner = is_owner(user_id)
    return {
        "mick": INFINITE if owner else user["mick"],
        "ve": INFINITE if owner else user.get("ve", 0),
        "level": user["level"],
        "xp": user["xp"],
        "xp_needed": needed,
        "uuid": user.get("uuid", ""),
        "is_owner": owner,
        "daily_streak": user.get("daily_streak", 0),
        "daily_history": user.get("daily_history", []),
    }


async def transfer_mick(from_id: int, to_id: int, amount: int) -> dict:
    """Trừ MICK người gửi, cộng cho người nhận (đã trừ phí). Trả {ok, reason?,
    from_balance, to_balance, fee, received}.

    Phí chuyển khoản (TRANSFER_FEE_PERCENT) bị trừ thẳng vào số tiền TRƯỚC
    KHI cộng cho người nhận - người gửi vẫn chỉ mất đúng `amount` đã nhập,
    người nhận thực nhận `amount - fee`. Phí "bốc hơi" khỏi lưu thông, không
    có quỹ chung nào giữ số này.

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

    fee = max(0, round(amount * TRANSFER_FEE_PERCENT / 100))
    received = max(0, amount - fee)

    first_id, second_id = sorted((from_id, to_id))
    async with user_lock(first_id):
        async with user_lock(second_id):
            sender = await db.get_user(from_id)
            if sender["mick"] < amount:
                return {"ok": False, "reason": "insufficient_funds"}

            new_sender_balance = sender["mick"] - amount
            sender_tx = _append_tx(
                sender, "transfer_out", -amount, new_sender_balance,
                note=f"Đến <@{to_id}>" + (f", phí {fee}" if fee else ""),
            )
            await db.save_user(from_id, {"mick": new_sender_balance, "tx_history": sender_tx})

            receiver = await db.get_user(to_id)
            new_receiver_balance = receiver["mick"] + received
            receiver_tx = _append_tx(
                receiver, "transfer_in", received, new_receiver_balance,
                note=f"Từ <@{from_id}>",
            )
            await db.save_user(to_id, {"mick": new_receiver_balance, "tx_history": receiver_tx})

    return {
        "ok": True,
        "from_balance": new_sender_balance,
        "to_balance": new_receiver_balance,
        "fee": fee,
        "received": received,
    }


# ---------------------------------------------------------------------------
# ATM: giữ MICK hộ, tách khỏi ví tiêu xài (mick)
# ---------------------------------------------------------------------------


async def _apply_atm_interest(user_id: int, user: dict) -> dict:
    """Cộng lãi ATM dồn từ lần cuối tính lãi đến giờ (lãi kép theo ngày,
    ATM_INTEREST_RATE_PER_DAY %/ngày). Gọi lười (lazy) mỗi khi user tương
    tác ATM (gửi/rút/xem số dư) - không cần vòng lặp nền riêng.

    Trả về user dict đã cập nhật (atm_balance/atm_last_interest_at mới nếu
    có lãi phát sinh, đã ghi DB kèm dòng lịch sử). Nếu chưa gửi ATM lần nào
    hoặc chưa đủ 1 giờ trôi qua thì không làm gì (đỡ ghi DB liên tục)."""
    atm = user.get("atm_balance", 0)
    last_at = user.get("atm_last_interest_at", 0)
    now = int(time.time())

    if atm <= 0:
        return user
    if last_at <= 0:
        # Lần đầu có tiền trong ATM nhưng chưa có mốc tính lãi -> chỉ đặt mốc,
        # chưa cộng lãi (tránh lãi ảo tính từ epoch 0).
        await db.save_user(user_id, {"atm_last_interest_at": now})
        user["atm_last_interest_at"] = now
        return user

    elapsed_days = max(0.0, (now - last_at) / 86400)
    if elapsed_days < (1 / 24):  # chưa đủ 1 tiếng, bỏ qua để đỡ tốn ghi Firebase
        return user

    rate = ATM_INTEREST_RATE_PER_DAY / 100
    new_atm_float = atm * ((1 + rate) ** elapsed_days)
    interest = round(new_atm_float - atm)
    if interest <= 0:
        await db.save_user(user_id, {"atm_last_interest_at": now})
        user["atm_last_interest_at"] = now
        return user

    new_atm = atm + interest
    tx = _append_tx(
        user, "atm_interest", interest, new_atm,
        note=f"Lãi {ATM_INTEREST_RATE_PER_DAY}%/ngày × {elapsed_days:.1f} ngày",
    )
    await db.save_user(user_id, {"atm_balance": new_atm, "atm_last_interest_at": now, "tx_history": tx})
    user["atm_balance"] = new_atm
    user["atm_last_interest_at"] = now
    user["tx_history"] = tx
    return user


async def atm_deposit(user_id: int, amount: int) -> dict:
    """Gửi MICK từ ví vào ATM. Trả {ok, reason?, wallet, atm}."""
    if amount <= 0:
        return {"ok": False, "reason": "invalid_amount"}
    async with user_lock(user_id):
        user = await db.get_user(user_id)
        user = await _apply_atm_interest(user_id, user)
        if user["mick"] < amount:
            return {"ok": False, "reason": "insufficient_funds"}

        wallet = user["mick"] - amount
        atm_balance = user["atm_balance"] + amount
        tx = _append_tx(user, "atm_deposit", amount, atm_balance, note=f"Ví còn {wallet}")
        data = {"mick": wallet, "atm_balance": atm_balance, "tx_history": tx}
        if user.get("atm_last_interest_at", 0) <= 0:
            data["atm_last_interest_at"] = int(time.time())
        await db.save_user(user_id, data)
        return {"ok": True, "wallet": wallet, "atm": atm_balance}


async def atm_withdraw(user_id: int, amount: int) -> dict:
    """Rút MICK từ ATM về ví. Trả {ok, reason?, wallet, atm}."""
    if amount <= 0:
        return {"ok": False, "reason": "invalid_amount"}
    async with user_lock(user_id):
        user = await db.get_user(user_id)
        user = await _apply_atm_interest(user_id, user)
        if user["atm_balance"] < amount:
            return {"ok": False, "reason": "insufficient_funds"}

        wallet = user["mick"] + amount
        atm_balance = user["atm_balance"] - amount
        tx = _append_tx(user, "atm_withdraw", -amount, atm_balance, note=f"Ví còn {wallet}")
        await db.save_user(user_id, {"mick": wallet, "atm_balance": atm_balance, "tx_history": tx})
        return {"ok": True, "wallet": wallet, "atm": atm_balance}


async def get_atm_profile(user_id: int) -> dict:
    user = await db.get_user(user_id)
    user = await _apply_atm_interest(user_id, user)
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
    """Trừ tiền cược ngay khi đặt. Trả {ok, reason?, wallet}.
    Owner có MICK vô hạn nên không bị trừ, luôn được cược."""
    if amount <= 0:
        return {"ok": False, "reason": "invalid_amount"}
    if is_owner(user_id):
        return {"ok": True, "wallet": INFINITE}
    async with user_lock(user_id):
        user = await db.get_user(user_id)
        if user["mick"] < amount:
            return {"ok": False, "reason": "insufficient_funds"}
        wallet = user["mick"] - amount
        tx = _append_tx(user, "bet", -amount, wallet, note="Đặt cược")
        await db.save_user(user_id, {"mick": wallet, "tx_history": tx})
        return {"ok": True, "wallet": wallet}
