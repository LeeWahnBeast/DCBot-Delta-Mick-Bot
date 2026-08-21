"""
Discord Client: thông báo TikTok live (có retry nếu gửi lỗi), đồng bộ
avatar bot + icon/tên server mỗi 5 tiếng, hệ thống MICK + Level (XP theo tin
nhắn VÀ theo voice chat), Daily hàng ngày (0h-12h trưa giờ VN, có chuỗi + câu hỏi phụ), 3 minigame có ID
riêng và nhập liệu qua Modal (Wordle, Đoán số, Kéo Búa Bao), thành tựu, quest
hằng ngày, kinh doanh, ATM, chuyển MICK, AI chat. Tên lệnh slash đăng ký bằng
tiếng Anh (yêu cầu kỹ thuật của Discord), mô tả/nội dung hiển thị bằng tiếng
Việt - xem _HELP_CATEGORIES bên dưới, PHẢI khớp đúng tên lệnh thật khi sửa.

Toàn bộ dữ liệu bền vững (level/MICK, mốc Daily...) lưu ở Firestore qua
module db.py, không còn phụ thuộc file JSON local (ổ đĩa Render free là
ephemeral, dễ mất dữ liệu khi redeploy).

(Tính năng báo VIDEO MỚI từ TikTok đã bị XÓA - yt-dlp liên tục lỗi "Unable to
extract secondary user ID" do TikTok đổi cấu trúc API, và không có nguồn thay
thế nào khả thi trên free tier (mọi API lấy list video khác đều cần chữ ký
JS-VM hoặc dịch vụ trả phí). Chỉ còn giữ thông báo LIVE, vẫn dùng TikTokLive.)
"""

import asyncio
import math
import random
import secrets
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
    AI_GUESS_MEANING_INTERVAL_SEC,
    AI_GUESS_MEANING_BATCH_SIZE,
    AI_GUESS_MEANING_MIN_COUNT,
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
    UPDATE_LOG_CHANNEL_ID,
    AI_AUTO_CHAT_INTERVAL_SEC,
    AI_AUTO_CHAT_REQUIRE_ACTIVITY_SEC,
    AI_AUTO_CHAT_QUIET_START_HOUR,
    AI_AUTO_CHAT_QUIET_END_HOUR,
    VN_UTC_OFFSET_HOURS,
    BUSINESS_TICK_SEC,
    MICKCOIN_EMOJI,
    GUESS_NUMBER_MAX,
    TRANSFER_OTP_LENGTH,
    TRANSFER_OTP_TTL_SEC,
    TRIVIA_TIMEOUT_SEC,
    TICKET_EMOJI,
    GAME_TICKET_COST,
    QUEST_CHANNEL_ID,
    BOOST_CHANNEL_ID,
    MEMBER_MILESTONE_CHANNEL_ID,
    MEMBER_MILESTONE_STEP,
    CONFESSION_CHANNEL_ID,
    CONFESSION_COOLDOWN_SEC,
    WELCOME_CHANNEL_ID,
    BOT_ROLE_ID,
    log,
)
from tiktok_client import TikTokClient
import db
import economy
import features
import ai_chat
import level_card
import welcome_card
import versioning

# ---------------------------------------------------------------------------
# Discord Bot
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True  # cần để đọc nội dung tin nhắn (XP + đoán Wordle)
intents.voice_states = True  # cần để biết ai đang trong voice channel (XP voice chat)
intents.members = True  # cần để duyệt danh sách thành viên trong voice channel
intents.presences = True  # cần để đọc trạng thái Online/Idle/DND/Offline cho /profile
# Lưu ý: Presence Intent phải được BẬT thủ công trong Discord Developer Portal
# (Bot > Privileged Gateway Intents), nếu không bot sẽ luôn thấy mọi người là Offline.
client = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(client)

tiktok = TikTokClient()
_last_xp_ts: dict[int, float] = {}

# Đánh dấu user "có online/nhắn tin hôm nay" (giờ VN) - dùng để chốt chuỗi
# Daily (xem features.finalize_daily_streaks): phân biệt "quên điểm danh"
# (bị reset chuỗi) với "cả ngày không online" (giữ nguyên chuỗi). Chỉ ghi
# DB 1 lần/user/ngày (set RAM, tự xoá khi sang ngày mới) để đỡ tốn ghi Firebase.
_active_today: set[int] = set()
_active_today_date: str = ""
_synced = False
_version_checked = False
_bot_version: float = versioning.DEFAULT_START_VERSION

# Cache số lượt dùng (uses) của mỗi invite code theo guild, dùng để xác định
# AI mời khi có thành viên mới join (xem _refresh_invite_cache/on_member_join
# ở phần "Quest mời bạn bè" bên dưới). {guild_id: {invite_code: uses}}
_invite_uses_cache: dict[int, dict[str, int]] = {}


@client.event
async def on_ready():
    global _synced, _version_checked, _bot_version
    log.info("Đã đăng nhập với tài khoản %s (id=%s)", client.user, client.user.id)

    client.add_view(features.DailyClaimView())  # persistent view, sống sót qua restart

    if not _synced:
        await tree.sync()
        _synced = True

    if not _version_checked:
        _version_checked = True
        try:
            result = await versioning.check_and_bump_version()
            old_version = result["old_version"]  # đọc từ DB thật, KHÔNG dùng _bot_version
            # (biến RAM này bị reset về 1.0 mỗi lần bot restart nên trước đây
            # luôn báo sai kiểu "1.00 -> 2.11" dù bot đã ở version 2.11 từ trước).
            _bot_version = result["version"]
            if result.get("bumped"):
                _fire_and_forget(
                    _announce_bot_update(old_version, result),
                    "Đăng thông báo cập nhật bot lỗi",
                )
        except Exception as e:
            log.warning("Kiểm tra version bot lỗi: %s", e)

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
    if not learn_word_flush_loop.is_running():
        learn_word_flush_loop.start()
        guess_meaning_loop.start()
    if not anniversary_loop.is_running():
        anniversary_loop.start()
    guild = client.get_guild(DISCORD_GUILD_ID)
    if guild is not None:
        await _refresh_invite_cache(guild)


def get_bot_info() -> dict:
    """Thông tin bot cho trang web dạng Google Play: tên/avatar lấy trực tiếp
    từ token đang đăng nhập (client.user), không hardcode. Trả về dict rỗng-an
    toàn nếu bot chưa kết nối xong (client.user is None trước on_ready)."""
    user = client.user
    if user is None:
        return {
            "name": "Đang khởi động...",
            "avatar_url": "",
            "created_at": None,
            "guild_count": 0,
            "member_count": 0,
            "latency_ms": None,
            "online": False,
            "version": _bot_version,
        }

    guild_count = len(client.guilds)
    member_count = sum(g.member_count or 0 for g in client.guilds if g.member_count)
    latency_ms = (
        round(client.latency * 1000)
        if client.latency is not None and math.isfinite(client.latency)
        else None
    )

    return {
        "name": user.name,
        "avatar_url": user.display_avatar.url,
        "created_at": user.created_at.isoformat(),
        "guild_count": guild_count,
        "member_count": member_count,
        "latency_ms": latency_ms,
        "online": client.is_ready() and not client.is_closed(),
        "version": _bot_version,
    }


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
# Vòng lặp 1: trạng thái live TikTok
# (Tính năng báo video mới đã bị XÓA - yt-dlp liên tục lỗi "Unable to extract
# secondary user ID" do TikTok đổi cấu trúc, và mọi API lấy list video khác
# đều cần chữ ký JS-VM (X-Bogus/X-Gnarly) hoặc dịch vụ trả phí, không khả thi
# trên free tier. Chỉ còn giữ lại phần LIVE - vẫn dùng TikTokLive, ổn định.)
# ---------------------------------------------------------------------------


@tasks.loop(seconds=CHECK_INTERVAL_SEC)
async def check_tiktok_loop():
    profile = await tiktok.fetch_profile(TIKTOK_USERNAME)
    if profile is None:
        return

    channel = await _get_channel()
    await _handle_live_status(profile, channel)


def _format_delay(seconds: int) -> str:
    """Định dạng số giây trễ thành chuỗi dễ đọc (vd. '3 phút 12 giây')."""
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if hours:
        parts.append(f"{hours} giờ")
    if minutes:
        parts.append(f"{minutes} phút")
    if not hours and (secs or not minutes):
        parts.append(f"{secs} giây")
    return " ".join(parts)


