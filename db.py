"""
Lớp lưu trữ duy nhất của bot: Firebase Realtime Database (RTDB), qua REST API.

Gộp toàn bộ thao tác đọc/ghi dữ liệu bền vững ở đây:
- bot_state    : last_video_id, was_live, last_identity_avatar_url (thay cho data.json cũ)
- videos       : trạng thái "đã thông báo" (notified) của từng video TikTok
- users        : MICK, XP, level, ngày nhận Daily gần nhất
- daily        : mốc thời gian bắt đầu chu kỳ Daily hiện tại (để tính giảm dần theo giờ)
- site         : lượt xem + rating (sao) của web dashboard
- businesses   : minigame kinh doanh
- ai_words     : từ điển bot tự học được trong server

Vì sao RTDB thay vì Firestore: Firestore Spark (free) giới hạn CỨNG 50k đọc +
20k ghi/ngày -> server chat đông rất dễ bị 429 RESOURCE_EXHAUSTED. RTDB Spark
không tính theo số lượt đọc/ghi mà theo băng thông (10GB/tháng) + dung lượng
lưu (1GB) + 100 kết nối đồng thời - phù hợp hơn nhiều với kiểu ghi nhỏ, dồn
dập (XP mỗi tin nhắn, đếm từ học...) mà bot này đang làm.

Nếu chưa cấu hình đủ (thiếu FIREBASE_DATABASE_URL hoặc credentials), bot vẫn
chạy được nhờ một lớp fallback lưu tạm trong RAM, để không bị crash khi test
ở máy local.
"""

import asyncio
import json
import os
import time as _time_lib
import uuid as _uuid_lib

import aiohttp

from config import FIREBASE_CREDENTIALS_JSON, FIREBASE_DATABASE_URL, log

_use_memory_fallback = False
_memory_store: dict = {}

_credentials = None
_session: aiohttp.ClientSession | None = None
_access_token: str | None = None
_token_expiry: float = 0.0
_token_lock: asyncio.Lock | None = None

# RTDB không cho phép các ký tự này trong key: . # $ [ ] /
_FORBIDDEN_KEY_CHARS = str.maketrans({c: "_" for c in ".#$[]/"})


def _safe_key(key) -> str:
    return str(key).translate(_FORBIDDEN_KEY_CHARS)


# ---------------------------------------------------------------------------
# Chống spam log + crash khi bị Firebase giới hạn tốc độ (429) hoặc lỗi mạng
# tạm thời: thay vì để exception bay lên làm vỡ task nền (xem log Render), ta
# log CẢNH BÁO 1 LẦN MỖI 5 PHÚT rồi trả về rỗng/bỏ qua ghi, để bot vẫn chạy
# tiếp (chỉ tạm mất vài lượt ghi, không sập).
# ---------------------------------------------------------------------------
_last_warn_ts = 0.0
_WARN_INTERVAL_SEC = 300


def _warn_throttled(action: str, detail: str = "") -> None:
    global _last_warn_ts
    now = _time_lib.time()
    if now - _last_warn_ts >= _WARN_INTERVAL_SEC:
        _last_warn_ts = now
        log.warning(
            "Firebase bị giới hạn tốc độ khi %s (%s) - bot vẫn chạy tiếp, dữ liệu "
            "tạm thời bỏ qua thao tác này. Cảnh báo này bị giới hạn 1 lần/5 phút.",
            action, detail,
        )


def _init_client():
    """Khởi tạo credentials service account, ưu tiên lấy từ env var."""
    global _credentials, _use_memory_fallback, _token_lock

    _token_lock = asyncio.Lock()

    if not FIREBASE_DATABASE_URL:
        log.warning(
            "Chưa cấu hình FIREBASE_DATABASE_URL -> dùng bộ nhớ tạm (RAM, mất khi restart)."
        )
        _use_memory_fallback = True
        return

    try:
        from google.oauth2 import service_account

        scopes = [
            "https://www.googleapis.com/auth/firebase.database",
            "https://www.googleapis.com/auth/userinfo.email",
        ]
        if FIREBASE_CREDENTIALS_JSON:
            info = json.loads(FIREBASE_CREDENTIALS_JSON)
            _credentials = service_account.Credentials.from_service_account_info(info, scopes=scopes)
        else:
            cred_path = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
            _credentials = service_account.Credentials.from_service_account_file(cred_path, scopes=scopes)
        log.info("Firebase Realtime Database đã sẵn sàng.")
    except Exception as e:
        log.warning(
            "Không khởi tạo được Firebase Realtime Database (%s) -> dùng bộ nhớ tạm (RAM, mất khi restart).",
            e,
        )
        _credentials = None
        _use_memory_fallback = True


