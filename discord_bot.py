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
from datetime import datetime, timedelta, timezone

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
    AI_AUTO_CHAT_QUIET_START_HOUR,
    AI_AUTO_CHAT_QUIET_END_HOUR,
    VN_UTC_OFFSET_HOURS,
    BUSINESS_TICK_SEC,
    MICKCOIN_EMOJI,
    log,
)
from tiktok_client import TikTokClient
import db
import economy
import features
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

    client.add_view(features.DailyClaimView())  # persistent view, sống sót qua restart

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
    await features.maybe_post_daily(client, DAILY_CHANNEL_ID)


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
        await features.run_income_tick()
    except Exception as e:
        log.warning("Business tick lỗi: %s", e)


@business_tick_loop.before_loop
async def before_business_tick_loop():
    await client.wait_until_ready()


# ---------------------------------------------------------------------------
# Vòng lặp 5: AI tự chat mỗi 30 phút vào kênh chỉ định
# ---------------------------------------------------------------------------


def _in_ai_quiet_hours() -> bool:
    """True nếu đang trong khung giờ 'ngủ' (giờ VN) - bot không tự nhắn."""
    vn_hour = datetime.now(timezone(timedelta(hours=VN_UTC_OFFSET_HOURS))).hour
    start, end = AI_AUTO_CHAT_QUIET_START_HOUR, AI_AUTO_CHAT_QUIET_END_HOUR
    if start == end:
        return False
    if start < end:
        return start <= vn_hour < end
    return vn_hour >= start or vn_hour < end  # khung vắt qua nửa đêm, vd 22h -> 3h


@tasks.loop(seconds=AI_AUTO_CHAT_INTERVAL_SEC)
async def ai_auto_chat_loop():
    if _in_ai_quiet_hours():
        return  # đang trong khung giờ ngủ (mặc định 0h-3h sáng) -> không tự nhắn

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
        unlocked = await features.check_and_unlock_by_stats(member.id)
        if unlocked:
            await features.announce_unlocks(channel, member, unlocked)
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

    if features.has_active_wordle(message.author.id) and features.is_valid_guess(content):
        embed, _finished = await features.process_guess(message.author.id, content)
        try:
            await message.reply(embed=embed, mention_author=False)
        except Exception as e:
            log.warning("Gửi kết quả Wordle lỗi: %s", e)
        await _bump_quest_and_notify(message, "play_game_5")
        try:
            unlocked = await features.check_and_unlock_by_stats(message.author.id)
            if unlocked:
                await features.announce_unlocks(message.channel, message.author, unlocked)
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
        unlocked = await features.unlock(message.author.id, "first_message")
        if unlocked:
            await features.announce_unlocks(message.channel, message.author, [unlocked])
    except Exception as e:
        log.warning("Kiểm tra thành tựu first_message lỗi: %s", e)


async def _bump_quest_and_notify(message: discord.Message, quest_id: str):
    try:
        finished = await features.bump_progress(message.author.id, quest_id)
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
            unlocked = await features.check_and_unlock_by_stats(message.author.id)
            if unlocked:
                await features.announce_unlocks(message.channel, message.author, unlocked)
        except Exception as e:
            log.warning("Kiểm tra thành tựu lỗi: %s", e)


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------
#
# Các lệnh cùng chủ đề đã được gộp lại thành 1 lệnh duy nhất, có nút bấm để
# chuyển qua lại giữa các "view" (tương đương các lệnh cũ):
#   /profile   = /profile (cũ) + /level (cũ) + /rank (cũ)
#   /game      = /cup (cũ) + /wordle (cũ)
#   /business  = /business (cũ) + /open_business (cũ) + /hire (cũ)
#   /tudien    = /day_tu (cũ) + /tra_tu (cũ)
#   /help      = lệnh mới, liệt kê toàn bộ lệnh theo nhóm (nút bấm)


