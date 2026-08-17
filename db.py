"""
Lớp lưu trữ duy nhất của bot: Firestore.

Gộp toàn bộ thao tác đọc/ghi dữ liệu bền vững ở đây:
- bot_state    : last_video_id, was_live, last_identity_avatar_url (thay cho data.json cũ)
- videos       : trạng thái "đã thông báo" (notified) của từng video TikTok
- users        : MICK, XP, level, ngày nhận Daily gần nhất
- daily        : mốc thời gian bắt đầu chu kỳ Daily hiện tại (để tính giảm dần theo giờ)
- site         : lượt xem + rating (sao) của web dashboard

Nếu chưa cấu hình Firestore (thiếu credentials), bot vẫn chạy được nhờ một
lớp fallback lưu tạm trong RAM, để không bị crash khi test ở máy local.
"""

import json
import os
import tempfile

from config import FIREBASE_CREDENTIALS_JSON, FIRESTORE_PROJECT_ID, log

_client = None
_use_memory_fallback = False
_memory_store: dict = {}


def _init_client():
    """Khởi tạo AsyncClient của Firestore, ưu tiên credentials trong env var."""
    global _client, _use_memory_fallback

    try:
        from google.cloud import firestore

        if FIREBASE_CREDENTIALS_JSON:
            fd, path = tempfile.mkstemp(prefix="firebase-", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(FIREBASE_CREDENTIALS_JSON)
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = path

        kwargs = {}
        if FIRESTORE_PROJECT_ID:
            kwargs["project"] = FIRESTORE_PROJECT_ID

        _client = firestore.AsyncClient(**kwargs)
        log.info("Firestore đã sẵn sàng.")
    except Exception as e:
        log.warning("Không khởi tạo được Firestore (%s) -> dùng bộ nhớ tạm (RAM, mất khi restart).", e)
        _client = None
        _use_memory_fallback = True


_init_client()


# ---------------------------------------------------------------------------
# Helper nội bộ: fallback RAM khi không có Firestore (chỉ để dev/test local)
# ---------------------------------------------------------------------------


async def _get_doc(collection: str, doc_id: str) -> dict:
    if _use_memory_fallback:
        return dict(_memory_store.get(f"{collection}/{doc_id}", {}))
    snap = await _client.collection(collection).document(doc_id).get()
    return snap.to_dict() or {} if snap.exists else {}


async def _set_doc(collection: str, doc_id: str, data: dict, merge: bool = True) -> None:
    if _use_memory_fallback:
        key = f"{collection}/{doc_id}"
        if merge:
            _memory_store.setdefault(key, {}).update(data)
        else:
            _memory_store[key] = dict(data)
        return
    await _client.collection(collection).document(doc_id).set(data, merge=merge)


# ---------------------------------------------------------------------------
# bot_state: thay thế data.json cũ (last_video_id, was_live, avatar...)
# ---------------------------------------------------------------------------


async def get_bot_state() -> dict:
    return await _get_doc("bot_state", "main")


async def save_bot_state(state: dict) -> None:
    await _set_doc("bot_state", "main", state, merge=True)


# ---------------------------------------------------------------------------
# videos: theo dõi video đã thông báo (notified) hay chưa, để retry nếu lỗi
# ---------------------------------------------------------------------------


async def get_video(video_id: str) -> dict:
    return await _get_doc("videos", video_id)


async def save_video(video_id: str, data: dict) -> None:
    await _set_doc("videos", video_id, data, merge=True)


async def get_unnotified_video_ids(limit: int = 5) -> list[str]:
    """Trả về danh sách video_id chưa được đánh dấu notified=True (để bot gọi lại/ping lại)."""
    if _use_memory_fallback:
        return [
            key.split("/", 1)[1]
            for key, val in _memory_store.items()
            if key.startswith("videos/") and not val.get("notified")
        ][:limit]
    query = _client.collection("videos").where("notified", "==", False).limit(limit)
    docs = [d async for d in query.stream()]
    return [d.id for d in docs]


# ---------------------------------------------------------------------------
# users: MICK, XP, level, Daily
# ---------------------------------------------------------------------------

DEFAULT_USER = {
    "mick": 0,
    "xp": 0,
    "level": 0,
    "last_xp_at": 0,
    "last_daily_date": "",
    "atm_balance": 0,
    "achievements": [],  # list[str] id thành tựu đã mở
    "quest_date": "",
    "quest_ids": [],  # 3 quest hôm nay
    "quest_progress": {},  # {quest_id: count}
    "quest_done": [],  # quest_id đã hoàn thành hôm nay
}


async def get_user(user_id: int) -> dict:
    data = await _get_doc("users", str(user_id))
    merged = dict(DEFAULT_USER)
    merged.update(data)
    return merged


async def get_all_users() -> list[tuple[str, dict]]:
    """Trả về [(user_id_str, data), ...] toàn bộ user - dùng cho leaderboard/business tick."""
    if _use_memory_fallback:
        out = []
        for key, val in _memory_store.items():
            if key.startswith("users/"):
                out.append((key.split("/", 1)[1], dict(val)))
        return out
    docs = [d async for d in _client.collection("users").stream()]
    return [(d.id, d.to_dict() or {}) for d in docs]


async def save_user(user_id: int, data: dict) -> None:
    await _set_doc("users", str(user_id), data, merge=True)


# ---------------------------------------------------------------------------
# daily: mốc reset của chu kỳ Daily hiện tại
# ---------------------------------------------------------------------------


async def get_daily_state() -> dict:
    return await _get_doc("daily", "current")


async def save_daily_state(data: dict) -> None:
    await _set_doc("daily", "current", data, merge=True)


# ---------------------------------------------------------------------------
# site: lượt xem + rating cho web dashboard
# ---------------------------------------------------------------------------


async def increment_views() -> int:
    if _use_memory_fallback:
        doc = _memory_store.setdefault("site/dashboard", {"views": 0, "ratings": {}})
        doc["views"] = doc.get("views", 0) + 1
        return doc["views"]

    from google.cloud import firestore as fs

    ref = _client.collection("site").document("dashboard")
    await ref.set({"views": fs.Increment(1)}, merge=True)
    snap = await ref.get()
    return (snap.to_dict() or {}).get("views", 0)


async def submit_rating(voter_id: str, stars: int) -> None:
    stars = max(1, min(5, int(stars)))
    if _use_memory_fallback:
        doc = _memory_store.setdefault("site/dashboard", {"views": 0, "ratings": {}})
        doc.setdefault("ratings", {})[voter_id] = stars
        return
    ref = _client.collection("site").document("dashboard")
    await ref.set({"ratings": {voter_id: stars}}, merge=True)


# ---------------------------------------------------------------------------
# businesses: minigame kinh doanh (quán/công ty/nhà trọ/khách sạn)
# ---------------------------------------------------------------------------


async def get_business(user_id: int, kind: str) -> dict:
    return await _get_doc("businesses", f"{user_id}_{kind}")


async def save_business(user_id: int, kind: str, data: dict) -> None:
    await _set_doc("businesses", f"{user_id}_{kind}", data, merge=True)


async def get_all_businesses() -> list[tuple[str, dict]]:
    if _use_memory_fallback:
        out = []
        for key, val in _memory_store.items():
            if key.startswith("businesses/"):
                out.append((key.split("/", 1)[1], dict(val)))
        return out
    docs = [d async for d in _client.collection("businesses").stream()]
    return [(d.id, d.to_dict() or {}) for d in docs]


# ---------------------------------------------------------------------------
# ai_words: từ điển bot tự học được trong server (nghĩa tra trên mạng)
# ---------------------------------------------------------------------------


async def get_word(word: str) -> dict:
    return await _get_doc("ai_words", word.lower())


async def save_word(word: str, data: dict) -> None:
    await _set_doc("ai_words", word.lower(), data, merge=True)


async def get_site_stats() -> dict:
    """Trả về {views, rating_count, rating_avg}."""
    if _use_memory_fallback:
        doc = _memory_store.get("site/dashboard", {"views": 0, "ratings": {}})
    else:
        snap = await _client.collection("site").document("dashboard").get()
        doc = snap.to_dict() or {} if snap.exists else {}

    ratings = doc.get("ratings", {}) or {}
    count = len(ratings)
    avg = round(sum(ratings.values()) / count, 2) if count else 0.0
    return {"views": doc.get("views", 0), "rating_count": count, "rating_avg": avg}
