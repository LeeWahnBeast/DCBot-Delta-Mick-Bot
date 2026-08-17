"""
Điểm khởi chạy. Chạy: python main.py

Các file liên quan:
- config.py        : đọc biến môi trường, cấu hình chung
- tiktok_client.py  : lấy dữ liệu (avatar, video mới, live) từ TikTok
- discord_bot.py    : Discord client + lưu trạng thái + vòng lặp kiểm tra + gửi thông báo
- main.py (file này): ghép mọi thứ lại + web server nhỏ cho Render health-check
"""

import asyncio

from aiohttp import web

from config import DISCORD_TOKEN, DISCORD_CHANNEL_ID, PORT, log
from discord_bot import client


async def handle_health(request):
    return web.Response(text="OK - tiktok discord bot is running")


async def start_web_server():
    """Web server tối giản để Render coi service là 'healthy' (cần bind PORT)."""
    app = web.Application()
    app.router.add_get("/", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info("Web server (health check) đang chạy ở port %s", PORT)


async def main():
    if not DISCORD_TOKEN:
        raise RuntimeError("Thiếu biến môi trường DISCORD_TOKEN")
    if not DISCORD_CHANNEL_ID:
        raise RuntimeError("Thiếu biến môi trường DISCORD_CHANNEL_ID")

    await start_web_server()
    async with client:
        await client.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