_init_client()


# ---------------------------------------------------------------------------
# Access token (OAuth2 service account) - refresh đồng bộ chạy trong thread
# riêng (asyncio.to_thread) để không chặn event loop, cache tới gần hết hạn.
# ---------------------------------------------------------------------------


def _refresh_token_sync() -> tuple[str, float]:
    from google.auth.transport.requests import Request

    _credentials.refresh(Request())
    expiry = _credentials.expiry.timestamp() if _credentials.expiry else (_time_lib.time() + 3000)
    return _credentials.token, expiry


async def _get_access_token() -> str | None:
    global _access_token, _token_expiry
    if _credentials is None:
        return None
    async with _token_lock:
        if _access_token and _time_lib.time() < _token_expiry - 60:
            return _access_token
        try:
            token, expiry = await asyncio.to_thread(_refresh_token_sync)
        except Exception as e:
            log.warning("Không lấy được access token Firebase: %s", e)
            return None
        _access_token = token
        _token_expiry = expiry
        return _access_token


async def warmup() -> None:
    """Lấy trước access token Firebase ngay khi bot khởi động, chạy nền
    (không chặn web server/Discord client). Tránh việc REQUEST ĐẦU TIÊN (vd.
    Render health-check hoặc người dùng mở trang web ngay lúc vừa deploy) phải
    tự chờ/dễ lỗi vì lượt gọi mạng đổi service-account key lấy token OAuth2
    đầu tiên (thường mất 0.5-2s) chưa kịp xong."""
    if _use_memory_fallback or _credentials is None:
        return
    try:
        await _get_access_token()
    except Exception as e:
        log.warning("Làm nóng access token Firebase lúc khởi động lỗi (sẽ tự thử lại ở request đầu): %s", e)


async def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


_TIMEOUT = aiohttp.ClientTimeout(total=15)


async def _rtdb_request(method: str, path: str, params: dict | None = None, json_body=None):
    """Gửi 1 request REST tới Firebase RTDB. Trả về (data, ok) - ok=False khi
    bị giới hạn tốc độ (429), caller nên coi như "bỏ qua đợt này, thử lại sau"
    giống cơ chế hết quota Firestore cũ."""
    token = await _get_access_token()
    if token is None:
        raise RuntimeError("Firebase chưa sẵn sàng (không lấy được access token).")

    url = f"{FIREBASE_DATABASE_URL}/{path}.json"
    query = dict(params or {})
    query["access_token"] = token

    session = await _get_session()
    async with session.request(method, url, params=query, json=json_body, timeout=_TIMEOUT) as resp:
        text = await resp.text()
        if resp.status == 429:
            _warn_throttled(f"{method} {path}")
            return None, False
        if resp.status >= 400:
            raise RuntimeError(f"Firebase lỗi {resp.status} tại {path}: {text[:300]}")
        data = json.loads(text) if text else None
        return data, True


# ---------------------------------------------------------------------------
# Helper nội bộ: fallback RAM khi không có Firebase (chỉ để dev/test local)
# ---------------------------------------------------------------------------


async def _get_doc(collection: str, doc_id: str) -> dict:
    if _use_memory_fallback:
        return dict(_memory_store.get(f"{collection}/{doc_id}", {}))
    try:
        data, ok = await _rtdb_request("GET", f"{collection}/{_safe_key(doc_id)}")
        if not ok:
            return {}
        return data or {}
    except Exception as e:
        _warn_throttled(f"đọc {collection}/{doc_id}", str(e))
        return {}


async def _set_doc(collection: str, doc_id: str, data: dict, merge: bool = True) -> bool:
    """Trả về True nếu ghi thành công (kể cả fallback RAM), False nếu bị bỏ
    qua do lỗi/giới hạn tốc độ - để CALLER (vd. unlock thành tựu) biết mà
    KHÔNG báo thành công giả khi dữ liệu thực ra chưa lưu được."""
    if _use_memory_fallback:
        key = f"{collection}/{doc_id}"
        if merge:
            _memory_store.setdefault(key, {}).update(data)
        else:
            _memory_store[key] = dict(data)
        return True
    try:
        # PATCH = merge nông (chỉ ghi đè các key top-level được truyền vào,
        # giữ nguyên các key khác) - tương đương set(merge=True) của Firestore
        # với các document dạng phẳng mà bot này dùng.
        method = "PATCH" if merge else "PUT"
        _, ok = await _rtdb_request(method, f"{collection}/{_safe_key(doc_id)}", json_body=data)
        return ok
    except Exception as e:
        _warn_throttled(f"ghi {collection}/{doc_id}", str(e))
        return False


