"""
Lấy dữ liệu public từ trang TikTok của 1 user: avatar, video mới nhất,
trạng thái đang live hay không.

TikTok không có API chính thức cho việc này -> bóc tách JSON nhúng sẵn
trong HTML của trang profile. Nếu TikTok đổi cấu trúc trang, phần
fetch_tiktok_profile() bên dưới là nơi cần sửa lại đầu tiên.
"""

import re
import json

import aiohttp

from config import UA, log


async def fetch_tiktok_profile(session: aiohttp.ClientSession, username: str) -> dict | None:
    """
    Trả về dict:
      {
        "username": str,
        "nickname": str,
        "avatar_url": str | None,
        "is_live": bool,
        "room_id": str | None,
        "latest_video_id": str | None,
      }
    hoặc None nếu không lấy được dữ liệu.
    """
    url = f"https://www.tiktok.com/@{username}"
    headers = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
    try:
        async with session.get(url, headers=headers, timeout=20) as resp:
            if resp.status != 200:
                log.warning("Fetch profile %s: HTTP %s", username, resp.status)
                return None
            html = await resp.text()
    except Exception as e:
        log.warning("Lỗi khi fetch profile TikTok: %s", e)
        return None

    user_info, videos = _parse_html(html, username)

    if not user_info:
        log.warning(
            "Không parse được dữ liệu user cho @%s (TikTok có thể đã đổi cấu trúc trang).",
            username,
        )
        return None

    user = user_info.get("user", {})
    avatar_url = user.get("avatarLarger") or user.get("avatarMedium") or user.get("avatarThumb")
    room_id = user.get("roomId") or user_info.get("liveRoom", {}).get("roomId") or None
    is_live_flag = bool(room_id) and str(room_id) != "0"

    latest_video = None
    if videos:
        videos.sort(key=lambda v: int(v.get("createTime") or 0), reverse=True)
        latest_video = videos[0]

    return {
        "username": username,
        "nickname": user.get("nickname") or username,
        "avatar_url": avatar_url,
        "is_live": is_live_flag,
        "room_id": room_id,
        "latest_video_id": latest_video["id"] if latest_video else None,
        "latest_video_create_time": int(latest_video["createTime"]) if latest_video and latest_video.get("createTime") else None,
    }


def _parse_html(html: str, username: str):
    """Trả về (user_info: dict|None, videos: list)."""
    data_json = None

    # Cách 1: __UNIVERSAL_DATA_FOR_REHYDRATION__ (giao diện TikTok mới)
    m = re.search(
        r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    )
    if m:
        try:
            data_json = json.loads(m.group(1))
        except Exception:
            data_json = None

    user_info = None
    videos = []

    if data_json:
        try:
            scope = data_json["__DEFAULT_SCOPE__"]
            user_detail = scope.get("webapp.user-detail", {})
            user_info = user_detail.get("userInfo", {})
        except Exception:
            user_info = None

        if user_info:
            try:
                item_list = data_json["__DEFAULT_SCOPE__"]["webapp.user-detail"]["itemList"]
                for item in item_list:
                    videos.append({"id": item.get("id"), "createTime": item.get("createTime")})
            except Exception:
                pass

    # Cách 2 (dự phòng): SIGI_STATE (giao diện cũ hơn)
    if not user_info:
        m2 = re.search(r'<script id="SIGI_STATE"[^>]*>(.*?)</script>', html, re.DOTALL)
        if m2:
            try:
                sigi = json.loads(m2.group(1))
                user_module = sigi.get("UserModule", {})
                users = user_module.get("users", {})
                if users:
                    u = next(iter(users.values()))
                    user_info = {"user": u, "stats": user_module.get("stats", {}).get(username, {})}
                item_module = sigi.get("ItemModule", {})
                for item in item_module.values():
                    videos.append({"id": item.get("id"), "createTime": item.get("createTime")})
            except Exception:
                pass

    return user_info, videos


async def download_bytes(session: aiohttp.ClientSession, url: str) -> bytes | None:
    try:
        async with session.get(url, headers={"User-Agent": UA}, timeout=20) as resp:
            if resp.status == 200:
                return await resp.read()
    except Exception as e:
        log.warning("Lỗi tải ảnh: %s", e)
    return None


class TikTokClient:
    """Wrapper giữ 1 aiohttp.ClientSession dùng chung, gọi lại 2 hàm ở trên.
    Tự tạo session lười (lazy) ở lần gọi đầu tiên, tái sử dụng cho các lần sau."""

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
