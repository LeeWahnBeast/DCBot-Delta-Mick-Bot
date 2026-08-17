"""
Hệ thống Daily: đúng 0h sáng giờ VN (UTC+7) đăng embed có nút "Nhận Daily".
Nhận càng trễ thì MICK càng ít (giảm DAILY_DECAY_RATE mỗi giờ), sàn DAILY_MIN_REWARD.
Hết hạn đúng DAILY_WINDOW_HOURS giờ sáng (mặc định 7h).
"""

import time
from datetime import datetime, timedelta, timezone

import discord

import db
import economy
from config import (
    VN_UTC_OFFSET_HOURS,
    DAILY_BASE_REWARD,
    DAILY_DECAY_RATE,
    DAILY_MIN_REWARD,
    DAILY_WINDOW_HOURS,
)

VN_TZ = timezone(timedelta(hours=VN_UTC_OFFSET_HOURS))
DAILY_CLAIM_CUSTOM_ID = "daily_claim_btn"


def vn_now() -> datetime:
    return datetime.now(VN_TZ)


def vn_today_str() -> str:
    return vn_now().strftime("%Y-%m-%d")


def compute_daily_reward(hours_elapsed: int) -> int:
    hours_elapsed = max(0, hours_elapsed)
    amount = DAILY_BASE_REWARD * ((1 - DAILY_DECAY_RATE) ** hours_elapsed)
    return max(DAILY_MIN_REWARD, round(amount))


def build_daily_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🎁 Daily hàng ngày",
        description=(
            f"Bấm nút bên dưới để nhận MICK miễn phí!\n"
            f"Nhận ngay lúc 0h: **{DAILY_BASE_REWARD} MICK**. "
            f"Càng nhận trễ, MICK càng giảm {int(DAILY_DECAY_RATE * 100)}%/giờ "
            f"(tối thiểu **{DAILY_MIN_REWARD} MICK**).\n"
            f"⏰ Hết hạn lúc **{DAILY_WINDOW_HOURS}:00 sáng**."
        ),
        color=discord.Color.gold(),
        timestamp=vn_now().astimezone(timezone.utc),
    )
    return embed


class DailyClaimView(discord.ui.View):
    """Persistent view (timeout=None, custom_id cố định) để sống sót qua restart bot."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Nhận Daily", emoji="🎁", style=discord.ButtonStyle.success, custom_id=DAILY_CLAIM_CUSTOM_ID)
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _handle_claim(interaction)


async def _handle_claim(interaction: discord.Interaction):
    now = vn_now()
    today = now.strftime("%Y-%m-%d")

    if now.hour >= DAILY_WINDOW_HOURS:
        await interaction.response.send_message(
            f"⏰ Daily hôm nay đã hết hạn (quá {DAILY_WINDOW_HOURS}h sáng). Chờ 0h mai nhé!", ephemeral=True
        )
        return

    user_id = interaction.user.id
    user = await db.get_user(user_id)
    if user.get("last_daily_date") == today:
        await interaction.response.send_message("✅ Bạn đã nhận Daily hôm nay rồi!", ephemeral=True)
        return

    daily_state = await db.get_daily_state()
    reset_epoch = daily_state.get("reset_at_epoch")
    if not reset_epoch or daily_state.get("date") != today:
        # Phòng trường hợp bot restart lệch nhịp và chưa có mốc reset hôm nay.
        reset_epoch = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())

    hours_elapsed = int((time.time() - reset_epoch) // 3600)
    reward = compute_daily_reward(hours_elapsed)

    new_balance = await economy.add_mick(user_id, reward)
    await db.save_user(user_id, {"last_daily_date": today})

    await interaction.response.send_message(
        f"🎁 Bạn nhận được **{reward} MICK**! (Số dư hiện tại: **{new_balance} MICK**)", ephemeral=True
    )


async def maybe_post_daily(client: discord.Client, channel_id: int) -> None:
    """Gọi mỗi phút từ vòng lặp; tự đăng embed đúng lúc 0h VN nếu chưa đăng hôm nay."""
    now = vn_now()
    today = now.strftime("%Y-%m-%d")

    if now.hour != 0:
        return

    daily_state = await db.get_daily_state()
    if daily_state.get("date") == today:
        return  # đã đăng hôm nay rồi

    channel = client.get_channel(channel_id)
    if channel is None:
        try:
            channel = await client.fetch_channel(channel_id)
        except Exception:
            return

    await channel.send(embed=build_daily_embed(), view=DailyClaimView())
    await db.save_daily_state({"date": today, "reset_at_epoch": int(time.time())})
