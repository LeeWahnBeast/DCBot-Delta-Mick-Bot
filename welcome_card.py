"""
Render ảnh "welcome card" khi có thành viên mới join server - dùng ảnh nền
tĩnh (assets/welcome/welcome_bg.png, do admin tự thiết kế/đổi) và đè lên:

  - Avatar hình chữ nhật (không tròn), chiếm gần nửa phải card, mép trái cắt
    chéo (diagonal cut) - style lấy theo mẫu "Tohmcord" Mango gửi.
  - Số thứ tự thành viên ("#12345") - to, MỜ (watermark xám nhạt), font
    Poppins Bold tròn/mập, THẲNG ĐỨNG, nằm phía sau/trên phần đầu của tên.
  - Tên hiển thị - đậm, trắng, đè lên ngay trên số, căn giữa, tự xuống dòng
    nếu dài (tối đa 2 dòng, tự co cỡ chữ nếu vẫn không vừa).

Bố cục toạ độ đo theo khung nền 500x350 - nếu đổi nền khác kích thước khác
thì cần chỉnh lại các hằng số toạ độ bên dưới.

Font: DejaVu Sans-Bold cho tên (đủ dấu tiếng Việt), Poppins Bold cho số
thứ tự (tròn/mập hiện đại kiểu Montserrat/Baloo - Poppins bundle sẵn không
có đủ 1 số dấu tiếng Việt như ệ/ữ/ễ nên chỉ dùng cho số, không dùng cho
tên). Cả 2 đều bundle sẵn trong assets/fonts/.
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
# Font riêng cho số thứ tự - Poppins Bold tròn/mập hơn DejaVu, gần với style
# ảnh mẫu "Tohmcord" hơn (DejaVu vẫn dùng cho tên/chữ Việt có dấu vì Poppins
# không có bộ dấu tiếng Việt đầy đủ).
_FONT_NUMBER_PATH = os.path.join(_FONT_DIR, "Poppins-Bold.ttf")

if not os.path.exists(_FONT_BOLD_PATH) or not os.path.exists(_FONT_REGULAR_PATH):
    raise FileNotFoundError(
        f"Thiếu font trong {_FONT_DIR} — cần DejaVuSans.ttf và DejaVuSans-Bold.ttf "
        "để vẽ được tiếng Việt có dấu trên welcome card."
    )
if not os.path.exists(_FONT_NUMBER_PATH):
    raise FileNotFoundError(
        f"Thiếu font Poppins-Bold.ttf trong {_FONT_DIR} — cần để vẽ số thứ tự "
        "kiểu tròn/mập giống ảnh mẫu."
    )

_BG_PATH = os.path.join(_HERE, "assets", "welcome", "welcome_bg.png")
if not os.path.exists(_BG_PATH):
    raise FileNotFoundError(
        f"Thiếu ảnh nền welcome card tại {_BG_PATH} — bỏ file nền (500x350) vào đây."
    )

_W, _H = 500, 350
_WHITE = (255, 255, 255)
_NUMBER_GREY = (100, 100, 105)  # xám vừa phải kiểu watermark, đo theo ảnh mẫu Tohmcord (nét số ~99,99,99 trên nền ~31,31,31)

# --- Avatar: khối chữ nhật chiếm nửa phải card, mép trái cắt chéo ---
# Đo pixel-by-pixel từ ảnh mẫu "Tohmcord" (rabbit avatar, 500x350):
# hộp RỘNG ở trên, HẸP ở dưới (không phải ngược lại như bản cũ).
_AVATAR_RIGHT = 471          # biên phải hộp (đo: blue kết thúc ~470-472)
_AVATAR_TOP = 58             # biên trên hộp (đo: blue bắt đầu y~58-60)
_AVATAR_BOTTOM = 288         # biên dưới hộp (đo: blue kết thúc y~288)
_AVATAR_LEFT_TOP = 303       # x mép trái ở hàng TRÊN - RỘNG hơn ở đây
_AVATAR_LEFT_BOTTOM = 393    # x mép trái ở hàng DƯỚI - HẸP hơn ở đây

# --- Vùng chữ (số + tên) ---
_TEXT_LEFT = 24
_TEXT_RIGHT_MAX = 305  # không lấn qua avatar ở hàng trên


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


# Font tên: DejaVu Sans-Bold (đủ dấu tiếng Việt). Font số: Poppins Bold
# (tròn/mập, không cần dấu vì chỉ có chữ số + #) - cả 2 đều render THẲNG
# ĐỨNG, không còn small-caps hay nghiêng giả nữa.


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


_AVATAR_CORNER_RADIUS = 22  # bo góc rõ hơn, đo lại theo phản hồi thực tế


def _offset_polygon_vertex(
    p_prev: tuple[float, float],
    p_curr: tuple[float, float],
    p_next: tuple[float, float],
    r: float,
) -> tuple[float, float]:
    """Dịch đỉnh p_curr vào TRONG đa giác một khoảng sao cho hình tròn bán
    kính r đặt tại điểm trả về tiếp tuyến với cả 2 cạnh kề đỉnh đó - dùng
    để tính tâm bo góc cho một đa giác bất kỳ (kể cả góc không vuông,
    trường hợp 2 góc trái của avatar nằm trên mép cắt chéo)."""
    import math

    def unit(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        return dx / length, dy / length

    e1 = unit(p_curr, p_prev)  # hướng dọc cạnh, từ đỉnh về phía prev
    e2 = unit(p_curr, p_next)  # hướng dọc cạnh, từ đỉnh về phía next
    bx, by = e1[0] + e2[0], e1[1] + e2[1]  # vector phân giác (chưa chuẩn hoá)
    blen = math.hypot(bx, by)
    bx, by = bx / blen, by / blen
    cos_half = max(min(e1[0] * bx + e1[1] * by, 1.0), -1.0)
    half_angle = math.acos(cos_half)
    dist = r / math.sin(half_angle) if math.sin(half_angle) > 1e-6 else r
    return p_curr[0] + bx * dist, p_curr[1] + by * dist


def _build_avatar_mask() -> Image.Image:
    """Mask hình chữ nhật mép trái cắt chéo, khớp vùng avatar bên phải card,
    với 4 góc bo tròn nhẹ giống ảnh mẫu "Tohmcord".

    Vẽ ở độ phân giải gấp đôi rồi thu nhỏ lại (supersampling) để cạnh chéo
    và các góc bo mượt, không răng cưa.

    Thuật toán "rounded arbitrary polygon" chuẩn: với mỗi đỉnh, tính tâm bo
    góc bằng cách dịch đỉnh vào trong theo hướng phân giác một khoảng vừa đủ
    để hình tròn bán kính r tiếp tuyến 2 cạnh kề - áp dụng được cho cả góc
    vuông (2 góc phải) lẫn góc lệch trên mép cắt chéo (2 góc trái)."""
    scale = 4
    r = _AVATAR_CORNER_RADIUS * scale
    W, H = _W * scale, _H * scale

    pts = [
        (_AVATAR_LEFT_TOP * scale, _AVATAR_TOP * scale),
        (_AVATAR_RIGHT * scale, _AVATAR_TOP * scale),
        (_AVATAR_RIGHT * scale, _AVATAR_BOTTOM * scale),
        (_AVATAR_LEFT_BOTTOM * scale, _AVATAR_BOTTOM * scale),
    ]
    n = len(pts)
    centers = [
        _offset_polygon_vertex(pts[(i - 1) % n], pts[i], pts[(i + 1) % n], r)
        for i in range(n)
    ]

    # Với mỗi cạnh, điểm tiếp tuyến nơi vòng tròn bo góc chạm vào cạnh đó -
    # dùng làm mép của dải viền thay vì kéo dải viền tới tận đỉnh thật (nếu
    # kéo tới đỉnh thật, dải viền tự lấp luôn phần góc vuông ra ngoài đường
    # tròn, khiến ellipse bo góc trở nên vô nghĩa vì cả 2 đều fill=255).
    def _tangent_point(edge_start: tuple[float, float], edge_end: tuple[float, float], center: tuple[float, float]) -> tuple[float, float]:
        import math
        ex, ey = edge_end[0] - edge_start[0], edge_end[1] - edge_start[1]
        elen = math.hypot(ex, ey)
        ux, uy = ex / elen, ey / elen
        # hình chiếu vector (center - edge_start) lên hướng cạnh
        proj = (center[0] - edge_start[0]) * ux + (center[1] - edge_start[1]) * uy
        return edge_start[0] + ux * proj, edge_start[1] + uy * proj

    big = Image.new("L", (W, H), 0)
    mdraw = ImageDraw.Draw(big)
    # Thân chính: polygon nối các tâm bo góc (đã thu nhỏ vào trong đúng r)
    mdraw.polygon(centers, fill=255)
    # Dải viền lấp phần giữa polygon thu nhỏ và mép thật, CHỈ tới điểm tiếp
    # tuyến của vòng tròn bo góc trên mỗi cạnh (không kéo tới tận đỉnh thật,
    # để phần góc còn lại được ellipse bo mượt đảm nhiệm).
    for i in range(n):
        p_start, p_end = pts[i], pts[(i + 1) % n]
        tangent_start = _tangent_point(p_start, p_end, centers[i])
        tangent_end = _tangent_point(p_start, p_end, centers[(i + 1) % n])
        quad = [centers[i], tangent_start, tangent_end, centers[(i + 1) % n]]
        mdraw.polygon(quad, fill=255)
    # 4 hình tròn bo góc, tâm đã tính tiếp tuyến đúng 2 cạnh kề
    for cx, cy in centers:
        mdraw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)

    return big.resize((_W, _H), Image.LANCZOS)


def _render_text(
    text: str,
    font: ImageFont.FreeTypeFont,
    color: tuple[int, int, int],
) -> Image.Image:
    """Vẽ text thẳng đứng (không nghiêng/shear) - dùng font tròn/mập
    (Poppins) trực tiếp để giữ nét chữ sạch, tránh hiệu ứng shear giả-nghiêng
    trước đây làm chữ bị méo/rỗ cạnh khi render lại kích thước khác."""
    bbox = font.getbbox(text)
    pad = 6
    tw = bbox[2] - bbox[0] + pad * 2
    th = bbox[3] - bbox[1] + pad * 2
    layer = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((pad - bbox[0], pad - bbox[1]), text, font=font, fill=color)
    ink_bbox = layer.getbbox()
    if ink_bbox:
        margin = 3
        l, t, r, b = ink_bbox
        l = max(0, l - margin)
        t = max(0, t - margin)
        r = min(layer.width, r + margin)
        b = min(layer.height, b + margin)
        layer = layer.crop((l, t, r, b))
    return layer


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

    # --- Số thứ tự: to, mờ (watermark), font Poppins Bold tròn/mập,
    # THẲNG ĐỨNG (bỏ nghiêng giả trước đây làm chữ méo) ---
    number_text = f"#{member_number}"
    number_font = _fit_text(draw, number_text, _FONT_NUMBER_PATH, text_max_width, start_size=52, min_size=30)
    number_y = 138
    number_img = _render_text(number_text, number_font, _NUMBER_GREY)
    card.alpha_composite(number_img, (_TEXT_LEFT, number_y))

    # --- Tên hiển thị: đậm, trắng, font Poppins Medium tròn/mập (đủ dấu
    # tiếng Việt qua DejaVu fallback), CĂN GIỮA, THẲNG ĐỨNG ---
    name_font, name_lines = _wrap_two_lines(draw, display_name, _FONT_BOLD_PATH, text_max_width, start_size=24, min_size=15)
    name_y = number_y + number_img.height + 10
    line_gap = 6
    for line in name_lines:
        name_img = _render_text(line, name_font, _WHITE)
        line_x = _TEXT_LEFT + max(0, (text_max_width - name_img.width) // 2)
        card.alpha_composite(name_img, (line_x, name_y))
        name_y += name_img.height + line_gap

    buf = io.BytesIO()
    # Giữ nguyên RGBA (không convert("RGB")) để Discord hiển thị đúng góc bo
    # tròn trong suốt của khung nền - convert("RGB") trước đây làm lộ màu
    # "ma" ở vùng alpha=0 (RGB cũ giữ nguyên dù trong suốt) thành nền đặc.
    card.save(buf, format="PNG")
    buf.seek(0)
    return buf, avatar_err
