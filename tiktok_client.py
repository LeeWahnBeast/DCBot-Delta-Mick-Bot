"""
Lấy dữ liệu public từ trang TikTok của 1 user: avatar, video mới nhất,
trạng thái đang live hay không.

TRƯỚC ĐÂY file này tự viết regex bóc JSON nhúng trong HTML của trang profile
- cách này RẤT DỄ VỠ vì TikTok đổi cấu trúc trang thường xuyên, mỗi lần đổi
là bot im re không báo gì (đây chính là lý do video không được thông báo).

BÂY GIỜ dùng 2 THƯ VIỆN NGOÀI được cộng đồng bảo trì liên tục thay vì tự viết
tay, để không phải tự sửa code mỗi khi TikTok đổi cấu trúc:

  - yt-dlp   : lấy video MỚI NHẤT (id, thời gian đăng). yt-dlp có hẳn 1
    extractor riêng cho TikTok, gọi thẳng API nội bộ
    "/api/creator/item_list/" của TikTok (không phải regex HTML), được
    hàng nghìn contributor bảo trì, gần như tuần nào cũng có bản vá khi
    TikTok đổi cấu trúc -> đáng tin hơn NHIỀU so với 1 file regex tự viết.

  - TikTokLive: check trạng thái ĐANG LIVE + lấy avatar. Thư viện async
    native chuyên biệt cho TikTok LIVE (isaackogan/TikTokLive), chỉ cần
    username, không cần đăng nhập/cookie.

Cả 2 đều chạy qua HTTP thường (KHÔNG cần Playwright/Selenium/Chromium) nên
vẫn nhẹ, chạy tốt trên Render free tier.
"""

import asyncio
import codecs

import aiohttp
import yt_dlp
from TikTokLive import TikTokLiveClient

from config import UA, log


def _clean_avatar_url(url: str | None) -> str | None:
    """TikTokLive đôi khi trả URL còn nguyên dạng JSON-escaped lấy từ
    protobuf (vd. "https:\\/\\/p16-...") thay vì URL thật. Giải escape unicode
    (\\uXXXX -> ký tự thật) rồi kiểm tra lại URL có hợp lệ (bắt đầu bằng
    http:// hoặc https://) trước khi dùng, để không lưu/tải rác vào bot_state
    hay ghép domain sai như "https:///...".
    """
    if not url:
        return None
    if "\\u" in url:
        try:
            url = codecs.decode(url, "unicode_escape")
        except Exception:
            pass
    url = url.replace("\\/", "/")
    if not url.startswith(("http://", "https://")):
        log.warning("avatar_url không hợp lệ sau khi làm sạch, bỏ qua: %s", url[:120])
        return None
    return url

_YDL_COMMON_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "socket_timeout": 20,
}


def _extract_latest_video_sync(username: str) -> dict | None:
    """CHẠY ĐỒNG BỘ (blocking) - gọi trong executor thread riêng ở
    fetch_tiktok_profile() bên dưới, vì yt-dlp không phải thư viện async.

    Trả về {"video_id": str, "create_time": int|None, "nickname": str} của
    video mới nhất, hoặc None nếu không lấy được (lỗi mạng/user không tồn
    tại/TikTok chặn...).
    """
    profile_url = f"https://www.tiktok.com/@{username}"

    # Bước 1: lấy DANH SÁCH video (extract_flat = nhanh, không tải chi tiết
    # từng video) - chỉ cần video đầu tiên (mới nhất, TikTok trả về theo thứ
    # tự mới nhất trước).
    flat_opts = {**_YDL_COMMON_OPTS, "extract_flat": "in_playlist", "playlistend": 1}
    try:
        with yt_dlp.YoutubeDL(flat_opts) as ydl:
            flat_info = ydl.extract_info(profile_url, download=False)
    except Exception as e:
        log.warning("yt-dlp lấy danh sách video @%s lỗi: %s", username, e)
        return None

    entries = (flat_info or {}).get("entries") or []
    if not entries:
        return None

    latest = entries[0]
    video_id = latest.get("id")
    video_url = latest.get("url") or f"{profile_url}/video/{video_id}"
    if not video_id:
        return None

    # Bước 2: lấy metadata ĐẦY ĐỦ (có timestamp đăng chính xác) nhưng CHỈ
    # cho đúng 1 video mới nhất này - không extract_flat=False cho cả danh
    # sách vì sẽ tải chi tiết TOÀN BỘ video, chậm và tốn request vô ích.
    create_time = None
    nickname = username
    try:
        with yt_dlp.YoutubeDL(_YDL_COMMON_OPTS) as ydl:
            full_info = ydl.extract_info(video_url, download=False)
        create_time = full_info.get("timestamp")
        nickname = full_info.get("uploader") or full_info.get("channel") or username
    except Exception as e:
        # Vẫn có video_id rồi (quan trọng nhất để phát hiện "có video mới"),
        # chỉ thiếu timestamp/nickname chính xác -> không coi là thất bại.
        log.warning("yt-dlp lấy chi tiết video %s lỗi (vẫn dùng id đã có): %s", video_id, e)

    return {
        "video_id": str(video_id),
        "create_time": int(create_time) if create_time else None,
        "nickname": nickname,
    }


