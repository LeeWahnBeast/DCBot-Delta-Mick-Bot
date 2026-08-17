"""
Discord Client: thông báo TikTok (video/live, có retry nếu gửi lỗi), đồng bộ
avatar bot + icon/tên server mỗi 5 tiếng, hệ thống MICK + Level (XP theo tin
nhắn), Daily hàng ngày (0h-7h giờ VN), và 2 minigame (úp ly, wordle).

Toàn bộ dữ liệu bền vững (video đã báo, level/MICK, mốc Daily...) lưu ở
Firestore qua module db.py, không còn phụ thuộc file JSON local (ổ đĩa Render
free là ephemeral, dễ mất dữ liệu khi redeploy).
"""

import asyncio
import random
import time
from datetime import datetime, timezone

import discord
from discord.ext import tasks

from config import (
    DISCORD_CHANNEL_ID,
    DISCORD_GUILD_ID,
    TIKTOK_USERNAME,
    NOTIFY_MENTION,
    CHECK_INTERVAL_SEC,
    IDENTITY_SYNC_INTERVAL_SEC,
    GUILD_NAME_TEMPLATE,
    DAILY_CHANNEL_ID,
    XP_MIN_PER_MESSAGE,
    XP_MAX_PER_MESSAGE,
    XP_MESSAGE_COOLDOWN_SEC,
    log,
)
from tiktok_client import TikTokClient
import db
import economy
import daily
import games

# ---------------------------------------------------------------------------
# Discord Bot
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True  # cần để đọc nội dung tin nhắn (XP + đoán Wordle)
client = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(client)

tiktok = TikTokClient()
_last_xp_ts: dict[int, float] = {}
_synced = False


@client.event
async def on_ready():
    global _synced
    log.info("Đã đăng nhập với tài khoản %s (id=%s)", client.user, client.user.id)

    client.add_view(daily.DailyClaimView())  # persistent view, sống sót qua restart

    if not _synced:
        await tree.sync()
        _synced = True

    if not check_tiktok_loop.is_running():
        check_tiktok_loop.start()
    if not sync_identity_loop.is_running():
        sync_identity_loop.start()
    if not daily_loop.is_running():
        daily_loop.start()


def _mention_prefix() -> str:
    return f"{NOTIFY_MENTION} " if NOTIFY_MENTION else ""


async def _get_channel(channel_id: int = DISCORD_CHANNEL_ID):
    channel = client.get_channel(channel_id)
    if channel is None:
        try:
            channel = await client.fetch_channel(channel_id)
        except Exception:
            log.warning("Không tìm thấy kênh Discord với id=%s", channel_id)
            channel = None
    return channel


# ---------------------------------------------------------------------------
# Vòng lặp 1: video mới (có retry nếu gửi lỗi) + trạng thái live
# ---------------------------------------------------------------------------


@tasks.loop(seconds=CHECK_INTERVAL_SEC)
async def check_tiktok_loop():
    profile = await tiktok.fetch_profile(TIKTOK_USERNAME)
    if profile is None:
        return

    channel = await _get_channel()
    await _handle_new_video(profile, channel)
    await _retry_unnotified_videos(channel)
    await _handle_live_status(profile, channel)


def _video_embed(video_id: str, profile: dict | None, is_retry: bool) -> discord.Embed:
    video_url = f"https://www.tiktok.com/@{TIKTOK_USERNAME}/video/{video_id}"
    nickname = profile["nickname"] if profile else TIKTOK_USERNAME
    title = f"🎬 {nickname} vừa đăng video TikTok mới!" if not is_retry else f"🎬 Video từ {nickname} (gửi lại)"
    embed = discord.Embed(
        title=title,
        url=video_url,
        description=video_url,
        color=discord.Color.from_rgb(254, 44, 85),
        timestamp=datetime.now(timezone.utc),
    )
    if profile and profile.get("avatar_url"):
        embed.set_thumbnail(url=profile["avatar_url"])
    return embed


