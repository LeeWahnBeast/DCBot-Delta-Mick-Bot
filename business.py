"""
Minigame kinh doanh: quán (quan) / công ty (congty) / nhà trọ (nhatro) /
khách sạn (khachsan). Mở cơ sở tốn 1 lần MICK, sau đó thuê nhân viên
(mỗi nhân viên tốn MICK cố định). Mỗi BUSINESS_TICK_SEC (mặc định 30 phút),
mỗi nhân viên đang thuê tạo ra thu nhập cho chủ - kể cả khi chủ offline,
vì tick chạy nền trong loop của bot (không phụ thuộc user đang online).
"""

import time

import discord

import db
from config import (
    BUSINESS_INCOME_PER_TICK,
    BUSINESS_OPEN_COST,
    BUSINESS_HIRE_COST,
    BUSINESS_MAX_STAFF,
    BUSINESS_TICK_SEC,
    log,
)

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

    import economy

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

    import economy

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


# ---------------------------------------------------------------------------
# Tick nền: chạy trong loop định kỳ, cộng thu nhập cho MỌI cơ sở đang có staff
# ---------------------------------------------------------------------------


async def run_income_tick() -> int:
    """Duyệt toàn bộ business, cộng thu nhập nếu đã đủ BUSINESS_TICK_SEC kể từ last_tick.
    Trả về số cơ sở vừa được trả lương."""
    import economy

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
