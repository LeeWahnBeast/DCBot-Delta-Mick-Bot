"""
Render ảnh "level card" cho lệnh /level theo phong cách Google Material You 3:
- Dynamic color: màu chủ đạo được lấy ra từ avatar của người dùng (giống cách
  Material You tạo bảng màu từ ảnh nền/avatar điện thoại), rồi suy ra tông
  sáng/tối/nhạt để tô nền, khung avatar và thanh XP.
- Gradient mềm giữa 2 sắc độ của màu chủ đạo (không dùng nền xám phẳng cũ).
- Bo góc lớn (card, avatar, thanh XP, "pill" Level đều bo tròn kiểu M3).
- Typography: tên = chữ đậm cỡ lớn, phụ đề (Level/XP) = chữ cỡ nhỏ hơn, có
  tương phản rõ để dễ đọc trên nền gradient.
- Thanh XP đặt ở HÀNG RIÊNG, có khoảng đệm cố định phía trên/dưới avatar +
  tên, không bao giờ chồng lên avatar hay chữ (đây là lỗi bản cũ).

Font dùng DejaVu Sans (bundle sẵn trong assets/fonts/) chứ không dựa vào font
hệ thống, vì môi trường Render (Linux, free tier) không đảm bảo có sẵn font
hỗ trợ tiếng Việt có dấu. Nếu thiếu file font thì lỗi phải rõ ràng ngay,
không âm thầm vẽ chữ méo/thiếu dấu.
"""

from __future__ import annotations

import io
import os
import colorsys

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

# --- Kích thước card theo tỉ lệ M3 "wide card" ---
_W, _H = 1000, 320
_PAD = 44
_AVATAR_SIZE = 176
_CARD_RADIUS = 40
_AVATAR_RADIUS = 44  # squircle-ish, không bo tròn hoàn toàn -> đúng chất M3
_BAR_HEIGHT = 26
_BAR_RADIUS = _BAR_HEIGHT // 2

_WHITE = (255, 255, 255)
_FALLBACK_SEED = (103, 80, 164)  # tím Material mặc định khi không lấy được màu từ avatar


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


def _extract_seed_color(avatar_img: Image.Image) -> tuple[int, int, int]:
    """Lấy màu chủ đạo (dynamic color) từ avatar bằng cách quantize xuống ít
    màu rồi chọn màu có tỉ lệ pixel lớn nhất, mô phỏng thuật toán Material You."""
    try:
        small = avatar_img.convert("RGB").resize((48, 48))
        quantized = small.quantize(colors=6, method=Image.MEDIANCUT)
        palette = quantized.getpalette()
        color_counts = sorted(quantized.getcolors(), reverse=True)
        for count, idx in color_counts:
            r, g, b = palette[idx * 3: idx * 3 + 3]
            h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            # bỏ qua màu gần trắng/đen/xám thuần vì không tạo được gradient đẹp
            if s > 0.15 and 0.12 < v < 0.95:
                return (r, g, b)
        r, g, b = palette[color_counts[0][1] * 3: color_counts[0][1] * 3 + 3]
        return (r, g, b)
    except Exception:
        return _FALLBACK_SEED


def _tone(rgb: tuple[int, int, int], lightness_delta: float, sat_mult: float = 1.0) -> tuple[int, int, int]:
    """Trả về 1 sắc độ (tone) khác của cùng 1 hue — dùng để build gradient/khung/pill
    kiểu 'tonal palette' của Material You mà không cần thư viện màu sắc riêng."""
    h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
    v = max(0.0, min(1.0, v + lightness_delta))
    s = max(0.0, min(1.0, s * sat_mult))
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return (int(r * 255), int(g * 255), int(b * 255))


def _make_gradient_bg(size: tuple[int, int], color_top: tuple[int, int, int], color_bottom: tuple[int, int, int]) -> Image.Image:
    w, h = size
    base = Image.new("RGB", (1, h), color_top)
    top = colorsys.rgb_to_hsv(*(c / 255 for c in color_top))
    bottom = colorsys.rgb_to_hsv(*(c / 255 for c in color_bottom))
    for y in range(h):
        t = y / max(1, h - 1)
        h_ = top[0] + (bottom[0] - top[0]) * t
        s_ = top[1] + (bottom[1] - top[1]) * t
        v_ = top[2] + (bottom[2] - top[2]) * t
        r, g, b = colorsys.hsv_to_rgb(h_, s_, v_)
        base.putpixel((0, y), (int(r * 255), int(g * 255), int(b * 255)))
    return base.resize((w, h))


