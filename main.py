"""
Điểm khởi chạy. Chạy: python main.py

Các file liên quan:
- config.py        : đọc biến môi trường, cấu hình chung
- tiktok_client.py  : lấy dữ liệu (avatar, video mới, live) từ TikTok
- discord_bot.py    : Discord client + lưu trạng thái + vòng lặp kiểm tra + gửi thông báo
- web_server.py     : dashboard web (lượt xem, đánh giá sao, CPU/RAM) + health-check
- main.py (file này): ghép mọi thứ lại
"""

import asyncio

from config import DISCORD_TOKEN, DISCORD_CHANNEL_ID, log
from discord_bot import client
from web_server import start_web_server


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