async def _handle_live_status(profile: dict, channel):
    bot_state = await db.get_bot_state()
    was_live = bot_state.get("was_live", False)
    is_live = profile["is_live"]

    if is_live and not was_live:
        await db.save_bot_state({"was_live": True})
        if channel is not None:
            live_url = f"https://www.tiktok.com/@{TIKTOK_USERNAME}/live"
            now_ts = int(time.time())
            header_text = discord.ui.TextDisplay(
                f"### 🔴 {profile['nickname']} đang LIVESTREAM trên TikTok!\n{live_url}"
            )
            container = discord.ui.Container(accent_color=discord.Color.red())
            if profile.get("avatar_url"):
                container.add_item(discord.ui.Section(header_text, accessory=discord.ui.Thumbnail(profile["avatar_url"])))
            else:
                container.add_item(header_text)
            container.add_item(
                discord.ui.TextDisplay(
                    f"**📥 Bot phát hiện lúc**\n"
                    f"<t:{now_ts}:F> (<t:{now_ts}:R>)\n"
                    f"⚠️ TikTok không cho biết chính xác giờ bắt đầu live, nên "
                    f"buổi live có thể đã bắt đầu tới ~{_format_delay(CHECK_INTERVAL_SEC)} "
                    f"trước đó (chu kỳ bot kiểm tra)."
                )
            )
            try:
                await channel.send(
                    content=f"{_mention_prefix()}🔴 @{TIKTOK_USERNAME} đang livestream, vào xem ngay!",
                    view=features.SimpleContainerLayout(container),
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

    # Đã bỏ đổi TÊN server theo yêu cầu - chỉ còn đồng bộ icon server theo TikTok.
    try:
        await guild.edit(icon=img_bytes, reason=f"Đồng bộ icon theo TikTok @{TIKTOK_USERNAME}")
        log.info("Đã đổi icon server theo TikTok @%s.", TIKTOK_USERNAME)
    except discord.Forbidden:
        log.warning("Bot không có quyền 'Manage Server' để đổi icon.")
    except Exception as e:
        log.warning("Đổi icon server lỗi: %s", e)


@sync_identity_loop.before_loop
async def before_sync_identity_loop():
    await client.wait_until_ready()


# ---------------------------------------------------------------------------
# Vòng lặp 3: Daily - kiểm tra mỗi phút, tự đăng lúc đúng 0h giờ VN
# ---------------------------------------------------------------------------


@tasks.loop(seconds=60)
async def daily_loop():
    await features.maybe_post_daily(client, DAILY_CHANNEL_ID)
    await features.maybe_repost_daily_idle(client, DAILY_CHANNEL_ID)


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


# Gộp việc ghi "từ học được" (ai_words) thành từng đợt 3 phút/lần thay vì ghi
# Firestore mỗi tin nhắn - đây là fix cho lỗi 429 RESOURCE_EXHAUSTED (hết quota
# Firestore free tier) khi server chat đông người.
@tasks.loop(seconds=180)
async def learn_word_flush_loop():
    try:
        await ai_chat.flush_learned_words()
    except Exception as e:
        log.warning("Flush từ học định kỳ lỗi: %s", e)

    try:
        await features.flush_emoji_counts()
    except Exception as e:
        log.warning("Flush emoji định kỳ lỗi: %s", e)

    try:
        features.cleanup_stale_games()
    except Exception as e:
        log.warning("Dọn ván minigame bỏ dở lỗi: %s", e)


@learn_word_flush_loop.before_loop
async def before_learn_word_flush_loop():
    await client.wait_until_ready()


# ---------------------------------------------------------------------------
# Kỉ niệm N tháng thành lập server - check 1 lần/giờ, thực chất chỉ đăng khi
# đúng ngày "sinh nhật" server VÀ tháng này chưa đăng (xem
# features.maybe_post_anniversary), nên check dày hơn daily_loop không sao.
# ---------------------------------------------------------------------------


@tasks.loop(hours=1)
async def anniversary_loop():
    guild = client.get_guild(DISCORD_GUILD_ID)
    if guild is None:
        return
    try:
        await features.maybe_post_anniversary(client, MEMBER_MILESTONE_CHANNEL_ID, guild)
    except Exception as e:
        log.warning("Check kỉ niệm tháng lỗi: %s", e)


@anniversary_loop.before_loop
async def before_anniversary_loop():
    await client.wait_until_ready()


# Định kỳ nhờ AI đoán nghĩa hàng loạt cho top từ đã "học" (đếm tần suất)
# nhưng chưa có nghĩa -> có nghĩa thì mới được _build_slang_context() dùng
# trong AI chat. Chỉ 1 lượt Groq call cho cả batch, không đoán từng từ riêng.
@tasks.loop(seconds=AI_GUESS_MEANING_INTERVAL_SEC)
async def guess_meaning_loop():
    try:
        n = await ai_chat.guess_meanings_for_top_words(
            batch_size=AI_GUESS_MEANING_BATCH_SIZE,
            min_count=AI_GUESS_MEANING_MIN_COUNT,
        )
        if n:
            log.info("AI đã tự đoán nghĩa cho %d từ mới.", n)
    except Exception as e:
        log.warning("Đoán nghĩa từ định kỳ lỗi: %s", e)


@guess_meaning_loop.before_loop
async def before_guess_meaning_loop():
    await client.wait_until_ready()


def _fire_and_forget(coro, err_label: str):
    """Chạy 1 coroutine nền (asyncio.create_task) nhưng bắt lỗi thay vì để bay
    lên thành exception không ai xử lý (unhandled task exception) - vốn làm
    spam traceback trong log và có thể che mất lỗi thật khác."""

    async def _runner():
        try:
            await coro
        except Exception as e:
            log.warning("%s: %s", err_label, e)

    return asyncio.create_task(_runner())


async def _send_level_up_notice(channel, member: discord.Member, result: dict):
    """Gửi thông báo lên level: text có mention thật + rank hiện tại, kèm
    ẢNH LEVEL CARD RENDER ĐỘNG (level_card.render_level_card - đúng level/
    rank/XP thật của người vừa lên), KHÔNG dùng ảnh tĩnh assets/levelup.png
    nữa vì ảnh tĩnh không phản ánh đúng level/rank thật (xem yêu cầu An)."""
    try:
        rank, _total = await _get_level_rank(member.id)
        xp_needed = economy.xp_needed_for_level(result["level"])
        buf = await level_card.render_level_card(
            display_name=member.display_name,
            avatar_url=member.display_avatar.replace(size=256).url,
            level=result["level"],
            xp=result["xp"],
            xp_needed=xp_needed,
            rank=rank,
        )
        text = (
            f"{member.mention} Bạn vừa lên level {result['level']}, "
            f"hiện bạn đang ở rank {rank}, cố gắng bạn nhé"
        )
        await channel.send(
            text,
            file=discord.File(buf, filename="levelup.png"),
            allowed_mentions=discord.AllowedMentions(users=[member]),
        )
    except Exception as e:
        log.warning("Gửi thông báo lên level lỗi: %s", e)


async def _announce_level_up(member: discord.Member, result: dict, channel=None):
    """Thông báo lên level (dùng chung cho XP nhắn tin lẫn XP voice chat).
    channel=None -> tự lấy kênh chat chính (AI_CHAT_CHANNEL_ID), KHÔNG dùng
    kênh TikTok (DISCORD_CHANNEL_ID) - 2 kênh này phục vụ mục đích khác nhau."""
    if channel is None:
        channel = await _get_channel(AI_CHAT_CHANNEL_ID)
    if channel is None:
        return

    await _send_level_up_notice(channel, member, result)

    try:
        unlocked = await features.check_and_unlock_by_stats(member.id)
        if unlocked:
            await features.announce_unlocks(channel, member, unlocked)
    except Exception as e:
        log.warning("Kiểm tra thành tựu sau voice XP lỗi: %s", e)


def _format_version(v: float) -> str:
    """2.20 -> '2.2', 2.61 -> '2.61', 1.0 -> '1.0' (giữ ít nhất 1 số lẻ cho đẹp)."""
    s = f"{v:.2f}".rstrip("0")
    if s.endswith("."):
        s += "0"
    return s


class _UpdateAnnouncementView(discord.ui.LayoutView):
    """Components V2 (cần discord.py>=2.6) - dùng discord.ui.Separator, đường
    kẻ ngang THẬT do Discord vẽ (visible=True), giống hệt bot nhạc/bot
    "Vựa Sử Quan" trong ảnh mẫu - không còn là hack qua field embed nữa."""

    def __init__(self, version: str, unix_ts: int, sections: list, footer_text: str):
        super().__init__()
        container = discord.ui.Container()
        container.add_item(discord.ui.TextDisplay(f"# CẬP NHẬT {version}"))
        container.add_item(discord.ui.TextDisplay(f"<t:{unix_ts}:f>"))
        container.add_item(discord.ui.Separator(visible=True))
        for name, bullets in sections:
            body = f"**{name}**\n" + "\n".join(bullets) if bullets else f"**{name}**"
            container.add_item(discord.ui.TextDisplay(body))
        container.add_item(discord.ui.Separator(visible=True))
        container.add_item(discord.ui.TextDisplay(f"-# {footer_text}"))
        self.add_item(container)


async def _announce_bot_update(old_version: float, bump_result: dict):
    """Sau khi version bump lúc khởi động (xem on_ready), nhờ AI tóm tắt cập
    nhật rồi đăng vào UPDATE_LOG_CHANNEL_ID. Chạy nền (fire-and-forget) để
    không làm chậm on_ready nếu gọi Groq bị lag."""
    channel = await _get_channel(UPDATE_LOG_CHANNEL_ID)
    if channel is None:
        log.warning("Không tìm thấy kênh log cập nhật (id=%s)", UPDATE_LOG_CHANNEL_ID)
        return

    summary, ai_ok = await ai_chat.summarize_bot_update(
        old_version=old_version,
        new_version=bump_result["version"],
        diffs=bump_result.get("diffs", {}),
        removed_paths=bump_result.get("removed_paths", []),
    )
    now = discord.utils.utcnow()

    # Gom các dòng "- ..." của AI thành bullet "• ..." theo TỪNG MỤC (mỗi
    # dòng tiêu đề không có "-" đầu dòng, vd "🐛 **Bản vá**:" -> 1 khối text
    # riêng, các dòng "-" theo sau -> nội dung khối đó). Nếu AI không chia
    # mục (vd rớt về bản liệt kê file) thì gom hết vào 1 khối chung.
    sections: list[tuple[str, list[str]]] = []
    for raw in summary.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("-"):
            bullet = f"• {line.lstrip('- ').strip()}"
            if sections:
                sections[-1][1].append(bullet)
            else:
                sections.append(("Cập nhật:", [bullet]))
        else:
            # AI có thể tự bold sẵn tên mục (vd "🐛 **Bản vá**:") - bóc hết
            # "**" có sẵn ra để lát nữa bọc lại ĐÚNG 1 LỚP bold, tránh bị
            # lồng "****" (2 lớp bold) khi TextDisplay bọc thêm lần nữa.
            name = line.rstrip(":").replace("**", "").strip()
            sections.append((name + ":", []))
    if not sections:
        sections = [("Cập nhật:", [f"• {summary.strip()}"])]
    if not ai_ok:
        sections.append((
            "⚠️ Lưu ý:",
            ["• AI chưa tóm tắt được (thiếu `GROQ_API_KEY` hoặc lỗi gọi Groq API) — danh sách trên chỉ là file đã đổi, không phải mô tả nội dung."],
        ))

    footer_text = f"{bump_result.get('changed_files', 0)} file thay đổi · v{old_version:.2f} → v{bump_result['version']:.2f}"
    version_str = _format_version(bump_result["version"])
    unix_ts = int(now.timestamp())

    try:
        view = _UpdateAnnouncementView(version_str, unix_ts, sections, footer_text)
        await channel.send(view=view)
        return
    except AttributeError:
        log.warning("discord.py phiên bản đang chạy chưa hỗ trợ Components V2 (cần >=2.6) - dùng embed fallback")
    except Exception as e:
        log.warning("Gửi Components V2 lỗi, dùng embed fallback: %s", e)

    # Fallback: embed thường (cho discord.py cũ hơn 2.6 hoặc lỗi gửi Components V2)
    embed = discord.Embed(title=f"CẬP NHẬT {version_str}", description=f"<t:{unix_ts}:f>")
    for name, bullets in sections:
        embed.add_field(name=name, value="\n".join(bullets) or "\u200b", inline=False)
    embed.set_footer(text=footer_text)
    await channel.send(embed=embed)


# ---------------------------------------------------------------------------
# Quest mời bạn bè: bot TỰ TẠO 1 link mời riêng cho từng user (xem
# _get_or_create_invite_link, gọi từ lệnh /quest), rồi nhận diện AI vừa mời
# khi có thành viên mới join bằng cách so khớp CHÍNH LINK ĐÓ - không dựa vào
# invite.inviter (vì link do bot tạo nên inviter luôn là bot, không phải
# user) và không cần quan tâm vanity URL của server.
#
# Link được tạo với max_uses = số lượt còn thiếu để hoàn thành quest, nên
# Discord sẽ TỰ ĐỘNG xoá link ngay khi đạt đủ số lượt cần.
# Cần bot có quyền "Create Invite" ở kênh tạo link (và "Manage Server" để đọc
# lại danh sách invite khi có người join).
# ---------------------------------------------------------------------------


async def _refresh_invite_cache(guild: discord.Guild) -> dict[str, int]:
    """Đọc lại toàn bộ invite hiện có của guild và lưu vào cache. Trả về cache
    CŨ (trước khi refresh) để nơi gọi có thể so sánh tìm invite vừa dùng."""
    old = _invite_uses_cache.get(guild.id, {})
    try:
        invites = await guild.invites()
    except discord.Forbidden:
        log.warning(
            "Bot không có quyền 'Manage Server' nên không đọc được danh sách invite "
            "- quest mời bạn bè sẽ không hoạt động."
        )
        return old
    except Exception as e:
        log.warning("Đọc danh sách invite lỗi: %s", e)
        return old

    _invite_uses_cache[guild.id] = {inv.code: (inv.uses or 0) for inv in invites}
    return old


@client.event
async def on_invite_create(invite: discord.Invite):
    if invite.guild is None:
        return
    _invite_uses_cache.setdefault(invite.guild.id, {})[invite.code] = invite.uses or 0


@client.event
async def on_invite_delete(invite: discord.Invite):
    if invite.guild is None:
        return
    _invite_uses_cache.get(invite.guild.id, {}).pop(invite.code, None)
    # Dọn luôn mapping code -> user nếu link này từng được bot tạo cho quest
    # (vd. Discord tự xoá link do đạt max_uses, hoặc admin xoá tay).
    _fire_and_forget(db.delete_invite_owner(invite.code), "Dọn invite_codes lỗi")


async def _get_or_create_invite_link(guild: discord.Guild, user: discord.abc.User, user_doc: dict) -> str | None:
    """Trả về link mời (discord.gg/...) dành riêng cho user để phục vụ quest
    mời bạn bè hôm nay. Dùng lại link cũ nếu còn hợp lệ (tạo cùng ngày VÀ vẫn
    còn tồn tại trên Discord), ngược lại tạo mới."""
    today = features.vn_today_str()
    code = user_doc.get("quest_invite_code") or ""

    if code and user_doc.get("quest_invite_code_date") == today:
        try:
            invites = await guild.invites()
            if any(inv.code == code for inv in invites):
                return f"https://discord.gg/{code}"
        except Exception:
            pass  # không đọc được thì cứ tạo link mới cho chắc

    channel = guild.get_channel(QUEST_CHANNEL_ID)
    if channel is None or not isinstance(channel, discord.TextChannel):
        channel = guild.system_channel
    if channel is None or not channel.permissions_for(guild.me).create_instant_invite:
        for ch in guild.text_channels:
            if ch.permissions_for(guild.me).create_instant_invite:
                channel = ch
                break
        else:
            channel = None
    if channel is None:
        return None

    remaining = features.quest_invite_remaining(user_doc)
    try:
        invite = await channel.create_invite(
            max_age=0,
            max_uses=remaining,
            unique=True,
            reason=f"Quest mời bạn bè - {user}",
        )
    except discord.Forbidden:
        log.warning("Bot không có quyền 'Create Invite' ở kênh %s.", getattr(channel, "name", channel))
        return None
    except Exception as e:
        log.warning("Tạo invite cho quest mời bạn bè lỗi: %s", e)
        return None

    _invite_uses_cache.setdefault(guild.id, {})[invite.code] = invite.uses or 0
    await db.save_user(user.id, {"quest_invite_code": invite.code, "quest_invite_code_date": today})
    await db.save_invite_owner(invite.code, user.id)
    return f"https://discord.gg/{invite.code}"


async def _maybe_announce_member_milestone(guild: discord.Guild) -> None:
    """Nhắn kèm code quà khi tổng số member vừa chạm mốc tròn
    MEMBER_MILESTONE_STEP (mặc định 50) - vd 50, 100, 150 member..."""
    member_count = guild.member_count or 0
    if member_count <= 0 or member_count % MEMBER_MILESTONE_STEP != 0:
        return

    channel = client.get_channel(MEMBER_MILESTONE_CHANNEL_ID)
    if channel is None:
        return

    try:
        info = await features.create_milestone_code(guild.id, member_count)
    except Exception as e:
        log.warning("Tạo code mốc thành viên lỗi: %s", e)
        return

    ttl_hours = int(info["expires_at"] - info["created_at"]) // 3600
    try:
        await channel.send(
            f"Yoo, ae chúng ta có **{member_count}** member rồi và tôi sẽ tạo code "
            f"`{info['code']}` cho các ae, giới hạn **{info['max_uses']}** người nhập nhé!😛\n"
            f"Dùng lệnh `/code` để nhập. Hết hạn trong **{ttl_hours}H**."
        )
    except Exception:
        pass


async def _send_welcome_card(member: discord.Member) -> None:
    """Gửi ảnh welcome card + tin nhắn chào ở WELCOME_CHANNEL_ID khi có
    thành viên mới (không tính bot). Số thứ tự thành viên lấy từ
    member_count hiện tại của guild ngay lúc join."""
    channel = member.guild.get_channel(WELCOME_CHANNEL_ID) or client.get_channel(WELCOME_CHANNEL_ID)
    if channel is None:
        # get_channel chỉ tra cache - nếu bot chưa cache đúng kênh này (hoặc
        # ID sai/bot không có quyền xem kênh) thì fetch trực tiếp từ API để
        # biết CHÍNH XÁC lý do, thay vì âm thầm bỏ qua như trước.
        try:
            channel = await client.fetch_channel(WELCOME_CHANNEL_ID)
        except Exception as e:
            log.warning(
                "_send_welcome_card: không lấy được kênh %s (kiểm tra ID kênh + quyền View Channel của bot): %s",
                WELCOME_CHANNEL_ID, e,
            )
            return

    try:
        buf, avatar_err = await welcome_card.render_welcome_card(
            display_name=member.display_name,
            avatar_url=member.display_avatar.replace(size=256).url,
            member_number=member.guild.member_count,
        )
        file = discord.File(buf, filename="welcome.png")
        if avatar_err:
            log.warning("Welcome card: tải/dán avatar của %s lỗi (card vẫn gửi, chỉ thiếu avatar): %s", member, avatar_err)
    except Exception as e:
        log.warning("Render welcome card lỗi: %s", e)
        file = None

    text = (
        f"# Chào mừng bradar {member.mention} đã đến với server của Delta Mick <:mango:1529287058072408195>\n\n"
        f"- Hãy xem qua <id:guide> và đọc luật nhé, nhớ tích cực chat nhiều vào để không bị ghẻ bi 🗿\n\n"
        f"- Và đương nhiên là tôi cũng sẽ khá thất vọng nếu các bradar chưa follow kênh "
        f"[***Delta Mick***](<https://www.tiktok.com/@tahnuyo_0?_r=1&_t=ZS-993g64YqnBd>) đấy <:sad:1531310182016094328>"
    )

    try:
        if file is not None:
            await channel.send(content=text, file=file)
        else:
            await channel.send(content=text)
        log.info("Đã gửi welcome card cho %s (#%s) ở kênh %s", member, member.guild.member_count, WELCOME_CHANNEL_ID)
    except Exception as e:
        log.warning("Gửi welcome card lỗi (kiểm tra quyền Send Messages + Attach Files của bot ở kênh %s): %s", WELCOME_CHANNEL_ID, e)


async def _assign_bot_role(member: discord.Member) -> None:
    """Tự gán role Bot (BOT_ROLE_ID) khi có bot khác được add vào server -
    member thường KHÔNG đụng tới role này (role member do bot khác tự lo)."""
    role = member.guild.get_role(BOT_ROLE_ID)
    if role is None:
        log.warning("_assign_bot_role: không tìm thấy role %s trong guild", BOT_ROLE_ID)
        return
    try:
        await member.add_roles(role, reason="Tự động gán role Bot khi bot mới được add vào server")
    except Exception as e:
        log.warning("Gán role Bot cho %s lỗi: %s", member, e)


@client.event
async def on_member_join(member: discord.Member):
    log.info("on_member_join fired: %s (bot=%s) guild=%s", member, member.bot, member.guild.id)
    if member.guild.id != DISCORD_GUILD_ID:
        log.warning("on_member_join: guild %s khác DISCORD_GUILD_ID cấu hình (%s) - bỏ qua toàn bộ xử lý", member.guild.id, DISCORD_GUILD_ID)
        return

    _fire_and_forget(_maybe_announce_member_milestone(member.guild), "Thông báo mốc thành viên lỗi")

    if member.bot:
        _fire_and_forget(_assign_bot_role(member), "Gán role Bot lỗi")
        return

    _fire_and_forget(_send_welcome_card(member), "Gửi welcome card lỗi")

    old_uses = await _refresh_invite_cache(member.guild)
    new_uses = _invite_uses_cache.get(member.guild.id, {})

    # Code nào tăng uses, hoặc biến mất hẳn (Discord tự xoá do vừa chạm
    # max_uses ngay lượt join này), đều là ứng viên "vừa được dùng".
    candidates = [c for c, u in old_uses.items() if new_uses.get(c, -1) != u]
    candidates += [c for c in new_uses if c not in old_uses]

    inviter_id = None
    for code in candidates:
        owner_id = await db.get_invite_owner(code)
        if owner_id and owner_id != member.id:
            inviter_id = owner_id
            break

    if inviter_id is None:
        return

    try:
        finished = await features.bump_invite_progress(inviter_id)
    except Exception as e:
        log.warning("Cập nhật quest mời bạn bè lỗi: %s", e)
        return
    if finished is None:
        return

    channel = client.get_channel(QUEST_CHANNEL_ID)
    if channel is None:
        return

    try:
        inviter_mention = f"<@{inviter_id}>"
        text = (
            f"🎉 {inviter_mention} vừa mời {member.mention} vào server! "
            f"💰 Nhận **{finished['reward']} Mick** (số dư: {finished['new_balance']}) "
            f"— tiến độ quest mời bạn: `{min(finished['invited_count'], finished['target'])}/{finished['target']}`"
        )
        if finished["completed"]:
            text += "\n✅ Hoàn thành quest **Mời bạn bè vào server**! Link mời đã tự động bị Discord xoá."
        await channel.send(text)
    except Exception:
        pass


@client.event
async def on_reaction_add(reaction: discord.Reaction, user: discord.abc.User):
    """Ghi nhận lượt reaction bằng emoji - phục vụ thống kê 'top emoji' khi
    đăng kỉ niệm N tháng (xem features.maybe_post_anniversary)."""
    if user.bot or reaction.message.guild is None or reaction.message.guild.id != DISCORD_GUILD_ID:
        return
    emoji = reaction.emoji
    display = str(emoji) if isinstance(emoji, (discord.Emoji, discord.PartialEmoji)) else emoji
    features.track_emoji_reaction(display)


@client.event
async def on_member_update(before: discord.Member, after: discord.Member):
    """Nhắn ở BOOST_CHANNEL_ID mỗi khi 1 member vừa BẮT ĐẦU boost server
    (before.premium_since None -> after.premium_since có giá trị) - đây là
    cách chính xác để bắt đúng người vừa boost, khác on_guild_update (chỉ
    biết tổng số boost thay đổi, không biết ai)."""
    if after.guild.id != DISCORD_GUILD_ID:
        return
    if before.premium_since is None and after.premium_since is not None:
        channel = client.get_channel(BOOST_CHANNEL_ID)
        if channel is None:
            return
        try:
            await channel.send(
                f"{after.mention} omg omg tôi thực sự đã vui mừng và có cảm xúc nhưng tôi là bot🤑🤑🤑"
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# XP theo tin nhắn + đoán Wordle qua tin nhắn thường
# ---------------------------------------------------------------------------


# id quest <-> chuỗi cần khớp (không phân biệt hoa/thường)
_QUEST_TRIGGERS = {
    "meow_3": "meow meow",
    "femboy_3": "i am femboy",
    "ai_hoi_3": "ai hỏi",
    "ghet_tomboy": "tôi ghét tomboy",
    "depchai_gay": "btw, i love <@1011257705031274536> because he's is my girlfriend and gay <3",
    "nsc_tree": "i love nsc because he crashed into a tree.",
}


@client.event
async def on_message(message: discord.Message):
    if message.author.bot or message.guild is None:
        return

    content = message.content.strip()
    lowered = content.lower()

    # Quest: "i love @ai đó" - cần có mention thật trong tin nhắn
    if lowered.startswith("i love") and message.mentions:
        await _bump_quest_and_notify(message, "love_tag")

    for qid, trigger in _QUEST_TRIGGERS.items():
        if trigger in lowered:
            await _bump_quest_and_notify(message, qid)

    # Học từ mới trong server (không chặn xử lý chính, chỉ cộng dồn vào RAM -
    # xem learn_word_flush_loop để biết khi nào thật sự ghi lên Firebase).
    # Chỉ học khi server đủ đông (xem AI_LEARN_MIN_MEMBERS trong config.py).
    member_count = message.guild.member_count or 0
    _fire_and_forget(ai_chat.learn_from_message(content, member_count), "Học từ lỗi")

    # Đếm emoji (custom + unicode) dùng trong tin nhắn - phục vụ thống kê
    # "top emoji" khi đăng kỉ niệm N tháng (xem features.maybe_post_anniversary).
    features.track_emojis_in_text(content)

    # Thành tựu: tin nhắn đầu tiên
    asyncio.create_task(_check_first_message_achievement(message))

    # AI Chat: reply hoặc tag bot
    if ai_chat.wants_bot_reply(message, client.user):
        _fire_and_forget(_handle_ai_reply(message), "AI reply lỗi")

    _maybe_grant_xp(message)
    _mark_active_today(message.author.id)

    # Daily: đếm tin nhắn trong kênh Daily để tự bump lại nếu bị trôi mất
    # giữa dòng chat đông người (xem features.on_daily_channel_message).
    if message.channel.id == DAILY_CHANNEL_ID:
        _fire_and_forget(features.on_daily_channel_message(client, DAILY_CHANNEL_ID), "Bump Daily lỗi")


async def _check_first_message_achievement(message: discord.Message):
    try:
        unlocked = await features.unlock(message.author.id, "first_message")
        if unlocked:
            await features.announce_unlocks(message.channel, message.author, [unlocked])
    except Exception as e:
        log.warning("Kiểm tra thành tựu first_message lỗi: %s", e)


async def _bump_quest_and_notify(message: discord.Message, quest_id: str):
    await _bump_quest_and_notify_ctx(message.channel, message.author, quest_id)


async def _bump_quest_and_notify_ctx(channel, user, quest_id: str):
    """Giống _bump_quest_and_notify nhưng dùng cho ngữ cảnh Interaction (không có discord.Message),
    ví dụ khi tiến độ đến từ việc bấm nút/submit modal minigame."""
    try:
        finished = await features.bump_progress(user.id, quest_id)
    except Exception as e:
        log.warning("Cập nhật quest lỗi: %s", e)
        return
    if finished:
        try:
            await channel.send(
                f"✅ {user.mention} hoàn thành quest **{finished['desc']}**! "
                f"💰 Bạn đã nhận **{finished['reward']} Mick** (số dư: {finished['new_balance']})"
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


def _mark_active_today(user_id: int):
    global _active_today_date
    today = features.vn_today_str()
    if today != _active_today_date:
        _active_today_date = today
        _active_today.clear()
    if user_id in _active_today:
        return
    _active_today.add(user_id)
    asyncio.create_task(_save_active_today(user_id, today))


async def _save_active_today(user_id: int, today: str):
    try:
        await db.save_user(user_id, {"last_active_date": today})
    except Exception as e:
        log.warning("Lưu last_active_date lỗi: %s", e)


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
        await _send_level_up_notice(message.channel, message.author, result)

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
# Toàn bộ tên lệnh dùng tiếng Việt có dấu (Discord cho phép chữ Unicode có
# dấu trong tên lệnh) để tự thân lệnh đã rõ nghĩa, không cần hỏi lại AI.
# Các lệnh cùng chủ đề đã được gộp lại thành 1 lệnh duy nhất, có nút bấm để
# chuyển qua lại giữa các "view" (tương đương các lệnh cũ):
#   /profile        = /profile (cũ) + /level (cũ) + /rank (cũ), có thêm UUID + ngày tham gia
#   /trò-chơi     = /cup (cũ, đã bỏ) + /wordle (cũ) + 2 game mới (Đoán số, Kéo Búa Bao)
#   /check-game     = lệnh mới, tra 1 ván minigame theo ID riêng
#   /kinh-doanh   = /business (cũ) + /open_business (cũ) + /hire (cũ)
#   /từ-điển      = /day_tu (cũ) + /tra_tu (cũ)
#   /trợ-giúp     = lệnh mới, liệt kê toàn bộ lệnh theo nhóm (nút bấm)


def _progress_bar(current: int, needed: int, length: int = 12) -> str:
    filled = int(length * current / needed) if needed else 0
    filled = max(0, min(length, filled))
    return "█" * filled + "░" * (length - filled)


async def _get_level_rank(user_id: int) -> tuple[int, int]:
    """Trả (rank, total) - thứ hạng của user_id trên bảng xếp hạng toàn server
    theo Level (dùng chung logic sort với /leaderboard và /rank)."""
    users = await db.get_all_users()
    users.sort(key=lambda u: (u[1].get("level", 0), u[1].get("xp", 0)), reverse=True)
    position = next((i for i, (uid, _) in enumerate(users, start=1) if uid == str(user_id)), len(users) + 1)
    return position, len(users)


async def _build_rank_container(target: discord.Member) -> discord.ui.Container:
    profile = await economy.get_profile(target.id)
    position, total = await _get_level_rank(target.id)

    bar = _progress_bar(profile["xp"], profile["xp_needed"])
    container = discord.ui.Container(accent_color=discord.Color.blurple())
    container.add_item(
        discord.ui.Section(
            discord.ui.TextDisplay(f"### 🏅 Rank của {target.display_name}"),
            discord.ui.TextDisplay(
                f"**Hạng:** #{position}/{total} · **Level:** {profile['level']}\n"
                f"**MICK:** {MICKCOIN_EMOJI} {economy.format_mick(profile['mick'])} · "
                f"**Vé:** {TICKET_EMOJI} {economy.format_ve(profile['ve'])}\n"
                f"**XP:** {bar}\n{profile['xp']}/{profile['xp_needed']}"
            ),
            accessory=discord.ui.Thumbnail(target.display_avatar.url),
        )
    )
    return container


_STATUS_LABELS = {
    discord.Status.online: "🟢 Online",
    discord.Status.idle: "🌙 Idle",
    discord.Status.dnd: "⛔ Do Not Disturb",
    discord.Status.offline: "⚫ Offline",
    discord.Status.invisible: "⚫ Offline",
}


def _current_role_text(target: discord.Member) -> str:
    # top_role mặc định luôn là @everyone nếu không có role nào khác -> bỏ qua nó
    roles = [r for r in getattr(target, "roles", []) if r.name != "@everyone"]
    if not roles:
        return "Chưa có role"
    top = max(roles, key=lambda r: r.position)
    return top.mention


def _xp_progress_bar(xp: int, xp_needed: int, length: int = 12) -> str:
    """Vẽ thanh tiến trình XP dạng text (vd ▰▰▰▰▱▱▱▱▱▱▱▱) cho embed profile."""
    if xp_needed <= 0:
        filled = length
    else:
        filled = max(0, min(length, round(length * xp / xp_needed)))
    return "▰" * filled + "▱" * (length - filled)


async def _build_profile_content(target: discord.Member) -> tuple[discord.ui.Container, discord.File]:
    data = await economy.get_profile(target.id)
    rank, _total = await _get_level_rank(target.id)

    buf = await level_card.render_level_card(
        display_name=target.display_name,
        avatar_url=target.display_avatar.replace(size=256).url,
        level=data["level"],
        xp=data["xp"],
        xp_needed=data["xp_needed"],
        rank=rank,
    )
    file = discord.File(buf, filename="profile_card.png")

    container = discord.ui.Container(accent_color=target.color if target.color.value else discord.Color.blurple())
    container.add_item(discord.ui.TextDisplay(f"### Hồ sơ của {target.display_name}"))
    container.add_item(discord.ui.Separator(visible=True))

    bar = _xp_progress_bar(data["xp"], data["xp_needed"])
    container.add_item(
        discord.ui.TextDisplay(f"**🏅 Level {data['level']} · Rank #{rank}**\n{bar}\n`{data['xp']}/{data['xp_needed']} XP`")
    )
    status = _STATUS_LABELS.get(getattr(target, "status", discord.Status.offline), "⚫ Offline")
    container.add_item(
        discord.ui.TextDisplay(
            f"**💰 Ví**\n{MICKCOIN_EMOJI} {economy.format_mick(data['mick'])} MICK\n{TICKET_EMOJI} {economy.format_ve(data['ve'])} Vé\n\n"
            f"**📶 Trạng thái**\n{status}\n{_current_role_text(target)}"
        )
    )
    container.add_item(
        discord.ui.TextDisplay(
            f"**🔥 Chuỗi Daily**\n{features.format_streak_line(data.get('daily_streak', 0), data.get('daily_history', []))}"
        )
    )

    created_ts = int(target.created_at.timestamp())
    joined_at = getattr(target, "joined_at", None)
    dates = f"📅 Tạo tài khoản: <t:{created_ts}:R>"
    if joined_at:
        dates += f"\n📥 Vào server: <t:{int(joined_at.timestamp())}:R>"
    container.add_item(discord.ui.TextDisplay(dates))
    container.add_item(discord.ui.Separator(visible=True))
    container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(f"attachment://{file.filename}")))
    container.add_item(discord.ui.TextDisplay(f"-# UUID: {data.get('uuid', '?')}"))
    return container, file


class _SimpleContainerLayout(discord.ui.LayoutView):
    """LayoutView dùng 1 lần, chỉ để bọc 1 Container có sẵn (không có nút) -
    dùng cho mấy chỗ chỉ cần hiện hồ sơ 1 lần, không cần đổi qua lại view."""

    def __init__(self, container: discord.ui.Container):
        super().__init__(timeout=1)
        self.add_item(container)


class ProfileLayoutView(discord.ui.LayoutView):
    """Components V2 - thay cho ProfileView (embed) cũ. Gộp /profile +
    /level + /rank cũ: 3 nút chuyển nội dung trên cùng 1 message."""

    def __init__(self, target: discord.Member, owner_id: int):
        super().__init__(timeout=90)
        self.target = target
        self.owner_id = owner_id
        self._container = discord.ui.Container()
        self.add_item(self._container)
        row = discord.ui.ActionRow()
        row.add_item(self._make_button("Hồ sơ", "📊", discord.ButtonStyle.primary, self._fill_profile))
        row.add_item(self._make_button("Level Card", "🖼️", discord.ButtonStyle.secondary, self._fill_level))
        row.add_item(self._make_button("Rank", "🏅", discord.ButtonStyle.secondary, self._fill_rank))
        self.add_item(row)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Dùng `/profile` để xem của riêng bạn nhé!", ephemeral=True)
            return False
        return True

    def _make_button(self, label: str, emoji: str, style: discord.ButtonStyle, handler) -> discord.ui.Button:
        button = discord.ui.Button(label=label, emoji=emoji, style=style)

        async def callback(interaction: discord.Interaction):
            await interaction.response.defer()
            await handler(interaction)

        button.callback = callback
        return button

    async def _fill_profile(self, interaction: discord.Interaction):
        new_container, file = await _build_profile_content(self.target)
        self._replace_container(new_container)
        await interaction.edit_original_response(view=self, attachments=[file])

    async def _fill_level(self, interaction: discord.Interaction):
        profile = await economy.get_profile(self.target.id)
        rank, _total = await _get_level_rank(self.target.id)
        buf = await level_card.render_level_card(
            display_name=self.target.display_name,
            avatar_url=self.target.display_avatar.replace(size=256).url,
            level=profile["level"],
            xp=profile["xp"],
            xp_needed=profile["xp_needed"],
            rank=rank,
        )
        file = discord.File(buf, filename="level.png")
        new_container = discord.ui.Container()
        new_container.add_item(discord.ui.TextDisplay(f"### 🖼️ Level card của {self.target.display_name}"))
        new_container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://level.png")))
        self._replace_container(new_container)
        await interaction.edit_original_response(view=self, attachments=[file])

    async def _fill_rank(self, interaction: discord.Interaction):
        new_container = await _build_rank_container(self.target)
        self._replace_container(new_container)
        await interaction.edit_original_response(view=self, attachments=[])

    def _replace_container(self, new_container: discord.ui.Container) -> None:
        """Đổ item của container mới vào container cũ tại chỗ (giữ đúng vị
        trí trong LayoutView, phía trên hàng nút) thay vì gắn container mới
        vào cuối view (sẽ làm nút bị đẩy lên trên nội dung)."""
        self._container.clear_items()
        for item in new_container.children:
            self._container.add_item(item)


@tree.command(name="profile", description="Xem hồ sơ, level card, rank, UUID và ngày tham gia của bạn (hoặc người khác)")
@discord.app_commands.describe(thanh_vien="Xem của người khác (bỏ trống để xem của bạn)")
async def profile_cmd(interaction: discord.Interaction, thanh_vien: discord.Member = None):
    target = thanh_vien or interaction.user
    await interaction.response.defer()
    view = ProfileLayoutView(target=target, owner_id=interaction.user.id)
    container, file = await _build_profile_content(target)
    view._replace_container(container)
    await interaction.followup.send(view=view, file=file)


@tree.command(name="level", description="Xem level (cấp độ) hiện tại của bạn hoặc người khác, kèm ảnh thẻ level")
@discord.app_commands.describe(thanh_vien="Xem của người khác (bỏ trống để xem của bạn)")
async def level_cmd(interaction: discord.Interaction, thanh_vien: discord.Member = None):
    target = thanh_vien or interaction.user
    await interaction.response.defer()
    profile = await economy.get_profile(target.id)
    rank, _total = await _get_level_rank(target.id)
    buf = await level_card.render_level_card(
        display_name=target.display_name,
        avatar_url=target.display_avatar.replace(size=256).url,
        level=profile["level"],
        xp=profile["xp"],
        xp_needed=profile["xp_needed"],
        rank=rank,
    )
    file = discord.File(buf, filename="level.png")
    container = discord.ui.Container(accent_color=discord.Color.blurple())
    container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://level.png")))
    await interaction.followup.send(view=features.SimpleContainerLayout(container), file=file)


# ---------------------------------------------------------------------------
# Slash command: Minigame - Wordle / Đoán số / Kéo Búa Bao (chọn qua nút bấm)
#
# Mỗi ván có 1 ID riêng (vd #a1b2c3d4), nhập liệu qua Modal (form popup) thay
# vì gõ vào kênh chat - tránh người khác lỡ gõ giùm/gõ nhầm, và nhiều ván có
# thể chạy song song. Ván nào cũng tra lại được bằng lệnh /check-game.
# ---------------------------------------------------------------------------


class WordleGuessModal(discord.ui.Modal, title="Đoán từ Wordle"):
    tu = discord.ui.TextInput(label="Từ tiếng Anh 5 chữ cái", min_length=5, max_length=5, placeholder="vd: apple")

    def __init__(self, game_id: str, owner_id: int):
        super().__init__()
        self.game_id = game_id
        self.owner_id = owner_id

    async def on_submit(self, interaction: discord.Interaction):
        guess = str(self.tu).strip().lower()
        if not features.is_valid_guess(guess):
            await interaction.response.send_message("❌ Từ phải gồm đúng 5 chữ cái tiếng Anh (a-z)!", ephemeral=True)
            return

        result = await features.process_wordle_guess(self.game_id, guess)
        if result is None:
            await interaction.response.send_message("❌ Ván này đã kết thúc hoặc không tồn tại!", ephemeral=True)
            return

        container, finished = result
        await _append_ticket_footer(container, self.owner_id)
        if finished:
            layout = GameLayoutView(timeout=1)
            layout._set_container(container)
            await interaction.response.edit_message(view=layout)
            await _finish_minigame(interaction, self.owner_id)
        else:
            view = WordleView(self.game_id, self.owner_id)
            view._set_container(container)
            await interaction.response.edit_message(view=view)


class GameLayoutView(discord.ui.LayoutView):
    """LayoutView (Components V2) cơ sở dùng chung cho mọi minigame: 1
    Container hiện nội dung ván + 1 hoặc nhiều ActionRow chứa nút bấm, thay
    cho discord.ui.View + embed kiểu cũ."""

    def __init__(self, timeout: float | None):
        super().__init__(timeout=timeout)
        self._container = discord.ui.Container()
        self.add_item(self._container)
        self._action_rows: list[discord.ui.ActionRow] = []
        self._buttons: list[discord.ui.Button] = []

    def _set_container(self, new_container: discord.ui.Container) -> None:
        self._container.clear_items()
        for item in new_container.children:
            self._container.add_item(item)

    def _set_text(self, text: str) -> None:
        """Thay nội dung Container bằng 1 dòng text đơn giản (vd thông báo dừng ván)."""
        self._container.clear_items()
        self._container.add_item(discord.ui.TextDisplay(text))

    def _add_button(self, button: discord.ui.Button, row: int = 0) -> discord.ui.Button:
        while len(self._action_rows) <= row:
            new_row = discord.ui.ActionRow()
            self._action_rows.append(new_row)
            self.add_item(new_row)
        self._action_rows[row].add_item(button)
        self._buttons.append(button)
        return button

    def _disable_all_buttons(self) -> None:
        for b in self._buttons:
            b.disabled = True

    async def on_timeout(self):
        self._disable_all_buttons()


class StopGameButton(discord.ui.Button):
    """Nút Stop dùng chung cho mọi minigame: chỉ owner_id bấm được (interaction_check
    của View cha đã chặn người khác rồi, nút này chỉ xử lý dừng + dọn dẹp).
    Nếu ván có cược tiền (Tài Xỉu/Xì Dách) mà chưa có kết quả, tiền cược được hoàn lại."""

    def __init__(self, game_id: str, owner_id: int, row: int | None = None):
        super().__init__(label="Dừng ván", emoji="🛑", style=discord.ButtonStyle.danger, row=row)
        self.game_id = game_id
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction):
        ok, refunded = await features.stop_game(self.game_id, self.owner_id)
        if not ok:
            await interaction.response.send_message(
                "❌ Ván này đã kết thúc hoặc không còn để dừng!", ephemeral=True
            )
            return

        view: GameLayoutView = self.view
        text = f"🛑 Ván #{self.game_id} đã bị dừng."
        if refunded is not None:
            text += f" Đã hoàn lại tiền cược, số dư hiện tại: **{refunded} MICK**."
        view._disable_all_buttons()
        view._set_text(text)
        view.stop()
        await interaction.response.edit_message(view=view)


