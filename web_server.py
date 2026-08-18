"""
Web server cho Render health-check + trang "bot card" dạng dashboard tối màu,
theme riêng cho bot (bản mới, phong cách Discord/neon):
- Icon/tên bot lấy TRỰC TIẾP từ token đang đăng nhập (discord_bot.client.user),
  không hardcode -> đổi avatar/tên bot trên Discord là trang web tự cập nhật.
- Ngày tạo tài khoản bot suy ra từ Discord snowflake ID, trạng thái hoạt động
  (online/latency) lấy real-time từ gateway.
- Chủ sở hữu hiển thị cố định theo BOT_OWNER_NAME (config.py).
- Đăng nhập BẰNG DISCORD (OAuth2, thay cho Google trước đây) trước khi gửi
  đánh giá - vì cùng 1 tài khoản Discord này chính là tài khoản đang chơi
  MICK/Vé trong server, nên:
    - Đánh giá đủ 5 sao (lần đầu) -> cộng thẳng RATE_5_STAR_REWARD_MICK MICK.
    - Liên kết Discord lần đầu trên web -> cộng thẳng LINK_DISCORD_REWARD_MICK MICK.
  ID Discord (không đổi được) dùng làm khoá chống 1 người vote nhiều lần.
  - Phiên đăng nhập lưu trong cookie đã ký HMAC (không thể giả mạo nếu không
    biết WEB_SESSION_SECRET).
- Có panel "Hồ sơ" hiển thị MICK / Vé / Level của chính người đang đăng nhập,
  đồng bộ trực tiếp với dữ liệu kinh tế trong bot Discord.
"""

import hashlib
import hmac
import html
import os
import secrets
import time
import urllib.parse
import uuid

import psutil
from aiohttp import web, ClientSession

import db
import economy
from config import (
    PORT,
    log,
    BOT_OWNER_NAME,
    DISCORD_OAUTH_CLIENT_ID,
    DISCORD_OAUTH_CLIENT_SECRET,
    DISCORD_OAUTH_REDIRECT_URI,
    RATE_5_STAR_REWARD_MICK,
    LINK_DISCORD_REWARD_MICK,
)

_process = psutil.Process(os.getpid())
_process.cpu_percent(interval=None)  # lần gọi đầu luôn ra 0.0, "mồi" trước cho lần sau chính xác

VOTER_COOKIE = "mick_voter_id"
SESSION_COOKIE = "mick_discord_session"
OAUTH_STATE_COOKIE = "mick_oauth_state"

DISCORD_API = "https://discord.com/api"

# Khoá ký cookie session - PHẢI set qua biến môi trường WEB_SESSION_SECRET trên
# Render, nếu không mỗi lần restart sẽ tạo khoá mới -> mọi người bị đăng xuất.
_SESSION_SECRET = os.environ.get("WEB_SESSION_SECRET", "") or "dev-only-insecure-secret-change-me"


def _sign_session(user_id: str, username: str, avatar_url: str) -> str:
    payload = f"{user_id}|{username}|{avatar_url}"
    sig = hmac.new(_SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}|{sig}"


