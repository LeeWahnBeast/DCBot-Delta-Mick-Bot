"""
Render ảnh "level card" (kiểu MEE6) cho lệnh /level: nền xám đen bo góc, tên
bên trái, avatar bên phải, thanh XP vàng ngang bên dưới, kèm "x/xp" và
"Level N".

Font dùng DejaVu Sans (bundle sẵn trong assets/fonts/) chứ không dựa vào font
hệ thống, vì môi trường Render (Linux, free tier) không đảm bảo có sẵn font
hỗ trợ tiếng Việt có dấu. Không được dùng font hệ thống trực tiếp - nếu thiếu
file trong assets/fonts/ thì phải biết ngay (lỗi rõ ràng) chứ không âm thầm
vẽ chữ méo/thiếu dấu.
"""

import io
import os

from PIL import Image, ImageDraw, ImageFont, ImageOps
import aiohttp

_FONT_DIR = os.path.join(os.path.dirname(__file__), "assets", "fonts")
_FONT_BOLD_PATH = os.path.join(_FONT_DIR, "DejaVuSans-Bold.ttf")
_FONT_REGULAR_PATH = os.path.join(_FONT_DIR, "DejaVuSans.ttf")

# Kích thước card
_W, _H = 934, 282
_PAD = 40
_AVATAR_SIZE = 210
_CORNER_RADIUS = 28

_BG_COLOR = (26, 26, 26)
_BAR_BG_COLOR = (58, 58, 58)
_BAR_FILL_COLOR = (255, 200, 20)
_TEXT_COLOR = (255, 255, 255)
_SUBTEXT_COLOR = (220, 220, 220)


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([(0, 0), (size[0] - 1, size[1] - 1)], radius=radius, fill=255)
    return mask


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font_path: str, max_width: int, start_size: int, min_size: int = 20) -> ImageFont.FreeTypeFont:
    """Giảm cỡ chữ dần cho tới khi vừa max_width (tên dài/nhiều ký tự không bị tràn ra ngoài card)."""
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


async def render_level_card(
    display_name: str,
    avatar_url: str,
    level: int,
    xp: int,
    xp_needed: int,
) -> io.BytesIO:
    """Render 1 card level dạng ảnh PNG, trả về BytesIO sẵn sàng gửi qua discord.File."""
    card = Image.new("RGB", (_W, _H), _BG_COLOR)
    mask = _rounded_mask((_W, _H), _CORNER_RADIUS)
    rounded = Image.new("RGB", (_W, _H), _BG_COLOR)
    rounded.paste(card, (0, 0))

    draw = ImageDraw.Draw(rounded)

    # --- Avatar (bên phải, bo góc) ---
    avatar_x = _W - _PAD - _AVATAR_SIZE
    avatar_y = (_H - _AVATAR_SIZE) // 2
    avatar_bytes = await _download_avatar_bytes(avatar_url)
    if avatar_bytes:
        try:
            avatar_img = Image.open(io.BytesIO(avatar_bytes)).convert("RGB")
            avatar_img = ImageOps.fit(avatar_img, (_AVATAR_SIZE, _AVATAR_SIZE))
            avatar_mask = _rounded_mask((_AVATAR_SIZE, _AVATAR_SIZE), 18)
            rounded.paste(avatar_img, (avatar_x, avatar_y), avatar_mask)
        except Exception:
            pass  # avatar lỗi -> vẫn render card, chỉ thiếu ảnh

    # --- Tên (trên bên trái, tự co cỡ chữ nếu quá dài) ---
    name_max_width = avatar_x - _PAD * 2
    name_font = _fit_text(draw, display_name, _FONT_BOLD_PATH, name_max_width, start_size=48)
    draw.text((_PAD, 50), display_name, font=name_font, fill=_TEXT_COLOR)

    # --- Thanh XP (dưới cùng, full chiều ngang trừ padding) ---
    bar_x0 = _PAD
    bar_x1 = _W - _PAD
    bar_y0 = _H - 70
    bar_y1 = _H - 45
    bar_width = bar_x1 - bar_x0
    progress = min(1.0, xp / xp_needed) if xp_needed else 0.0
    fill_width = int(bar_width * progress)

    draw.rounded_rectangle([(bar_x0, bar_y0), (bar_x1, bar_y1)], radius=12, fill=_BAR_BG_COLOR)
    if fill_width > 0:
        # đảm bảo tối thiểu bằng chiều cao thanh để bo góc không bị méo khi progress rất nhỏ
        fill_width = max(fill_width, (bar_y1 - bar_y0))
        draw.rounded_rectangle(
            [(bar_x0, bar_y0), (bar_x0 + fill_width, bar_y1)], radius=12, fill=_BAR_FILL_COLOR
        )

    # --- Text "x / y xp" (trái, trên thanh) + "Level N" (phải, trên thanh) ---
    info_font = _font(_FONT_REGULAR_PATH, 26)
    xp_text = f"{xp} / {xp_needed} xp"
    draw.text((bar_x0, bar_y0 - 40), xp_text, font=info_font, fill=_SUBTEXT_COLOR)

    level_text = f"Level {level}"
    level_bbox = draw.textbbox((0, 0), level_text, font=info_font)
    level_w = level_bbox[2] - level_bbox[0]
    draw.text((bar_x1 - level_w, bar_y0 - 40), level_text, font=info_font, fill=_SUBTEXT_COLOR)

    buf = io.BytesIO()
    rounded.save(buf, format="PNG")
    buf.seek(0)
    return buf
