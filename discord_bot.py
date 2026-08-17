"""
Discord Client + vòng lặp định kỳ kiểm tra TikTok.
Bao gồm luôn phần lưu/đọc trạng thái đã thông báo lần gần nhất (tránh spam trùng).

Lưu ý: trên Render free, ổ đĩa là ephemeral -> file DATA_FILE có thể mất khi
service restart hoặc deploy lại.
"""

import json
import os
import time
from datetime import datetime, timezone

import aiohttp
import discord
from discord.ext import tasks

from config import (
    DISCORD_CHANNEL_ID,
    DISCORD_GUILD_ID,
    TIKTOK_USERNAME,
    NOTIFY_MENTION,
    CHECK_INTERVAL_SEC,
    DATA_FILE,
    log,
)
from tiktok_client import fetch_tiktok_profile, download_bytes

# ---------------------------------------------------------------------------
# Lưu / đọc trạng thái
# ---------------------------------------------------------------------------


def load_state() -> dict:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            log.warning("Không đọc được %s, dùng state rỗng.", DATA_FILE)
    return {}


def save_state(state: dict) -> None:
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning("Không lưu được state: %s", e)


# ---------------------------------------------------------------------------
# Discord Bot
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
client = discord.Client(intents=intents)

state = load_state()
http_session: aiohttp.ClientSession | None = None


@client.event
async def on_ready():
    log.info("Đã đăng nhập với tài khoản %s (id=%s)", client.user, client.user.id)
    if not check_tiktok_loop.is_running():
        check_tiktok_loop.start()


@tasks.loop(seconds=CHECK_INTERVAL_SEC)
async def check_tiktok_loop():
    global http_session, state

    if http_session is None:
        http_session = aiohttp.ClientSession()

    profile = await fetch_tiktok_profile(http_session, TIKTOK_USERNAME)
    if profile is None:
        return

    channel = await _get_channel()

    await _handle_new_video(profile, channel)
    await _handle_live_status(profile, channel)
    await _handle_avatar_change(profile, channel)


async def _get_channel():
    channel = client.get_channel(DISCORD_CHANNEL_ID)
    if channel is None:
        try:
            channel = await client.fetch_channel(DISCORD_CHANNEL_ID)
        except Exception:
            log.warning("Không tìm thấy kênh Discord với id=%s", DISCORD_CHANNEL_ID)
            channel = None
    return channel


def _mention_prefix() -> str:
    return f"{NOTIFY_MENTION} " if NOTIFY_MENTION else ""


async def _handle_new_video(profile: dict, channel):
    global state

    latest_id = profile["latest_video_id"]
    if not latest_id or latest_id == state.get("last_video_id"):
        return

    is_first_run = state.get("last_video_id") is None
    state["last_video_id"] = latest_id
    save_state(state)

    if channel is None or is_first_run:
        return

    video_url = f"https://www.tiktok.com/@{TIKTOK_USERNAME}/video/{latest_id}"
    detected_at = int(time.time())
    embed = discord.Embed(
        title=f"🎬 {profile['nickname']} vừa đăng video TikTok mới!",
        url=video_url,
        description=video_url,
        color=discord.Color.from_rgb(254, 44, 85),
        timestamp=datetime.now(timezone.utc),
    )
    post_time = profile.get("latest_video_create_time")
    if post_time:
        embed.add_field(name="Đăng lúc", value=f"<t:{post_time}:F> (<t:{post_time}:R>)", inline=False)
    embed.add_field(name="Bot phát hiện lúc", value=f"<t:{detected_at}:R>", inline=False)
    if profile.get("avatar_url"):
        embed.set_thumbnail(url=profile["avatar_url"])

    try:
        await channel.send(content=f"{_mention_prefix()}📢 Có video mới từ @{TIKTOK_USERNAME}!", embed=embed)
    except Exception as e:
        log.warning("Gửi thông báo video lỗi: %s", e)


async def _handle_live_status(profile: dict, channel):
    global state

    was_live = state.get("was_live", False)
    is_live = profile["is_live"]

    if is_live and not was_live:
        state["was_live"] = True
        save_state(state)

        if channel is not None:
            live_url = f"https://www.tiktok.com/@{TIKTOK_USERNAME}/live"
            detected_at = int(time.time())
            embed = discord.Embed(
                title=f"🔴 {profile['nickname']} đang LIVESTREAM trên TikTok!",
                url=live_url,
                description=live_url,
                color=discord.Color.red(),
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(name="Bot phát hiện lúc", value=f"<t:{detected_at}:R>", inline=False)
            if profile.get("avatar_url"):
                embed.set_thumbnail(url=profile["avatar_url"])
            try:
                await channel.send(
                    content=f"{_mention_prefix()}🔴 @{TIKTOK_USERNAME} đang livestream, vào xem ngay!",
                    embed=embed,
                )
            except Exception as e:
                log.warning("Gửi thông báo live lỗi: %s", e)

    elif not is_live and was_live:
        state["was_live"] = False
        save_state(state)


async def _handle_avatar_change(profile: dict, channel):
    global state, http_session

    avatar_url = profile.get("avatar_url")
    if not avatar_url or avatar_url == state.get("last_avatar_url"):
        return

    is_first_run = state.get("last_avatar_url") is None
    img_bytes = await download_bytes(http_session, avatar_url)
    if not img_bytes:
        return

    state["last_avatar_url"] = avatar_url
    save_state(state)

    guild = client.get_guild(DISCORD_GUILD_ID)
    if guild is None:
        log.warning("Không tìm thấy guild id=%s để đổi icon.", DISCORD_GUILD_ID)
        return

    try:
        await guild.edit(icon=img_bytes, reason=f"Đồng bộ avatar theo TikTok @{TIKTOK_USERNAME}")
        log.info("Đã đổi icon server theo avatar TikTok mới.")
        if channel is not None and not is_first_run:
            await channel.send(f"🖼️ Đã cập nhật icon server theo avatar mới của @{TIKTOK_USERNAME}.")
    except discord.Forbidden:
        log.warning("Bot không có quyền 'Manage Server' để đổi icon.")
    except Exception as e:
        log.warning("Đổi icon server lỗi: %s", e)


@check_tiktok_loop.before_loop
async def before_check_tiktok_loop():
    await client.wait_until_ready()
