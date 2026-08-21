"""
Render ảnh "welcome card" khi có thành viên mới join server - dùng ảnh nền
tĩnh (assets/welcome/welcome_bg.png, do admin tự thiết kế/đổi) và đè lên:

  - Avatar hình chữ nhật (không tròn), chiếm gần nửa phải card, mép trái cắt
    chéo (diagonal cut) - style lấy theo mẫu "Tohmcord" Mango gửi.
  - Số thứ tự thành viên ("#12345") - to, MỜ (watermark xám nhạt), nằm phía
    sau/trên phần đầu của tên.
  - Tên hiển thị - đậm, trắng, đè lên ngay trên số, căn trái, tự xuống dòng
    nếu dài (tối đa 2 dòng, tự co cỡ chữ nếu vẫn không vừa).

Bố cục toạ độ đo theo khung nền 500x350 - nếu đổi nền khác kích thước khác
thì cần chỉnh lại các hằng số toạ độ bên dưới.

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
_NUMBER_GREY = (150, 152, 158)  # số thứ tự mờ kiểu watermark, gần màu nền tối

# --- Avatar: khối chữ nhật chiếm nửa phải card, mép trái cắt chéo ---
_AVATAR_RIGHT = _W - 14      # cách mép phải 1 chút để không đè viền bo góc
_AVATAR_TOP = 78             # bắt đầu ngay dưới logo header, không đè "DeltaCord"
_AVATAR_BOTTOM = _H - 60     # dừng trước dòng chữ "Chào mừng..." ở đáy card
_AVATAR_LEFT_TOP = 330       # x của mép trái avatar ở hàng trên (chỗ bắt đầu cắt chéo)
_AVATAR_LEFT_BOTTOM = 260    # x của mép trái avatar ở hàng dưới (chéo rộng hơn ở đáy)

# --- Vùng chữ (số + tên) ---
_TEXT_LEFT = 24
_TEXT_RIGHT_MAX = 305  # không lấn qua avatar ở hàng trên


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: str,
    max_width: int,
    start_size: int,
    min_size: int = 16,
) -> ImageFont.FreeTypeFont:
    size = start_size
    while size > min_size:
        font = _font(font_path, size)
        bbox = draw.textbbox((0, 0), text, font=font)
        if bbox[2] - bbox[0] <= max_width:
            return font
        size -= 2
    return _font(font_path, min_size)


def _wrap_two_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: str,
    max_width: int,
    start_size: int,
    min_size: int = 16,
) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """Co cỡ chữ dần tới khi tên vừa trong TỐI ĐA 2 dòng (ngắt theo từ)."""
    size = start_size
    words = text.split()
    while size >= min_size:
        font = _font(font_path, size)
        lines: list[str] = []
        current = ""
        for word in words:
            trial = f"{current} {word}".strip()
            bbox = draw.textbbox((0, 0), trial, font=font)
            if bbox[2] - bbox[0] <= max_width or not current:
                current = trial
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
        if len(lines) <= 2:
            return font, lines
        size -= 2
    # Cỡ nhỏ nhất vẫn không vừa 2 dòng -> cắt bớt dòng 2 kèm "…"
    font = _font(font_path, min_size)
    return font, lines[:2]


async def _download_avatar_bytes(avatar_url: str) -> tuple[bytes | None, str]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(avatar_url, timeout=10) as resp:
                if resp.status == 200:
                    return await resp.read(), ""
                return None, f"http_{resp.status}"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _build_avatar_mask() -> Image.Image:
    """Mask hình chữ nhật mép trái cắt chéo, khớp vùng avatar bên phải card.
    Vẽ ở độ phân giải gấp đôi rồi thu nhỏ lại (supersampling) để cạnh chéo
    mượt, không bị răng cưa/lem như vẽ polygon trực tiếp ở kích thước gốc."""
    scale = 4
    big = Image.new("L", (_W * scale, _H * scale), 0)
    mdraw = ImageDraw.Draw(big)
    polygon = [
        (_AVATAR_LEFT_TOP * scale, _AVATAR_TOP * scale),
        (_AVATAR_RIGHT * scale, _AVATAR_TOP * scale),
        (_AVATAR_RIGHT * scale, _AVATAR_BOTTOM * scale),
        (_AVATAR_LEFT_BOTTOM * scale, _AVATAR_BOTTOM * scale),
    ]
    mdraw.polygon(polygon, fill=255)
    return big.resize((_W, _H), Image.LANCZOS)


def _render_slanted_text(text: str, font: ImageFont.FreeTypeFont, color: tuple[int, int, int]) -> Image.Image:
    """Vẽ text rồi nghiêng nhẹ bằng shear transform (giả italic) - font
    DejaVu Sans-Bold bundle sẵn không có bản in nghiêng thật, nên đây là cách
    gần nhất với style chữ nghiêng của mẫu tham khảo mà không cần thêm font."""
    bbox = font.getbbox(text)
    pad = 10
    tw = bbox[2] - bbox[0] + pad * 2
    th = bbox[3] - bbox[1] + pad * 2
    layer = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((pad - bbox[0], pad - bbox[1]), text, font=font, fill=color)

    shear = 0.18
    sheared = layer.transform(
        (tw + int(th * shear), th),
        Image.AFFINE,
        (1, -shear, th * shear, 0, 1, 0),
        resample=Image.BICUBIC,
    )
    return sheared


async def render_welcome_card(
    display_name: str,
    avatar_url: str,
    member_number: int,
) -> tuple[io.BytesIO, str]:
    """Render welcome card PNG, trả về (BytesIO sẵn sàng gửi qua discord.File,
    lý_do_lỗi_avatar). lý_do_lỗi_avatar rỗng "" nếu avatar tải/dán thành công.

    member_number: số thứ tự thành viên (vd 10797 -> hiện '#10797')."""

    card = Image.open(_BG_PATH).convert("RGBA")
    if card.size != (_W, _H):
        card = card.resize((_W, _H))

    # --- Avatar chữ nhật chéo, dán TRƯỚC chữ để số/tên đè lên trên nó ---
    avatar_bytes, avatar_err = await _download_avatar_bytes(avatar_url)
    if avatar_bytes:
        try:
            avatar_img = Image.open(io.BytesIO(avatar_bytes)).convert("RGB")
            box_w = _AVATAR_RIGHT - min(_AVATAR_LEFT_TOP, _AVATAR_LEFT_BOTTOM)
            box_h = _AVATAR_BOTTOM - _AVATAR_TOP
            fitted = ImageOps.fit(avatar_img, (box_w, box_h))
            layer = Image.new("RGBA", (_W, _H), (0, 0, 0, 0))
            layer.paste(fitted, (min(_AVATAR_LEFT_TOP, _AVATAR_LEFT_BOTTOM), _AVATAR_TOP))
            mask = _build_avatar_mask()
            card = Image.composite(layer, card, mask)
        except Exception as e:
            avatar_err = f"decode lỗi: {type(e).__name__}: {e}"
    # Nếu tải avatar lỗi, card vẫn hiển thị đầy đủ số + tên (không để trống
    # loang lổ) - chỉ đơn giản là thiếu ảnh avatar, không crash toàn bộ card.
    # avatar_err được trả kèm để caller (discord_bot.py) log lý do cụ thể.

    draw = ImageDraw.Draw(card)

    text_max_width = _TEXT_RIGHT_MAX - _TEXT_LEFT

    # --- Số thứ tự: to, mờ (watermark) ---
    number_font = _font(_FONT_BOLD_PATH, 44)
    number_text = f"#{member_number}"
    number_y = 128
    draw.text((_TEXT_LEFT, number_y), number_text, font=number_font, fill=_NUMBER_GREY)

    # --- Tên hiển thị: đậm, trắng, DÒNG RIÊNG ngay dưới số (không đè lên
    # nhau, giống mẫu tham khảo) ---
    name_bbox_font = _fit_text(draw, display_name, _FONT_BOLD_PATH, text_max_width, start_size=26, min_size=15)
    name_y = number_y + 62
    name_slanted = _render_slanted_text(display_name, name_bbox_font, _WHITE)
    card.alpha_composite(name_slanted, (_TEXT_LEFT, name_y))

    buf = io.BytesIO()
    # Giữ nguyên RGBA (không convert("RGB")) để Discord hiển thị đúng góc bo
    # tròn trong suốt của khung nền - convert("RGB") trước đây làm lộ màu
    # "ma" ở vùng alpha=0 (RGB cũ giữ nguyên dù trong suốt) thành nền đặc.
    card.save(buf, format="PNG")
    buf.seek(0)
    return buf, avatar_err
