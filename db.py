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
import time as _time_lib
import uuid as _uuid_lib

from config import FIREBASE_CREDENTIALS_JSON, FIRESTORE_PROJECT_ID, log

_client = None
_use_memory_fallback = False
_memory_store: dict = {}

# ---------------------------------------------------------------------------
# Chống spam log + crash khi Firestore hết quota (free tier: 50k đọc/20k ghi
# mỗi ngày). Khi bị 429 RESOURCE_EXHAUSTED, thay vì để exception bay lên làm
# vỡ task nền (xem log Render), ta log CẢNH BÁO 1 LẦN MỖI 5 PHÚT rồi trả về
# rỗng/ bỏ qua ghi, để bot vẫn chạy tiếp (chỉ tạm mất vài lượt ghi, không sập).
# ---------------------------------------------------------------------------
_last_quota_warn_ts = 0.0
_QUOTA_WARN_INTERVAL_SEC = 300


def _is_quota_error(e: Exception) -> bool:
    try:
        from google.api_core.exceptions import ResourceExhausted

        if isinstance(e, ResourceExhausted):
            return True
    except ImportError:
        pass
    return "RESOURCE_EXHAUSTED" in str(e) or "Quota exceeded" in str(e)


def _warn_quota_throttled(action: str, e: Exception) -> None:
    global _last_quota_warn_ts
    now = _time_lib.time()
    if now - _last_quota_warn_ts >= _QUOTA_WARN_INTERVAL_SEC:
        _last_quota_warn_ts = now
        log.warning(
            "Firestore hết quota khi %s (%s) - bot vẫn chạy tiếp, dữ liệu tạm "
            "thời bỏ qua thao tác này. Cảnh báo này bị giới hạn 1 lần/5 phút.",
            action, e,
        )


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
    try:
        snap = await _client.collection(collection).document(doc_id).get()
        return snap.to_dict() or {} if snap.exists else {}
    except Exception as e:
        if _is_quota_error(e):
            _warn_quota_throttled(f"đọc {collection}/{doc_id}", e)
            return {}
        raise


async def _set_doc(collection: str, doc_id: str, data: dict, merge: bool = True) -> bool:
    """Trả về True nếu ghi thành công (kể cả fallback RAM), False nếu bị bỏ
    qua do Firestore hết quota - để CALLER (vd. unlock thành tựu) biết mà
    KHÔNG báo thành công giả khi dữ liệu thực ra chưa lưu được."""
    if _use_memory_fallback:
        key = f"{collection}/{doc_id}"
        if merge:
            _memory_store.setdefault(key, {}).update(data)
        else:
            _memory_store[key] = dict(data)
        return True
    try:
        await _client.collection(collection).document(doc_id).set(data, merge=merge)
        return True
    except Exception as e:
        if _is_quota_error(e):
            _warn_quota_throttled(f"ghi {collection}/{doc_id}", e)
            return False
        raise


# ---------------------------------------------------------------------------
# bot_state: thay thế data.json cũ (last_video_id, was_live, avatar...)
# ---------------------------------------------------------------------------


async def get_bot_state() -> dict:
    return await _get_doc("bot_state", "main")


async def save_bot_state(state: dict) -> bool:
    return await _set_doc("bot_state", "main", state, merge=True)


# ---------------------------------------------------------------------------
# videos: theo dõi video đã thông báo (notified) hay chưa, để retry nếu lỗi
# ---------------------------------------------------------------------------


async def get_video(video_id: str) -> dict:
    return await _get_doc("videos", video_id)


async def save_video(video_id: str, data: dict) -> bool:
    return await _set_doc("videos", video_id, data, merge=True)


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
    "uuid": "",  # UUID riêng, cấp 1 lần duy nhất khi user xuất hiện lần đầu
}


async def get_user(user_id: int) -> dict:
    data = await _get_doc("users", str(user_id))
    merged = dict(DEFAULT_USER)
    merged.update(data)

    if not merged.get("uuid"):
        # Cấp UUID riêng cho thành viên ngay lần đầu tra dữ liệu (lazy-init,
        # không cần script migrate riêng). Chỉ ghi 1 lần vì lần sau đã có uuid.
        new_uuid = str(_uuid_lib.uuid4())
        merged["uuid"] = new_uuid
        await save_user(user_id, {"uuid": new_uuid})

    return merged


_all_users_cache: list[tuple[str, dict]] | None = None
_all_users_cache_ts: float = 0.0
_ALL_USERS_CACHE_TTL = 60  # giây


