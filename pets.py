"""
Hệ thống Pet (thú cưng): nuôi bằng MICK, có độ no (hunger) giảm dần theo thời
gian thật, cần cho ăn định kỳ. Nếu để đói quá lâu (HUNGER = 0 kéo dài), pet có
thể BỎ TRỐN (mất pet, mất luôn XP/level đã train) - tạo động lực chăm sóc đều
đặn thay vì chỉ mua 1 lần rồi bỏ quên.

Lưu trong Firebase qua db._get_doc/_set_doc, collection "pets", doc_id = user_id
(1 user 1 pet tại 1 thời điểm, đơn giản hoá cho bản đầu).

Cấu trúc data 1 pet:
{
    "species": "cho" | "meo" | "rong" | "cop",
    "name": str,               # tên do user đặt
    "level": int,
    "xp": int,
    "hunger": int,             # 0-100
    "happiness": int,          # 0-100 (ảnh hưởng chút ít tới train)
    "last_fed_at": float,      # epoch giây
    "adopted_at": float,
    "battles_won": int,
    "battles_lost": int,
}
"""

import random
import time

import db
import economy
import features
from economy import is_owner
from config import CURRENCY_EMOJI

# ---------------------------------------------------------------------------
# Cấu hình
# ---------------------------------------------------------------------------

ADOPT_COST = 200  # MICK để nhận nuôi 1 pet

SPECIES = {
    "cho": {"name": "Chó", "emoji": "🐶", "flavor": "Trung thành, dễ nuôi, train nhanh."},
    "meo": {"name": "Mèo", "emoji": "🐱", "flavor": "Kiêu kỳ, ăn ít nhưng dễ hờn dỗi (happiness giảm nhanh hơn)."},
    "rong": {"name": "Rồng con", "emoji": "🐉", "flavor": "Hiếm, ăn nhiều, nhưng train ra XP cao hơn hẳn."},
    "cop": {"name": "Hổ con", "emoji": "🐯", "flavor": "Mạnh trong đấu PvP, hơi khó chiều."},
}

# Tốc độ đói: mỗi giờ mất bao nhiêu hunger, tuỳ loài (mèo/rồng ăn nhanh hơn)
HUNGER_DECAY_PER_HOUR = {
    "cho": 3.0,
    "meo": 4.0,
    "rong": 5.0,
    "cop": 4.0,
}
HAPPINESS_DECAY_PER_HOUR = {
    "cho": 1.5,
    "meo": 3.0,
    "rong": 2.0,
    "cop": 2.5,
}

FEED_COST = 20  # MICK / lần cho ăn
FEED_HUNGER_GAIN = 40
PLAY_HAPPINESS_GAIN = 25
PLAY_COOLDOWN_SEC = 30 * 60  # 30 phút / lần chơi cùng pet

TRAIN_XP_MIN = 8
TRAIN_XP_MAX = 20
TRAIN_MICK_COST = 10
TRAIN_COOLDOWN_SEC = 20 * 60

# Nếu hunger chạm 0 và cứ đứng ở 0 quá thời gian này -> pet bỏ trốn khi user
# tương tác lại (hoặc quét nền), mất trắng pet.
STARVE_ABANDON_HOURS = 48

PET_XP_PER_LEVEL = 60  # tuyến tính đơn giản, khác công thức level người chơi


def xp_needed(level: int) -> int:
    return PET_XP_PER_LEVEL + (level - 1) * 20


def _now() -> float:
    return time.time()


async def get_pet(user_id: int) -> dict | None:
    data = await db._get_doc("pets", str(user_id))
    return data or None


async def _save_pet(user_id: int, data: dict) -> bool:
    return await db._set_doc("pets", str(user_id), data, merge=True)


async def delete_pet(user_id: int) -> bool:
    return await db._delete_doc("pets", str(user_id))