def _readable_text_color(bg_rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    r, g, b = bg_rgb
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return (20, 18, 24) if luminance > 0.6 else _WHITE


async def render_level_card(
    display_name: str,
    avatar_url: str,
    level: int,
    xp: int,
    xp_needed: int,
) -> io.BytesIO:
    """Render 1 level card PNG theo phong cách Material You 3, trả về BytesIO
    sẵn sàng gửi qua discord.File."""

    avatar_bytes = await _download_avatar_bytes(avatar_url)
    avatar_img: Image.Image | None = None
    seed_color = _FALLBACK_SEED
    if avatar_bytes:
        try:
            avatar_img = Image.open(io.BytesIO(avatar_bytes)).convert("RGB")
            seed_color = _extract_seed_color(avatar_img)
        except Exception:
            avatar_img = None

    # --- Bảng tông màu (tonal palette) suy ra từ dynamic color ---
    bg_top = _tone(seed_color, lightness_delta=0.30, sat_mult=0.55)
    bg_bottom = _tone(seed_color, lightness_delta=-0.05, sat_mult=0.85)
    accent = _tone(seed_color, lightness_delta=0.05, sat_mult=1.0)
    surface = _tone(seed_color, lightness_delta=0.42, sat_mult=0.30)  # nền "container" nhạt cho thanh XP
    text_color = _readable_text_color(bg_top)
    if text_color == _WHITE:
        subtext_color = tuple(max(0, c - 45) for c in _WHITE)
    else:
        subtext_color = tuple(min(255, c + 60) for c in text_color)

    card = _make_gradient_bg((_W, _H), bg_top, bg_bottom)
    draw = ImageDraw.Draw(card)

    # --- Avatar: khung "squircle" bo góc lớn kiểu M3, viền nhẹ theo accent ---
    avatar_x = _PAD
    avatar_y = (_H - _BAR_HEIGHT - 56 - _AVATAR_SIZE) // 2 + 6
    ring_pad = 6
    ring_box = (
        avatar_x - ring_pad,
        avatar_y - ring_pad,
        avatar_x + _AVATAR_SIZE + ring_pad,
        avatar_y + _AVATAR_SIZE + ring_pad,
    )
    draw.rounded_rectangle(ring_box, radius=_AVATAR_RADIUS + ring_pad, fill=accent)

    if avatar_img is not None:
        try:
            fitted = ImageOps.fit(avatar_img, (_AVATAR_SIZE, _AVATAR_SIZE))
            avatar_mask = _rounded_mask((_AVATAR_SIZE, _AVATAR_SIZE), _AVATAR_RADIUS)
            card.paste(fitted, (avatar_x, avatar_y), avatar_mask)
        except Exception:
            pass

    # --- Cột chữ bên phải avatar ---
    text_x = avatar_x + _AVATAR_SIZE + 40
    text_max_width = _W - _PAD - text_x

    # "Level N" pill nhỏ phía trên tên, đúng ngôn ngữ M3 (badge/chip bo tròn)
    level_text = f"LEVEL {level}"
    chip_font = _font(_FONT_BOLD_PATH, 22)
    chip_bbox = draw.textbbox((0, 0), level_text, font=chip_font)
    chip_text_w = chip_bbox[2] - chip_bbox[0]
    chip_text_h = chip_bbox[3] - chip_bbox[1]
    chip_pad_x, chip_pad_y = 20, 10
    chip_y0 = avatar_y + 4
    chip_box = (text_x, chip_y0, text_x + chip_text_w + chip_pad_x * 2, chip_y0 + chip_text_h + chip_pad_y * 2 + 6)
    draw.rounded_rectangle(chip_box, radius=(chip_box[3] - chip_box[1]) // 2, fill=surface)
    draw.text((text_x + chip_pad_x, chip_y0 + chip_pad_y - 2), level_text, font=chip_font, fill=_tone(seed_color, -0.25, 1.0))

    # Tên hiển thị (chữ đậm, cỡ lớn, tự co nếu dài)
    name_font = _fit_text(draw, display_name, _FONT_BOLD_PATH, text_max_width, start_size=54)
    name_y = chip_box[3] + 18
    draw.text((text_x, name_y), display_name, font=name_font, fill=text_color)

    # --- Thanh XP: hàng RIÊNG ở đáy card, cách xa khối avatar/tên phía trên ---
    bar_x0 = _PAD
    bar_x1 = _W - _PAD
    bar_y1 = _H - _PAD
    bar_y0 = bar_y1 - _BAR_HEIGHT
    bar_width = bar_x1 - bar_x0
    progress = min(1.0, xp / xp_needed) if xp_needed else 0.0
    fill_width = max(_BAR_HEIGHT, int(bar_width * progress)) if progress > 0 else 0

    # Nhãn "x / y XP" nằm NGAY TRÊN thanh, đủ khoảng cách cố định — không bao
    # giờ đụng avatar vì avatar dừng lại ở avatar_y + _AVATAR_SIZE, còn label
    # này neo theo bar_y0 (đáy card), tách biệt hoàn toàn hai khối.
    info_font = _font(_FONT_REGULAR_PATH, 24)
    xp_text = f"{xp} / {xp_needed} XP"
    label_y = bar_y0 - 34
    draw.text((bar_x0, label_y), xp_text, font=info_font, fill=subtext_color)

    pct_text = f"{int(progress * 100)}%"
    pct_bbox = draw.textbbox((0, 0), pct_text, font=info_font)
    pct_w = pct_bbox[2] - pct_bbox[0]
    draw.text((bar_x1 - pct_w, label_y), pct_text, font=info_font, fill=subtext_color)

    # Track (nền thanh) + fill (tiến độ), bo tròn hoàn toàn kiểu M3 progress bar
    draw.rounded_rectangle([(bar_x0, bar_y0), (bar_x1, bar_y1)], radius=_BAR_RADIUS, fill=surface)
    if fill_width > 0:
        draw.rounded_rectangle(
            [(bar_x0, bar_y0), (bar_x0 + fill_width, bar_y1)], radius=_BAR_RADIUS, fill=accent
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
