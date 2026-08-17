"""
Discord Client: thông báo TikTok (video/live, có retry nếu gửi lỗi), đồng bộ
avatar bot + icon/tên server mỗi 5 tiếng, hệ thống MICK + Level (XP theo tin
nhắn VÀ theo voice chat), Daily hàng ngày (0h-7h giờ VN), 2 minigame (úp ly,
wordle), thành tựu, quest hằng ngày, kinh doanh, ATM, chuyển MICK, AI chat.

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
    VOICE_XP_TICK_SEC,
    VOICE_XP_MIN_PER_TICK,
    VOICE_XP_MAX_PER_TICK,
    VOICE_XP_MIN_MEMBERS,
    AI_CHAT_CHANNEL_ID,
    AI_AUTO_CHAT_INTERVAL_SEC,
    AI_AUTO_CHAT_REQUIRE_ACTIVITY_SEC,
    BUSINESS_TICK_SEC,
    MICKCOIN_EMOJI,
    log,
)
from tiktok_client import TikTokClient
import db
import economy
import daily
import games
import achievements
import quests
import business
import ai_chat
import level_card

# ---------------------------------------------------------------------------
# Discord Bot
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True  # cần để đọc nội dung tin nhắn (XP + đoán Wordle)
intents.voice_states = True  # cần để biết ai đang trong voice channel (XP voice chat)
intents.members = True  # cần để duyệt danh sách thành viên trong voice channel
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
    if not business_tick_loop.is_running():
        business_tick_loop.start()
    if not ai_auto_chat_loop.is_running():
        ai_auto_chat_loop.start()
    if not voice_xp_loop.is_running():
        voice_xp_loop.start()


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
# Vòng lặp 4: Business tick - trả thu nhập cho mọi cơ sở đang có nhân viên,
# kể cả chủ đang offline (chạy nền độc lập với việc user có online hay không)
# ---------------------------------------------------------------------------


@tasks.loop(seconds=BUSINESS_TICK_SEC)
async def business_tick_loop():
    try:
        await business.run_income_tick()
    except Exception as e:
        log.warning("Business tick lỗi: %s", e)


@business_tick_loop.before_loop
async def before_business_tick_loop():
    await client.wait_until_ready()


# ---------------------------------------------------------------------------
# Vòng lặp 5: AI tự chat mỗi 30 phút vào kênh chỉ định
# ---------------------------------------------------------------------------


@tasks.loop(seconds=AI_AUTO_CHAT_INTERVAL_SEC)
async def ai_auto_chat_loop():
    channel = await _get_channel(AI_CHAT_CHANNEL_ID)
    if channel is None:
        return

    if not await _has_recent_human_activity(channel):
        return  # kênh vắng người thật trong 1 tiếng qua -> bot không tự nhắn

    text = await ai_chat.generate_auto_message()
    if not text:
        return
    try:
        await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
    except Exception as e:
        log.warning("Gửi AI auto chat lỗi: %s", e)


async def _has_recent_human_activity(channel) -> bool:
    """Kiểm tra có tin nhắn của người thật (không phải bot) trong
    AI_AUTO_CHAT_REQUIRE_ACTIVITY_SEC giây gần nhất trong kênh hay không."""
    cutoff = datetime.now(timezone.utc).timestamp() - AI_AUTO_CHAT_REQUIRE_ACTIVITY_SEC
    try:
        async for msg in channel.history(limit=30):
            if msg.created_at.timestamp() < cutoff:
                break  # đã lùi quá xa mốc thời gian cần check -> dừng sớm
            if not msg.author.bot:
                return True
        return False
    except Exception as e:
        log.warning("Kiểm tra hoạt động kênh lỗi: %s", e)
        return False  # lỗi -> an toàn là không tự nhắn


@ai_auto_chat_loop.before_loop
async def before_ai_auto_chat_loop():
    await client.wait_until_ready()


# ---------------------------------------------------------------------------
# Vòng lặp 6: XP theo Voice Chat - quét mọi voice channel mỗi VOICE_XP_TICK_SEC,
# cộng XP cho ai đang không mute/deaf VÀ kênh có >= VOICE_XP_MIN_MEMBERS người
# (tránh farm XP bằng cách tự vào voice 1 mình rồi AFK).
# ---------------------------------------------------------------------------


@tasks.loop(seconds=VOICE_XP_TICK_SEC)
async def voice_xp_loop():
    guild = client.get_guild(DISCORD_GUILD_ID)
    if guild is None:
        return

    for voice_channel in guild.voice_channels:
        members = [m for m in voice_channel.members if not m.bot]
        if len(members) < VOICE_XP_MIN_MEMBERS:
            continue  # kênh trống hoặc chỉ có 1 mình -> không tính XP

        for member in members:
            state = member.voice
            if state is None:
                continue
            # Bỏ qua nếu tự mute/deaf hoặc bị server mute/deaf (coi như không "nói chuyện")
            if state.self_mute or state.self_deaf or state.mute or state.deaf:
                continue
            if state.afk:
                continue

            amount = random.randint(VOICE_XP_MIN_PER_TICK, VOICE_XP_MAX_PER_TICK)
            try:
                result = await economy.add_xp(member.id, amount)
            except Exception as e:
                log.warning("Cộng XP voice cho %s lỗi: %s", member.id, e)
                continue

            if result["levels_gained"] > 0:
                await _announce_level_up(member, result)


@voice_xp_loop.before_loop
async def before_voice_xp_loop():
    await client.wait_until_ready()


async def _announce_level_up(member: discord.Member, result: dict):
    """Thông báo lên level (dùng chung cho XP nhắn tin lẫn XP voice chat)."""
    channel = await _get_channel()
    if channel is None:
        return
    try:
        await channel.send(
            f"🎉 {member.mention} đã lên **Level {result['level']}**! "
            f"Nhận **{result['mick_awarded']} MICK**. (từ voice chat 🎙️)"
        )
    except Exception:
        pass

    try:
        unlocked = await achievements.check_and_unlock_by_stats(member.id)
        if unlocked:
            await achievements.announce_unlocks(channel, member, unlocked)
    except Exception as e:
        log.warning("Kiểm tra thành tựu sau voice XP lỗi: %s", e)


# ---------------------------------------------------------------------------
# XP theo tin nhắn + đoán Wordle qua tin nhắn thường
# ---------------------------------------------------------------------------


# id quest <-> chuỗi cần khớp (không phân biệt hoa/thường)
_QUEST_TRIGGERS = {
    "meow_3": "meow meow",
    "femboy_3": "i am femboy",
    "ai_hoi_3": "ai hỏi",
    "ghet_tomboy": "tôi ghét tomboy",
    "depchai_gay": "btw i love depchai because he's gay",
}


@client.event
async def on_message(message: discord.Message):
    if message.author.bot or message.guild is None:
        return

    content = message.content.strip()
    lowered = content.lower()

    if games.has_active_wordle(message.author.id) and games.is_valid_guess(content):
        embed, _finished = await games.process_guess(message.author.id, content)
        try:
            await message.reply(embed=embed, mention_author=False)
        except Exception as e:
            log.warning("Gửi kết quả Wordle lỗi: %s", e)
        await _bump_quest_and_notify(message, "play_game_5")
        try:
            unlocked = await achievements.check_and_unlock_by_stats(message.author.id)
            if unlocked:
                await achievements.announce_unlocks(message.channel, message.author, unlocked)
        except Exception as e:
            log.warning("Kiểm tra thành tựu sau wordle lỗi: %s", e)
        return  # không cộng XP cho tin nhắn dùng để đoán Wordle

    # Quest: "i love @ai đó" - cần có mention thật trong tin nhắn
    if lowered.startswith("i love") and message.mentions:
        await _bump_quest_and_notify(message, "love_tag")

    for qid, trigger in _QUEST_TRIGGERS.items():
        if trigger in lowered:
            await _bump_quest_and_notify(message, qid)

    # Học từ mới trong server (không chặn xử lý chính)
    asyncio.create_task(ai_chat.learn_from_message(content))

    # Thành tựu: tin nhắn đầu tiên
    asyncio.create_task(_check_first_message_achievement(message))

    # AI Chat: reply hoặc tag bot
    if ai_chat.wants_bot_reply(message, client.user):
        asyncio.create_task(_handle_ai_reply(message))

    _maybe_grant_xp(message)


async def _check_first_message_achievement(message: discord.Message):
    try:
        unlocked = await achievements.unlock(message.author.id, "first_message")
        if unlocked:
            await achievements.announce_unlocks(message.channel, message.author, [unlocked])
    except Exception as e:
        log.warning("Kiểm tra thành tựu first_message lỗi: %s", e)


async def _bump_quest_and_notify(message: discord.Message, quest_id: str):
    try:
        finished = await quests.bump_progress(message.author.id, quest_id)
    except Exception as e:
        log.warning("Cập nhật quest lỗi: %s", e)
        return
    if finished:
        try:
            await message.channel.send(
                f"✅ {message.author.mention} hoàn thành quest **{finished['desc']}**! "
                f"+**{finished['reward']} MICK** (số dư: {finished['new_balance']})"
            )
        except Exception:
            pass


async def _handle_ai_reply(message: discord.Message):
    try:
        reply_text = await ai_chat.reply_to_message(message)
    except Exception as e:
        log.warning("AI reply lỗi: %s", e)
        return
    if not reply_text:
        return
    try:
        await message.reply(reply_text, mention_author=False, allowed_mentions=discord.AllowedMentions.none())
    except Exception as e:
        log.warning("Gửi AI reply lỗi: %s", e)


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

        await _bump_quest_and_notify(message, "level_up")

        try:
            unlocked = await achievements.check_and_unlock_by_stats(message.author.id)
            if unlocked:
                await achievements.announce_unlocks(message.channel, message.author, unlocked)
        except Exception as e:
            log.warning("Kiểm tra thành tựu lỗi: %s", e)


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


@tree.command(name="level", description="Xem card level (ảnh) của bạn hoặc người khác")
@discord.app_commands.describe(thanh_vien="Xem level của người khác (bỏ trống để xem của bạn)")
async def level_cmd(interaction: discord.Interaction, thanh_vien: discord.Member = None):
    await interaction.response.defer()
    target = thanh_vien or interaction.user
    profile = await economy.get_profile(target.id)

    buf = await level_card.render_level_card(
        display_name=target.display_name,
        avatar_url=target.display_avatar.replace(size=256).url,
        level=profile["level"],
        xp=profile["xp"],
        xp_needed=profile["xp_needed"],
    )
    file = discord.File(buf, filename="level.png")
    await interaction.followup.send(file=file)


# ---------------------------------------------------------------------------
# Slash commands: Rank (xem level/xp/hạng của bản thân)
# ---------------------------------------------------------------------------


def _progress_bar(current: int, needed: int, length: int = 12) -> str:
    filled = int(length * current / needed) if needed else 0
    filled = max(0, min(length, filled))
    return "█" * filled + "░" * (length - filled)


@tree.command(name="rank", description="Xem level, XP và hạng của bạn (hoặc người khác)")
@discord.app_commands.describe(thanh_vien="Xem rank của người khác (bỏ trống để xem của bạn)")
async def rank_cmd(interaction: discord.Interaction, thanh_vien: discord.Member = None):
    await interaction.response.defer()
    target = thanh_vien or interaction.user

    profile = await economy.get_profile(target.id)
    users = await db.get_all_users()
    users.sort(key=lambda u: (u[1].get("level", 0), u[1].get("xp", 0)), reverse=True)
    position = next((i for i, (uid, _) in enumerate(users, start=1) if uid == str(target.id)), len(users) + 1)

    bar = _progress_bar(profile["xp"], profile["xp_needed"])
    embed = discord.Embed(title=f"📊 Rank của {target.display_name}", color=discord.Color.blurple())
    embed.add_field(name="Hạng", value=f"#{position}/{len(users)}", inline=True)
    embed.add_field(name="Level", value=str(profile["level"]), inline=True)
    embed.add_field(name="MICK", value=f"{MICKCOIN_EMOJI} {profile['mick']}", inline=True)
    embed.add_field(
        name="XP",
        value=f"{bar}\n{profile['xp']}/{profile['xp_needed']}",
        inline=False,
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    await interaction.followup.send(embed=embed)


# ---------------------------------------------------------------------------
# Slash commands: Leaderboard
# ---------------------------------------------------------------------------


@tree.command(name="leaderboard", description="Bảng xếp hạng Level và MICK Coin")
@discord.app_commands.describe(loai="Xếp theo Level hay MICK")
@discord.app_commands.choices(loai=[
    discord.app_commands.Choice(name="Level", value="level"),
    discord.app_commands.Choice(name="MICK Coin", value="mick"),
])
async def leaderboard_cmd(interaction: discord.Interaction, loai: discord.app_commands.Choice[str] = None):
    await interaction.response.defer()
    sort_key = loai.value if loai else "level"

    users = await db.get_all_users()
    if sort_key == "level":
        users.sort(key=lambda u: (u[1].get("level", 0), u[1].get("xp", 0)), reverse=True)
    else:
        users.sort(key=lambda u: u[1].get("mick", 0), reverse=True)

    top = users[:10]
    lines = []
    for i, (uid, data) in enumerate(top, start=1):
        member = interaction.guild.get_member(int(uid)) if interaction.guild else None
        name = member.display_name if member else f"User {uid}"
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
        if sort_key == "level":
            lines.append(f"{medal} **{name}** — Level {data.get('level', 0)} ({MICKCOIN_EMOJI} {data.get('mick', 0)})")
        else:
            lines.append(f"{medal} **{name}** — {MICKCOIN_EMOJI} {data.get('mick', 0)} (Level {data.get('level', 0)})")

    title = "🏆 Xếp hạng Level" if sort_key == "level" else "🏆 Xếp hạng MICK Coin"
    embed = discord.Embed(title=title, description="\n".join(lines) or "Chưa có dữ liệu", color=discord.Color.orange())
    await interaction.followup.send(embed=embed)


# ---------------------------------------------------------------------------
# Slash commands: Thành tựu
# ---------------------------------------------------------------------------


@tree.command(name="achievements", description="Xem danh sách thành tựu")
async def achievements_cmd(interaction: discord.Interaction):
    user = await db.get_user(interaction.user.id)
    embed = achievements.build_list_embed(user.get("achievements", []))
    await interaction.response.send_message(embed=embed)


# ---------------------------------------------------------------------------
# Slash commands: Quest
# ---------------------------------------------------------------------------


@tree.command(name="quest", description="Xem quest hằng ngày của bạn")
async def quest_cmd(interaction: discord.Interaction):
    user = await quests.get_today_quests(interaction.user.id)
    embed = quests.build_quest_embed(user, interaction.user.display_name)
    await interaction.response.send_message(embed=embed)


# ---------------------------------------------------------------------------
# Slash commands: Chuyển MICK (delay theo số tiền)
# ---------------------------------------------------------------------------


@tree.command(name="transfer", description="Chuyển MICK cho người khác (tiền càng cao xử lý càng lâu)")
@discord.app_commands.describe(nguoi_nhan="Người nhận MICK", so_tien="Số MICK muốn chuyển")
async def transfer_cmd(interaction: discord.Interaction, nguoi_nhan: discord.Member, so_tien: int):
    if so_tien <= 0:
        await interaction.response.send_message("Số tiền phải lớn hơn 0!", ephemeral=True)
        return
    if nguoi_nhan.id == interaction.user.id:
        await interaction.response.send_message("Không thể tự chuyển cho chính mình!", ephemeral=True)
        return

    sender = await db.get_user(interaction.user.id)
    if sender["mick"] < so_tien:
        await interaction.response.send_message(
            f"Bạn không đủ MICK! Số dư hiện tại: **{sender['mick']} MICK**", ephemeral=True
        )
        return

    delay = economy.transfer_delay_seconds(so_tien)
    await interaction.response.send_message(
        f"⏳ Đang xử lý chuyển **{so_tien} MICK** cho {nguoi_nhan.mention}... "
        f"(mất khoảng **{delay:.0f} giây**, tiền càng cao xử lý càng lâu)"
    )
    await asyncio.sleep(delay)

    result = await economy.transfer_mick(interaction.user.id, nguoi_nhan.id, so_tien)
    if result["ok"]:
        await interaction.followup.send(
            f"✅ Đã chuyển **{so_tien} MICK** từ {interaction.user.mention} đến {nguoi_nhan.mention}!\n"
            f"Số dư người gửi: **{result['from_balance']} MICK**"
        )
    else:
        await interaction.followup.send(f"❌ Chuyển tiền thất bại ({result['reason']}). MICK chưa bị trừ.")


# ---------------------------------------------------------------------------
# Slash commands: ATM (giữ MICK hộ, tách khỏi ví tiêu xài)
# ---------------------------------------------------------------------------


@tree.command(name="atm", description="Gửi/rút MICK vào ATM (giữ tiền hộ, tách khỏi ví tiêu xài)")
@discord.app_commands.describe(hanh_dong="Gửi hay rút", so_tien="Số MICK")
@discord.app_commands.choices(hanh_dong=[
    discord.app_commands.Choice(name="Gửi (deposit)", value="deposit"),
    discord.app_commands.Choice(name="Rút (withdraw)", value="withdraw"),
    discord.app_commands.Choice(name="Xem số dư", value="check"),
])
async def atm_cmd(interaction: discord.Interaction, hanh_dong: discord.app_commands.Choice[str], so_tien: int = 0):
    if hanh_dong.value == "check":
        info = await economy.get_atm_profile(interaction.user.id)
        embed = discord.Embed(title="🏧 ATM MICK Coin", color=discord.Color.blue())
        embed.add_field(name="Ví (tiêu xài)", value=f"{info['wallet']} 🪙", inline=True)
        embed.add_field(name="ATM (giữ hộ)", value=f"{info['atm']} 🪙", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    if so_tien <= 0:
        await interaction.response.send_message("Số tiền phải lớn hơn 0!", ephemeral=True)
        return

    if hanh_dong.value == "deposit":
        result = await economy.atm_deposit(interaction.user.id, so_tien)
    else:
        result = await economy.atm_withdraw(interaction.user.id, so_tien)

    if result["ok"]:
        action_label = "gửi vào" if hanh_dong.value == "deposit" else "rút từ"
        await interaction.response.send_message(
            f"🏧 Đã {action_label} ATM **{so_tien} MICK**!\n"
            f"Ví: **{result['wallet']} MICK** · ATM: **{result['atm']} MICK**",
            ephemeral=True,
        )
    else:
        await interaction.response.send_message("❌ Không đủ MICK để thực hiện!", ephemeral=True)


# ---------------------------------------------------------------------------
# Slash commands: Kinh doanh (quán/công ty/nhà trọ/khách sạn)
# ---------------------------------------------------------------------------

_BIZ_CHOICES = [
    discord.app_commands.Choice(name="🍜 Quán ăn", value="quan"),
    discord.app_commands.Choice(name="🏢 Công ty", value="congty"),
    discord.app_commands.Choice(name="🏠 Nhà trọ", value="nhatro"),
    discord.app_commands.Choice(name="🏨 Khách sạn", value="khachsan"),
]


@tree.command(name="business", description="Xem cơ ngơi kinh doanh của bạn")
async def business_cmd(interaction: discord.Interaction):
    summary = await business.get_summary(interaction.user.id)
    embed = business.build_summary_embed(interaction.user.display_name, summary)
    await interaction.response.send_message(embed=embed)


@tree.command(name="open_business", description="Mở cơ sở kinh doanh mới")
@discord.app_commands.describe(loai="Loại hình kinh doanh")
@discord.app_commands.choices(loai=_BIZ_CHOICES)
async def open_business_cmd(interaction: discord.Interaction, loai: discord.app_commands.Choice[str]):
    result = await business.open_business(interaction.user.id, loai.value)
    if result["ok"]:
        await interaction.response.send_message(
            f"🎉 Đã mở **{loai.name}**! Tốn **{result['cost']} MICK**. Dùng `/hire` để thuê nhân viên."
        )
        try:
            first = await achievements.unlock(interaction.user.id, "first_business")
            stats_based = await achievements.check_and_unlock_by_stats(interaction.user.id)
            all_unlocked = ([first] if first else []) + stats_based
            if all_unlocked:
                await achievements.announce_unlocks(interaction.channel, interaction.user, all_unlocked)
        except Exception as e:
            log.warning("Kiểm tra thành tựu sau mở business lỗi: %s", e)
    else:
        reason = result["reason"]
        if reason == "already_open":
            await interaction.response.send_message("Bạn đã mở loại hình này rồi!", ephemeral=True)
        elif reason == "insufficient_funds":
            await interaction.response.send_message(
                f"Không đủ MICK! Cần **{result['cost']} MICK** để mở.", ephemeral=True
            )
        else:
            await interaction.response.send_message("Có lỗi xảy ra, thử lại sau.", ephemeral=True)


@tree.command(name="hire", description="Thuê thêm nhân viên cho cơ sở kinh doanh")
@discord.app_commands.describe(loai="Loại hình kinh doanh")
@discord.app_commands.choices(loai=_BIZ_CHOICES)
async def hire_cmd(interaction: discord.Interaction, loai: discord.app_commands.Choice[str]):
    result = await business.hire_staff(interaction.user.id, loai.value)
    if result["ok"]:
        await interaction.response.send_message(
            f"👥 Đã thuê thêm nhân viên cho **{loai.name}**! Hiện có **{result['staff']}** nhân viên. "
            f"Tốn **{result['cost']} MICK**. Nhân viên vẫn làm việc kể cả khi bạn offline!"
        )
    else:
        reason = result["reason"]
        messages_map = {
            "not_opened": f"Bạn chưa mở **{loai.name}**! Dùng `/open_business` trước.",
            "max_staff": "Cơ sở này đã thuê tối đa nhân viên rồi!",
            "insufficient_funds": f"Không đủ MICK! Cần **{result.get('cost')} MICK** để thuê.",
        }
        await interaction.response.send_message(
            messages_map.get(reason, "Có lỗi xảy ra."), ephemeral=True
        )


# ---------------------------------------------------------------------------
# Slash command: AI chat trực tiếp (không cần tag/reply)
# ---------------------------------------------------------------------------


@tree.command(name="day_tu", description="Dạy bot nghĩa của 1 từ/cụm từ lóng trong server")
@discord.app_commands.describe(tu="Từ hoặc cụm từ", nghia="Nghĩa của từ đó")
async def day_tu_cmd(interaction: discord.Interaction, tu: str, nghia: str):
    result = await ai_chat.teach_word(tu, nghia)
    if result["ok"]:
        await interaction.response.send_message(f"✅ Đã học: **{tu.strip().lower()}** = {nghia.strip()}")
    elif result["reason"] == "mention_blocked":
        await interaction.response.send_message(
            "❌ Không thể dạy nội dung có chứa @everyone/@here.", ephemeral=True
        )
    elif result["reason"] == "too_long":
        await interaction.response.send_message(
            "❌ Từ hoặc nghĩa quá dài (từ ≤50 ký tự, nghĩa ≤300 ký tự).", ephemeral=True
        )
    else:
        await interaction.response.send_message("❌ Từ hoặc nghĩa không được để trống.", ephemeral=True)


@tree.command(name="tra_tu", description="Xem bot đã học nghĩa từ/cụm từ này chưa")
@discord.app_commands.describe(tu="Từ hoặc cụm từ muốn tra")
async def tra_tu_cmd(interaction: discord.Interaction, tu: str):
    data = await db.get_word(tu)
    if data.get("meaning"):
        source_label = "member dạy" if data.get("source") == "taught" else "bot tự học"
        await interaction.response.send_message(
            f"📖 **{tu.strip().lower()}**: {data['meaning']}\n-# ({source_label})"
        )
    else:
        await interaction.response.send_message(
            f"🤔 Bot chưa biết nghĩa của **{tu.strip().lower()}**. Dùng `/day_tu` để dạy bot nhé!",
            ephemeral=True,
        )


@tree.command(name="ai", description="Chat trực tiếp với AI của bot (Groq)")
@discord.app_commands.describe(noi_dung="Bạn muốn nói gì với bot?")
async def ai_cmd(interaction: discord.Interaction, noi_dung: str):
    await interaction.response.defer()
    reply_text = await ai_chat._groq_chat([
        {"role": "system", "content": ai_chat.SYSTEM_PROMPT},
        {"role": "user", "content": noi_dung},
    ])
    if reply_text:
        await interaction.followup.send(
            ai_chat._sanitize_ai_output(reply_text), allowed_mentions=discord.AllowedMentions.none()
        )
    else:
        await interaction.followup.send("😵 AI hiện chưa sẵn sàng (thiếu GROQ_API_KEY hoặc lỗi kết nối).")
