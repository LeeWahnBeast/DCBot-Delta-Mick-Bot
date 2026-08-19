"""
Lấy dữ liệu public từ trang TikTok của 1 user: avatar, trạng thái đang live
hay không.

(Tính năng lấy VIDEO MỚI NHẤT qua yt-dlp đã bị XÓA - yt-dlp liên tục lỗi
"Unable to extract secondary user ID" do TikTok đổi cấu trúc API nội bộ, bug
đã báo cáo trên GitHub yt-dlp (issue #14876, #10160) chưa có bản vá. Mọi
nguồn thay thế để lấy list video (gọi thẳng api/post/item_list/, dùng
TikTok-Api...) đều cần chữ ký JS-VM (X-Bogus/X-Gnarly) sinh bởi Chromium thật
hoặc dịch vụ trả phí (CreatorCrawl, EnsembleData...) - không khả thi trên
Render free tier. Quyết định: bỏ hẳn tính năng báo video, chỉ giữ live.)

Dùng TikTokLive (isaackogan/TikTokLive) để check đang live + lấy avatar -
thư viện async native chuyên biệt cho TikTok LIVE, chỉ cần username, không
cần đăng nhập/cookie, không cần Playwright/Selenium/Chromium nên vẫn nhẹ,
chạy tốt trên Render free tier.
"""

import codecs

import aiohttp
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
      }
    hoặc None nếu KHÔNG lấy được gì cả (cả avatar lẫn trạng thái live đều lỗi).

    Tham số `session` giữ lại để không phải sửa nơi gọi (TikTokClient bên
    dưới) - TikTokLive tự quản lý HTTP session riêng, không dùng session này
    nữa.
    """
    is_live, avatar_url = await _check_live_and_avatar(username)

    if not is_live and avatar_url is None:
        log.warning("Không lấy được dữ liệu TikTok @%s (TikTokLive lỗi).", username)
        return None

    return {
        "username": username,
        "nickname": username,
        "avatar_url": avatar_url,
        "is_live": is_live,
        "room_id": None,
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
    còn dùng session này nữa (TikTokLive tự quản lý riêng) nhưng vẫn giữ
    session ở đây để download_bytes() hoạt động."""

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
