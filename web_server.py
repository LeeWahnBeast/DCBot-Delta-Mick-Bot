"""
Web server cho Render health-check + trang "cửa hàng ứng dụng" của bot, thiết
kế mô phỏng Google Play Store:
- Icon/tên bot lấy TRỰC TIẾP từ token đang đăng nhập (discord_bot.client.user),
  không hardcode -> đổi avatar/tên bot trên Discord là trang web tự cập nhật.
- Ngày tạo tài khoản bot suy ra từ Discord snowflake ID, trạng thái hoạt động
  (online/latency) lấy real-time từ gateway.
- Chủ sở hữu hiển thị cố định theo BOT_OWNER_NAME (config.py).
- Đánh giá BẮT BUỘC đăng nhập Google (Google Identity Services) trước khi gửi
  - JWT credential được xác minh phía server qua endpoint tokeninfo của Google,
    dùng "sub" (Google user id, không đổi được) làm khoá chống vote nhiều lần,
    thay cho cookie ẩn danh (xoá cookie/ẩn danh trước đây có thể vote lại được).
  - Phiên đăng nhập lưu trong cookie đã ký HMAC (không thể giả mạo nếu không
    biết WEB_SESSION_SECRET).
"""

import hashlib
import hmac
import html
import os
import time
import uuid

import psutil
from aiohttp import web, ClientSession

import db
from config import PORT, log, BOT_OWNER_NAME, GOOGLE_OAUTH_CLIENT_ID

_process = psutil.Process(os.getpid())
_process.cpu_percent(interval=None)  # lần gọi đầu luôn ra 0.0, "mồi" trước cho lần sau chính xác

VOTER_COOKIE = "mick_voter_id"
SESSION_COOKIE = "mick_google_session"

# Khoá ký cookie session - PHẢI set qua biến môi trường WEB_SESSION_SECRET trên
# Render, nếu không mỗi lần restart sẽ tạo khoá mới -> mọi người bị đăng xuất.
_SESSION_SECRET = os.environ.get("WEB_SESSION_SECRET", "") or "dev-only-insecure-secret-change-me"


def _sign_session(sub: str, name: str, picture: str) -> str:
    payload = f"{sub}|{name}|{picture}"
    sig = hmac.new(_SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}|{sig}"


