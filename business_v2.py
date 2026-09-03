"""
Mở rộng hệ thống Business (features.py) mà KHÔNG sửa code gốc, để dễ bật/tắt
và không phá vỡ hệ thống cũ nếu có lỗi:

1. NÂNG CẤP CƠ SỞ (upgrade level 1-5): mỗi level tăng % thu nhập cố định,
   tốn MICK theo cấp số nhân. Lưu thêm field "level" vào business doc hiện có
   (dùng chung db.get_business/save_business, KHÔNG tạo collection mới).

2. THỊ TRƯỜNG BIẾN ĐỘNG: mỗi loại hình kinh doanh có 1 "market_multiplier"
   thay đổi nhẹ mỗi giờ (random walk trong khoảng 0.8x - 1.3x), lưu trong
   collection riêng "market_state" để không đụng dữ liệu business của user.
   Dùng run_income_tick_v2() THAY THẾ features.run_income_tick() trong vòng
   lặp business_tick_loop của discord_bot.py để áp dụng cả upgrade lẫn market
   vào công thức tính thu nhập.
"""

import random
import time

import db
import economy
import features
from features import BUSINESS_NAMES
from config import BUSINESS_INCOME_PER_TICK, BUSINESS_TICK_SEC, log

UPGRADE_MAX_LEVEL = 5
UPGRADE_INCOME_BONUS_PER_LEVEL = 0.15  # +15% thu nhập mỗi level nâng cấp
UPGRADE_BASE_COST = 150  # cấp độ 2 tốn 150, cấp 3 tốn 300, v.v. (nhân theo level)

MARKET_TICK_SEC = 3600  # thị trường đổi hệ số mỗi giờ
MARKET_MIN_MULT = 0.8
MARKET_MAX_MULT = 1.3
MARKET_MAX_STEP = 0.08  # mỗi lần đổi, dao động tối đa +-0.08 so với hiện tại


def upgrade_cost(current_level: int) -> int:
    return UPGRADE_BASE_COST * current_level


async def upgrade_business(user_id: int, kind: str) -> dict:
    if kind not in BUSINESS_NAMES:
        return {"ok": False, "reason": "invalid_kind"}

    biz = await db.get_business(user_id, kind)
    if not biz.get("opened_at"):
        return {"ok": False, "reason": "not_opened"}

    level = biz.get("level", 1)
    if level >= UPGRADE_MAX_LEVEL:
        return {"ok": False, "reason": "max_level"}

    cost = upgrade_cost(level)
    if not economy.is_owner(user_id):
        profile = await economy.get_profile(user_id)
        if profile["mick"] < cost:
            return {"ok": False, "reason": "insufficient_funds", "cost": cost}

    await economy.add_mick(user_id, -cost)
    new_level = level + 1
    await db.save_business(user_id, kind, {"level": new_level})
    return {"ok": True, "level": new_level, "cost": cost}


# ---------------------------------------------------------------------------
# Thị trường: mỗi kind có 1 doc trong "market_state" collection.
# ---------------------------------------------------------------------------


async def get_market_multiplier(kind: str) -> float:
    state = await db._get_doc("market_state", kind)
    now = time.time()
    if not state or now - state.get("updated_at", 0) >= MARKET_TICK_SEC:
        current = state.get("multiplier", 1.0) if state else 1.0
        step = random.uniform(-MARKET_MAX_STEP, MARKET_MAX_STEP)
        new_mult = max(MARKET_MIN_MULT, min(MARKET_MAX_MULT, current + step))
        await db._set_doc("market_state", kind, {"multiplier": new_mult, "updated_at": now}, merge=True)
        return new_mult
    return state.get("multiplier", 1.0)


async def get_all_market_multipliers() -> dict[str, float]:
    out = {}
    for kind in BUSINESS_NAMES:
        out[kind] = await get_market_multiplier(kind)
    return out


def market_trend_emoji(mult: float) -> str:
    if mult >= 1.15:
        return "📈"
    if mult <= 0.9:
        return "📉"
    return "➖"


# ---------------------------------------------------------------------------
# Tick thu nhập v2: thay thế features.run_income_tick(), áp dụng cả upgrade
# level lẫn market_multiplier vào công thức.
# ---------------------------------------------------------------------------


async def run_income_tick_v2() -> int:
    now = int(time.time())
    paid = 0
    market_cache: dict[str, float] = {}

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

        if kind not in market_cache:
            market_cache[kind] = await get_market_multiplier(kind)
        market_mult = market_cache[kind]

        level = biz.get("level", 1)
        level_mult = 1.0 + (level - 1) * UPGRADE_INCOME_BONUS_PER_LEVEL

        base_income = staff * BUSINESS_INCOME_PER_TICK[kind]
        income = round(base_income * level_mult * market_mult)

        await economy.add_mick(user_id, income)
        try:
            import season
            await season.add_season_score(user_id, income)
        except Exception:
            pass

        total_earned = biz.get("total_earned", 0) + income
        await db.save_business(user_id, kind, {"last_tick": now, "total_earned": total_earned})
        paid += 1

    if paid:
        log.info("Business tick v2 (upgrade+market): đã trả thu nhập cho %s cơ sở.", paid)
    return paid


def build_market_container(display_name: str, multipliers: dict[str, float]):
    import discord
    lines = []
    for kind, mult in multipliers.items():
        name = BUSINESS_NAMES.get(kind, kind)
        trend = market_trend_emoji(mult)
        lines.append(f"{trend} {name}: **x{mult:.2f}**")
    desc = "\n".join(lines) + "\n\n-# Thị trường đổi hệ số mỗi giờ, ảnh hưởng thu nhập tick tiếp theo."
    return features.build_container(
        title="📊 Thị trường kinh doanh hiện tại",
        description=desc,
        color=discord.Color.teal(),
    )