class WordleView(GameLayoutView):
    def __init__(self, game_id: str, owner_id: int):
        super().__init__(timeout=300)
        self.game_id = game_id
        self.owner_id = owner_id
        button = discord.ui.Button(label="Nhập từ", emoji="⌨️", style=discord.ButtonStyle.primary)
        button.callback = self._btn_input
        self._add_button(button)
        self._add_button(StopGameButton(game_id, owner_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                f"Đây không phải ván của bạn! Gõ `/trò-chơi` để mở ván riêng, hoặc `/check-game` với ID `{self.game_id}` để xem.",
                ephemeral=True,
            )
            return False
        return True

    async def _btn_input(self, interaction: discord.Interaction):
        await interaction.response.send_modal(WordleGuessModal(self.game_id, self.owner_id))


class GuessNumberModal(discord.ui.Modal, title="Đoán số"):
    def __init__(self, game_id: str, owner_id: int):
        super().__init__()
        self.game_id = game_id
        self.owner_id = owner_id
        self.so = discord.ui.TextInput(
            label=f"Nhập số nguyên (1-{GUESS_NUMBER_MAX})",
            max_length=6,
            placeholder="vd: 50",
        )
        self.add_item(self.so)

    async def on_submit(self, interaction: discord.Interaction):
        raw = str(self.so).strip()
        if not raw.lstrip("-").isdigit():
            await interaction.response.send_message("❌ Phải nhập số nguyên hợp lệ!", ephemeral=True)
            return

        result = await features.process_guess_number(self.game_id, int(raw))
        if result is None:
            await interaction.response.send_message("❌ Ván này đã kết thúc hoặc không tồn tại!", ephemeral=True)
            return

        container, finished = result
        await _append_ticket_footer(container, self.owner_id)
        if finished:
            layout = GameLayoutView(timeout=1)
            layout._set_container(container)
            await interaction.response.edit_message(view=layout)
            await _finish_minigame(interaction, self.owner_id)
        else:
            view = GuessNumberView(self.game_id, self.owner_id)
            view._set_container(container)
            await interaction.response.edit_message(view=view)


class GuessNumberView(GameLayoutView):
    def __init__(self, game_id: str, owner_id: int):
        super().__init__(timeout=300)
        self.game_id = game_id
        self.owner_id = owner_id
        button = discord.ui.Button(label="Nhập số", emoji="🔢", style=discord.ButtonStyle.primary)
        button.callback = self._btn_input
        self._add_button(button)
        self._add_button(StopGameButton(game_id, owner_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                f"Đây không phải ván của bạn! Dùng `/check-game` với ID `{self.game_id}` để xem.", ephemeral=True
            )
            return False
        return True

    async def _btn_input(self, interaction: discord.Interaction):
        await interaction.response.send_modal(GuessNumberModal(self.game_id, self.owner_id))


class RPSView(GameLayoutView):
    def __init__(self, game_id: str, owner_id: int):
        super().__init__(timeout=60)
        self.game_id = game_id
        self.owner_id = owner_id
        for label, emoji, choice in (("Kéo", "✂️", "keo"), ("Búa", "🪨", "bua"), ("Bao", "📄", "bao")):
            button = discord.ui.Button(label=label, emoji=emoji, style=discord.ButtonStyle.secondary)
            button.callback = self._make_callback(choice)
            self._add_button(button)
        self._add_button(StopGameButton(game_id, owner_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Đây không phải ván của bạn!", ephemeral=True)
            return False
        return True

    def _make_callback(self, choice: str):
        async def callback(interaction: discord.Interaction):
            await self._play(interaction, choice)
        return callback

    async def _play(self, interaction: discord.Interaction, choice: str):
        container = await features.process_rps(self.game_id, choice)
        if container is None:
            await interaction.response.send_message("❌ Ván này đã kết thúc hoặc không tồn tại!", ephemeral=True)
            return
        self._disable_all_buttons()
        await _append_ticket_footer(container, self.owner_id)
        self._set_container(container)
        await interaction.response.edit_message(view=self)
        await _finish_minigame(interaction, self.owner_id)
        self.stop()


class ChanLeView(GameLayoutView):
    def __init__(self, game_id: str, owner_id: int):
        super().__init__(timeout=60)
        self.game_id = game_id
        self.owner_id = owner_id
        for label, emoji, style, choice in (
            ("Chẵn", "🔵", discord.ButtonStyle.primary, "chan"),
            ("Lẻ", "🟠", discord.ButtonStyle.secondary, "le"),
        ):
            button = discord.ui.Button(label=label, emoji=emoji, style=style)
            button.callback = self._make_callback(choice)
            self._add_button(button)
        self._add_button(StopGameButton(game_id, owner_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Đây không phải ván của bạn!", ephemeral=True)
            return False
        return True

    def _make_callback(self, choice: str):
        async def callback(interaction: discord.Interaction):
            await self._play(interaction, choice)
        return callback

    async def _play(self, interaction: discord.Interaction, choice: str):
        container = await features.process_chanle(self.game_id, choice)
        if container is None:
            await interaction.response.send_message("❌ Ván này đã kết thúc hoặc không tồn tại!", ephemeral=True)
            return
        self._disable_all_buttons()
        await _append_ticket_footer(container, self.owner_id)
        self._set_container(container)
        await interaction.response.edit_message(view=self)
        await _finish_minigame(interaction, self.owner_id)
        self.stop()


class DoanMauView(GameLayoutView):
    def __init__(self, game_id: str, owner_id: int):
        super().__init__(timeout=60)
        self.game_id = game_id
        self.owner_id = owner_id
        for label, style, choice in (
            ("Đỏ", discord.ButtonStyle.danger, "do"),
            ("Xanh", discord.ButtonStyle.primary, "xanh"),
            ("Vàng", discord.ButtonStyle.secondary, "vang"),
            ("Tím", discord.ButtonStyle.secondary, "tim"),
        ):
            button = discord.ui.Button(label=label, style=style)
            button.callback = self._make_callback(choice)
            self._add_button(button)
        self._add_button(StopGameButton(game_id, owner_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Đây không phải ván của bạn!", ephemeral=True)
            return False
        return True

    def _make_callback(self, choice: str):
        async def callback(interaction: discord.Interaction):
            await self._play(interaction, choice)
        return callback

    async def _play(self, interaction: discord.Interaction, choice: str):
        container = await features.process_doanmau(self.game_id, choice)
        if container is None:
            await interaction.response.send_message("❌ Ván này đã kết thúc hoặc không tồn tại!", ephemeral=True)
            return
        self._disable_all_buttons()
        await _append_ticket_footer(container, self.owner_id)
        self._set_container(container)
        await interaction.response.edit_message(view=self)
        await _finish_minigame(interaction, self.owner_id)
        self.stop()


class VongQuayView(GameLayoutView):
    def __init__(self, game_id: str, owner_id: int):
        super().__init__(timeout=60)
        self.game_id = game_id
        self.owner_id = owner_id
        button = discord.ui.Button(label="Quay", emoji="🎡", style=discord.ButtonStyle.success)
        button.callback = self._btn_spin
        self._add_button(button)
        self._add_button(StopGameButton(game_id, owner_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Đây không phải ván của bạn!", ephemeral=True)
            return False
        return True

    async def _btn_spin(self, interaction: discord.Interaction):
        container = await features.process_vongquay(self.game_id)
        if container is None:
            await interaction.response.send_message("❌ Ván này đã kết thúc hoặc không tồn tại!", ephemeral=True)
            return
        self._disable_all_buttons()
        await _append_ticket_footer(container, self.owner_id)
        self._set_container(container)
        await interaction.response.edit_message(view=self)
        await _finish_minigame(interaction, self.owner_id)
        self.stop()


class BetAmountModal(discord.ui.Modal, title="Nhập số MICK muốn cược"):
    def __init__(self, game_kind: str):
        super().__init__()
        self.game_kind = game_kind  # "taixiu" hoặc "xidach"
        self.so_tien = discord.ui.TextInput(
            label="Số MICK muốn cược", max_length=10, placeholder="vd: 50"
        )
        self.add_item(self.so_tien)

    async def on_submit(self, interaction: discord.Interaction):
        raw = str(self.so_tien).strip()
        if not raw.isdigit() or int(raw) <= 0:
            await interaction.response.send_message("❌ Phải nhập số nguyên dương hợp lệ!", ephemeral=True)
            return

        bet = int(raw)
        if not await _require_ticket(interaction):
            return
        result = await economy.place_bet(interaction.user.id, bet)
        if not result["ok"]:
            reason = "Số dư không đủ!" if result["reason"] == "insufficient_funds" else "Số tiền không hợp lệ!"
            await interaction.response.send_message(f"❌ {reason}", ephemeral=True)
            await economy.add_ve(interaction.user.id, 1)  # hoàn vé vì ván không mở được
            return

        owner_id = interaction.user.id
        wallet_display = economy.format_mick(result["wallet"])
        if self.game_kind == "taixiu":
            gid, container = features.start_taixiu(owner_id, bet, wallet_display)
            await _append_ticket_footer(container, owner_id)
            view = TaiXiuView(gid, owner_id)
            view._set_container(container)
            await interaction.response.send_message(view=view)
        else:
            gid, container = features.start_xidach(owner_id, bet, wallet_display)
            await _append_ticket_footer(container, owner_id)
            view = XiDachView(gid, owner_id)
            view._set_container(container)
            await interaction.response.send_message(view=view)


class TaiXiuView(GameLayoutView):
    def __init__(self, game_id: str, owner_id: int):
        super().__init__(timeout=60)
        self.game_id = game_id
        self.owner_id = owner_id
        for label, emoji, style, choice in (
            ("Tài (11-18)", "⬆️", discord.ButtonStyle.success, "tai"),
            ("Xỉu (3-10)", "⬇️", discord.ButtonStyle.danger, "xiu"),
        ):
            button = discord.ui.Button(label=label, emoji=emoji, style=style)
            button.callback = self._make_callback(choice)
            self._add_button(button)
        self._add_button(StopGameButton(game_id, owner_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Đây không phải ván của bạn!", ephemeral=True)
            return False
        return True

    def _make_callback(self, choice: str):
        async def callback(interaction: discord.Interaction):
            await self._play(interaction, choice)
        return callback

    async def _play(self, interaction: discord.Interaction, choice: str):
        container = await features.process_taixiu(self.game_id, choice)
        if container is None:
            await interaction.response.send_message("❌ Ván này đã kết thúc hoặc không tồn tại!", ephemeral=True)
            return
        self._disable_all_buttons()
        await _append_ticket_footer(container, self.owner_id)
        self._set_container(container)
        await interaction.response.edit_message(view=self)
        await _finish_minigame(interaction, self.owner_id)
        self.stop()


class XiDachView(GameLayoutView):
    def __init__(self, game_id: str, owner_id: int):
        super().__init__(timeout=120)
        self.game_id = game_id
        self.owner_id = owner_id
        draw_btn = discord.ui.Button(label="Rút thêm", emoji="🃏", style=discord.ButtonStyle.primary)
        draw_btn.callback = self._btn_draw
        self._add_button(draw_btn)
        stand_btn = discord.ui.Button(label="Dằn bài", emoji="✋", style=discord.ButtonStyle.success)
        stand_btn.callback = self._btn_stand
        self._add_button(stand_btn)
        self._add_button(StopGameButton(game_id, owner_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Đây không phải ván của bạn!", ephemeral=True)
            return False
        return True

    async def _btn_draw(self, interaction: discord.Interaction):
        result = features.xidach_draw(self.game_id)
        if result is None:
            await interaction.response.send_message("❌ Ván này đã kết thúc hoặc không tồn tại!", ephemeral=True)
            return
        container, finished = result
        await _append_ticket_footer(container, self.owner_id)
        if finished:
            self._disable_all_buttons()
            self._set_container(container)
            await interaction.response.edit_message(view=self)
            await _finish_minigame(interaction, self.owner_id)
            self.stop()
        else:
            self._set_container(container)
            await interaction.response.edit_message(view=self)

    async def _btn_stand(self, interaction: discord.Interaction):
        container = await features.xidach_stand(self.game_id)
        if container is None:
            await interaction.response.send_message("❌ Ván này đã kết thúc hoặc không tồn tại!", ephemeral=True)
            return
        self._disable_all_buttons()
        await _append_ticket_footer(container, self.owner_id)
        self._set_container(container)
        await interaction.response.edit_message(view=self)
        await _finish_minigame(interaction, self.owner_id)
        self.stop()


class TriviaView(GameLayoutView):
    def __init__(self, game_id: str, owner_id: int, options: list[str]):
        super().__init__(timeout=TRIVIA_TIMEOUT_SEC)
        self.game_id = game_id
        self.owner_id = owner_id
        for i, opt in enumerate(options):
            self._add_button(self._make_button(i, opt), row=i // 5)
        self._add_button(StopGameButton(game_id, owner_id), row=(len(options) - 1) // 5 + 1)

    def _make_button(self, index: int, label: str) -> discord.ui.Button:
        button = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary)

        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("Đây không phải ván của bạn!", ephemeral=True)
                return
            container = await features.process_trivia(self.game_id, index)
            if container is None:
                await interaction.response.send_message("❌ Ván này đã kết thúc hoặc không tồn tại!", ephemeral=True)
                return
            self._disable_all_buttons()
            await _append_ticket_footer(container, self.owner_id)
            self._set_container(container)
            await interaction.response.edit_message(view=self)
            await _finish_minigame(interaction, self.owner_id)
            self.stop()

        button.callback = callback
        return button

    async def on_timeout(self):
        await features.trivia_timeout(self.game_id)
        self._disable_all_buttons()


async def _append_ticket_footer(container: discord.ui.Container, user_id: int):
    """Gắn số Vé còn lại vào cuối Container - hiển thị mỗi lần chơi game hoặc
    đang trong game (theo yêu cầu), không trừ thêm Vé."""
    ve = await economy.get_ve(user_id)
    line = f"{TICKET_EMOJI} Vé còn lại: {economy.format_ve(ve)}"
    container.add_item(discord.ui.TextDisplay(f"-# {line}"))


async def _require_ticket(interaction: discord.Interaction) -> bool:
    """Trừ GAME_TICKET_COST Vé để mở ván mới. Trả True nếu đủ (đã trừ xong),
    False nếu không đủ Vé (đã tự trả lời interaction luôn nên caller return ngay)."""
    result = await economy.spend_game_ticket(interaction.user.id)
    if not result["ok"]:
        await interaction.response.send_message(
            f"❌ Bạn hết Vé rồi! {TICKET_EMOJI} Vé hiện tại: {economy.format_ve(result['ve'])}. "
            "Nhận thêm Vé qua `/daily` (Daily) mỗi ngày.",
            ephemeral=True,
        )
        return False
    return True


async def _finish_minigame(interaction: discord.Interaction, owner_id: int):
    """Gọi khi 1 ván minigame vừa kết thúc: cập nhật quest + kiểm tra thành tựu."""
    await _bump_quest_and_notify_ctx(interaction.channel, interaction.user, "play_game_5")
    try:
        unlocked = await features.check_and_unlock_by_stats(owner_id)
        if unlocked:
            await features.announce_unlocks(interaction.channel, interaction.user, unlocked)
    except Exception as e:
        log.warning("Kiểm tra thành tựu sau minigame lỗi: %s", e)


class GameChooserView(GameLayoutView):
    def __init__(self, owner_id: int):
        super().__init__(timeout=30)
        self.owner_id = owner_id
        self._container.add_item(
            discord.ui.TextDisplay(
                "### 🎮 Chọn minigame\n"
                "Bấm nút bên dưới để chơi. Mỗi ván có 1 ID riêng, tra lại bằng `/check-game`.\n"
                "Mọi ván đều có nút 🛑 **Dừng ván** nếu muốn thoát giữa chừng.\n\n"
                "🎲 **Tài Xỉu** và 🃏 **Xì Dách** ăn thua MICK thật (cược tự do, miễn đủ số dư) — "
                "các game còn lại chơi miễn phí, thắng nhận thưởng cố định (riêng 🎡 **Vòng Quay May Mắn** "
                "không bao giờ thua, chỉ random mức thưởng)."
            )
        )

        row0 = (
            ("Wordle", "🟩", discord.ButtonStyle.success, self._btn_wordle),
            ("Đoán số", "🔢", discord.ButtonStyle.primary, self._btn_guess_number),
            ("Kéo Búa Bao", "✊", discord.ButtonStyle.secondary, self._btn_rps),
        )
        row1 = (
            ("Trivia (đố vui)", "🧠", discord.ButtonStyle.primary, self._btn_trivia),
            ("Tài Xỉu (cược MICK)", "🎲", discord.ButtonStyle.danger, self._btn_taixiu),
            ("Xì Dách (cược MICK)", "🃏", discord.ButtonStyle.danger, self._btn_xidach),
        )
        row2 = (
            ("Chẵn Lẻ", "🎲", discord.ButtonStyle.primary, self._btn_chanle),
            ("Đoán Màu", "🎨", discord.ButtonStyle.primary, self._btn_doanmau),
            ("Vòng Quay May Mắn", "🎡", discord.ButtonStyle.success, self._btn_vongquay),
        )
        for row_index, row_defs in enumerate((row0, row1, row2)):
            for label, emoji, style, handler in row_defs:
                button = discord.ui.Button(label=label, emoji=emoji, style=style)
                button.callback = handler
                self._add_button(button, row=row_index)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Dùng `/trò-chơi` để chơi phần của riêng bạn!", ephemeral=True)
            return False
        return True

    async def _btn_wordle(self, interaction: discord.Interaction):
        existing_gid = features.user_active_wordle_id(self.owner_id)
        if existing_gid:
            await interaction.response.send_message(
                f"Bạn đang có ván Wordle **#{existing_gid}** chưa xong! Dùng `/check-game` với ID đó để xem lại.",
                ephemeral=True,
            )
            return
        if not await _require_ticket(interaction):
            return
        gid, container = features.start_wordle(self.owner_id)
        await _append_ticket_footer(container, self.owner_id)
        view = WordleView(gid, self.owner_id)
        view._set_container(container)
        await interaction.response.edit_message(view=view)

    async def _btn_guess_number(self, interaction: discord.Interaction):
        if not await _require_ticket(interaction):
            return
        gid, container = features.start_guess_number(self.owner_id)
        await _append_ticket_footer(container, self.owner_id)
        view = GuessNumberView(gid, self.owner_id)
        view._set_container(container)
        await interaction.response.edit_message(view=view)

    async def _btn_rps(self, interaction: discord.Interaction):
        if not await _require_ticket(interaction):
            return
        gid, container = features.start_rps(self.owner_id)
        await _append_ticket_footer(container, self.owner_id)
        view = RPSView(gid, self.owner_id)
        view._set_container(container)
        await interaction.response.edit_message(view=view)

    async def _btn_trivia(self, interaction: discord.Interaction):
        if not await _require_ticket(interaction):
            return
        gid, container, options = features.start_trivia(self.owner_id)
        await _append_ticket_footer(container, self.owner_id)
        view = TriviaView(gid, self.owner_id, options)
        view._set_container(container)
        await interaction.response.edit_message(view=view)

    async def _btn_taixiu(self, interaction: discord.Interaction):
        await interaction.response.send_modal(BetAmountModal("taixiu"))

    async def _btn_xidach(self, interaction: discord.Interaction):
        await interaction.response.send_modal(BetAmountModal("xidach"))

    async def _btn_chanle(self, interaction: discord.Interaction):
        if not await _require_ticket(interaction):
            return
        gid, container = features.start_chanle(self.owner_id)
        await _append_ticket_footer(container, self.owner_id)
        view = ChanLeView(gid, self.owner_id)
        view._set_container(container)
        await interaction.response.edit_message(view=view)

    async def _btn_doanmau(self, interaction: discord.Interaction):
        if not await _require_ticket(interaction):
            return
        gid, container = features.start_doanmau(self.owner_id)
        await _append_ticket_footer(container, self.owner_id)
        view = DoanMauView(gid, self.owner_id)
        view._set_container(container)
        await interaction.response.edit_message(view=view)

    async def _btn_vongquay(self, interaction: discord.Interaction):
        if not await _require_ticket(interaction):
            return
        gid, container = features.start_vongquay(self.owner_id)
        await _append_ticket_footer(container, self.owner_id)
        view = VongQuayView(gid, self.owner_id)
        view._set_container(container)
        await interaction.response.edit_message(view=view)


@tree.command(
    name="game",
    description="Chơi minigame: Wordle, Đoán số, Kéo Búa Bao, Trivia, Tài Xỉu, Xì Dách, Chẵn Lẻ, Đoán Màu, Vòng Quay",
)
async def game_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(view=GameChooserView(interaction.user.id))


@tree.command(name="check-game", description="Tra thông tin/trạng thái 1 ván minigame theo ID")
@discord.app_commands.describe(id_van="ID ván game (vd: a1b2c3d4), xem ở footer embed ván chơi")
async def tra_game_cmd(interaction: discord.Interaction, id_van: str):
    container = features.lookup_game(id_van)
    if container is None:
        await interaction.response.send_message(
            f"❌ Không tìm thấy ván với ID `{id_van}` (gõ sai hoặc đã hết hạn tra).", ephemeral=True
        )
        return
    await interaction.response.send_message(view=features.SimpleContainerLayout(container), ephemeral=True)


# ---------------------------------------------------------------------------
# Slash commands: Leaderboard
# ---------------------------------------------------------------------------


# Số dòng hiển thị mỗi trang leaderboard + nút "Xem thêm" bấm để lộ thêm
# 1 trang nữa (không load lại toàn bộ danh sách, chỉ tăng số dòng hiển thị).
_LEADERBOARD_PAGE_SIZE = 10
_LEADERBOARD_MAX_ENTRIES = 50


def _build_leaderboard_container(interaction: discord.Interaction, users: list, sort_key: str, shown: int) -> discord.ui.Container:
    top = users[:shown]
    lines = []
    for i, (uid, data) in enumerate(top, start=1):
        member = interaction.guild.get_member(int(uid)) if interaction.guild else None
        name = member.display_name if member else f"User {uid}"
        is_owner_row = economy.is_owner(int(uid))
        mick_display = economy.OWNER_DISPLAY_AMOUNT if is_owner_row else data.get("mick", 0)
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
        crown = " 👑" if is_owner_row else ""
        if sort_key == "level":
            lines.append(f"{medal} **{name}**{crown} — Level {data.get('level', 0)} ({MICKCOIN_EMOJI} {mick_display})")
        else:
            lines.append(f"{medal} **{name}**{crown} — {MICKCOIN_EMOJI} {mick_display} (Level {data.get('level', 0)})")

    title = "🏆 Xếp hạng Level" if sort_key == "level" else "🏆 Xếp hạng MICK Coin"
    footer = None
    if shown < len(users) and shown < _LEADERBOARD_MAX_ENTRIES:
        footer = f"Đang hiện {min(shown, len(users))}/{min(len(users), _LEADERBOARD_MAX_ENTRIES)} — bấm \"Xem thêm\" để xem tiếp"
    return features.build_container(
        title=title,
        description="\n".join(lines) or "Chưa có dữ liệu",
        color=discord.Color.orange(),
        footer=footer,
    )


class LeaderboardView(discord.ui.LayoutView):
    """Components V2 - thay cho LeaderboardView (embed) cũ. Bảng xếp hạng có
    nút 'Xem thêm' (lộ thêm 1 trang) + select để xem nhanh hồ sơ 1 người
    trong danh sách. Hồ sơ của owner bị khoá - chỉ chính owner mới xem được
    qua đây, người khác bấm vào sẽ bị báo lỗi."""

    def __init__(self, interaction: discord.Interaction, users: list, sort_key: str, owner_id: int):
        super().__init__(timeout=120)
        self._interaction = interaction
        self.users = users[:_LEADERBOARD_MAX_ENTRIES]
        self.sort_key = sort_key
        self.owner_id = owner_id  # id người đã gõ lệnh /leaderboard (không phải chủ bot)
        self.shown = min(_LEADERBOARD_PAGE_SIZE, len(self.users))

        self._container = discord.ui.Container()
        self.add_item(self._container)
        self._select_row = discord.ui.ActionRow()
        self.add_item(self._select_row)
        self._button_row = discord.ui.ActionRow()
        self.add_item(self._button_row)

        self._select: discord.ui.Select | None = None
        self.btn_more = discord.ui.Button(label="Xem thêm", emoji="➕", style=discord.ButtonStyle.secondary)
        self.btn_more.callback = self._on_more
        self._button_row.add_item(self.btn_more)

        self._render()

    def _render(self):
        new_container = _build_leaderboard_container(self._interaction, self.users, self.sort_key, self.shown)
        self._container.clear_items()
        for item in new_container.children:
            self._container.add_item(item)
        self._sync_more_button()
        self._rebuild_select()

    def _sync_more_button(self):
        self.btn_more.disabled = self.shown >= len(self.users)

    def _rebuild_select(self):
        self._select_row.clear_items()
        top = self.users[: self.shown]
        if not top:
            self._select = None
            return
        options = []
        for i, (uid, data) in enumerate(top, start=1):
            options.append(discord.SelectOption(label=f"#{i} · {data.get('_display_name', uid)}", value=uid))
        select = discord.ui.Select(placeholder="🔍 Xem hồ sơ 1 người trong bảng...", options=options[:25])
        select.callback = self._on_select
        self._select = select
        self._select_row.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        target_id = int(self._select.values[0])

        if economy.is_owner(target_id) and interaction.user.id != target_id:
            await interaction.response.send_message(
                "Yoo bro, bro không có quyền truy cập hồ sơ của owner trừ owner 👑",
                ephemeral=True,
            )
            return

        member = interaction.guild.get_member(target_id) if interaction.guild else None
        if member is None:
            await interaction.response.send_message("Không tìm thấy thành viên này trong server.", ephemeral=True)
            return

        container, file = await _build_profile_content(member)
        await interaction.response.send_message(view=_SimpleContainerLayout(container), file=file, ephemeral=True)

    async def _on_more(self, interaction: discord.Interaction):
        self.shown = min(self.shown + _LEADERBOARD_PAGE_SIZE, len(self.users))
        self._render()
        await interaction.response.edit_message(view=self)


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
    users = users[:_LEADERBOARD_MAX_ENTRIES]

    # Gắn sẵn display_name vào data để select box dùng lại, đỡ phải gọi
    # guild.get_member() lần nữa lúc build select.
    for uid, data in users:
        member = interaction.guild.get_member(int(uid)) if interaction.guild else None
        data["_display_name"] = member.display_name if member else f"User {uid}"

    view = LeaderboardView(interaction, users, sort_key, owner_id=interaction.user.id)
    await interaction.followup.send(view=view)


# ---------------------------------------------------------------------------
# Slash commands: Thành tựu + Quest
# ---------------------------------------------------------------------------


@tree.command(name="achievements", description="Xem danh sách thành tựu")
async def achievements_cmd(interaction: discord.Interaction):
    user = await db.get_user(interaction.user.id)
    container = features.build_list_container(user.get("achievements", []))
    await interaction.response.send_message(view=features.SimpleContainerLayout(container))


@tree.command(name="quest", description="Xem quest hằng ngày của bạn")
async def quest_cmd(interaction: discord.Interaction):
    user = await features.get_today_quests(interaction.user.id)

    invite_link = None
    if "invite_friends" in user.get("quest_ids", []) and "invite_friends" not in user.get("quest_done", []):
        if interaction.guild is not None:
            invite_link = await _get_or_create_invite_link(interaction.guild, interaction.user, user)

    container = features.build_quest_container(user, interaction.user.display_name, invite_link=invite_link)
    await interaction.response.send_message(view=features.SimpleContainerLayout(container))


@tree.command(
    name="daily",
    description="Nhận Daily ngay (dùng khi embed Daily bị trôi tin nhắn) - còn hạn từ 0h đến 12h trưa",
)
async def diem_danh_cmd(interaction: discord.Interaction):
    await features.claim_daily(interaction)


# ---------------------------------------------------------------------------
# Slash commands: Chuyển MICK (delay theo số tiền) + ATM
# ---------------------------------------------------------------------------


_OTP_FAIL_REASONS = {
    "not_found": "Không tìm thấy giao dịch nào đang chờ xác minh (có thể đã hết hạn hoặc chưa từng tạo).",
    "expired": "Mã OTP đã hết hạn. Dùng lại `/chuyển-tiền` để nhận mã mới.",
    "wrong_code": "Mã OTP không đúng!",
    "too_many_attempts": "Nhập sai quá số lần cho phép, giao dịch đã bị huỷ. Dùng lại `/chuyển-tiền` để thử lại.",
}


class TransferOtpModal(discord.ui.Modal, title="Xác minh chuyển tiền"):
    def __init__(self, sender_id: int, receiver: discord.Member, amount: int):
        super().__init__()
        self.sender_id = sender_id
        self.receiver = receiver
        self.amount = amount
        self.ma_otp = discord.ui.TextInput(
            label="Nhập mã OTP đã gửi qua DM",
            min_length=TRANSFER_OTP_LENGTH,
            max_length=TRANSFER_OTP_LENGTH,
            placeholder="vd: 123456",
        )
        self.add_item(self.ma_otp)

    async def on_submit(self, interaction: discord.Interaction):
        verify = features.verify_transfer_otp(self.sender_id, str(self.ma_otp).strip())
        if not verify["ok"]:
            msg = _OTP_FAIL_REASONS.get(verify["reason"], "Xác minh thất bại.")
            await interaction.response.send_message(f"❌ {msg}", ephemeral=True)
            return

        # OTP đúng -> giờ mới thực sự trừ/cộng tiền (atomic, có lock trong transfer_mick).
        delay = economy.transfer_delay_seconds(self.amount)
        await interaction.response.send_message(
            f"✅ Xác minh thành công! Đang xử lý chuyển **{self.amount} MICK** cho {self.receiver.mention}... "
            f"(mất khoảng **{delay:.0f} giây**, tiền càng cao xử lý càng lâu)"
        )
        await asyncio.sleep(delay)

        result = await economy.transfer_mick(self.sender_id, self.receiver.id, self.amount)
        if result["ok"]:
            await interaction.followup.send(
                f"✅ Đã chuyển **{self.amount} MICK** từ <@{self.sender_id}> đến {self.receiver.mention}!\n"
                f"Số dư người gửi: **{result['from_balance']} MICK**"
            )
            try:
                await self.receiver.send(
                    f"💰 Bạn đã nhận **{self.amount} Mick** từ <@{self.sender_id}>! "
                    f"Số dư hiện tại: **{result['to_balance']} MICK**"
                )
            except Exception:
                pass  # người nhận tắt DM -> bỏ qua, không chặn giao dịch
        else:
            await interaction.followup.send(f"❌ Chuyển tiền thất bại ({result['reason']}). MICK chưa bị trừ.")


class TransferOtpView(discord.ui.View):
    def __init__(self, sender_id: int, receiver: discord.Member, amount: int):
        super().__init__(timeout=TRANSFER_OTP_TTL_SEC)
        self.sender_id = sender_id
        self.receiver = receiver
        self.amount = amount

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.sender_id:
            await interaction.response.send_message("Đây không phải giao dịch của bạn!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Nhập mã OTP", emoji="🔐", style=discord.ButtonStyle.primary)
    async def btn_enter_otp(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TransferOtpModal(self.sender_id, self.receiver, self.amount))

    @discord.ui.button(label="Huỷ", emoji="✖️", style=discord.ButtonStyle.secondary)
    async def btn_cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        features.cancel_transfer_otp(self.sender_id)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="✖️ Đã huỷ giao dịch.", view=self)
        self.stop()

    async def on_timeout(self):
        features.cancel_transfer_otp(self.sender_id)
        for child in self.children:
            child.disabled = True


_MILESTONE_CODE_FAIL_REASONS = {
    "not_found": "❌ Code không tồn tại hoặc đã sai!",
    "expired": "⌛ Code này đã hết hạn rồi!",
    "already_used": "⚠️ Bạn đã nhập code này rồi, không nhập lại được nữa!",
    "full": "🚫 Code này đã hết lượt nhập rồi, nhanh tay lần sau nhé!",
}


@tree.command(name="code", description="Nhập code quà mốc thành viên server (giới hạn số lượt + thời gian)")
@discord.app_commands.describe(code="Code nhận được ở kênh thông báo mốc thành viên")
async def nhap_code_cmd(interaction: discord.Interaction, code: str):
    result = await features.redeem_milestone_code(interaction.user.id, code)
    if not result["ok"]:
        msg = _MILESTONE_CODE_FAIL_REASONS.get(result["reason"], "Nhập code thất bại.")
        await interaction.response.send_message(msg, ephemeral=True)
        return

    await interaction.response.send_message(
        f"🎉 Nhập code thành công! Nhận **{result['reward']} MICK** (số dư: {result['new_balance']}). "
        f"Code này còn **{result['remaining']}** lượt nhập.",
        ephemeral=True,
    )


@tree.command(name="transfer-money", description="Chuyển MICK cho người khác (cần xác minh OTP qua DM, tiền càng cao xử lý càng lâu)")
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

    code = features.create_transfer_otp(interaction.user.id, nguoi_nhan.id, so_tien)
    try:
        await interaction.user.send(
            f"🔐 Mã OTP xác minh chuyển **{so_tien} MICK** cho {nguoi_nhan.mention}: **{code}**\n"
            f"Mã có hiệu lực trong **{TRANSFER_OTP_TTL_SEC} giây**. Không chia sẻ mã này cho ai."
        )
    except discord.Forbidden:
        features.cancel_transfer_otp(interaction.user.id)
        await interaction.response.send_message(
            "❌ Không gửi được DM cho bạn! Hãy bật nhận tin nhắn riêng từ thành viên server rồi thử lại.",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        f"📨 Đã gửi mã OTP qua DM. Nhập mã trong **{TRANSFER_OTP_TTL_SEC} giây** để xác nhận chuyển "
        f"**{so_tien} MICK** cho {nguoi_nhan.mention}.",
        view=TransferOtpView(interaction.user.id, nguoi_nhan, so_tien),
        ephemeral=True,
    )


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
        container = features.build_container(
            title="🏧 ATM MICK Coin",
            color=discord.Color.blue(),
            fields=[
                ("Ví (tiêu xài)", f"{info['wallet']} 🪙"),
                ("ATM (giữ hộ)", f"{info['atm']} 🪙"),
            ],
        )
        await interaction.response.send_message(view=features.SimpleContainerLayout(container), ephemeral=True)
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
                text = f"🎉 Đã mở **{label}**! Tốn **{result['cost']} MICK**. Bấm nút \"Thuê nhân viên\" ở `/kinh-doanh` để thuê người."
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
                    "not_opened": f"❌ Bạn chưa mở **{label}**! Bấm nút \"Mở cơ sở mới\" ở `/kinh-doanh` trước.",
                    "max_staff": f"❌ **{label}** đã thuê tối đa nhân viên rồi!",
                    "insufficient_funds": f"❌ Không đủ MICK! Cần **{result.get('cost')} MICK** để thuê.",
                }
                text = messages_map.get(reason, "❌ Có lỗi xảy ra.")

        await interaction.response.edit_message(content=text, view=None)


class BusinessActionView(discord.ui.View):
    def __init__(self, action: str, owner_id: int):
        super().__init__(timeout=60)
        self.add_item(BusinessKindSelect(action, owner_id))


class BusinessView(discord.ui.LayoutView):
    """Components V2 - thay cho BusinessView (embed) cũ. Gộp /business +
    /open_business + /hire cũ: 3 nút trên cùng 1 message."""

    def __init__(self, owner_id: int, container: discord.ui.Container):
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self._container = container
        self.add_item(self._container)

        row = discord.ui.ActionRow()
        self.add_item(row)

        btn_view = discord.ui.Button(label="Xem tổng quan", emoji="📊", style=discord.ButtonStyle.primary)
        btn_view.callback = self._btn_view
        row.add_item(btn_view)

        btn_open = discord.ui.Button(label="Mở cơ sở mới", emoji="🏪", style=discord.ButtonStyle.success)
        btn_open.callback = self._btn_open
        row.add_item(btn_open)

        btn_hire = discord.ui.Button(label="Thuê nhân viên", emoji="👥", style=discord.ButtonStyle.secondary)
        btn_hire.callback = self._btn_hire
        row.add_item(btn_hire)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Dùng `/kinh-doanh` để xem cơ ngơi của riêng bạn!", ephemeral=True)
            return False
        return True

    def _set_container(self, new_container: discord.ui.Container) -> None:
        self._container.clear_items()
        for item in new_container.children:
            self._container.add_item(item)

    async def _btn_view(self, interaction: discord.Interaction):
        summary = await features.get_summary(self.owner_id)
        new_container = features.build_summary_container(interaction.user.display_name, summary)
        self._set_container(new_container)
        await interaction.response.edit_message(view=self)

    async def _btn_open(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "Chọn loại hình muốn mở:", view=BusinessActionView("open", self.owner_id), ephemeral=True
        )

    async def _btn_hire(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "Chọn loại hình muốn thuê thêm nhân viên:", view=BusinessActionView("hire", self.owner_id), ephemeral=True
        )


@tree.command(name="business", description="Xem, mở cơ sở mới, hoặc thuê nhân viên cho cơ ngơi kinh doanh")
async def business_cmd(interaction: discord.Interaction):
    summary = await features.get_summary(interaction.user.id)
    container = features.build_summary_container(interaction.user.display_name, summary)
    await interaction.response.send_message(view=BusinessView(interaction.user.id, container))


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
                f"🤔 Bot chưa biết nghĩa của **{word.strip().lower()}**. Bấm nút \"Dạy từ\" ở `/từ-điển` để dạy bot nhé!",
                ephemeral=True,
            )


class DictionaryView(discord.ui.LayoutView):
    """Components V2 - thay cho DictionaryView (embed) cũ."""

    def __init__(self):
        super().__init__(timeout=120)
        container = features.build_container(
            title="📚 Từ điển server",
            description="Bấm nút bên dưới để tra 1 từ đã học, hoặc dạy bot nghĩa 1 từ mới.",
            color=discord.Color.blue(),
        )
        self.add_item(container)
        row = discord.ui.ActionRow()
        self.add_item(row)

        btn_lookup = discord.ui.Button(label="Tra từ", emoji="📖", style=discord.ButtonStyle.primary)
        btn_lookup.callback = self._btn_lookup
        row.add_item(btn_lookup)

        btn_teach = discord.ui.Button(label="Dạy từ", emoji="✏️", style=discord.ButtonStyle.success)
        btn_teach.callback = self._btn_teach
        row.add_item(btn_teach)

    async def _btn_lookup(self, interaction: discord.Interaction):
        await interaction.response.send_modal(LookupWordModal())

    async def _btn_teach(self, interaction: discord.Interaction):
        await interaction.response.send_modal(TeachWordModal())


@tree.command(name="dictionary", description="Tra hoặc dạy bot nghĩa từ/cụm từ lóng trong server")
async def tudien_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(view=DictionaryView(), ephemeral=True)


@tree.command(name="ask-ai", description="Chat trực tiếp với AI của bot (Groq)")
@discord.app_commands.describe(noi_dung="Bạn muốn nói gì với bot?")
async def ai_cmd(interaction: discord.Interaction, noi_dung: str):
    await interaction.response.defer()
    messages = [
        {"role": "system", "content": ai_chat.SYSTEM_PROMPT},
        {"role": "user", "content": noi_dung},
    ]

    if ai_chat.needs_web_search(noi_dung):
        status_msg = await interaction.followup.send("-# 🔎 đang tìm kiếm trên mạng...", wait=True)
        result, ok, reason = await ai_chat._tavily_search_answer(ai_chat.SYSTEM_PROMPT, noi_dung)
        if ok and result:
            final_text = ai_chat._sanitize_ai_output(result)
            if len(final_text) > 2000:
                final_text = final_text[:1990] + "…"
        else:
            final_text = (
                "-# ⚠️ tìm kiếm lỗi\n"
                "Sorry, hiện mình tìm kiếm không được, thử hỏi lại sau nha 🙏\n"
                f"-# lý do: {reason}"
            )
        await status_msg.edit(content=final_text)
        return

    reply_text = await ai_chat._groq_chat(messages)
    if reply_text:
        await interaction.followup.send(
            ai_chat._sanitize_ai_output(reply_text), allowed_mentions=discord.AllowedMentions.none()
        )
    else:
        await interaction.followup.send("😵 AI hiện chưa sẵn sàng (thiếu GROQ_API_KEY hoặc lỗi kết nối).")


# ---------------------------------------------------------------------------
# Slash command: Thú tội ẩn danh (/confession) - đăng vào CONFESSION_CHANNEL_ID
# dưới dạng embed đánh số thứ tự, không hiện tên/avatar người gửi. Mỗi lần
# gửi -> 1 mã "ID" NGẪU NHIÊN riêng (không liên quan tới user, không lặp lại
# giữa các lần gửi kể cả cùng 1 người), lưu vào Firestore kèm user ID thật
# (xem db.save_confession) - CHỈ để dev tra cứu trực tiếp trong Firebase khi
# có report/lạm dụng, không có lệnh bot nào tra ngược lại được. Nhập nội
# dung qua Modal (mở form) vì input chỉ hiện trong tương tác riêng của user.
# ---------------------------------------------------------------------------

_confession_last_ts: dict[int, float] = {}


class ConfessionModal(discord.ui.Modal, title="Gửi thú tội ẩn danh"):
    noi_dung = discord.ui.TextInput(
        label="Nội dung thú tội (ẩn danh 100%)",
        style=discord.TextStyle.paragraph,
        max_length=1900,
        placeholder="Kể đi, không ai biết là bạn đâu...",
    )

    async def on_submit(self, interaction: discord.Interaction):
        now = time.time()
        last = _confession_last_ts.get(interaction.user.id, 0)
        remaining = CONFESSION_COOLDOWN_SEC - (now - last)
        if remaining > 0:
            await interaction.response.send_message(
                f"⏳ Gửi hơi nhanh, đợi thêm **{int(remaining) + 1}s** rồi gửi thú tội tiếp nhé!",
                ephemeral=True,
            )
            return

        channel = client.get_channel(CONFESSION_CHANNEL_ID)
        if channel is None:
            await interaction.response.send_message(
                "❌ Bot chưa tìm thấy kênh thú tội (chưa cấu hình hoặc bot không có quyền xem kênh).",
                ephemeral=True,
            )
            return

        text = ai_chat._sanitize_ai_output(str(self.noi_dung)).strip()
        if not text:
            await interaction.response.send_message("❌ Nội dung thú tội trống.", ephemeral=True)
            return

        _confession_last_ts[interaction.user.id] = now
        number = await db.next_confession_number()
        random_id = secrets.token_urlsafe(24)  # random 100%, KHÔNG suy ra được từ user ID
        await db.save_confession(random_id, {"user_id": interaction.user.id, "number": number, "ts": now})

        unix_ts = int(datetime.now(timezone.utc).timestamp())
        container = features.build_container(
            title=f"Lời Thú Tội Ẩn Danh #{number}",
            description=f"{text}\n\n-# ID: {random_id} · <t:{unix_ts}:f>",
            color=discord.Color.dark_purple(),
            footer="Thú tội ẩn danh · không thể truy ra người gửi",
        )

        await channel.send(
            view=features.SimpleContainerLayout(container),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await interaction.response.send_message(
            f"✅ Đã gửi thú tội **#{number}** ẩn danh vào {channel.mention}!", ephemeral=True
        )


@tree.command(name="confession", description="Gửi 1 lời thú tội ẩn danh 100% vào kênh thú tội")
async def confession_cmd(interaction: discord.Interaction):
    await interaction.response.send_modal(ConfessionModal())


# ---------------------------------------------------------------------------
# Slash command: Help - liệt kê toàn bộ lệnh theo nhóm, bấm nút để xem chi tiết
# ---------------------------------------------------------------------------

_HELP_CATEGORIES = [
    {
        "key": "level",
        "label": "Level & Kinh tế",
        "emoji": "📊",
        "commands": [
            ("/profile [thanh_vien]", "Hồ sơ · Level card ảnh · Rank · UUID · ngày tham gia — bấm nút để chuyển view"),
            ("/level [thanh_vien]", "Xem level (cấp độ) hiện tại của bạn hoặc người khác dưới dạng ảnh thẻ level"),
            ("/leaderboard [loai]", "Bảng xếp hạng theo Level hoặc theo MICK Coin"),
            ("/atm [hanh_dong] [so_tien]", "Gửi/rút MICK vào ATM, hoặc xem số dư"),
            ("/transfer-money [nguoi_nhan] [so_tien]", "Chuyển MICK cho người khác (cần xác minh mã OTP gửi qua DM)"),
        ],
    },
    {
        "key": "game",
        "label": "Minigame & Quest",
        "emoji": "🎮",
        "commands": [
            ("/game", "Chơi Wordle · Đoán số · Kéo Búa Bao · Trivia · Chẵn Lẻ · Đoán Màu · Vòng Quay May Mắn · 🎲 Tài Xỉu · 🃏 Xì Dách (2 game cuối cược MICK thật)"),
            ("/check-game [id_van]", "Tra thông tin/trạng thái 1 ván minigame theo ID riêng"),
            ("/quest", "Xem quest hằng ngày của bạn"),
            ("/daily", "Nhận Daily ngay (phòng khi embed Daily bị trôi tin nhắn) - còn hạn tới 12h trưa"),
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
            ("/ask-ai [noi_dung]", "Chat trực tiếp với AI của bot"),
            ("/dictionary", "Tra từ hoặc dạy bot nghĩa từ mới — bấm nút, nhập qua form"),
        ],
    },
    {
        "key": "confession",
        "label": "Thú tội ẩn danh",
        "emoji": "🤫",
        "commands": [
            ("/confession", "Gửi 1 lời thú tội ẩn danh 100% vào kênh thú tội — nhập qua form"),
        ],
    },
]


def _fill_help_container(container: discord.ui.Container, cat: dict | None) -> None:
    """Đổ nội dung 1 nhóm lệnh (hoặc màn hình chào ban đầu nếu cat=None) vào
    container có sẵn - dùng chung cho lúc khởi tạo lẫn khi bấm đổi nhóm,
    tránh phải tạo/gắn lại Container mới (giữ đúng vị trí trong LayoutView)."""
    container.clear_items()
    if cat is None:
        container.add_item(discord.ui.TextDisplay("# 📖 Trợ giúp"))
        container.add_item(discord.ui.TextDisplay("Bấm nút bên dưới để xem lệnh theo từng nhóm."))
    else:
        container.add_item(discord.ui.TextDisplay(f"# {cat['emoji']} {cat['label']}"))
        container.add_item(discord.ui.Separator(visible=True))
        body = "\n\n".join(f"**{name}**\n{desc}" for name, desc in cat["commands"])
        container.add_item(discord.ui.TextDisplay(body))


class HelpLayoutView(discord.ui.LayoutView):
    """Components V2 - có Separator thật, thay cho HelpView (embed) cũ."""

    def __init__(self):
        super().__init__(timeout=180)
        self._container = discord.ui.Container()
        _fill_help_container(self._container, None)
        self.add_item(self._container)
        row = discord.ui.ActionRow()
        for cat in _HELP_CATEGORIES:
            row.add_item(self._make_button(cat))
        self.add_item(row)

    def _make_button(self, cat: dict) -> discord.ui.Button:
        button = discord.ui.Button(label=cat["label"], emoji=cat["emoji"], style=discord.ButtonStyle.secondary)

        async def callback(interaction: discord.Interaction):
            _fill_help_container(self._container, cat)
            await interaction.response.edit_message(view=self)

        button.callback = callback
        return button


@tree.command(name="help", description="Xem danh sách lệnh của bot theo từng nhóm")
async def help_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(view=HelpLayoutView(), ephemeral=True)