async def get_all_users(use_cache: bool = True) -> list[tuple[str, dict]]:
    """Trả về [(user_id_str, data), ...] toàn bộ user - dùng cho bảng xếp hạng/business tick.

    Có cache RAM 60s (use_cache=True) để tránh đọc lại toàn bộ collection Firestore
    mỗi lần user gọi `/hồ-sơ` hoặc `/bảng-xếp-hạng` (đỡ tốn quota + nhanh hơn trên free tier).
    Khi save_user() được gọi, cache sẽ tự invalidate.
    """
    global _all_users_cache, _all_users_cache_ts
    import time as _time

    if use_cache and _all_users_cache is not None and (_time.time() - _all_users_cache_ts) < _ALL_USERS_CACHE_TTL:
        return _all_users_cache

    if _use_memory_fallback:
        out = []
        for key, val in _memory_store.items():
            if key.startswith("users/"):
                out.append((key.split("/", 1)[1], dict(val)))
    else:
        docs = [d async for d in _client.collection("users").stream()]
        out = [(d.id, d.to_dict() or {}) for d in docs]

    _all_users_cache = out
    _all_users_cache_ts = _time.time()
    return out


async def save_user(user_id: int, data: dict) -> bool:
    """Trả về True nếu lưu thành công, False nếu bị bỏ qua do Firestore hết
    quota (caller nên kiểm tra giá trị này trước khi coi 1 thay đổi - vd. mở
    khóa thành tựu - là đã chắc chắn lưu, tránh báo/công thông báo giả)."""
    global _all_users_cache
    ok = await _set_doc("users", str(user_id), data, merge=True)
    _all_users_cache = None
    return ok


# ---------------------------------------------------------------------------
# daily: mốc reset của chu kỳ Daily hiện tại
# ---------------------------------------------------------------------------


async def get_daily_state() -> dict:
    return await _get_doc("daily", "current")


async def save_daily_state(data: dict) -> bool:
    return await _set_doc("daily", "current", data, merge=True)


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


async def submit_rating(voter_id: str, stars: int, name: str, comment: str) -> None:
    """Lưu 1 lượt đánh giá: sao + tên + lý do (bắt buộc phải có đủ tên và lý do).
    Ghi đè lượt đánh giá cũ của cùng voter_id (tránh 1 người vote nhiều lần)."""
    global _site_stats_cache
    import time as _time

    stars = max(1, min(5, int(stars)))
    name = (name or "").strip()[:60]
    comment = (comment or "").strip()[:500]
    entry = {"stars": stars, "name": name, "comment": comment, "ts": _time.time()}

    if _use_memory_fallback:
        doc = _memory_store.setdefault("site/dashboard", {"views": 0, "ratings": {}})
        doc.setdefault("ratings", {})[voter_id] = entry
    else:
        ref = _client.collection("site").document("dashboard")
        await ref.set({"ratings": {voter_id: entry}}, merge=True)
    _site_stats_cache = None  # invalidate để user thấy điểm mới ngay sau khi vote


# ---------------------------------------------------------------------------
# businesses: minigame kinh doanh (quán/công ty/nhà trọ/khách sạn)
# ---------------------------------------------------------------------------


async def get_business(user_id: int, kind: str) -> dict:
    return await _get_doc("businesses", f"{user_id}_{kind}")


async def save_business(user_id: int, kind: str, data: dict) -> bool:
    return await _set_doc("businesses", f"{user_id}_{kind}", data, merge=True)


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
    global _learned_words_cache
    await _set_doc("ai_words", word.lower(), data, merge=True)
    _learned_words_cache = None


async def bump_word_counts(counts: dict[str, int], last_seen: dict[str, int]) -> None:
    """Tăng tần suất cho NHIỀU từ cùng lúc, gộp thành 1 lượt ghi (Firestore
    Increment - không cần đọc trước, và dùng WriteBatch - 1 lần gọi mạng cho
    cả batch). Dùng thay cho get_word()+save_word() gọi riêng từng từ, vốn tốn
    2 lượt quota Firestore (1 đọc + 1 ghi) cho MỖI từ lạ trong MỖI tin nhắn -
    đây chính là nguyên nhân hết quota free tier khi chat đông người.
    """
    global _learned_words_cache
    if not counts:
        return

    if _use_memory_fallback:
        for word, delta in counts.items():
            key = f"ai_words/{word}"
            doc = _memory_store.setdefault(key, {})
            doc["count"] = doc.get("count", 0) + delta
            doc["last_seen"] = last_seen.get(word, doc.get("last_seen", 0))
        _learned_words_cache = None
        return

    try:
        from google.cloud import firestore as fs

        batch = _client.batch()
        for word, delta in counts.items():
            ref = _client.collection("ai_words").document(word)
            batch.set(ref, {"count": fs.Increment(delta), "last_seen": last_seen.get(word, 0)}, merge=True)
        await batch.commit()
    except Exception as e:
        if _is_quota_error(e):
            _warn_quota_throttled(f"cập nhật {len(counts)} từ học", e)
            return
        raise
    _learned_words_cache = None