def _apply_decay(pet: dict) -> dict:
    """Tính lại hunger/happiness hiện tại dựa trên thời gian trôi qua kể từ
    last_fed_at / cập nhật gần nhất, KHÔNG ghi DB (chỉ tính để hiển thị/dùng).
    Gọi _save_pet riêng nếu muốn chốt số liệu xuống DB."""
    species = pet.get("species", "cho")
    elapsed_hours = max(0.0, (_now() - pet.get("last_fed_at", _now())) / 3600)
    hunger_decay = HUNGER_DECAY_PER_HOUR.get(species, 3.0) * elapsed_hours
    happiness_decay = HAPPINESS_DECAY_PER_HOUR.get(species, 2.0) * elapsed_hours

    pet = dict(pet)
    pet["hunger"] = max(0, round(pet.get("hunger", 100) - hunger_decay))
    pet["happiness"] = max(0, round(pet.get("happiness", 100) - happiness_decay))
    return pet


async def get_pet_live(user_id: int) -> dict | None:
    """Lấy pet + áp dụng decay tính đến hiện tại (không ghi DB)."""
    pet = await get_pet(user_id)
    if not pet:
        return None
    return _apply_decay(pet)


async def check_starvation(user_id: int) -> bool:
    """Kiểm tra xem pet có bị bỏ trốn do đói lâu không. Nếu có, xoá pet và trả
    True. Gọi hàm này TRƯỚC mọi thao tác feed/train/play/status để đảm bảo
    trạng thái luôn nhất quán."""
    pet = await get_pet(user_id)
    if not pet:
        return False
    live = _apply_decay(pet)
    if live["hunger"] <= 0:
        # hunger đã chạm 0 - xem đã chạm 0 được bao lâu (ước lượng ngược từ
        # last_fed_at + thời điểm hunger lý thuyết chạm 0)
        species = pet.get("species", "cho")
        decay_rate = HUNGER_DECAY_PER_HOUR.get(species, 3.0)
        hours_to_zero = pet.get("hunger", 100) / decay_rate if decay_rate else 999
        zero_at = pet.get("last_fed_at", _now()) + hours_to_zero * 3600
        hours_starving = max(0.0, (_now() - zero_at) / 3600)
        if hours_starving >= STARVE_ABANDON_HOURS:
            await delete_pet(user_id)
            return True
    return False


async def adopt_pet(user_id: int, species: str, name: str) -> dict:
    if species not in SPECIES:
        return {"ok": False, "reason": "invalid_species"}
    existing = await get_pet(user_id)
    if existing:
        return {"ok": False, "reason": "already_has_pet"}
    name = name.strip()[:24] or SPECIES[species]["name"]

    if not is_owner(user_id):
        profile = await economy.get_profile(user_id)
        if profile["mick"] < ADOPT_COST:
            return {"ok": False, "reason": "insufficient_funds"}
        await economy.add_mick(user_id, -ADOPT_COST)

    pet = {
        "species": species,
        "name": name,
        "level": 1,
        "xp": 0,
        "hunger": 100,
        "happiness": 100,
        "last_fed_at": _now(),
        "adopted_at": _now(),
        "battles_won": 0,
        "battles_lost": 0,
        "last_trained_at": 0,
        "last_played_at": 0,
    }
    await _save_pet(user_id, pet)
    return {"ok": True, "pet": pet}


async def feed_pet(user_id: int) -> dict:
    ran_away = await check_starvation(user_id)
    if ran_away:
        return {"ok": False, "reason": "ran_away"}
    pet = await get_pet(user_id)
    if not pet:
        return {"ok": False, "reason": "no_pet"}

    if not is_owner(user_id):
        profile = await economy.get_profile(user_id)
        if profile["mick"] < FEED_COST:
            return {"ok": False, "reason": "insufficient_funds"}
        await economy.add_mick(user_id, -FEED_COST)

    live = _apply_decay(pet)
    live["hunger"] = min(100, live["hunger"] + FEED_HUNGER_GAIN)
    live["last_fed_at"] = _now()
    await _save_pet(user_id, live)
    return {"ok": True, "pet": live}


async def play_with_pet(user_id: int) -> dict:
    ran_away = await check_starvation(user_id)
    if ran_away:
        return {"ok": False, "reason": "ran_away"}
    pet = await get_pet(user_id)
    if not pet:
        return {"ok": False, "reason": "no_pet"}

    live = _apply_decay(pet)
    last_played = live.get("last_played_at", 0)
    remaining = PLAY_COOLDOWN_SEC - (_now() - last_played)
    if remaining > 0:
        return {"ok": False, "reason": "cooldown", "remaining": int(remaining)}

    live["happiness"] = min(100, live["happiness"] + PLAY_HAPPINESS_GAIN)
    live["last_played_at"] = _now()
    await _save_pet(user_id, live)
    return {"ok": True, "pet": live}


