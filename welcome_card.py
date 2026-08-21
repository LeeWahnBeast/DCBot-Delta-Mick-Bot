"""
Render ảnh "welcome card" khi có thành viên mới join server - dùng ảnh nền
tĩnh (assets/welcome/welcome_bg.png, do admin tự thiết kế/đổi) và đè lên:

  - Số thứ tự thành viên ("#12345") - to, ngay dưới dòng "Con gà thứ:"
  - Tên hiển thị (display_name) - nhỏ hơn, ngay dưới số thứ tự
  - Avatar tròn của người vừa join - góc phải, xu

Bố cục toạ độ được đo/tinh chỉnh theo đúng khung nền 500x350 mà Mango cung
cấp (assets/welcome/welcome_bg.png) - nếu đổi nền khác kích thước khác thì
cần chỉnh lại các hằng số toạ độ bên dưới.

Font dùng chung DejaVu Sans bundle sẵn trong assets/fonts/ (giống
level_card.py) để đảm bảo hiển thị đúng tiếng Việt có dấu trên mọi môi
trường deploy (không phụ thuộc font hệ thống).
"""

from __future__ import annotations

import io
import os

from PIL import Image, ImageDraw, ImageFont, ImageOps
import aiohttp

_HERE = os.path.dirname(__file__)
_FONT_DIR = os.path.join(_HERE, "assets", "fonts")
_FONT_BOLD_PATH = os.path.join(_FONT_DIR, "DejaVuSans-Bold.ttf")
_FONT_REGULAR_PATH = os.path.join(_FONT_DIR, "DejaVuSans.ttf")

if not os.path.exists(_FONT_BOLD_PATH) or not os.path.exists(_FONT_REGULAR_PATH):
    raise FileNotFoundError(
        f"Thiếu font trong {_FONT_DIR} — cần DejaVuSans.ttf và DejaVuSans-Bold.ttf "
        "để vẽ được tiếng Việt có dấu trên welcome card."
    )

_BG_PATH = os.path.join(_HERE, "assets", "welcome", "welcome_bg.png")
if not os.path.exists(_BG_PATH):
    raise FileNotFoundError(
        f"Thiếu ảnh nền welcome card tại {_BG_PATH} — bỏ file nền (500x350) vào đây."
    )

_W, _H = 500, 350
_WHITE = (255, 255, 255)
_TEAL = (78, 178, 194)  # màu gần với "Con gà thứ:" trên nền mẫu

# Avatar: tròn, đặt góc phải giữa card (dưới header, trên dòng welcome)
_AVATAR_SIZE = 120
_AVATAR_CENTER = (390, 175)


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: str,
    max_width: int,
    start_size: int,
    min_size: int = 18,
) -> ImageFont.FreeTypeFont:
    size = start_size
    while size > min_size:
        font = _font(font_path, size)
        bbox = draw.textbbox((0, 0), text, font=font)
        if bbox[2] - bbox[0] <= max_width:
            return font
        size -= 2
    return _font(font_path, min_size)


async def _download_avatar_bytes(avatar_url: str) -> bytes | None:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(avatar_url, timeout=10) as resp:
                if resp.status == 200:
                    return await resp.read()
    except Exception:
        return None
    return None


async def render_welcome_card(
    display_name: str,
    avatar_url: str,
    member_number: int,
) -> io.BytesIO:
    """Render welcome card PNG, trả về BytesIO sẵn sàng gửi qua discord.File.

    member_number: số thứ tự thành viên (vd 10797 -> hiện '#10797')."""

    card = Image.open(_BG_PATH).convert("RGBA")
    if card.size != (_W, _H):
        card = card.resize((_W, _H))
    draw = ImageDraw.Draw(card)

    # --- Số thứ tự thành viên: to, ngay dưới "Con gà thứ:" ---
    number_font = _font(_FONT_BOLD_PATH, 44)
    number_text = f"#{member_number}"
    draw.text((40, 132), number_text, font=number_font, fill=_WHITE)

    # --- Tên hiển thị: ngay dưới số thứ tự, tự co cỡ nếu dài ---
    name_max_width = _W - 40 - 40
    name_font = _fit_text(draw, display_name, _FONT_BOLD_PATH, name_max_width, start_size=30, min_size=18)
    draw.text((40, 190), display_name, font=name_font, fill=_WHITE)

    # --- Avatar tròn, viền trắng mảnh, góc phải ---
    avatar_bytes = await _download_avatar_bytes(avatar_url)
    avatar_img: Image.Image | None = None
    if avatar_bytes:
        try:
            avatar_img = Image.open(io.BytesIO(avatar_bytes)).convert("RGB")
        except Exception:
            avatar_img = None

    ring_pad = 4
    ax0 = _AVATAR_CENTER[0] - _AVATAR_SIZE // 2
    ay0 = _AVATAR_CENTER[1] - _AVATAR_SIZE // 2
    ring_box = (
        ax0 - ring_pad,
        ay0 - ring_pad,
        ax0 + _AVATAR_SIZE + ring_pad,
        ay0 + _AVATAR_SIZE + ring_pad,
    )
    draw.ellipse(ring_box, fill=_WHITE)

    if avatar_img is not None:
        try:
            fitted = ImageOps.fit(avatar_img, (_AVATAR_SIZE, _AVATAR_SIZE))
            avatar_mask = Image.new("L", (_AVATAR_SIZE, _AVATAR_SIZE), 0)
            ImageDraw.Draw(avatar_mask).ellipse([(0, 0), (_AVATAR_SIZE, _AVATAR_SIZE)], fill=255)
            card.paste(fitted, (ax0, ay0), avatar_mask)
        except Exception:
            pass

    buf = io.BytesIO()
    card.convert("RGB").save(buf, format="PNG")
    buf.seek(0)
    return buf