def _verify_session(token: str) -> dict | None:
    try:
        sub, name, picture, sig = token.rsplit("|", 3)
    except ValueError:
        return None
    expected = hmac.new(_SESSION_SECRET.encode(), f"{sub}|{name}|{picture}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    return {"sub": sub, "name": name, "picture": picture}


async def _verify_google_credential(credential: str) -> dict | None:
    """Xác minh JWT credential từ Google Identity Services bằng cách gọi endpoint
    tokeninfo chính thức của Google (không cần cài thư viện google-auth riêng).
    Trả về {sub, name, picture, email} nếu hợp lệ và đúng client_id, ngược lại None."""
    if not GOOGLE_OAUTH_CLIENT_ID:
        log.warning("GOOGLE_OAUTH_CLIENT_ID chưa được set - không thể xác minh đăng nhập Google.")
        return None
    try:
        async with ClientSession() as session:
            async with session.get(
                "https://oauth2.googleapis.com/tokeninfo",
                params={"id_token": credential},
                timeout=10,
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
    except Exception as e:
        log.warning("Xác minh Google id_token lỗi: %s", e)
        return None

    if data.get("aud") != GOOGLE_OAUTH_CLIENT_ID:
        log.warning("Google id_token có aud không khớp GOOGLE_OAUTH_CLIENT_ID - có thể bị giả mạo.")
        return None

    return {
        "sub": data.get("sub", ""),
        "name": data.get("name", "Người dùng Google"),
        "picture": data.get("picture", ""),
        "email": data.get("email", ""),
    }


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{bot_name} - Google Play</title>
<script src="https://accounts.google.com/gsi/client" async defer></script>
<style>
  :root {{
    color-scheme: dark;
    --bg: #0f1115; --surface: #1b1e24; --surface-2: #22262e;
    --on-surface: #e8eaed; --on-surface-variant: #9aa0a6;
    --primary: #8ab4f8; --outline: #3c4043; --star: #ffb300;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--on-surface);
    font-family: Roboto, "Segoe UI", system-ui, sans-serif;
    display: flex; justify-content: center;
  }}
  .wrap {{ width: 100%; max-width: 480px; padding-bottom: 60px; }}

  .topbar {{ display: flex; align-items: center; gap: 16px; padding: 14px 16px; }}
  .topbar .back {{ font-size: 20px; color: var(--on-surface-variant); }}

  .app-header {{ display: flex; gap: 16px; padding: 8px 16px 20px; align-items: flex-start; }}
  .app-icon {{
    width: 72px; height: 72px; border-radius: 16px; object-fit: cover; flex-shrink: 0;
    background: var(--surface-2);
  }}
  .app-meta h1 {{ font-size: 20px; margin: 0 0 4px; font-weight: 500; }}
  .app-meta .dev {{ color: var(--primary); font-size: 13px; margin: 0 0 2px; }}
  .app-meta .cat {{ color: var(--on-surface-variant); font-size: 12px; margin: 0; }}

  .btn-row {{ display: flex; gap: 10px; padding: 4px 16px 20px; }}
  .btn-pill {{
    flex: 1; text-align: center; padding: 10px 0; border-radius: 100px; font-size: 14px;
    font-weight: 600; cursor: pointer; border: none;
  }}
  .btn-pill.primary {{ background: var(--primary); color: #062e6f; }}
  .btn-pill.outline {{ background: transparent; color: var(--primary); border: 1px solid var(--outline); }}

  .quickstats {{ display: flex; justify-content: space-around; padding: 12px 16px 22px; text-align: center; border-bottom: 1px solid var(--outline); }}
  .quickstats .qs .val {{ font-size: 15px; font-weight: 600; }}
  .quickstats .qs .val .star {{ color: var(--star); }}
  .quickstats .qs .label {{ font-size: 11px; color: var(--on-surface-variant); margin-top: 2px; }}

  .section {{ padding: 20px 16px; border-bottom: 1px solid var(--outline); }}
  .section h2 {{ font-size: 16px; font-weight: 500; margin: 0 0 12px; }}
  .row {{ display: flex; align-items: center; justify-content: space-between; }}
  .muted {{ color: var(--on-surface-variant); font-size: 13px; }}

  .info-line {{ display: flex; justify-content: space-between; padding: 8px 0; font-size: 13px; }}
  .info-line .k {{ color: var(--on-surface-variant); }}
  .dot-online {{ color: #81c995; }}
  .dot-offline {{ color: #f28b82; }}

  .rating-overview {{ display: flex; gap: 24px; align-items: center; }}
  .rating-big {{ text-align: center; min-width: 90px; }}
  .rating-big .num {{ font-size: 40px; font-weight: 400; }}
  .rating-big .stars {{ color: var(--star); font-size: 14px; margin: 2px 0; }}
  .rating-big .count {{ font-size: 12px; color: var(--on-surface-variant); }}
  .dist {{ flex: 1; }}
  .dist-row {{ display: flex; align-items: center; gap: 8px; margin: 5px 0; }}
  .dist-row .n {{ font-size: 11px; color: var(--on-surface-variant); width: 10px; }}
  .dist-row .bar-track {{ flex: 1; height: 6px; background: var(--surface-2); border-radius: 4px; overflow: hidden; }}
  .dist-row .bar-fill {{ height: 100%; background: var(--on-surface-variant); border-radius: 4px; }}

  .review {{ padding: 16px 0; border-bottom: 1px solid var(--outline); }}
  .review:last-child {{ border-bottom: none; }}
  .review-top {{ display: flex; align-items: center; gap: 10px; }}
  .avatar-circle {{
    width: 34px; height: 34px; border-radius: 50%; background: var(--surface-2);
    display: flex; align-items: center; justify-content: center; font-weight: 600; font-size: 14px; flex-shrink: 0;
  }}
  .review-name {{ font-weight: 500; font-size: 13px; }}
  .review-date {{ font-size: 11px; color: var(--on-surface-variant); margin-top: 1px; }}
  .review-stars {{ color: var(--star); font-size: 12px; margin: 6px 0 6px 44px; }}
  .review-comment {{ font-size: 13px; color: var(--on-surface); margin-left: 44px; line-height: 1.5; }}
  .empty-note {{ color: var(--on-surface-variant); font-size: 13px; text-align: center; padding: 12px 0; }}

  /* --- form đánh giá --- */
  .gsi-wrap {{ margin-bottom: 14px; }}
  .review-form {{ display: none; }}
  .review-form.active {{ display: block; }}
  .signed-in-as {{ display: flex; align-items: center; gap: 10px; margin-bottom: 14px; }}
  .signed-in-as img {{ width: 32px; height: 32px; border-radius: 50%; }}
  .signed-in-as .name {{ font-size: 13px; }}
  .signed-in-as .signout {{ margin-left: auto; font-size: 12px; color: var(--primary); cursor: pointer; }}
  .stars-picker {{ font-size: 30px; letter-spacing: 6px; margin: 4px 0 14px; user-select: none; }}
  .stars-picker span {{ cursor: pointer; opacity: .3; transition: transform .1s; }}
  .stars-picker span.filled {{ opacity: 1; color: var(--star); }}
  .stars-picker span:hover {{ transform: scale(1.15); }}
  textarea {{
    width: 100%; background: var(--surface-2); color: var(--on-surface);
    border: 1px solid var(--outline); border-radius: 10px; padding: 10px 12px;
    font-size: 13px; font-family: inherit; resize: vertical; min-height: 64px;
  }}
  textarea:focus {{ outline: none; border-color: var(--primary); }}
  button.submit {{
    margin-top: 12px; width: 100%; padding: 12px; border: none; border-radius: 100px;
    background: var(--primary); color: #062e6f; font-weight: 600; font-size: 13px; cursor: pointer;
  }}
  button.submit:disabled {{ opacity: .4; cursor: not-allowed; }}
  .form-msg {{ font-size: 12px; margin-top: 8px; min-height: 14px; }}
  .form-msg.error {{ color: #f28b82; }}
  .form-msg.ok {{ color: #81c995; }}

  .footer {{ font-size: 11px; color: var(--on-surface-variant); text-align: center; padding: 20px 16px 0; }}
</style>
</head>
<body>
<div class="wrap">

  <div class="topbar"><span class="back">&larr;</span></div>

  <div class="app-header">
    <img class="app-icon" id="app-icon" src="" alt="icon">
    <div class="app-meta">
      <h1 id="app-name">Đang tải...</h1>
      <p class="dev">{owner_name}</p>
      <p class="cat">Discord Bot</p>
    </div>
  </div>

  <div class="btn-row">
    <button class="btn-pill outline" onclick="window.location.reload()">Làm mới</button>
    <button class="btn-pill primary" onclick="document.getElementById('reviews-section').scrollIntoView({{behavior:'smooth'}})">Xem đánh giá</button>
  </div>

  <div class="quickstats">
    <div class="qs"><div class="val" id="qs-rating">-</div><div class="label">Đánh giá</div></div>
    <div class="qs"><div class="val" id="qs-members">-</div><div class="label">Thành viên</div></div>
    <div class="qs"><div class="val" id="qs-status">-</div><div class="label">Trạng thái</div></div>
  </div>

  <div class="section">
    <h2>Thông tin bot</h2>
    <div class="info-line"><span class="k">Chủ sở hữu</span><span>{owner_name}</span></div>
    <div class="info-line"><span class="k">Ngày tạo</span><span id="info-created">-</span></div>
    <div class="info-line"><span class="k">Đang hoạt động</span><span id="info-online">-</span></div>
    <div class="info-line"><span class="k">Độ trễ (ping)</span><span id="info-latency">-</span></div>
    <div class="info-line"><span class="k">Server đang dùng</span><span id="info-guilds">-</span></div>
    <div class="info-line"><span class="k">Lượt xem trang</span><span id="info-views">-</span></div>
  </div>

  <div class="section" id="reviews-section">
    <h2>Xếp hạng và đánh giá</h2>
    <div class="rating-overview">
      <div class="rating-big">
        <div class="num" id="rb-avg">-</div>
        <div class="stars" id="rb-stars">☆☆☆☆☆</div>
        <div class="count" id="rb-count">0 đánh giá</div>
      </div>
      <div class="dist" id="rb-dist"></div>
    </div>
  </div>

  <div class="section">
    <h2>Viết đánh giá</h2>

    <div id="signed-out-view">
      <div class="gsi-wrap">
        <div id="g_id_onload"
          data-client_id="{google_client_id}"
          data-callback="handleGoogleCredential"
          data-auto_prompt="false">
        </div>
        <div class="g_id_signin" data-type="standard" data-shape="pill" data-theme="filled_blue" data-text="signin_with" data-size="large"></div>
      </div>
      <p class="muted">Cần đăng nhập Google để gửi đánh giá (chống spam vote ảo).</p>
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

  <div class="section" style="border-bottom:none;">
    <div id="reviews-list"><div class="empty-note">Đang tải...</div></div>
  </div>

  <div class="footer">Cập nhật mỗi 15 giây</div>
</div>

<script>
  const starsEl = document.querySelectorAll('#stars span');
  const commentInput = document.getElementById('comment-input');
  const submitBtn = document.getElementById('submit-btn');
  const formMsg = document.getElementById('form-msg');
  const reviewsList = document.getElementById('reviews-list');
  const signedOutView = document.getElementById('signed-out-view');
  const reviewForm = document.getElementById('review-form');

  let selectedStars = 0;
  let mySession = null; // {{name, picture}}

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

  async function handleGoogleCredential(response) {{
    formMsg.textContent = '';
    try {{
      const res = await fetch('/api/verify-google', {{
        method: 'POST', headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{ credential: response.credential }}),
      }});
      const data = await res.json();
      if (!res.ok) {{ formMsg.textContent = data.error || 'Đăng nhập thất bại.'; formMsg.className = 'form-msg error'; return; }}
      mySession = data;
      showSignedIn();
    }} catch (e) {{ formMsg.textContent = 'Đăng nhập thất bại, thử lại sau.'; formMsg.className = 'form-msg error'; }}
  }}

  function showSignedIn() {{
    document.getElementById('me-avatar').src = mySession.picture || '';
    document.getElementById('me-name').textContent = mySession.name || '';
    signedOutView.style.display = 'none';
    reviewForm.classList.add('active');
  }}

  document.getElementById('signout-btn').addEventListener('click', async () => {{
    await fetch('/api/logout-google', {{ method: 'POST' }});
    mySession = null;
    signedOutView.style.display = 'block';
    reviewForm.classList.remove('active');
  }});

  async function checkSession() {{
    try {{
      const res = await fetch('/api/me');
      if (!res.ok) return;
      const data = await res.json();
      if (data.signed_in) {{ mySession = data; showSignedIn(); }}
    }} catch (e) {{}}
  }}

  function renderDistribution(dist, total) {{
    const wrap = document.getElementById('rb-dist');
    wrap.innerHTML = [5,4,3,2,1].map(n => {{
      const count = dist[n] || 0;
      const pct = total ? Math.round((count / total) * 100) : 0;
      return `<div class="dist-row"><span class="n">${{n}}</span><div class="bar-track"><div class="bar-fill" style="width:${{pct}}%"></div></div></div>`;
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
        <div class="review-top">
          <div class="avatar-circle">${{escapeHtml(initial)}}</div>
          <div>
            <div class="review-name">${{escapeHtml(r.name)}}</div>
            <div class="review-date">${{timeAgo(r.ts)}}</div>
          </div>
        </div>
        <div class="review-stars">${{'★'.repeat(r.stars)}}${{'☆'.repeat(5 - r.stars)}}</div>
        <div class="review-comment">${{escapeHtml(r.comment)}}</div>
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
      document.getElementById('qs-status').textContent = data.online ? 'Hoạt động' : 'Ngoại tuyến';
      document.getElementById('info-created').textContent = data.created_at ? new Date(data.created_at).toLocaleDateString('vi-VN') : '-';
      document.getElementById('info-online').innerHTML = data.online
        ? '<span class="dot-online">● Đang hoạt động</span>' : '<span class="dot-offline">● Ngoại tuyến</span>';
      document.getElementById('info-latency').textContent = data.latency_ms != null ? data.latency_ms + ' ms' : '-';
      document.getElementById('info-guilds').textContent = data.guild_count ?? '-';
    }} catch (e) {{ console.error(e); }}
  }}

  async function refreshStats() {{
    try {{
      const res = await fetch('/api/stats');
      const data = await res.json();
      document.getElementById('info-views').textContent = data.views;
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
      formMsg.textContent = 'Cảm ơn bạn đã đánh giá!'; formMsg.className = 'form-msg ok';
      commentInput.value = '';
      await Promise.all([refreshStats(), refreshDistribution(), refreshReviews()]);
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
        google_client_id=html.escape(GOOGLE_OAUTH_CLIENT_ID),
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


async def handle_api_verify_google(request: web.Request):
    try:
        payload = await request.json()
        credential = str(payload.get("credential", ""))
    except Exception:
        return web.json_response({"error": "invalid payload"}, status=400)

    if not credential:
        return web.json_response({"error": "Thiếu credential"}, status=400)

    profile = await _verify_google_credential(credential)
    if profile is None:
        return web.json_response({"error": "Không xác minh được tài khoản Google"}, status=401)

    response = web.json_response(
        {"signed_in": True, "name": profile["name"], "picture": profile["picture"]}
    )
    token = _sign_session(profile["sub"], profile["name"], profile["picture"])
    response.set_cookie(SESSION_COOKIE, token, max_age=30 * 24 * 3600, httponly=True, samesite="Lax")
    return response


async def handle_api_me(request: web.Request):
    session = _get_session(request)
    if not session:
        return web.json_response({"signed_in": False})
    return web.json_response({"signed_in": True, "name": session["name"], "picture": session["picture"]})


async def handle_api_logout_google(request: web.Request):
    response = web.json_response({"ok": True})
    response.del_cookie(SESSION_COOKIE)
    return response


async def handle_api_rate(request: web.Request):
    session = _get_session(request)
    if not session:
        return web.json_response({"error": "Bạn cần đăng nhập Google trước khi đánh giá"}, status=401)

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

    # sub của Google (không đổi được, không tạo lại bằng ẩn danh/xoá cookie) làm
    # khoá chống 1 người vote nhiều lần - ghi đè review cũ nếu vote lại.
    voter_id = f"google:{session['sub']}"
    await db.submit_rating(voter_id, stars, session["name"], comment)
    return web.json_response({"ok": True})


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/api/stats", handle_api_stats)
    app.router.add_get("/api/bot-info", handle_api_bot_info)
    app.router.add_get("/api/rating-distribution", handle_api_rating_distribution)
    app.router.add_get("/api/reviews", handle_api_reviews)
    app.router.add_get("/api/me", handle_api_me)
    app.router.add_post("/api/verify-google", handle_api_verify_google)
    app.router.add_post("/api/logout-google", handle_api_logout_google)
    app.router.add_post("/api/rate", handle_api_rate)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info("Web dashboard đang chạy ở port %s", PORT)
