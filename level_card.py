"""
Render ảnh "level card" cho lệnh /level - UI dạng thẻ nền tối phẳng (dark
card), bố cục:

  [Avatar tròn]   LEVEL {level}      RANK {rank}
                  {tên hiển thị}
                  ─────────────────────  {xp} / {xp_needed}
                  [======thanh XP======]

- "Level" ở đây LUÔN là cấp độ (level) của NGƯỜI DÙNG, tính từ XP nhắn tin/
  voice chat - không liên quan gì tới "material"/nguyên liệu; xem hàm
  render_level_card() bên dưới.
- Avatar tròn hoàn toàn (khác bản cũ dùng squircle bo góc).
- "RANK n" lấy từ thứ hạng trên bảng xếp hạng toàn server (theo Level), do
  caller tự tính và truyền vào qua tham số `rank`.
- xp_needed hiển thị rút gọn kiểu "6.5K" khi >= 1000 cho gọn, giống UI tham
  khảo, nhưng xp hiện tại vẫn hiện số đầy đủ để không gây hiểu lầm.

Font dùng DejaVu Sans (bundle sẵn trong assets/fonts/) chứ không dựa vào font
hệ thống, vì môi trường Render (Linux, free tier) không đảm bảo có sẵn font
hỗ trợ tiếng Việt có dấu. Nếu thiếu file font thì lỗi phải rõ ràng ngay,
không âm thầm vẽ chữ méo/thiếu dấu.
"""

from __future__ import annotations

import io
import os

from PIL import Image, ImageDraw, ImageFont, ImageOps
import aiohttp

_FONT_DIR = os.path.join(os.path.dirname(__file__), "assets", "fonts")
_FONT_BOLD_PATH = os.path.join(_FONT_DIR, "DejaVuSans-Bold.ttf")
_FONT_REGULAR_PATH = os.path.join(_FONT_DIR, "DejaVuSans.ttf")

if not os.path.exists(_FONT_BOLD_PATH) or not os.path.exists(_FONT_REGULAR_PATH):
    raise FileNotFoundError(
        f"Thiếu font trong {_FONT_DIR} — cần DejaVuSans.ttf và DejaVuSans-Bold.ttf "
        "để vẽ được tiếng Việt có dấu trên level card."
    )

# Ảnh chúc mừng lên level (tùy chọn) — bỏ vào assets/levelup.png nếu có, nếu
# không có file thì bot tự chuyển sang embed text thay vì báo lỗi.
LEVEL_UP_IMAGE_PATH = os.path.join(os.path.dirname(__file__), "assets", "levelup.png")

# --- Kích thước card (dark card, tỉ lệ ~3:1) ---
_W, _H = 934, 294
_PAD = 40
_AVATAR_SIZE = 176
_CARD_RADIUS = 28
_BAR_HEIGHT = 14
_BAR_RADIUS = _BAR_HEIGHT // 2

_WHITE = (255, 255, 255)
_CARD_BG = (30, 31, 34)       # nền thẻ tối, kiểu Discord dark
_TRACK_COLOR = (60, 61, 66)   # nền thanh XP (chưa fill)
_ACCENT = (255, 255, 255)     # màu fill thanh XP + viền avatar
_SUBTEXT = (170, 172, 178)


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([(0, 0), (size[0] - 1, size[1] - 1)], radius=radius, fill=255)
    return mask


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: str,
    max_width: int,
    start_size: int,
    min_size: int = 22,
) -> ImageFont.FreeTypeFont:
    """Giảm cỡ chữ dần cho tới khi vừa max_width (tên dài không tràn ra ngoài card)."""
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


def _format_short_number(n: int) -> str:
    """Rút gọn số lớn kiểu '6.5K' cho gọn (chỉ dùng cho xp_needed hiển thị,
    xp hiện tại vẫn hiện đầy đủ để tránh hiểu lầm số liệu)."""
    if n < 1000:
        return str(n)
    value = n / 1000
    text = f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{text}K"