async def _handle_new_video(profile: dict, channel):
    latest_id = profile["latest_video_id"]
    if not latest_id:
        return

    bot_state = await db.get_bot_state()
    if latest_id == bot_state.get("last_video_id"):
        return  # không có video mới

    is_first_run = not bot_state.get("last_video_id")
    await db.save_bot_state({"last_video_id": latest_id})

    existing = await db.get_video(latest_id)
    if not existing:
        await db.save_video(
            latest_id,
            {
                "notified": False,
                "username": TIKTOK_USERNAME,
                "create_time": profile.get("latest_video_create_time"),
                "first_seen_at": int(time.time()),
            },
        )

    if is_first_run:
        # Deploy lần đầu -> chỉ đánh dấu đã biết, không ping video cũ.
        await db.save_video(latest_id, {"notified": True})
        return

    if channel is None:
        return  # notified vẫn False -> vòng lặp sau sẽ retry

    try:
        await channel.send(
            content=f"{_mention_prefix()}📢 Có video mới từ @{TIKTOK_USERNAME}!",
            embed=_video_embed(latest_id, profile, is_retry=False),
        )
        await db.save_video(latest_id, {"notified": True})
    except Exception as e:
        log.warning("Gửi thông báo video lỗi (sẽ thử lại): %s", e)


async def _retry_unnotified_videos(channel):
    """Video nào chưa notified=True (do gửi lỗi/bot restart giữa chừng) thì gọi lại + ping lại."""
    if channel is None:
        return

    for video_id in await db.get_unnotified_video_ids():
        try:
            await channel.send(
                content=f"{_mention_prefix()}📢 Video từ @{TIKTOK_USERNAME} (gửi lại)!",
                embed=_video_embed(video_id, None, is_retry=True),
            )
            await db.save_video(video_id, {"notified": True})
        except Exception as e:
            log.warning("Retry gửi video %s vẫn lỗi: %s", video_id, e)


async def _handle_live_status(profile: dict, channel):
    bot_state = await db.get_bot_state()
    was_live = bot_state.get("was_live", False)
    is_live = profile["is_live"]

    if is_live and not was_live:
        await db.save_bot_state({"was_live": True})
        if channel is not None:
            live_url = f"https://www.tiktok.com/@{TIKTOK_USERNAME}/live"
            embed = discord.Embed(
                title=f"🔴 {profile['nickname']} đang LIVESTREAM trên TikTok!",
                url=live_url,
                description=live_url,
                color=discord.Color.red(),
                timestamp=datetime.now(timezone.utc),
            )
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
        await db.save_bot_state({"was_live": False})


@check_tiktok_loop.before_loop
async def before_check_tiktok_loop():
    await client.wait_until_ready()


# ---------------------------------------------------------------------------
# Vòng lặp 2: đổi avatar bot + icon/tên server theo TikTok, mỗi 5 tiếng
# ---------------------------------------------------------------------------


@tasks.loop(seconds=IDENTITY_SYNC_INTERVAL_SEC)
async def sync_identity_loop():
    profile = await tiktok.fetch_profile(TIKTOK_USERNAME)
    if profile is None or not profile.get("avatar_url"):
        return

    bot_state = await db.get_bot_state()
    if profile["avatar_url"] == bot_state.get("last_identity_avatar_url"):
        return

    img_bytes = await tiktok.download_bytes(profile["avatar_url"])
    if not img_bytes:
        return

    await _sync_bot_avatar(img_bytes)
    await _sync_guild_identity(profile, img_bytes)
    await db.save_bot_state({"last_identity_avatar_url": profile["avatar_url"]})


async def _sync_bot_avatar(img_bytes: bytes):
    try:
        await client.user.edit(avatar=img_bytes)
        log.info("Đã đổi avatar bot theo TikTok @%s.", TIKTOK_USERNAME)
    except discord.HTTPException as e:
        log.warning("Đổi avatar bot lỗi (có thể đang bị rate-limit): %s", e)
    except Exception as e:
        log.warning("Đổi avatar bot lỗi: %s", e)