def _progress_bar(current: int, needed: int, length: int = 12) -> str:
    filled = int(length * current / needed) if needed else 0
    filled = max(0, min(length, filled))
    return "█" * filled + "░" * (length - filled)


async def _build_rank_embed(target: discord.Member) -> discord.Embed:
    profile = await economy.get_profile(target.id)
    users = await db.get_all_users()
    users.sort(key=lambda u: (u[1].get("level", 0), u[1].get("xp", 0)), reverse=True)
    position = next((i for i, (uid, _) in enumerate(users, start=1) if uid == str(target.id)), len(users) + 1)

    bar = _progress_bar(profile["xp"], profile["xp_needed"])
    embed = discord.Embed(title=f"🏅 Rank của {target.display_name}", color=discord.Color.blurple())
    embed.add_field(name="Hạng", value=f"#{position}/{len(users)}", inline=True)
    embed.add_field(name="Level", value=str(profile["level"]), inline=True)
    embed.add_field(name="MICK", value=f"{MICKCOIN_EMOJI} {profile['mick']}", inline=True)
    embed.add_field(name="XP", value=f"{bar}\n{profile['xp']}/{profile['xp_needed']}", inline=False)
    embed.set_thumbnail(url=target.display_avatar.url)
    return embed


async def _build_profile_embed(target: discord.Member) -> discord.Embed:
    data = await economy.get_profile(target.id)
    embed = discord.Embed(title=f"📊 Hồ sơ của {target.display_name}", color=discord.Color.blurple())
    embed.add_field(name="MICK", value=f"{data['mick']} 🪙", inline=True)
    embed.add_field(name="Level", value=str(data["level"]), inline=True)
    embed.add_field(name="XP", value=f"{data['xp']}/{data['xp_needed']}", inline=True)
    embed.set_thumbnail(url=target.display_avatar.url)
    return embed