def _verify_session(token: str) -> dict | None:
    try:
        user_id, username, avatar_url, sig = token.rsplit("|", 3)
    except ValueError:
        return None
    expected = hmac.new(_SESSION_SECRET.encode(), f"{user_id}|{username}|{avatar_url}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    return {"id": user_id, "username": username, "avatar_url": avatar_url}


def _redirect_uri(request: web.Request) -> str:
    """Ưu tiên DISCORD_OAUTH_REDIRECT_URI (set cứng trên Render), nếu trống thì
    tự suy ra từ request hiện tại (tiện cho chạy local)."""
    if DISCORD_OAUTH_REDIRECT_URI:
        return DISCORD_OAUTH_REDIRECT_URI
    proto = request.headers.get("X-Forwarded-Proto", request.scheme)
    host = request.headers.get("X-Forwarded-Host", request.host)
    return f"{proto}://{host}/api/discord-callback"


async def _exchange_discord_code(code: str, redirect_uri: str) -> str | None:
    """Đổi authorization code lấy access_token."""
    if not (DISCORD_OAUTH_CLIENT_ID and DISCORD_OAUTH_CLIENT_SECRET):
        log.warning("Chưa set DISCORD_OAUTH_CLIENT_ID/SECRET - không thể xác minh đăng nhập Discord.")
        return None
    data = {
        "client_id": DISCORD_OAUTH_CLIENT_ID,
        "client_secret": DISCORD_OAUTH_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    try:
        async with ClientSession() as session:
            async with session.post(
                f"{DISCORD_API}/oauth2/token",
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10,
            ) as resp:
                if resp.status != 200:
                    log.warning("Đổi code lấy token Discord lỗi: HTTP %s", resp.status)
                    return None
                payload = await resp.json()
                return payload.get("access_token")
    except Exception as e:
        log.warning("Đổi code lấy token Discord lỗi: %s", e)
        return None


async def _fetch_discord_user(access_token: str) -> dict | None:
    try:
        async with ClientSession() as session:
            async with session.get(
                f"{DISCORD_API}/users/@me",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            ) as resp:
                if resp.status != 200:
                    return None
                return await resp.json()
    except Exception as e:
        log.warning("Lấy thông tin user Discord lỗi: %s", e)
        return None


def _discord_avatar_url(user: dict) -> str:
    uid, avatar = user.get("id"), user.get("avatar")
    if avatar:
        ext = "gif" if avatar.startswith("a_") else "png"
        return f"https://cdn.discordapp.com/avatars/{uid}/{avatar}.{ext}?size=128"
    # Không có avatar tuỳ chỉnh -> avatar mặc định theo discriminator/id
    default_idx = (int(uid) >> 22) % 6 if uid else 0
    return f"https://cdn.discordapp.com/embed/avatars/{default_idx}.png"


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{bot_name} · Bot Card</title>
<link rel="icon" href="data:,">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
  :root {{
    color-scheme: dark;
    --bg: #08070f; --bg-2: #0d0c1a; --surface: #141225; --surface-2: #1c1a33;
    --ink: #f3f2fb; --muted: #918cb0; --outline: #2a2748;
    --blurple: #5865f2; --cyan: #35e6c8; --violet: #b06bff; --pink: #ff5fa2;
    --online: #35e67a; --offline: #ff5f6d;
    --font-display: "Space Grotesk", sans-serif;
    --font-body: "Inter", "Segoe UI", sans-serif;
    --font-mono: "JetBrains Mono", ui-monospace, monospace;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; color: var(--ink);
    background:
      radial-gradient(600px 300px at 15% -5%, rgba(88,101,242,.35), transparent 60%),
      radial-gradient(500px 260px at 100% 0%, rgba(176,107,255,.25), transparent 55%),
      linear-gradient(180deg, var(--bg-2), var(--bg) 30%);
    font-family: var(--font-body);
    display: flex; justify-content: center;
  }}
  .wrap {{ width: 100%; max-width: 480px; padding-bottom: 60px; }}
  a {{ color: inherit; }}
  :focus-visible {{ outline: 2px solid var(--cyan); outline-offset: 2px; }}

  /* --- top tag --- */
  .topbar {{ display: flex; justify-content: space-between; align-items: center; padding: 18px 20px 4px; }}
  .tag {{
    font-family: var(--font-mono); font-size: 11px; letter-spacing: .08em; text-transform: uppercase;
    color: var(--muted); border: 1px solid var(--outline); border-radius: 100px; padding: 4px 10px;
    background: rgba(88,101,242,.08);
  }}

  /* --- hero player card --- */
  .hero {{ display: flex; flex-direction: column; align-items: center; text-align: center; padding: 20px 20px 8px; }}
  .avatar-ring {{
    width: 96px; height: 96px; border-radius: 28px; padding: 3px;
    background: conic-gradient(from 0deg, var(--blurple), var(--cyan), var(--violet), var(--pink), var(--blurple));
    animation: spin 6s linear infinite;
    box-shadow: 0 0 30px rgba(88,101,242,.35);
  }}
  @media (prefers-reduced-motion: reduce) {{ .avatar-ring {{ animation: none; }} }}
  @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
  .avatar-ring img {{
    width: 100%; height: 100%; border-radius: 25px; object-fit: cover; display: block;
    background: var(--surface-2);
  }}
  .hero h1 {{
    font-family: var(--font-display); font-weight: 700; font-size: 24px; margin: 14px 0 2px;
    background: linear-gradient(90deg, #fff, var(--cyan) 120%);
    -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent;
  }}
  .hero .dev {{ color: var(--muted); font-size: 13px; margin: 0; }}
  .status-chip {{
    display: inline-flex; align-items: center; gap: 6px; margin-top: 10px;
    font-family: var(--font-mono); font-size: 11px; padding: 5px 12px; border-radius: 100px;
    background: var(--surface-2); border: 1px solid var(--outline);
  }}
  .status-chip .dot {{ width: 7px; height: 7px; border-radius: 50%; }}
  .status-chip .dot.on {{ background: var(--online); box-shadow: 0 0 8px var(--online); }}
  .status-chip .dot.off {{ background: var(--offline); }}

  .btn-row {{ display: flex; gap: 10px; padding: 18px 20px 4px; }}
  .btn-pill {{
    flex: 1; text-align: center; padding: 11px 0; border-radius: 12px; font-size: 14px;
    font-weight: 600; cursor: pointer; border: none; font-family: var(--font-body);
    text-decoration: none; display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  }}
  .btn-pill.primary {{ background: linear-gradient(90deg, var(--blurple), var(--violet)); color: #fff; }}
  .btn-pill.outline {{ background: transparent; color: var(--ink); border: 1px solid var(--outline); }}
  .btn-pill.primary:hover {{ filter: brightness(1.1); }}
  .btn-pill.outline:hover {{ border-color: var(--cyan); color: var(--cyan); }}

  /* --- scoreboard strip --- */
  .scoreboard {{ display: flex; gap: 10px; padding: 18px 20px 4px; }}
  .score-block {{
    flex: 1; background: var(--surface); border-radius: 12px; padding: 12px 10px;
    text-align: center; position: relative; overflow: hidden;
  }}
  .score-block::before {{
    content: ""; position: absolute; top: 0; left: 0; right: 0; height: 3px;
    background: linear-gradient(90deg, var(--blurple), var(--cyan));
  }}
  .score-block .val {{ font-family: var(--font-mono); font-weight: 600; font-size: 17px; }}
  .score-block .val .star {{ color: var(--cyan); }}
  .score-block .label {{ font-size: 10px; color: var(--muted); margin-top: 4px; text-transform: uppercase; letter-spacing: .06em; }}

  /* --- panel --- */
  .panel {{ margin: 18px 20px 0; background: var(--surface); border-radius: 16px; padding: 18px; position: relative; overflow: hidden; border: 1px solid var(--outline); }}
  .panel::before {{
    content: ""; position: absolute; top: 0; left: 18px; right: 18px; height: 2px;
    background: linear-gradient(90deg, var(--blurple), transparent 70%);
  }}
  .panel h2 {{ font-family: var(--font-display); font-size: 15px; font-weight: 700; margin: 4px 0 14px; }}
  .row {{ display: flex; align-items: center; justify-content: space-between; }}
  .muted {{ color: var(--muted); font-size: 13px; }}

  .info-line {{ display: flex; justify-content: space-between; padding: 7px 0; font-size: 13px; }}
  .info-line .k {{ color: var(--muted); }}
  .info-line .v {{ font-family: var(--font-mono); }}
  .dot-online {{ color: var(--online); }}
  .dot-offline {{ color: var(--offline); }}

  /* --- profile panel --- */
  .profile-hidden {{ display: none; }}
  .profile-card {{ display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }}
  .profile-card img {{ width: 48px; height: 48px; border-radius: 14px; border: 2px solid var(--blurple); }}
  .profile-card .pname {{ font-weight: 700; font-size: 15px; }}
  .profile-card .plevel {{ font-family: var(--font-mono); font-size: 11px; color: var(--cyan); }}
  .profile-stats {{ display: flex; gap: 10px; }}
  .pstat {{ flex: 1; background: var(--surface-2); border-radius: 10px; padding: 10px; text-align: center; }}
  .pstat .n {{ font-family: var(--font-mono); font-weight: 700; font-size: 15px; }}
  .pstat .l {{ font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: .05em; margin-top: 2px; }}
  .xp-track {{ height: 8px; border-radius: 5px; background: var(--surface-2); margin-top: 12px; overflow: hidden; }}
  .xp-fill {{ height: 100%; border-radius: 5px; background: linear-gradient(90deg, var(--blurple), var(--cyan)); }}
  .xp-caption {{ font-family: var(--font-mono); font-size: 10px; color: var(--muted); margin-top: 4px; text-align: right; }}
  .reward-note {{ font-size: 11px; color: var(--cyan); margin-top: 10px; background: rgba(53,230,200,.08); border: 1px solid rgba(53,230,200,.25); border-radius: 10px; padding: 8px 10px; }}

  /* --- rating meter --- */
  .rating-overview {{ display: flex; gap: 22px; align-items: center; }}
  .rating-big {{ text-align: center; min-width: 84px; }}
  .rating-big .num {{ font-family: var(--font-display); font-size: 38px; font-weight: 700; }}
  .rating-big .stars {{ color: var(--cyan); font-size: 14px; margin: 2px 0; }}
  .rating-big .count {{ font-size: 11px; color: var(--muted); font-family: var(--font-mono); }}
  .meter {{ flex: 1; }}
  .meter-row {{ display: flex; align-items: center; gap: 8px; margin: 6px 0; }}
  .meter-row .n {{ font-family: var(--font-mono); font-size: 11px; color: var(--muted); width: 8px; }}
  .meter-row .track {{ flex: 1; height: 7px; background: var(--surface-2); border-radius: 4px; overflow: hidden; }}
  .meter-row .fill {{ height: 100%; border-radius: 4px; background: linear-gradient(90deg, var(--blurple), var(--cyan)); }}

  /* --- review form --- */
  .discord-login-btn {{
    display: flex; align-items: center; justify-content: center; gap: 8px; width: 100%;
    background: var(--blurple); color: #fff; border: none; border-radius: 12px; padding: 12px 0;
    font-weight: 700; font-size: 14px; cursor: pointer; text-decoration: none; font-family: var(--font-body);
  }}
  .discord-login-btn:hover {{ filter: brightness(1.1); }}
  .review-form {{ display: none; }}
  .review-form.active {{ display: block; }}
  .signed-in-as {{ display: flex; align-items: center; gap: 10px; margin-bottom: 14px; }}
  .signed-in-as img {{ width: 32px; height: 32px; border-radius: 10px; }}
  .signed-in-as .name {{ font-size: 13px; }}
  .signed-in-as .signout {{ margin-left: auto; font-size: 12px; color: var(--pink); cursor: pointer; }}
  .stars-picker {{ font-size: 30px; letter-spacing: 6px; margin: 4px 0 14px; user-select: none; }}
  .stars-picker span {{ cursor: pointer; opacity: .3; transition: transform .1s; color: var(--cyan); }}
  .stars-picker span.filled {{ opacity: 1; text-shadow: 0 0 10px rgba(53,230,200,.6); }}
  .stars-picker span:hover {{ transform: scale(1.15); }}
  textarea {{
    width: 100%; background: var(--surface-2); color: var(--ink);
    border: 1px solid var(--outline); border-radius: 10px; padding: 10px 12px;
    font-size: 13px; font-family: var(--font-body); resize: vertical; min-height: 64px;
  }}
  textarea:focus {{ outline: none; border-color: var(--cyan); }}
  button.submit {{
    margin-top: 12px; width: 100%; padding: 12px; border: none; border-radius: 12px;
    background: linear-gradient(90deg, var(--blurple), var(--violet)); color: #fff; font-weight: 700; font-size: 13px; cursor: pointer;
    font-family: var(--font-body);
  }}
  button.submit:disabled {{ opacity: .35; cursor: not-allowed; }}
  .form-msg {{ font-size: 12px; margin-top: 8px; min-height: 14px; }}
  .form-msg.error {{ color: var(--offline); }}
  .form-msg.ok {{ color: var(--online); }}

  /* --- reviews as chat bubbles --- */
  .review {{ display: flex; gap: 10px; padding: 12px 0; }}
  .avatar-circle {{
    width: 32px; height: 32px; border-radius: 10px; flex-shrink: 0;
    background: linear-gradient(135deg, var(--blurple), var(--pink));
    display: flex; align-items: center; justify-content: center;
    font-weight: 700; font-size: 13px; color: #fff; font-family: var(--font-display);
  }}
  .review-bubble {{
    background: var(--surface-2); border-radius: 4px 14px 14px 14px; padding: 10px 12px; flex: 1; min-width: 0;
  }}
  .review-top {{ display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }}
  .review-name {{ font-weight: 600; font-size: 13px; }}
  .review-date {{ font-size: 10px; color: var(--muted); font-family: var(--font-mono); }}
  .review-stars {{ color: var(--cyan); font-size: 11px; margin: 4px 0; }}
  .review-comment {{ font-size: 13px; color: var(--ink); line-height: 1.5; word-wrap: break-word; }}
  .empty-note {{ color: var(--muted); font-size: 13px; text-align: center; padding: 12px 0; }}

  .footer {{
    font-family: var(--font-mono); font-size: 10px; color: var(--muted); text-align: center; padding: 22px 16px 0;
    display: flex; align-items: center; justify-content: center; gap: 6px;
  }}
  .footer .pulse {{ width: 6px; height: 6px; border-radius: 50%; background: var(--online); }}
  @media (prefers-reduced-motion: no-preference) {{
    .footer .pulse {{ animation: pulse 2s ease-in-out infinite; }}
  }}
  @keyframes pulse {{ 0%,100% {{ opacity: 1; }} 50% {{ opacity: .3; }} }}
</style>
</head>
<body>
<div class="wrap">

  <div class="topbar"><span class="tag">TikTok · Discord Bot</span></div>

  <div class="hero">
    <div class="avatar-ring"><img id="app-icon" src="" alt="icon"></div>
    <h1 id="app-name">Đang tải...</h1>
    <p class="dev">bởi {owner_name}</p>
    <span class="status-chip"><span class="dot" id="status-dot"></span><span id="status-text">—</span></span>
  </div>

  <div class="btn-row">
    <button class="btn-pill outline" onclick="window.location.reload()">↻ Làm mới</button>
    <button class="btn-pill primary" onclick="document.getElementById('reviews-section').scrollIntoView({{behavior:'smooth'}})">Xem đánh giá</button>
  </div>

  <div class="scoreboard">
    <div class="score-block"><div class="val" id="qs-rating">-</div><div class="label">Đánh giá</div></div>
    <div class="score-block"><div class="val" id="qs-members">-</div><div class="label">Thành viên</div></div>
    <div class="score-block"><div class="val" id="qs-latency">-</div><div class="label">Ping</div></div>
  </div>

  <div class="panel">
    <h2>Thông tin bot</h2>
    <div class="info-line"><span class="k">Chủ sở hữu</span><span class="v">{owner_name}</span></div>
    <div class="info-line"><span class="k">Ngày tạo</span><span class="v" id="info-created">-</span></div>
    <div class="info-line"><span class="k">Đang hoạt động</span><span class="v" id="info-online">-</span></div>
    <div class="info-line"><span class="k">Server đang dùng</span><span class="v" id="info-guilds">-</span></div>
    <div class="info-line"><span class="k">Lượt xem trang</span><span class="v" id="info-views">-</span></div>
    <div class="info-line"><span class="k">CPU tiến trình</span><span class="v" id="info-cpu">-</span></div>
    <div class="info-line"><span class="k">RAM tiến trình</span><span class="v" id="info-ram">-</span></div>
  </div>

  <div class="panel profile-hidden" id="profile-panel">
    <h2>Hồ sơ của bạn</h2>
    <div class="profile-card">
      <img id="pf-avatar" src="" alt="">
      <div>
        <div class="pname" id="pf-name">-</div>
        <div class="plevel" id="pf-level">Level -</div>
      </div>
    </div>
    <div class="profile-stats">
      <div class="pstat"><div class="n" id="pf-mick">-</div><div class="l">MICK</div></div>
      <div class="pstat"><div class="n" id="pf-ve">-</div><div class="l">Vé</div></div>
    </div>
    <div class="xp-track"><div class="xp-fill" id="pf-xp-fill" style="width:0%"></div></div>
    <div class="xp-caption" id="pf-xp-caption">0/0 XP</div>
  </div>

  <div class="panel" id="reviews-section">
    <h2>Xếp hạng và đánh giá</h2>
    <div class="rating-overview">
      <div class="rating-big">
        <div class="num" id="rb-avg">-</div>
        <div class="stars" id="rb-stars">☆☆☆☆☆</div>
        <div class="count" id="rb-count">0 đánh giá</div>
      </div>
      <div class="meter" id="rb-dist"></div>
    </div>
  </div>

  <div class="panel">
    <h2>Viết đánh giá</h2>

    <div id="signed-out-view">
      <a class="discord-login-btn" href="/api/discord-login">
        <span>🔗</span> Đăng nhập với Discord
      </a>
      <p class="muted">Cần đăng nhập Discord để gửi đánh giá - tài khoản này chính là tài khoản MICK của bạn trong server.</p>
      <p class="reward-note">🎁 Liên kết Discord lần đầu: +{link_reward} MICK · Đánh giá đủ 5 sao: +{rate_reward} MICK</p>
    </div>

    <div class="review-form" id="review-form">
      <div class="signed-in-as">
        <img id="me-avatar" src="" alt="">
        <span class="name" id="me-name"></span>
        <span class="signout" id="signout-btn">Đăng xuất</span>
      </div>
      <div class="stars-picker" id="stars">
        <span data-v="1">★</span><span data-v="2">★</span><span data-v="3">★</span><span data-v="4">★</span><span data-v="5">★</span>
      </div>
      <textarea id="comment-input" maxlength="500" placeholder="Bạn thấy bot thế nào?"></textarea>
      <button class="submit" id="submit-btn" disabled>Gửi đánh giá</button>
      <div class="form-msg" id="form-msg"></div>
    </div>
  </div>

  <div class="panel">
    <h2>Đánh giá gần đây</h2>
    <div id="reviews-list"><div class="empty-note">Đang tải...</div></div>
  </div>

  <div class="footer"><span class="pulse"></span> Tự cập nhật mỗi 15 giây</div>
</div>

<script>
  const starsEl = document.querySelectorAll('#stars span');
  const commentInput = document.getElementById('comment-input');
  const submitBtn = document.getElementById('submit-btn');
  const formMsg = document.getElementById('form-msg');
  const reviewsList = document.getElementById('reviews-list');
  const signedOutView = document.getElementById('signed-out-view');
  const reviewForm = document.getElementById('review-form');
  const profilePanel = document.getElementById('profile-panel');

  let selectedStars = 0;
  let mySession = null; // {{id, username, avatar_url}}

  function paintStars(n) {{
    starsEl.forEach(s => s.classList.toggle('filled', Number(s.dataset.v) <= n));
  }}
  function updateSubmitState() {{
    submitBtn.disabled = !(selectedStars > 0 && commentInput.value.trim().length > 0);
  }}
  starsEl.forEach(s => {{
    s.addEventListener('click', () => {{ selectedStars = Number(s.dataset.v); paintStars(selectedStars); updateSubmitState(); }});
  }});
  commentInput.addEventListener('input', updateSubmitState);

  function escapeHtml(str) {{
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }}

  function timeAgo(ts) {{
    const d = new Date(ts * 1000);
    return d.toLocaleDateString('vi-VN');
  }}

  function showSignedIn() {{
    document.getElementById('me-avatar').src = mySession.avatar_url || '';
    document.getElementById('me-name').textContent = mySession.username || '';
    signedOutView.style.display = 'none';
    reviewForm.classList.add('active');
    refreshProfile();
  }}

  document.getElementById('signout-btn').addEventListener('click', async () => {{
    await fetch('/api/logout', {{ method: 'POST' }});
    mySession = null;
    signedOutView.style.display = 'block';
    reviewForm.classList.remove('active');
    profilePanel.classList.add('profile-hidden');
  }});

  async function checkSession() {{
    try {{
      const res = await fetch('/api/me');
      if (!res.ok) return;
      const data = await res.json();
      if (data.signed_in) {{ mySession = data; showSignedIn(); }}
    }} catch (e) {{}}
  }}

  async function refreshProfile() {{
    if (!mySession) return;
    try {{
      const res = await fetch('/api/profile');
      if (!res.ok) return;
      const p = await res.json();
      profilePanel.classList.remove('profile-hidden');
      document.getElementById('pf-avatar').src = mySession.avatar_url || '';
      document.getElementById('pf-name').textContent = mySession.username || '';
      document.getElementById('pf-level').textContent = 'Level ' + p.level;
      document.getElementById('pf-mick').textContent = p.mick;
      document.getElementById('pf-ve').textContent = p.ve;
      const pct = p.xp_needed ? Math.min(100, Math.round((p.xp / p.xp_needed) * 100)) : 0;
      document.getElementById('pf-xp-fill').style.width = pct + '%';
      document.getElementById('pf-xp-caption').textContent = `${{p.xp}}/${{p.xp_needed}} XP`;
    }} catch (e) {{ console.error(e); }}
  }}

  function renderDistribution(dist, total) {{
    const wrap = document.getElementById('rb-dist');
    wrap.innerHTML = [5,4,3,2,1].map(n => {{
      const count = dist[n] || 0;
      const pct = total ? Math.round((count / total) * 100) : 0;
      return `<div class="meter-row"><span class="n">${{n}}</span><div class="track"><div class="fill" style="width:${{pct}}%"></div></div></div>`;
    }}).join('');
  }}

  function renderReviews(reviews) {{
    if (!reviews.length) {{
      reviewsList.innerHTML = '<div class="empty-note">Chưa có đánh giá nào, hãy là người đầu tiên!</div>';
      return;
    }}
    reviewsList.innerHTML = reviews.map(r => {{
      const initial = (r.name || '?').trim().charAt(0).toUpperCase();
      return `
      <div class="review">
        <div class="avatar-circle">${{escapeHtml(initial)}}</div>
        <div class="review-bubble">
          <div class="review-top">
            <span class="review-name">${{escapeHtml(r.name)}}</span>
            <span class="review-date">${{timeAgo(r.ts)}}</span>
          </div>
          <div class="review-stars">${{'★'.repeat(r.stars)}}${{'☆'.repeat(5 - r.stars)}}</div>
          <div class="review-comment">${{escapeHtml(r.comment)}}</div>
        </div>
      </div>`;
    }}).join('');
  }}

  async function refreshBotInfo() {{
    try {{
      const res = await fetch('/api/bot-info');
      const data = await res.json();
      document.getElementById('app-icon').src = data.avatar_url || '';
      document.getElementById('app-name').textContent = data.name || 'Bot';
      document.getElementById('qs-members').textContent = data.member_count ? data.member_count.toLocaleString('vi-VN') : '-';
      document.getElementById('qs-latency').textContent = data.latency_ms != null ? data.latency_ms + 'ms' : '-';
      document.getElementById('status-dot').className = 'dot ' + (data.online ? 'on' : 'off');
      document.getElementById('status-text').textContent = data.online ? 'Đang hoạt động' : 'Ngoại tuyến';
      document.getElementById('info-created').textContent = data.created_at ? new Date(data.created_at).toLocaleDateString('vi-VN') : '-';
      document.getElementById('info-online').innerHTML = data.online
        ? '<span class="dot-online">● Đang hoạt động</span>' : '<span class="dot-offline">● Ngoại tuyến</span>';
      document.getElementById('info-guilds').textContent = data.guild_count ?? '-';
    }} catch (e) {{ console.error(e); }}
  }}

  async function refreshStats() {{
    try {{
      const res = await fetch('/api/stats');
      const data = await res.json();
      document.getElementById('info-views').textContent = data.views;
      document.getElementById('info-cpu').textContent = data.cpu_percent != null ? data.cpu_percent.toFixed(1) + '%' : '-';
      document.getElementById('info-ram').textContent = data.ram_mb != null ? Math.round(data.ram_mb) + ' MB' : '-';
      document.getElementById('qs-rating').innerHTML = data.rating_avg ? `${{data.rating_avg}} <span class="star">★</span>` : '- ★';
      document.getElementById('rb-avg').textContent = data.rating_avg || '0.0';
      document.getElementById('rb-count').textContent = `${{data.rating_count}} đánh giá`;
      document.getElementById('rb-stars').textContent = '★'.repeat(Math.round(data.rating_avg)) + '☆'.repeat(5 - Math.round(data.rating_avg));
    }} catch (e) {{ console.error(e); }}
  }}

  async function refreshDistribution() {{
    try {{
      const res = await fetch('/api/rating-distribution');
      const data = await res.json();
      const total = Object.values(data.distribution).reduce((a,b) => a+b, 0);
      renderDistribution(data.distribution, total);
    }} catch (e) {{ console.error(e); }}
  }}

  async function refreshReviews() {{
    try {{
      const res = await fetch('/api/reviews');
      const data = await res.json();
      renderReviews(data.reviews || []);
    }} catch (e) {{ console.error(e); }}
  }}

  submitBtn.addEventListener('click', async () => {{
    const comment = commentInput.value.trim();
    if (!selectedStars || !comment) return;
    submitBtn.disabled = true;
    try {{
      const res = await fetch('/api/rate', {{
        method: 'POST', headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{ stars: selectedStars, comment }}),
      }});
      const data = await res.json();
      if (!res.ok) {{
        formMsg.textContent = data.error || 'Có lỗi xảy ra.'; formMsg.className = 'form-msg error';
        submitBtn.disabled = false; return;
      }}
      formMsg.textContent = data.reward ? `Cảm ơn bạn! +${{data.reward}} MICK đã vào ví.` : 'Cảm ơn bạn đã đánh giá!';
      formMsg.className = 'form-msg ok';
      commentInput.value = '';
      await Promise.all([refreshStats(), refreshDistribution(), refreshReviews(), refreshProfile()]);
    }} catch (e) {{
      formMsg.textContent = 'Có lỗi xảy ra.'; formMsg.className = 'form-msg error';
    }}
    updateSubmitState();
  }});

  checkSession();
  refreshBotInfo();
  refreshStats();
  refreshDistribution();
  refreshReviews();
  setInterval(refreshBotInfo, 15000);
  setInterval(refreshStats, 15000);
</script>
</body>
</html>"""


def _get_or_set_voter_id(request: web.Request, response: web.StreamResponse) -> str:
    voter_id = request.cookies.get(VOTER_COOKIE)
    if not voter_id:
        voter_id = uuid.uuid4().hex
        response.set_cookie(VOTER_COOKIE, voter_id, max_age=3650 * 24 * 3600, httponly=True, samesite="Lax")
    return voter_id


def _get_session(request: web.Request) -> dict | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return _verify_session(token)


async def handle_index(request: web.Request):
    from discord_bot import get_bot_info

    info = get_bot_info()
    page = PAGE_TEMPLATE.format(
        bot_name=html.escape(info.get("name") or "Bot"),
        owner_name=html.escape(BOT_OWNER_NAME),
        link_reward=LINK_DISCORD_REWARD_MICK,
        rate_reward=RATE_5_STAR_REWARD_MICK,
    )
    response = web.Response(text=page, content_type="text/html")
    _get_or_set_voter_id(request, response)
    await db.increment_views()
    return response


async def handle_health(request: web.Request):
    return web.Response(text="OK - tiktok discord bot is running")


def _process_stats() -> dict:
    return {
        "cpu_percent": _process.cpu_percent(interval=None),
        "ram_mb": _process.memory_info().rss / (1024 * 1024),
    }


async def handle_api_stats(request: web.Request):
    site = await db.get_site_stats()
    site.update(_process_stats())
    return web.json_response(site)


async def handle_api_bot_info(request: web.Request):
    from discord_bot import get_bot_info

    return web.json_response(get_bot_info())


async def handle_api_rating_distribution(request: web.Request):
    dist = await db.get_rating_distribution()
    return web.json_response({"distribution": {str(k): v for k, v in dist.items()}})


async def handle_api_reviews(request: web.Request):
    reviews = await db.get_reviews()
    safe = [
        {
            "name": html.escape(r.get("name", "")),
            "stars": r.get("stars", 0),
            "comment": html.escape(r.get("comment", "")),
            "ts": r.get("ts", 0),
        }
        for r in reviews
    ]
    return web.json_response({"reviews": safe})


# ---------------------------------------------------------------------------
# Đăng nhập Discord OAuth2
# ---------------------------------------------------------------------------


async def handle_discord_login(request: web.Request):
    if not DISCORD_OAUTH_CLIENT_ID:
        return web.Response(text="Chưa cấu hình DISCORD_OAUTH_CLIENT_ID trên server.", status=500)

    state = secrets.token_urlsafe(24)
    redirect_uri = _redirect_uri(request)
    params = {
        "client_id": DISCORD_OAUTH_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "identify",
        "state": state,
        "prompt": "consent",
    }
    url = f"{DISCORD_API}/oauth2/authorize?{urllib.parse.urlencode(params)}"
    response = web.HTTPFound(url)
    response.set_cookie(OAUTH_STATE_COOKIE, state, max_age=600, httponly=True, samesite="Lax")
    return response


async def handle_discord_callback(request: web.Request):
    code = request.query.get("code")
    state = request.query.get("state")
    expected_state = request.cookies.get(OAUTH_STATE_COOKIE)

    if not code or not state or not expected_state or not secrets.compare_digest(state, expected_state):
        return web.Response(text="Đăng nhập Discord thất bại (state không khớp). Hãy thử lại từ trang chủ.", status=400)

    access_token = await _exchange_discord_code(code, _redirect_uri(request))
    if not access_token:
        return web.Response(text="Không xác minh được tài khoản Discord.", status=401)

    discord_user = await _fetch_discord_user(access_token)
    if not discord_user or not discord_user.get("id"):
        return web.Response(text="Không lấy được thông tin tài khoản Discord.", status=401)

    user_id_str = str(discord_user["id"])
    username = discord_user.get("global_name") or discord_user.get("username") or "Người dùng Discord"
    avatar_url = _discord_avatar_url(discord_user)

    # Thưởng liên kết Discord lần đầu (chỉ 1 lần/tài khoản).
    try:
        db_user = await db.get_user(int(user_id_str))
        if not db_user.get("web_link_reward_claimed"):
            await economy.add_mick(int(user_id_str), LINK_DISCORD_REWARD_MICK)
            await db.save_user(int(user_id_str), {"web_link_reward_claimed": True})
    except Exception as e:
        log.warning("Thưởng liên kết Discord lỗi: %s", e)

    token = _sign_session(user_id_str, username, avatar_url)
    response = web.HTTPFound("/")
    response.set_cookie(SESSION_COOKIE, token, max_age=30 * 24 * 3600, httponly=True, samesite="Lax")
    response.del_cookie(OAUTH_STATE_COOKIE)
    return response


async def handle_api_me(request: web.Request):
    session = _get_session(request)
    if not session:
        return web.json_response({"signed_in": False})
    return web.json_response(
        {"signed_in": True, "id": session["id"], "username": session["username"], "avatar_url": session["avatar_url"]}
    )


async def handle_api_profile(request: web.Request):
    session = _get_session(request)
    if not session:
        return web.json_response({"error": "Chưa đăng nhập"}, status=401)
    profile = await economy.get_profile(int(session["id"]))
    return web.json_response(
        {
            "mick": economy.format_mick(profile["mick"]),
            "ve": economy.format_ve(profile["ve"]),
            "level": profile["level"],
            "xp": profile["xp"],
            "xp_needed": profile["xp_needed"],
        }
    )


async def handle_api_logout(request: web.Request):
    response = web.json_response({"ok": True})
    response.del_cookie(SESSION_COOKIE)
    return response


async def handle_api_rate(request: web.Request):
    session = _get_session(request)
    if not session:
        return web.json_response({"error": "Bạn cần đăng nhập Discord trước khi đánh giá"}, status=401)

    try:
        payload = await request.json()
        stars = int(payload.get("stars", 0))
        comment = str(payload.get("comment", "")).strip()
    except Exception:
        return web.json_response({"error": "invalid payload"}, status=400)

    if not (1 <= stars <= 5):
        return web.json_response({"error": "Số sao phải từ 1 đến 5"}, status=400)
    if not comment:
        return web.json_response({"error": "Vui lòng nhập lý do / nhận xét"}, status=400)

    user_id = int(session["id"])

    # ID Discord (không đổi được) làm khoá chống 1 người vote nhiều lần - ghi
    # đè review cũ nếu vote lại (không thưởng MICK lại lần 2).
    voter_id = f"discord:{user_id}"
    await db.submit_rating(voter_id, stars, session["username"], comment)

    reward = 0
    if stars == 5:
        try:
            db_user = await db.get_user(user_id)
            if not db_user.get("web_rate5_reward_claimed"):
                await economy.add_mick(user_id, RATE_5_STAR_REWARD_MICK)
                await db.save_user(user_id, {"web_rate5_reward_claimed": True})
                reward = RATE_5_STAR_REWARD_MICK
        except Exception as e:
            log.warning("Thưởng đánh giá 5 sao lỗi: %s", e)

    return web.json_response({"ok": True, "reward": reward})


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/api/stats", handle_api_stats)
    app.router.add_get("/api/bot-info", handle_api_bot_info)
    app.router.add_get("/api/rating-distribution", handle_api_rating_distribution)
    app.router.add_get("/api/reviews", handle_api_reviews)
    app.router.add_get("/api/me", handle_api_me)
    app.router.add_get("/api/profile", handle_api_profile)
    app.router.add_get("/api/discord-login", handle_discord_login)
    app.router.add_get("/api/discord-callback", handle_discord_callback)
    app.router.add_post("/api/logout", handle_api_logout)
    app.router.add_post("/api/rate", handle_api_rate)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info("Web dashboard đang chạy ở port %s", PORT)