async def _sync_guild_identity(profile: dict, img_bytes: bytes):
    guild = client.get_guild(DISCORD_GUILD_ID)
    if guild is None:
        log.warning("Không tìm thấy guild id=%s để đồng bộ.", DISCORD_GUILD_ID)
        return

    new_name = GUILD_NAME_TEMPLATE.format(nickname=profile["nickname"], username=profile["username"])
    try:
        await guild.edit(icon=img_bytes, name=new_name, reason=f"Đồng bộ theo TikTok @{TIKTOK_USERNAME}")
        log.info("Đã đổi icon + tên server theo TikTok @%s.", TIKTOK_USERNAME)
    except discord.Forbidden:
        log.warning("Bot không có quyền 'Manage Server' để đổi icon/tên.")
    except Exception as e:
        log.warning("Đổi icon/tên server lỗi: %s", e)


@sync_identity_loop.before_loop
async def before_sync_identity_loop():
    await client.wait_until_ready()


# ---------------------------------------------------------------------------
# Vòng lặp 3: Daily - kiểm tra mỗi phút, tự đăng lúc đúng 0h giờ VN
# ---------------------------------------------------------------------------


@tasks.loop(seconds=60)
async def daily_loop():
    await daily.maybe_post_daily(client, DAILY_CHANNEL_ID)


@daily_loop.before_loop
async def before_daily_loop():
    await client.wait_until_ready()


# ---------------------------------------------------------------------------
# XP theo tin nhắn + đoán Wordle qua tin nhắn thường
# ---------------------------------------------------------------------------


@client.event
async def on_message(message: discord.Message):
    if message.author.bot or message.guild is None:
        return

    content = message.content.strip()

    if games.has_active_wordle(message.author.id) and games.is_valid_guess(content):
        embed, _finished = await games.process_guess(message.author.id, content)
        try:
            await message.reply(embed=embed, mention_author=False)
        except Exception as e:
            log.warning("Gửi kết quả Wordle lỗi: %s", e)
        return  # không cộng XP cho tin nhắn dùng để đoán Wordle

    _maybe_grant_xp(message)


def _maybe_grant_xp(message: discord.Message):
    now = time.time()
    last = _last_xp_ts.get(message.author.id, 0)
    if now - last < XP_MESSAGE_COOLDOWN_SEC:
        return
    _last_xp_ts[message.author.id] = now
    asyncio.create_task(_apply_xp_gain(message))


async def _apply_xp_gain(message: discord.Message):
    amount = random.randint(XP_MIN_PER_MESSAGE, XP_MAX_PER_MESSAGE)
    try:
        result = await economy.add_xp(message.author.id, amount)
    except Exception as e:
        log.warning("Cộng XP lỗi: %s", e)
        return

    if result["levels_gained"] > 0:
        try:
            await message.channel.send(
                f"🎉 {message.author.mention} đã lên **Level {result['level']}**! "
                f"Nhận **{result['mick_awarded']} MICK**."
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------


@tree.command(name="profile", description="Xem MICK, level và XP của bạn")
async def profile_cmd(interaction: discord.Interaction):
    data = await economy.get_profile(interaction.user.id)
    embed = discord.Embed(title=f"📊 Hồ sơ của {interaction.user.display_name}", color=discord.Color.blurple())
    embed.add_field(name="MICK", value=f"{data['mick']} 🪙", inline=True)
    embed.add_field(name="Level", value=str(data["level"]), inline=True)
    embed.add_field(name="XP", value=f"{data['xp']}/{data['xp_needed']}", inline=True)
    embed.set_thumbnail(url=interaction.user.display_avatar.url)
    await interaction.response.send_message(embed=embed)


@tree.command(name="cup", description="Chơi úp ly chọn kẹo, đoán đúng nhận MICK")
async def cup_cmd(interaction: discord.Interaction):
    view = games.CupGameView(owner_id=interaction.user.id)
    await interaction.response.send_message(embed=games.build_cup_game_embed(), view=view)


@tree.command(name="wordle", description="Chơi Wordle, đoán đúng từ 5 chữ trong 6 lượt để nhận MICK")
async def wordle_cmd(interaction: discord.Interaction):
    if games.has_active_wordle(interaction.user.id):
        await interaction.response.send_message(
            "Bạn đang có ván Wordle chưa xong! Gõ 1 từ 5 chữ vào kênh để đoán tiếp.", ephemeral=True
        )
        return
    embed = games.start_wordle(interaction.user.id)
    await interaction.response.send_message(embed=embed)
