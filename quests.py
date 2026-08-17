"""
Quest hằng ngày: mỗi ngày random 3 quest trong danh sách cố định, reset theo
giờ VN (dùng chung mốc ngày với daily.py). Người dùng gõ /quest để xem, tiến
độ tự cập nhật qua on_message (hook trong discord_bot.py) hoặc lệnh liên quan.
"""

import random

import discord

import db
import economy
from config import QUEST_COUNT_PER_DAY, QUEST_REWARD_MICK

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


def vn_today_str() -> str:
    from daily import vn_today_str as _t

    return _t()


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