class ProfileView(discord.ui.View):
    """Gộp /profile + /level + /rank cũ: 3 nút chuyển view trên cùng 1 message."""

    def __init__(self, target: discord.Member, owner_id: int):
        super().__init__(timeout=90)
        self.target = target
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Dùng `/profile` để xem của riêng bạn nhé!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Hồ sơ", emoji="📊", style=discord.ButtonStyle.primary)
    async def btn_profile(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        embed = await _build_profile_embed(self.target)
        await interaction.edit_original_response(embed=embed, attachments=[], view=self)

    @discord.ui.button(label="Level Card", emoji="🖼️", style=discord.ButtonStyle.secondary)
    async def btn_level(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        profile = await economy.get_profile(self.target.id)
        buf = await level_card.render_level_card(
            display_name=self.target.display_name,
            avatar_url=self.target.display_avatar.replace(size=256).url,
            level=profile["level"],
            xp=profile["xp"],
            xp_needed=profile["xp_needed"],
        )
        file = discord.File(buf, filename="level.png")
        embed = discord.Embed(title=f"🖼️ Level card của {self.target.display_name}", color=discord.Color.blurple())
        embed.set_image(url="attachment://level.png")
        await interaction.edit_original_response(embed=embed, attachments=[file], view=self)

    @discord.ui.button(label="Rank", emoji="🏅", style=discord.ButtonStyle.secondary)
    async def btn_rank(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        embed = await _build_rank_embed(self.target)
        await interaction.edit_original_response(embed=embed, attachments=[], view=self)


@tree.command(name="profile", description="Xem hồ sơ, level card và rank của bạn (hoặc người khác)")
@discord.app_commands.describe(thanh_vien="Xem của người khác (bỏ trống để xem của bạn)")
async def profile_cmd(interaction: discord.Interaction, thanh_vien: discord.Member = None):
    target = thanh_vien or interaction.user
    embed = await _build_profile_embed(target)
    view = ProfileView(target=target, owner_id=interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view)


# ---------------------------------------------------------------------------
# Slash command: Minigame (gộp /cup + /wordle cũ, chọn qua nút bấm)
# ---------------------------------------------------------------------------


class GameChooserView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=30)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Dùng `/game` để chơi phần của riêng bạn!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Úp ly chọn kẹo", emoji="🥤", style=discord.ButtonStyle.primary)
    async def btn_cup(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = features.CupGameView(owner_id=self.owner_id)
        await interaction.response.edit_message(embed=features.build_cup_game_embed(), view=view)

    @discord.ui.button(label="Wordle", emoji="🟩", style=discord.ButtonStyle.success)
    async def btn_wordle(self, interaction: discord.Interaction, button: discord.ui.Button):
        if features.has_active_wordle(self.owner_id):
            await interaction.response.send_message(
                "Bạn đang có ván Wordle chưa xong! Gõ 1 từ 5 chữ vào kênh để đoán tiếp.", ephemeral=True
            )
            return
        embed = features.start_wordle(self.owner_id)
        await interaction.response.edit_message(embed=embed, view=None)


@tree.command(name="game", description="Chơi minigame: Úp ly chọn kẹo hoặc Wordle")
async def game_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🎮 Chọn minigame",
        description="Bấm nút bên dưới để chơi:",
        color=discord.Color.gold(),
    )
    await interaction.response.send_message(embed=embed, view=GameChooserView(interaction.user.id))


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
# Slash commands: Thành tựu + Quest
# ---------------------------------------------------------------------------


@tree.command(name="achievements", description="Xem danh sách thành tựu")
async def achievements_cmd(interaction: discord.Interaction):
    user = await db.get_user(interaction.user.id)
    embed = features.build_list_embed(user.get("achievements", []))
    await interaction.response.send_message(embed=embed)


@tree.command(name="quest", description="Xem quest hằng ngày của bạn")
async def quest_cmd(interaction: discord.Interaction):
    user = await features.get_today_quests(interaction.user.id)
    embed = features.build_quest_embed(user, interaction.user.display_name)
    await interaction.response.send_message(embed=embed)


# ---------------------------------------------------------------------------
# Slash commands: Chuyển MICK (delay theo số tiền) + ATM
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
# Slash command: Kinh doanh (gộp /business + /open_business + /hire cũ)
# ---------------------------------------------------------------------------


class BusinessKindSelect(discord.ui.Select):
    def __init__(self, action: str, owner_id: int):
        self.action = action  # "open" hoặc "hire"
        self.owner_id = owner_id
        options = [
            discord.SelectOption(label=name, value=kind) for kind, name in features.BUSINESS_NAMES.items()
        ]
        placeholder = "Chọn loại hình muốn mở..." if action == "open" else "Chọn loại hình muốn thuê nhân viên..."
        super().__init__(placeholder=placeholder, options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        kind = self.values[0]
        label = features.BUSINESS_NAMES[kind]

        if self.action == "open":
            result = await features.open_business(self.owner_id, kind)
            if result["ok"]:
                text = f"🎉 Đã mở **{label}**! Tốn **{result['cost']} MICK**. Bấm nút \"Thuê nhân viên\" ở `/business` để thuê người."
                try:
                    first = await features.unlock(self.owner_id, "first_business")
                    stats_based = await features.check_and_unlock_by_stats(self.owner_id)
                    all_unlocked = ([first] if first else []) + stats_based
                    if all_unlocked:
                        await features.announce_unlocks(interaction.channel, interaction.user, all_unlocked)
                except Exception as e:
                    log.warning("Kiểm tra thành tựu sau mở business lỗi: %s", e)
            else:
                reason = result["reason"]
                if reason == "already_open":
                    text = f"❌ Bạn đã mở **{label}** rồi!"
                elif reason == "insufficient_funds":
                    text = f"❌ Không đủ MICK! Cần **{result['cost']} MICK** để mở **{label}**."
                else:
                    text = "❌ Có lỗi xảy ra, thử lại sau."
        else:
            result = await features.hire_staff(self.owner_id, kind)
            if result["ok"]:
                text = (
                    f"👥 Đã thuê thêm nhân viên cho **{label}**! Hiện có **{result['staff']}** nhân viên. "
                    f"Tốn **{result['cost']} MICK**. Nhân viên vẫn làm việc kể cả khi bạn offline!"
                )
            else:
                reason = result["reason"]
                messages_map = {
                    "not_opened": f"❌ Bạn chưa mở **{label}**! Bấm nút \"Mở cơ sở mới\" ở `/business` trước.",
                    "max_staff": f"❌ **{label}** đã thuê tối đa nhân viên rồi!",
                    "insufficient_funds": f"❌ Không đủ MICK! Cần **{result.get('cost')} MICK** để thuê.",
                }
                text = messages_map.get(reason, "❌ Có lỗi xảy ra.")

        await interaction.response.edit_message(content=text, view=None)


class BusinessActionView(discord.ui.View):
    def __init__(self, action: str, owner_id: int):
        super().__init__(timeout=60)
        self.add_item(BusinessKindSelect(action, owner_id))


class BusinessView(discord.ui.View):
    """Gộp /business + /open_business + /hire cũ: 3 nút trên cùng 1 message."""

    def __init__(self, owner_id: int):
        super().__init__(timeout=120)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Dùng `/business` để xem cơ ngơi của riêng bạn!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Xem tổng quan", emoji="📊", style=discord.ButtonStyle.primary)
    async def btn_view(self, interaction: discord.Interaction, button: discord.ui.Button):
        summary = await features.get_summary(self.owner_id)
        embed = features.build_summary_embed(interaction.user.display_name, summary)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Mở cơ sở mới", emoji="🏪", style=discord.ButtonStyle.success)
    async def btn_open(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Chọn loại hình muốn mở:", view=BusinessActionView("open", self.owner_id), ephemeral=True
        )

    @discord.ui.button(label="Thuê nhân viên", emoji="👥", style=discord.ButtonStyle.secondary)
    async def btn_hire(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Chọn loại hình muốn thuê thêm nhân viên:", view=BusinessActionView("hire", self.owner_id), ephemeral=True
        )


@tree.command(name="business", description="Xem, mở cơ sở mới, hoặc thuê nhân viên cho cơ ngơi kinh doanh")
async def business_cmd(interaction: discord.Interaction):
    summary = await features.get_summary(interaction.user.id)
    embed = features.build_summary_embed(interaction.user.display_name, summary)
    await interaction.response.send_message(embed=embed, view=BusinessView(interaction.user.id))


# ---------------------------------------------------------------------------
# Slash command: AI chat trực tiếp (không cần tag/reply) + Từ điển server
# (gộp /day_tu + /tra_tu cũ, nhập liệu qua modal)
# ---------------------------------------------------------------------------


class TeachWordModal(discord.ui.Modal, title="Dạy từ mới cho bot"):
    tu = discord.ui.TextInput(label="Từ / cụm từ", max_length=50, placeholder="vd: gato")
    nghia = discord.ui.TextInput(
        label="Nghĩa", style=discord.TextStyle.paragraph, max_length=300, placeholder="Nghĩa của từ/cụm từ đó"
    )

    async def on_submit(self, interaction: discord.Interaction):
        result = await ai_chat.teach_word(str(self.tu), str(self.nghia))
        if result["ok"]:
            await interaction.response.send_message(
                f"✅ Đã học: **{str(self.tu).strip().lower()}** = {str(self.nghia).strip()}"
            )
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


class LookupWordModal(discord.ui.Modal, title="Tra từ"):
    tu = discord.ui.TextInput(label="Từ / cụm từ muốn tra", max_length=50, placeholder="vd: gato")

    async def on_submit(self, interaction: discord.Interaction):
        word = str(self.tu)
        data = await db.get_word(word)
        if data.get("meaning"):
            source_label = "member dạy" if data.get("source") == "taught" else "bot tự học"
            await interaction.response.send_message(
                f"📖 **{word.strip().lower()}**: {data['meaning']}\n-# ({source_label})"
            )
        else:
            await interaction.response.send_message(
                f"🤔 Bot chưa biết nghĩa của **{word.strip().lower()}**. Bấm nút \"Dạy từ\" ở `/tudien` để dạy bot nhé!",
                ephemeral=True,
            )


class DictionaryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.button(label="Tra từ", emoji="📖", style=discord.ButtonStyle.primary)
    async def btn_lookup(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(LookupWordModal())

    @discord.ui.button(label="Dạy từ", emoji="✏️", style=discord.ButtonStyle.success)
    async def btn_teach(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TeachWordModal())


@tree.command(name="tudien", description="Tra hoặc dạy bot nghĩa từ/cụm từ lóng trong server")
async def tudien_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📚 Từ điển server",
        description="Bấm nút bên dưới để tra 1 từ đã học, hoặc dạy bot nghĩa 1 từ mới.",
        color=discord.Color.blue(),
    )
    await interaction.response.send_message(embed=embed, view=DictionaryView(), ephemeral=True)


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


# ---------------------------------------------------------------------------
# Slash command: Help - liệt kê toàn bộ lệnh theo nhóm, bấm nút để xem chi tiết
# ---------------------------------------------------------------------------

_HELP_CATEGORIES = [
    {
        "key": "level",
        "label": "Level & Kinh tế",
        "emoji": "📊",
        "commands": [
            ("/profile [thành_viên]", "Hồ sơ · Level card ảnh · Rank — bấm nút để chuyển view"),
            ("/leaderboard [loại]", "Bảng xếp hạng Level hoặc MICK Coin"),
            ("/atm [hành_động] [số_tiền]", "Gửi/rút MICK vào ATM, hoặc xem số dư"),
            ("/transfer [người_nhận] [số_tiền]", "Chuyển MICK cho người khác"),
        ],
    },
    {
        "key": "game",
        "label": "Minigame & Quest",
        "emoji": "🎮",
        "commands": [
            ("/game", "Chơi Úp ly chọn kẹo hoặc Wordle — bấm nút để chọn"),
            ("/quest", "Xem quest hằng ngày của bạn"),
            ("/achievements", "Xem danh sách thành tựu"),
        ],
    },
    {
        "key": "business",
        "label": "Kinh doanh",
        "emoji": "💼",
        "commands": [
            ("/business", "Xem cơ ngơi · Mở cơ sở mới · Thuê nhân viên — bấm nút"),
        ],
    },
    {
        "key": "ai",
        "label": "AI & Từ điển",
        "emoji": "🤖",
        "commands": [
            ("/ai [nội_dung]", "Chat trực tiếp với AI của bot"),
            ("/tudien", "Tra từ hoặc dạy bot nghĩa từ mới — bấm nút, nhập qua form"),
        ],
    },
]


def _build_help_category_embed(cat: dict) -> discord.Embed:
    embed = discord.Embed(title=f"{cat['emoji']} {cat['label']}", color=discord.Color.blurple())
    for name, desc in cat["commands"]:
        embed.add_field(name=name, value=desc, inline=False)
    return embed


class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        for cat in _HELP_CATEGORIES:
            self.add_item(self._make_button(cat))

    def _make_button(self, cat: dict) -> discord.ui.Button:
        button = discord.ui.Button(label=cat["label"], emoji=cat["emoji"], style=discord.ButtonStyle.secondary)

        async def callback(interaction: discord.Interaction):
            await interaction.response.edit_message(embed=_build_help_category_embed(cat), view=self)

        button.callback = callback
        return button


@tree.command(name="help", description="Xem danh sách lệnh của bot theo từng nhóm")
async def help_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📖 Trợ giúp",
        description="Bấm nút bên dưới để xem lệnh theo từng nhóm.",
        color=discord.Color.blurple(),
    )
    await interaction.response.send_message(embed=embed, view=HelpView(), ephemeral=True)