_learned_words_cache: list[tuple[str, dict]] | None = None
_learned_words_cache_ts: float = 0.0
_LEARNED_WORDS_CACHE_TTL = 120  # giây


async def get_learned_words(use_cache: bool = True) -> list[tuple[str, dict]]:
    """Trả về [(word, data), ...] toàn bộ từ đã học (có nghĩa hoặc không).
    Có cache RAM 120s vì hàm này được gọi mỗi lần bot trả lời chat (build context)."""
    global _learned_words_cache, _learned_words_cache_ts
    import time as _time

    if use_cache and _learned_words_cache is not None and (_time.time() - _learned_words_cache_ts) < _LEARNED_WORDS_CACHE_TTL:
        return _learned_words_cache

    if _use_memory_fallback:
        out = []
        for key, val in _memory_store.items():
            if key.startswith("ai_words/"):
                out.append((key.split("/", 1)[1], dict(val)))
    else:
        docs = [d async for d in _client.collection("ai_words").stream()]
        out = [(d.id, d.to_dict() or {}) for d in docs]

    _learned_words_cache = out
    _learned_words_cache_ts = _time.time()
    return out


_site_stats_cache: dict | None = None
_site_stats_cache_ts: float = 0.0
_SITE_STATS_CACHE_TTL = 5  # giây - dashboard poll liên tục, cache để đỡ tốn CPU/quota Firestore


async def get_site_stats() -> dict:
    """Trả về {views, rating_count, rating_avg}. Có cache RAM ngắn (5s) vì
    dashboard web gọi API này định kỳ - tránh đọc Firestore mỗi request."""
    global _site_stats_cache, _site_stats_cache_ts
    import time as _time

    if _site_stats_cache is not None and (_time.time() - _site_stats_cache_ts) < _SITE_STATS_CACHE_TTL:
        return _site_stats_cache

    if _use_memory_fallback:
        doc = _memory_store.get("site/dashboard", {"views": 0, "ratings": {}})
    else:
        snap = await _client.collection("site").document("dashboard").get()
        doc = snap.to_dict() or {} if snap.exists else {}

    ratings = doc.get("ratings", {}) or {}
    count = len(ratings)
    # ratings cũ (trước khi có tên/comment) lưu trực tiếp là int stars -> vẫn đọc được
    stars_values = [r["stars"] if isinstance(r, dict) else r for r in ratings.values()]
    avg = round(sum(stars_values) / count, 2) if count else 0.0
    result = {"views": doc.get("views", 0), "rating_count": count, "rating_avg": avg}

    _site_stats_cache = result
    _site_stats_cache_ts = _time.time()
    return result


async def get_rating_distribution() -> dict[int, int]:
    """Trả về {1: count, 2: count, ..., 5: count} — dùng vẽ biểu đồ cột kiểu
    Google Play. Tính trên TOÀN BỘ rating (kể cả review cũ chỉ có số sao)."""
    if _use_memory_fallback:
        doc = _memory_store.get("site/dashboard", {"views": 0, "ratings": {}})
    else:
        snap = await _client.collection("site").document("dashboard").get()
        doc = snap.to_dict() or {} if snap.exists else {}

    ratings = doc.get("ratings", {}) or {}
    dist = {i: 0 for i in range(1, 6)}
    for r in ratings.values():
        stars = r["stars"] if isinstance(r, dict) else r
        stars = max(1, min(5, int(stars)))
        dist[stars] += 1
    return dist


async def get_reviews(limit: int = 30) -> list[dict]:
    """Trả về danh sách review gần nhất (có tên/sao/comment), mới nhất trước.
    Review cũ (chỉ có số sao, chưa có tên/comment) bị bỏ qua vì không đủ dữ liệu hiển thị."""
    if _use_memory_fallback:
        doc = _memory_store.get("site/dashboard", {"views": 0, "ratings": {}})
    else:
        snap = await _client.collection("site").document("dashboard").get()
        doc = snap.to_dict() or {} if snap.exists else {}

    ratings = doc.get("ratings", {}) or {}
    reviews = [r for r in ratings.values() if isinstance(r, dict) and r.get("name") and r.get("comment")]
    reviews.sort(key=lambda r: r.get("ts", 0), reverse=True)
    return reviews[:limit]
