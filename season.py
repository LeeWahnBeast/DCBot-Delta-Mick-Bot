"""
Season (mùa giải) theo tháng: mỗi tháng dương lịch là 1 season riêng, ID dạng
"2026-09". Trong mùa, mỗi lần user kiếm MICK "thật" (thắng game, level up,
daily, PvP...) cộng dồn thêm vào 1 bộ đếm RIÊNG "season_score" (không đụng
đến ví MICK thật) để xếp hạng mùa công bằng, không bị lệch bởi người chơi lâu
năm đã tích trữ sẵn nhiều MICK từ trước.

Khi bot phát hiện đã sang tháng mới (so với season_id lưu trong bot_state),
tự động: (1) chốt bảng xếp hạng mùa cũ, (2) phát thưởng MICK cho top 3,
(3) lưu vào lịch sử "season_history" để tra cứu, (4) mở season mới, mọi
season_score về 0.

Lưu trữ (Firebase RTDB qua db._get_doc/_set_doc):
- season_state/current: {"season_id": "2026-09", "started_at": epoch}
- season_scores/{season_id}_{user_id}: {"score": int, "season_id": str}
- season_history/{season_id}: {"top": [{user_id, score, reward}], "ended_at": epoch}
"""

import time
from datetime import datetime, timezone

import discord

import db
import economy
import features
from config import VN_UTC_OFFSET_HOURS

REWARD_TOP = {1: 1000, 2: 600, 3: 300}  # MICK thưởng cuối mùa cho hạng 1-3
LEADERBOARD_MAX = 10


def current_season_id() -> str:
    now = datetime.now(timezone.utc)
    # dùng giờ VN để season đổi mốc đúng nửa đêm VN thay vì UTC
    vn_now = now.timestamp() + VN_UTC_OFFSET_HOURS * 3600
    vn_dt = datetime.fromtimestamp(vn_now, tz=timezone.utc)
    return f"{vn_dt.year:04d}-{vn_dt.month:02d}"


async def get_season_state() -> dict:
    data = await db._get_doc("season_state", "current")
    if not data or not data.get("season_id"):
        data = {"season_id": current_season_id(), "started_at": time.time()}
        await db._set_doc("season_state", "current", data, merge=True)
    return data


async def _score_doc_id(season_id: str, user_id: int) -> str:
    return f"{season_id}_{user_id}"


async def add_season_score(user_id: int, amount: int) -> None:
    """Cộng điểm mùa cho user - gọi hàm này song song mỗi khi user kiếm được
    MICK thật (không thay thế add_mick, chỉ cộng thêm bộ đếm riêng)."""
    if amount <= 0 or economy.is_owner(user_id):
        return
    state = await get_season_state()
    season_id = state["season_id"]
    doc_id = await _score_doc_id(season_id, user_id)
    current = await db._get_doc("season_scores", doc_id)
    new_score = current.get("score", 0) + amount if current else amount
    await db._set_doc(
        "season_scores", doc_id,
        {"score": new_score, "season_id": season_id, "user_id": user_id},
        merge=True,
    )


async def get_season_leaderboard(season_id: str | None = None) -> list[dict]:
    state = await get_season_state()
    season_id = season_id or state["season_id"]
    all_scores, ok = await db._rtdb_request("GET", "season_scores")
    all_scores = all_scores or {}
    entries = [
        v for v in all_scores.values()
        if isinstance(v, dict) and v.get("season_id") == season_id
    ]
    entries.sort(key=lambda e: e.get("score", 0), reverse=True)
    return entries[:LEADERBOARD_MAX]


async def maybe_rollover_season() -> dict | None:
    """Gọi định kỳ (vd trong 1 tasks.loop có sẵn, hoặc lúc /season được dùng).
    Nếu tháng hiện tại khác season_id đang lưu -> chốt mùa cũ, phát thưởng,
    mở mùa mới. Trả về info mùa vừa kết thúc nếu có rollover, None nếu chưa
    tới lúc."""
    state = await get_season_state()
    old_season_id = state["season_id"]
    new_season_id = current_season_id()
    if new_season_id == old_season_id:
        return None

    leaderboard = await get_season_leaderboard(old_season_id)
    rewards_given = []
    for idx, entry in enumerate(leaderboard[:3], start=1):
        reward = REWARD_TOP.get(idx, 0)
        if reward <= 0:
            continue
        uid = entry.get("user_id")
        if uid is None:
            continue
        await economy.add_mick(int(uid), reward)
        rewards_given.append({"user_id": int(uid), "rank": idx, "score": entry.get("score", 0), "reward": reward})

    await db._set_doc(
        "season_history", old_season_id,
        {"top": rewards_given, "ended_at": time.time()},
        merge=True,
    )
    await db._set_doc(
        "season_state", "current",
        {"season_id": new_season_id, "started_at": time.time()},
        merge=False,
    )
    return {"old_season_id": old_season_id, "new_season_id": new_season_id, "rewards": rewards_given}


def build_leaderboard_container(season_id: str, entries: list[dict], name_lookup) -> discord.ui.Container:
    """name_lookup: callable(user_id:int) -> str hiển thị tên, do discord_bot
    truyền vào (cần guild.get_member nên không import discord ở đây)."""
    if not entries:
        desc = "Chưa có ai ghi điểm mùa này. Chơi minigame, nhận Daily, hoặc thắng PvP để lên bảng!"
    else:
        lines = []
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        for i, e in enumerate(entries, start=1):
            medal = medals.get(i, f"`#{i}`")
            name = name_lookup(int(e.get("user_id", 0)))
            lines.append(f"{medal} **{name}** — {e.get('score', 0)} điểm mùa")
        desc = "\n".join(lines)
        top_rewards = "  ·  ".join(f"Top {r}: {v} MICK" for r, v in REWARD_TOP.items())
        desc += f"\n\n🏆 Thưởng cuối mùa: {top_rewards}"

    return features.build_container(
        title=f"📅 Bảng xếp hạng mùa {season_id}",
        description=desc,
        color=discord.Color.purple(),
        footer="Điểm mùa reset mỗi tháng, không ảnh hưởng ví MICK thật.",
    )