# ---------------------------------------------------------------------------
# Đọc/ghi có kiểm tra ETag (transaction thủ công) - dùng cho Increment (lượt
# xem, tần suất từ học) để tránh mất dữ liệu khi 2 request ghi đồng thời.
# ---------------------------------------------------------------------------


async def _read_with_etag(path: str) -> tuple[object, str | None]:
    token = await _get_access_token()
    if token is None:
        raise RuntimeError("Firebase chưa sẵn sàng (không lấy được access token).")
    url = f"{FIREBASE_DATABASE_URL}/{path}.json"
    session = await _get_session()
    headers = {"X-Firebase-ETag": "true"}
    async with session.get(url, params={"access_token": token}, headers=headers, timeout=_TIMEOUT) as resp:
        text = await resp.text()
        if resp.status >= 400:
            raise RuntimeError(f"Firebase lỗi {resp.status} tại {path}: {text[:300]}")
        etag = resp.headers.get("ETag")
        data = json.loads(text) if text else None
        return data, etag


async def _write_with_etag(path: str, value, etag: str) -> bool:
    """Trả về True nếu ghi thành công, False nếu bị tranh chấp (412 - dữ liệu
    đã đổi kể từ lúc đọc, caller nên đọc lại và thử lại)."""
    token = await _get_access_token()
    url = f"{FIREBASE_DATABASE_URL}/{path}.json"
    session = await _get_session()
    headers = {"if-match": etag}
    async with session.put(url, params={"access_token": token}, json=value, headers=headers, timeout=_TIMEOUT) as resp:
        if resp.status == 412:
            return False
        text = await resp.text()
        if resp.status >= 400:
            raise RuntimeError(f"Firebase lỗi {resp.status} tại {path}: {text[:300]}")
        return True


async def _atomic_update(path: str, fn, retries: int = 5):
    """Đọc-sửa-ghi có kiểm tra ETag (giống transaction) tại 1 path cụ thể.
    fn nhận giá trị hiện tại (None nếu chưa có) và trả về giá trị mới."""
    for _ in range(retries):
        current, etag = await _read_with_etag(path)
        new_value = fn(current)
        if etag is None:
            # node chưa tồn tại -> ghi thẳng, không cần điều kiện
            await _rtdb_request("PUT", path, json_body=new_value)
            return new_value
        if await _write_with_etag(path, new_value, etag):
            return new_value
        await asyncio.sleep(0.05)
    raise RuntimeError(f"Không ghi được {path} sau {retries} lần thử (tranh chấp ETag).")


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
    """Trả về danh sách video_id chưa được đánh dấu notified=True (để bot gọi lại/ping lại).

    LƯU Ý: cần thêm rule index cho nhanh (không bắt buộc, RTDB vẫn chạy đúng
    nếu thiếu, chỉ chậm hơn khi node "videos" lớn):
        {"rules": {"videos": {".indexOn": ["notified"]}}}
    """
    if _use_memory_fallback:
        return [
            key.split("/", 1)[1]
            for key, val in _memory_store.items()
            if key.startswith("videos/") and not val.get("notified")
        ][:limit]
    try:
        params = {"orderBy": json.dumps("notified"), "equalTo": json.dumps(False), "limitToFirst": limit}
        data, ok = await _rtdb_request("GET", "videos", params=params)
        if not ok or not data:
            return []
        return list(data.keys())[:limit]
    except Exception as e:
        _warn_throttled("đọc videos chưa thông báo", str(e))
        return []


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

    Có cache RAM 60s (use_cache=True) để tránh đọc lại toàn bộ node "users"
    mỗi lần user gọi `/hồ-sơ` hoặc `/bảng-xếp-hạng` (đỡ tốn băng thông + nhanh
    hơn). Khi save_user() được gọi, cache sẽ tự invalidate.
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
        try:
            data, ok = await _rtdb_request("GET", "users")
            out = list((data or {}).items()) if ok else []
        except Exception as e:
            _warn_throttled("đọc toàn bộ users", str(e))
            out = _all_users_cache or []

    _all_users_cache = out
    _all_users_cache_ts = _time.time()
    return out