async def _check_live_and_avatar(username: str) -> tuple[bool, str | None]:
    """Dùng TikTokLive để check đang live + lấy avatar - thư viện async
    native, không cần tải HTML/regex tay. Trả về (is_live, avatar_url)."""
    is_live = False
    avatar_url = None
    live_client = None
    try:
        live_client = TikTokLiveClient(unique_id=f"@{username}")
        is_live = await live_client.is_live()
        avatar_url = _clean_avatar_url(await live_client.get_avatar_url())
    except Exception as e:
        log.warning("TikTokLive check @%s lỗi: %s", username, e)
    finally:
        # Đóng session HTTP nội bộ của client nếu có, tránh rò rỉ connection
        # (mỗi lần gọi hàm này tạo 1 client mới - xem check_tiktok_loop).
        web = getattr(live_client, "web", None)
        close = getattr(web, "close", None)
        if callable(close):
            try:
                await close()
            except Exception:
                pass
    return is_live, avatar_url


async def fetch_tiktok_profile(session: aiohttp.ClientSession, username: str) -> dict | None:
    """
    Trả về dict:
      {
        "username": str,
        "nickname": str,
        "avatar_url": str | None,
        "is_live": bool,
        "room_id": None,  # không dùng nữa, giữ lại field cho tương thích ngược
        "latest_video_id": str | None,
        "latest_video_create_time": int | None,
      }
    hoặc None nếu KHÔNG lấy được gì cả (cả video lẫn live/avatar đều lỗi).

    Tham số `session` giữ lại để không phải sửa nơi gọi (TikTokClient bên
    dưới) - yt-dlp và TikTokLive tự quản lý HTTP session riêng, không dùng
    session này nữa.
    """
    loop = asyncio.get_running_loop()
    video_info = await loop.run_in_executor(None, _extract_latest_video_sync, username)
    is_live, avatar_url = await _check_live_and_avatar(username)

    if video_info is None and not is_live and avatar_url is None:
        log.warning("Không lấy được dữ liệu TikTok @%s (cả yt-dlp lẫn TikTokLive đều lỗi).", username)
        return None

    return {
        "username": username,
        "nickname": (video_info or {}).get("nickname") or username,
        "avatar_url": avatar_url,
        "is_live": is_live,
        "room_id": None,
        "latest_video_id": (video_info or {}).get("video_id"),
        "latest_video_create_time": (video_info or {}).get("create_time"),
    }


async def download_bytes(session: aiohttp.ClientSession, url: str) -> bytes | None:
    try:
        async with session.get(url, headers={"User-Agent": UA}, timeout=20) as resp:
            if resp.status == 200:
                return await resp.read()
    except Exception as e:
        log.warning("Lỗi tải ảnh: %s", e)
    return None


class TikTokClient:
    """Wrapper giữ 1 aiohttp.ClientSession dùng chung (cho download_bytes -
    tải ảnh avatar), tái sử dụng cho các lần gọi sau. fetch_profile() không
    còn dùng session này nữa (yt-dlp/TikTokLive tự quản lý riêng) nhưng vẫn
    giữ session ở đây để download_bytes() hoạt động."""

    def __init__(self):
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def fetch_profile(self, username: str) -> dict | None:
        session = await self._get_session()
        return await fetch_tiktok_profile(session, username)

    async def download_bytes(self, url: str) -> bytes | None:
        session = await self._get_session()
        return await download_bytes(session, url)

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