async def render_level_card(
    display_name: str,
    avatar_url: str,
    level: int,
    xp: int,
    xp_needed: int,
    rank: int | None = None,
) -> io.BytesIO:
    """Render 1 level card PNG dạng thẻ nền tối phẳng, trả về BytesIO sẵn sàng
    gửi qua discord.File. `level` luôn là level của người dùng đang xem (hoặc
    người được tra); `rank` là thứ hạng của họ trên bảng xếp hạng server theo
    Level (None nếu caller không có/không cần hiển thị)."""

    avatar_bytes = await _download_avatar_bytes(avatar_url)
    avatar_img: Image.Image | None = None
    if avatar_bytes:
        try:
            avatar_img = Image.open(io.BytesIO(avatar_bytes)).convert("RGB")
        except Exception:
            avatar_img = None

    card = Image.new("RGB", (_W, _H), _CARD_BG)
    draw = ImageDraw.Draw(card)

    # --- Avatar: tròn hoàn toàn, viền trắng mảnh ---
    avatar_x = _PAD
    avatar_y = (_H - _AVATAR_SIZE) // 2
    ring_pad = 4
    ring_box = (
        avatar_x - ring_pad,
        avatar_y - ring_pad,
        avatar_x + _AVATAR_SIZE + ring_pad,
        avatar_y + _AVATAR_SIZE + ring_pad,
    )
    draw.ellipse(ring_box, fill=_ACCENT)

    if avatar_img is not None:
        try:
            fitted = ImageOps.fit(avatar_img, (_AVATAR_SIZE, _AVATAR_SIZE))
            avatar_mask = Image.new("L", (_AVATAR_SIZE, _AVATAR_SIZE), 0)
            ImageDraw.Draw(avatar_mask).ellipse([(0, 0), (_AVATAR_SIZE, _AVATAR_SIZE)], fill=255)
            card.paste(fitted, (avatar_x, avatar_y), avatar_mask)
        except Exception:
            pass

    # --- Cột chữ bên phải avatar ---
    text_x = avatar_x + _AVATAR_SIZE + 40
    text_max_width = _W - _PAD - text_x
    top_y = avatar_y + 6

    # Hàng trên cùng: "LEVEL n" bên trái, "RANK n" đẩy sát mép phải card
    level_font = _font(_FONT_BOLD_PATH, 34)
    level_text = f"LEVEL {level}"
    draw.text((text_x, top_y), level_text, font=level_font, fill=_WHITE)

    if rank is not None:
        rank_text = f"RANK {rank}"
        rank_bbox = draw.textbbox((0, 0), rank_text, font=level_font)
        rank_w = rank_bbox[2] - rank_bbox[0]
        draw.text((_W - _PAD - rank_w, top_y), rank_text, font=level_font, fill=_WHITE)

    # Tên hiển thị, ngay dưới hàng Level/Rank, tự co cỡ chữ nếu tên dài
    name_font = _fit_text(draw, display_name, _FONT_BOLD_PATH, text_max_width, start_size=40, min_size=20)
    name_y = top_y + 52
    draw.text((text_x, name_y), display_name, font=name_font, fill=_WHITE)

    # --- Thanh XP: hàng riêng dưới cùng cột chữ, số liệu "xp / xp_needed"
    # canh phải ngay phía trên thanh, giống UI tham khảo ---
    bar_x0 = text_x
    bar_x1 = _W - _PAD
    bar_y1 = avatar_y + _AVATAR_SIZE
    bar_y0 = bar_y1 - _BAR_HEIGHT
    bar_width = bar_x1 - bar_x0
    progress = min(1.0, xp / xp_needed) if xp_needed else 0.0
    fill_width = max(_BAR_HEIGHT, int(bar_width * progress)) if progress > 0 else 0

    info_font = _font(_FONT_REGULAR_PATH, 22)
    xp_text = f"{xp} / {_format_short_number(xp_needed)}"
    xp_bbox = draw.textbbox((0, 0), xp_text, font=info_font)
    xp_w = xp_bbox[2] - xp_bbox[0]
    label_y = bar_y0 - 32
    draw.text((bar_x1 - xp_w, label_y), xp_text, font=info_font, fill=_SUBTEXT)

    draw.rounded_rectangle([(bar_x0, bar_y0), (bar_x1, bar_y1)], radius=_BAR_RADIUS, fill=_TRACK_COLOR)
    if fill_width > 0:
        draw.rounded_rectangle(
            [(bar_x0, bar_y0), (bar_x0 + fill_width, bar_y1)], radius=_BAR_RADIUS, fill=_ACCENT
        )

    # --- Bo góc lớn cho toàn bộ card ---
    mask = _rounded_mask((_W, _H), _CARD_RADIUS)
    final = Image.new("RGBA", (_W, _H), (0, 0, 0, 0))
    final.paste(card, (0, 0), mask)

    buf = io.BytesIO()
    final.save(buf, format="PNG")
    buf.seek(0)
    return buf


def get_level_up_image_path() -> str | None:
    """Trả về đường dẫn ảnh chúc mừng lên level nếu file tồn tại, ngược lại None
    (bot sẽ chỉ gửi text, không báo lỗi thiếu file)."""
    return LEVEL_UP_IMAGE_PATH if os.path.exists(LEVEL_UP_IMAGE_PATH) else None
