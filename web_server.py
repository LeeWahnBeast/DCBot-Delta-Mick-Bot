"""
Web server cho Render health-check + dashboard: cho phép người vào trang
đánh giá sao, xem lượt xem, và xem bot đang ngốn bao nhiêu CPU/RAM.
"""

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
<title>Delta Mick Bot Discord Bot - Dashboard</title>
<style>
  :root { color-scheme: dark; }
  body {
    margin: 0; padding: 40px 16px; min-height: 100vh; box-sizing: border-box;
    background: radial-gradient(circle at top, #1f1230, #0b0714 65%);
    color: #f3eefc; font-family: "Segoe UI", system-ui, sans-serif;
    display: flex; justify-content: center;
  }
  .card {
    width: 100%; max-width: 520px; background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08); border-radius: 18px;
    padding: 32px; backdrop-filter: blur(6px);
    box-shadow: 0 20px 60px rgba(254,44,85,0.15);
  }
  h1 { font-size: 22px; margin: 0 0 4px; background: linear-gradient(90deg,#fe2c55,#25f4ee);
       -webkit-background-clip: text; background-clip: text; color: transparent; }
  p.sub { color: #a89bc4; margin: 0 0 28px; font-size: 14px; }
  .stat-row { display: flex; gap: 12px; margin-bottom: 24px; }
  .stat {
    flex: 1; background: rgba(255,255,255,0.05); border-radius: 12px;
    padding: 14px; text-align: center;
  }
  .stat .value { font-size: 20px; font-weight: 700; }
  .stat .label { font-size: 11px; color: #a89bc4; margin-top: 4px; }
  .stars { font-size: 30px; cursor: pointer; letter-spacing: 4px; margin: 8px 0 6px; }
  .stars span { transition: transform .1s; }
  .stars span:hover { transform: scale(1.2); }
  .rating-info { font-size: 13px; color: #a89bc4; }
  .footer { margin-top: 28px; font-size: 11px; color: #6f6488; text-align: center; }
</style>
</head>
<body>
  <div class="card">
    <h1>😎Delta Mick Bot - Discord Bot</h1>
    <p class="sub">Theo dõi trạng thái bot theo thời gian thực.</p>

    <div class="stat-row">
      <div class="stat"><div class="value" id="views">-</div><div class="label">👁️ Lượt xem</div></div>
      <div class="stat"><div class="value" id="cpu">-</div><div class="label">🧠 CPU</div></div>
      <div class="stat"><div class="value" id="ram">-</div><div class="label">💾 RAM</div></div>
    </div>

    <div>
      <div class="stars" id="stars">
        <span data-v="1">☆</span><span data-v="2">☆</span><span data-v="3">☆</span><span data-v="4">☆</span><span data-v="5">☆</span>
      </div>
      <div class="rating-info" id="rating-info">Đang tải đánh giá...</div>
    </div>

    <div class="footer">Cập nhật mỗi 15 giây</div>
  </div>

<script>
  const starsEl = document.querySelectorAll('#stars span');
  const infoEl = document.getElementById('rating-info');

  function paintStars(n) {
    starsEl.forEach(s => s.textContent = Number(s.dataset.v) <= n ? '★' : '☆');
  }

  async function refreshStats() {
    try {
      const res = await fetch('/api/stats');
      const data = await res.json();
      document.getElementById('views').textContent = data.views;
      document.getElementById('cpu').textContent = data.cpu_percent.toFixed(1) + '%';
      document.getElementById('ram').textContent = data.ram_mb.toFixed(0) + ' MB';
      infoEl.textContent = data.rating_count > 0
        ? `Điểm trung bình: ${data.rating_avg} / 5 (${data.rating_count} lượt đánh giá)`
        : 'Chưa có đánh giá nào, hãy là người đầu tiên!';
      paintStars(Math.round(data.rating_avg));
    } catch (e) { console.error(e); }
  }

  starsEl.forEach(s => {
    s.addEventListener('click', async () => {
      const v = Number(s.dataset.v);
      paintStars(v);
      await fetch('/api/rate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stars: v }),
      });
      refreshStats();
    });
  });

  refreshStats();
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


async def handle_api_rate(request: web.Request):
    try:
        payload = await request.json()
        stars = int(payload.get("stars", 0))
    except Exception:
        return web.json_response({"error": "invalid payload"}, status=400)

    if not (1 <= stars <= 5):
        return web.json_response({"error": "stars phải từ 1 đến 5"}, status=400)

    response = web.json_response({"ok": True})
    voter_id = _get_or_set_voter_id(request, response)
    await db.submit_rating(voter_id, stars)
    return response


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/api/stats", handle_api_stats)
    app.router.add_post("/api/rate", handle_api_rate)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info("Web dashboard đang chạy ở port %s", PORT)
