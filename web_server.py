"""
Web server cho Render health-check + dashboard: cho phép người vào trang
đánh giá sao (kèm bắt buộc tên + lý do/comment), xem danh sách review, xem
lượt xem, và xem bot đang ngốn bao nhiêu CPU/RAM.

Giao diện theo phong cách Google Material You 3: dynamic-color-esque gradient
tím, bo góc lớn, "surface container" cho từng khối, chip sao bo tròn.
"""

import html
import os
import uuid

import psutil
from aiohttp import web

import db
from config import PORT, log

_process = psutil.Process(os.getpid())
_process.cpu_percent(interval=None)  # lần gọi đầu luôn ra 0.0, "mồi" trước cho lần sau chính xác

VOTER_COOKIE = "mick_voter_id"

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Delta Mick Bot - Dashboard</title>
<style>
  :root {
    color-scheme: dark;
    --seed-h: 262; /* tím Material You mặc định */
    --primary: hsl(var(--seed-h) 70% 72%);
    --primary-container: hsl(var(--seed-h) 45% 24%);
    --surface: hsl(var(--seed-h) 22% 12%);
    --surface-container: hsl(var(--seed-h) 22% 17%);
    --surface-container-high: hsl(var(--seed-h) 22% 22%);
    --on-surface: hsl(var(--seed-h) 15% 94%);
    --on-surface-variant: hsl(var(--seed-h) 12% 72%);
    --outline: hsl(var(--seed-h) 12% 32%);
    --error: hsl(6 78% 68%);
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 40px 16px 64px; min-height: 100vh;
    background: radial-gradient(circle at top, hsl(var(--seed-h) 40% 16%), var(--surface) 65%);
    color: var(--on-surface); font-family: "Segoe UI", Roboto, system-ui, sans-serif;
    display: flex; justify-content: center;
  }
  .wrap { width: 100%; max-width: 560px; display: flex; flex-direction: column; gap: 20px; }
  .card {
    background: var(--surface-container); border: 1px solid hsl(var(--seed-h) 15% 26% / 0.5);
    border-radius: 28px; padding: 28px; box-shadow: 0 20px 50px rgba(0,0,0,0.35);
  }
  h1 { font-size: 24px; margin: 0 0 4px; font-weight: 700; color: var(--on-surface); }
  p.sub { color: var(--on-surface-variant); margin: 0 0 24px; font-size: 14px; }
  .stat-row { display: flex; gap: 10px; margin-bottom: 4px; }
  .stat {
    flex: 1; background: var(--surface-container-high); border-radius: 20px;
    padding: 14px; text-align: center;
  }
  .stat .value { font-size: 19px; font-weight: 700; }
  .stat .label { font-size: 11px; color: var(--on-surface-variant); margin-top: 4px; }

  .section-title { font-size: 16px; font-weight: 700; margin: 0 0 14px; }

  .stars-picker { font-size: 34px; letter-spacing: 6px; margin: 4px 0 18px; user-select: none; }
  .stars-picker span { cursor: pointer; transition: transform .1s; opacity: .35; }
  .stars-picker span.filled { opacity: 1; }
  .stars-picker span:hover { transform: scale(1.15); }

  label { display: block; font-size: 12px; color: var(--on-surface-variant); margin: 14px 0 6px; }
  input[type=text], textarea {
    width: 100%; background: var(--surface-container-high); color: var(--on-surface);
    border: 1px solid var(--outline); border-radius: 16px; padding: 12px 14px;
    font-size: 14px; font-family: inherit; resize: vertical;
  }
  input[type=text]:focus, textarea:focus { outline: none; border-color: var(--primary); }
  textarea { min-height: 72px; }

  button.submit {
    margin-top: 18px; width: 100%; padding: 14px; border: none; border-radius: 100px;
    background: var(--primary); color: hsl(var(--seed-h) 45% 14%); font-weight: 700;
    font-size: 14px; cursor: pointer; transition: opacity .15s, transform .1s;
  }
  button.submit:disabled { opacity: .4; cursor: not-allowed; }
  button.submit:not(:disabled):hover { transform: translateY(-1px); }

  .form-msg { font-size: 12px; margin-top: 10px; min-height: 16px; }
  .form-msg.error { color: var(--error); }
  .form-msg.ok { color: hsl(140 55% 65%); }

  .review {
    background: var(--surface-container-high); border-radius: 20px; padding: 16px 18px;
    margin-bottom: 12px;
  }
  .review:last-child { margin-bottom: 0; }
  .review-top { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
  .review-name { font-weight: 700; font-size: 14px; }
  .review-stars { color: var(--primary); font-size: 13px; letter-spacing: 1px; }
  .review-comment { margin-top: 6px; font-size: 13px; color: var(--on-surface-variant); line-height: 1.5; }
  .empty-note { color: var(--on-surface-variant); font-size: 13px; text-align: center; padding: 12px 0; }

  .footer { font-size: 11px; color: var(--on-surface-variant); text-align: center; opacity: .7; }
</style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>😎 Delta Mick Bot</h1>
      <p class="sub">Theo dõi trạng thái bot theo thời gian thực.</p>
      <div class="stat-row">
        <div class="stat"><div class="value" id="views">-</div><div class="label">👁️ Lượt xem</div></div>
        <div class="stat"><div class="value" id="cpu">-</div><div class="label">🧠 CPU</div></div>
        <div class="stat"><div class="value" id="ram">-</div><div class="label">💾 RAM</div></div>
      </div>
    </div>

    <div class="card">
      <div class="section-title" id="rating-summary">Đang tải đánh giá...</div>
      <div class="stars-picker" id="stars">
        <span data-v="1">★</span><span data-v="2">★</span><span data-v="3">★</span><span data-v="4">★</span><span data-v="5">★</span>
      </div>

      <label for="name-input">Tên của bạn *</label>
      <input type="text" id="name-input" maxlength="60" placeholder="vd: Minh Anh">

      <label for="comment-input">Lý do / Nhận xét *</label>
      <textarea id="comment-input" maxlength="500" placeholder="Bạn thấy bot thế nào?"></textarea>

      <button class="submit" id="submit-btn" disabled>Gửi đánh giá</button>
      <div class="form-msg" id="form-msg"></div>
    </div>

    <div class="card">
      <div class="section-title">Đánh giá gần đây</div>
      <div id="reviews-list"><div class="empty-note">Đang tải...</div></div>
    </div>

    <div class="footer">Cập nhật mỗi 15 giây</div>
  </div>

<script>
  const starsEl = document.querySelectorAll('#stars span');
  const nameInput = document.getElementById('name-input');
  const commentInput = document.getElementById('comment-input');
  const submitBtn = document.getElementById('submit-btn');
  const formMsg = document.getElementById('form-msg');
  const reviewsList = document.getElementById('reviews-list');
  const ratingSummary = document.getElementById('rating-summary');

  let selectedStars = 0;

  function paintStars(n) {
    starsEl.forEach(s => s.classList.toggle('filled', Number(s.dataset.v) <= n));
  }

  function updateSubmitState() {
    const ready = selectedStars > 0 && nameInput.value.trim().length > 0 && commentInput.value.trim().length > 0;
    submitBtn.disabled = !ready;
  }

  starsEl.forEach(s => {
    s.addEventListener('click', () => {
      selectedStars = Number(s.dataset.v);
      paintStars(selectedStars);
      updateSubmitState();
    });
  });
  nameInput.addEventListener('input', updateSubmitState);
  commentInput.addEventListener('input', updateSubmitState);

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  function renderReviews(reviews) {
    if (!reviews.length) {
      reviewsList.innerHTML = '<div class="empty-note">Chưa có đánh giá nào, hãy là người đầu tiên!</div>';
      return;
    }
    reviewsList.innerHTML = reviews.map(r => `
      <div class="review">
        <div class="review-top">
          <div class="review-name">${escapeHtml(r.name)}</div>
          <div class="review-stars">${'★'.repeat(r.stars)}${'☆'.repeat(5 - r.stars)}</div>
        </div>
        <div class="review-comment">${escapeHtml(r.comment)}</div>
      </div>
    `).join('');
  }

  async function refreshStats() {
    try {
      const res = await fetch('/api/stats');
      const data = await res.json();
      document.getElementById('views').textContent = data.views;
      document.getElementById('cpu').textContent = data.cpu_percent.toFixed(1) + '%';
      document.getElementById('ram').textContent = data.ram_mb.toFixed(0) + ' MB';
      ratingSummary.textContent = data.rating_count > 0
        ? `⭐ ${data.rating_avg} / 5 (${data.rating_count} lượt đánh giá)`
        : 'Chưa có đánh giá nào';
    } catch (e) { console.error(e); }
  }

  async function refreshReviews() {
    try {
      const res = await fetch('/api/reviews');
      const data = await res.json();
      renderReviews(data.reviews || []);
    } catch (e) { console.error(e); }
  }

  submitBtn.addEventListener('click', async () => {
    const name = nameInput.value.trim();
    const comment = commentInput.value.trim();
    if (!selectedStars || !name || !comment) {
      formMsg.textContent = 'Vui lòng chọn sao, nhập tên và lý do.';
      formMsg.className = 'form-msg error';
      return;
    }
    submitBtn.disabled = true;
    try {
      const res = await fetch('/api/rate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stars: selectedStars, name, comment }),
      });
      const data = await res.json();
      if (!res.ok) {
        formMsg.textContent = data.error || 'Có lỗi xảy ra, thử lại sau.';
        formMsg.className = 'form-msg error';
        submitBtn.disabled = false;
        return;
      }
      formMsg.textContent = 'Cảm ơn bạn đã đánh giá!';
      formMsg.className = 'form-msg ok';
      commentInput.value = '';
      await Promise.all([refreshStats(), refreshReviews()]);
    } catch (e) {
      formMsg.textContent = 'Có lỗi xảy ra, thử lại sau.';
      formMsg.className = 'form-msg error';
    }
    updateSubmitState();
  });

  refreshStats();
  refreshReviews();
  setInterval(refreshStats, 15000);
  setInterval(refreshReviews, 15000);