async def save_user(user_id: int, data: dict) -> bool:
    """Trả về True nếu lưu thành công, False nếu bị bỏ qua do lỗi/giới hạn
    tốc độ (caller nên kiểm tra giá trị này trước khi coi 1 thay đổi - vd. mở
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
    try:
        return await _atomic_update("site/dashboard/views", lambda v: (v or 0) + 1)
    except Exception as e:
        _warn_throttled("tăng lượt xem", str(e))
        return 0


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
        # PATCH ngay tại node "ratings" (không phải "dashboard") để chỉ ghi
        # đè đúng key voter_id, giữ nguyên rating của những người khác.
        try:
            await _rtdb_request("PATCH", "site/dashboard/ratings", json_body={_safe_key(voter_id): entry})
        except Exception as e:
            _warn_throttled("lưu đánh giá", str(e))
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
    try:
        data, ok = await _rtdb_request("GET", "businesses")
        return list((data or {}).items()) if ok else []
    except Exception as e:
        _warn_throttled("đọc toàn bộ businesses", str(e))
        return []


# ---------------------------------------------------------------------------
# ai_words: từ điển bot tự học được trong server (nghĩa do member dạy)
# ---------------------------------------------------------------------------


async def get_word(word: str) -> dict:
    return await _get_doc("ai_words", word.lower())


async def save_word(word: str, data: dict) -> None:
    global _learned_words_cache
    await _set_doc("ai_words", word.lower(), data, merge=True)
    _learned_words_cache = None


async def bump_word_counts(counts: dict[str, int], last_seen: dict[str, int]) -> None:
    """Tăng tần suất cho NHIỀU từ cùng lúc. Mỗi từ 1 phép Increment nguyên tử
    (đọc-sửa-ghi có ETag) chạy song song (asyncio.gather) - thay cho
    get_word()+save_word() gọi riêng từng từ mỗi tin nhắn, vốn tốn quota/băng
    thông rất nhanh khi chat đông người. Được gọi định kỳ (vài phút/lần) từ
    RAM đệm, không gọi trực tiếp mỗi tin nhắn.
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

    async def _bump_one(word: str, delta: int):
        key = _safe_key(word)
        try:
            await _atomic_update(f"ai_words/{key}/count", lambda v: (v or 0) + delta)
            await _rtdb_request("PATCH", f"ai_words/{key}", json_body={"last_seen": last_seen.get(word, 0)})
        except Exception as e:
            _warn_throttled(f"cập nhật từ học '{word}'", str(e))

    await asyncio.gather(*(_bump_one(w, d) for w, d in counts.items()))
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
        try:
            data, ok = await _rtdb_request("GET", "ai_words")
            out = list((data or {}).items()) if ok else (_learned_words_cache or [])
        except Exception as e:
            _warn_throttled("đọc toàn bộ ai_words", str(e))
            out = _learned_words_cache or []

    _learned_words_cache = out
    _learned_words_cache_ts = _time.time()
    return out


_site_stats_cache: dict | None = None
_site_stats_cache_ts: float = 0.0
_SITE_STATS_CACHE_TTL = 5  # giây - dashboard poll liên tục, cache để đỡ tốn CPU/băng thông


async def get_site_stats() -> dict:
    """Trả về {views, rating_count, rating_avg}. Có cache RAM ngắn (5s) vì
    dashboard web gọi API này định kỳ - tránh đọc Firebase mỗi request."""
    global _site_stats_cache, _site_stats_cache_ts
    import time as _time

    if _site_stats_cache is not None and (_time.time() - _site_stats_cache_ts) < _SITE_STATS_CACHE_TTL:
        return _site_stats_cache

    doc = await _get_doc("site", "dashboard")

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
    doc = await _get_doc("site", "dashboard")

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
    doc = await _get_doc("site", "dashboard")

    ratings = doc.get("ratings", {}) or {}
    reviews = [r for r in ratings.values() if isinstance(r, dict) and r.get("name") and r.get("comment")]
    reviews.sort(key=lambda r: r.get("ts", 0), reverse=True)
    return reviews[:limit]