async def train_pet(user_id: int) -> dict:
    ran_away = await check_starvation(user_id)
    if ran_away:
        return {"ok": False, "reason": "ran_away"}
    pet = await get_pet(user_id)
    if not pet:
        return {"ok": False, "reason": "no_pet"}

    live = _apply_decay(pet)
    if live["hunger"] <= 10:
        return {"ok": False, "reason": "too_hungry", "pet": live}

    last_trained = live.get("last_trained_at", 0)
    remaining = TRAIN_COOLDOWN_SEC - (_now() - last_trained)
    if remaining > 0:
        return {"ok": False, "reason": "cooldown", "remaining": int(remaining)}

    if not is_owner(user_id):
        profile = await economy.get_profile(user_id)
        if profile["mick"] < TRAIN_MICK_COST:
            return {"ok": False, "reason": "insufficient_funds"}
        await economy.add_mick(user_id, -TRAIN_MICK_COST)

    species = live.get("species", "cho")
    base_xp = random.randint(TRAIN_XP_MIN, TRAIN_XP_MAX)
    if species == "rong":
        base_xp = round(base_xp * 1.4)
    happiness_bonus = 1.15 if live["happiness"] >= 70 else 1.0
    gained_xp = round(base_xp * happiness_bonus)

    live["xp"] += gained_xp
    leveled_up = False
    while live["xp"] >= xp_needed(live["level"]):
        live["xp"] -= xp_needed(live["level"])
        live["level"] += 1
        leveled_up = True

    live["hunger"] = max(0, live["hunger"] - 8)
    live["last_trained_at"] = _now()
    await _save_pet(user_id, live)
    return {"ok": True, "pet": live, "gained_xp": gained_xp, "leveled_up": leveled_up}


def pet_power(pet: dict) -> int:
    """Sức mạnh dùng để tính lợi thế nhẹ trong PvP nếu 2 người đều mang pet.
    Không quyết định thắng thua tuyệt đối - chỉ dùng làm hệ số may mắn nhỏ."""
    species = pet.get("species", "cho")
    species_bonus = {"cop": 6, "rong": 5, "cho": 3, "meo": 2}.get(species, 3)
    return pet.get("level", 1) * 2 + species_bonus


def hunger_bar(value: int) -> str:
    filled = round(value / 10)
    return "🟩" * filled + "⬜" * (10 - filled)


def happiness_bar(value: int) -> str:
    filled = round(value / 10)
    return "🟨" * filled + "⬜" * (10 - filled)


def build_pet_container(display_name: str, pet: dict) -> "discord.ui.Container":
    import discord
    sp = SPECIES.get(pet.get("species", "cho"), SPECIES["cho"])
    hunger = pet.get("hunger", 100)
    happiness = pet.get("happiness", 100)

    warn = ""
    if hunger <= 20:
        warn = "\n⚠️ **Pet đang rất đói!** Cho ăn ngay kẻo bỏ trốn."
    elif hunger <= 50:
        warn = "\n🔸 Pet hơi đói rồi, nên cho ăn sớm."

    desc = (
        f"{sp['emoji']} **{pet.get('name')}** ({sp['name']}) · Lv.{pet.get('level', 1)}\n"
        f"XP: {pet.get('xp', 0)}/{xp_needed(pet.get('level', 1))}\n\n"
        f"🍖 Độ no: {hunger}/100\n{hunger_bar(hunger)}\n\n"
        f"😊 Vui vẻ: {happiness}/100\n{happiness_bar(happiness)}"
        f"{warn}"
    )
    color = discord.Color.green() if hunger > 50 else (discord.Color.orange() if hunger > 20 else discord.Color.red())
    return features.build_container(
        title=f"🐾 Thú cưng của {display_name}",
        description=desc,
        color=color,
        footer=f"Thắng {pet.get('battles_won', 0)} · Thua {pet.get('battles_lost', 0)} trận PvP",
    )