</script>
</body>
</html>"""


def _get_or_set_voter_id(request: web.Request, response: web.StreamResponse) -> str:
    voter_id = request.cookies.get(VOTER_COOKIE)
    if not voter_id:
        voter_id = uuid.uuid4().hex
        response.set_cookie(VOTER_COOKIE, voter_id, max_age=3650 * 24 * 3600, httponly=True, samesite="Lax")
    return voter_id


async def handle_index(request: web.Request):
    response = web.Response(text=PAGE_TEMPLATE, content_type="text/html")
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


async def handle_api_reviews(request: web.Request):
    reviews = await db.get_reviews()
    # html.escape phòng thêm 1 lớp (front-end cũng escape) - không tin dữ liệu người dùng nhập.
    safe = [
        {"name": html.escape(r.get("name", "")), "stars": r.get("stars", 0), "comment": html.escape(r.get("comment", ""))}
        for r in reviews
    ]
    return web.json_response({"reviews": safe})


async def handle_api_rate(request: web.Request):
    try:
        payload = await request.json()
        stars = int(payload.get("stars", 0))
        name = str(payload.get("name", "")).strip()
        comment = str(payload.get("comment", "")).strip()
    except Exception:
        return web.json_response({"error": "invalid payload"}, status=400)

    if not (1 <= stars <= 5):
        return web.json_response({"error": "Số sao phải từ 1 đến 5"}, status=400)
    if not name:
        return web.json_response({"error": "Vui lòng nhập tên"}, status=400)
    if not comment:
        return web.json_response({"error": "Vui lòng nhập lý do / nhận xét"}, status=400)

    response = web.json_response({"ok": True})
    voter_id = _get_or_set_voter_id(request, response)
    await db.submit_rating(voter_id, stars, name, comment)
    return response


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/api/stats", handle_api_stats)
    app.router.add_get("/api/reviews", handle_api_reviews)
    app.router.add_post("/api/rate", handle_api_rate)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info("Web dashboard đang chạy ở port %s", PORT)
