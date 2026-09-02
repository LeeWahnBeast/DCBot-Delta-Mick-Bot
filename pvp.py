"""
PvP 1v1: thách đấu 1 người chơi khác, cả 2 cùng cược MICK bằng nhau, đấu kiểu
Oẳn Tù Tì (Kéo Búa Bao) hoặc Tài Xỉu đối đầu (ai đoán đúng tổng xúc xắc chung
thì thắng). Người thắng lấy trọn tiền cược của cả 2 (trừ 1 phí nhỏ, tương tự
TRANSFER_FEE_PERCENT của hệ thống chuyển khoản).

Nếu người chơi đang có pet còn sống (hunger > 0), pet_power cộng thêm % nhỏ
vào tỉ lệ thắng - khuyến khích chăm pet mà không làm nó quyết định tuyệt đối.

Lời mời thách đấu lưu tạm trong RAM (_pending_challenges), hết hạn sau
CHALLENGE_TTL_SEC nếu không được nhận.
"""

import random
import time

import discord

import economy
import features
import pets
from economy import is_owner
from config import CURRENCY_EMOJI

CHALLENGE_TTL_SEC = 120
PVP_FEE_PERCENT = 5  # phí nhà cái trên tổng pool khi có người thắng

_pending_challenges: dict[str, dict] = {}
_active_matches: dict[str, dict] = {}

RPS_CHOICES = {"bua": "✊ Búa", "keo": "✌️ Kéo", "bao": "🖐️ Bao"}
_RPS_BEATS = {"bua": "keo", "keo": "bao", "bao": "bua"}


def _new_id(prefix: str) -> str:
    return f"{prefix}_{random.randint(100000, 999999)}"


def _gc_challenges() -> None:
    now = time.time()
    expired = [cid for cid, c in _pending_challenges.items() if now - c["created_at"] > CHALLENGE_TTL_SEC]
    for cid in expired:
        _pending_challenges.pop(cid, None)


async def create_challenge(challenger_id: int, opponent_id: int, mode: str, bet: int) -> dict:
    """mode: 'rps' (Oẳn tù tì) hoặc 'taixiu' (Tài Xỉu đối đầu)."""
    _gc_challenges()
    if challenger_id == opponent_id:
        return {"ok": False, "reason": "self_challenge"}
    if bet <= 0:
        return {"ok": False, "reason": "invalid_amount"}
    if mode not in ("rps", "taixiu"):
        return {"ok": False, "reason": "invalid_mode"}

    challenger_profile = await economy.get_profile(challenger_id)
    if challenger_profile["mick"] < bet:
        return {"ok": False, "reason": "insufficient_funds"}

    cid = _new_id("pvp")
    _pending_challenges[cid] = {
        "challenger_id": challenger_id,
        "opponent_id": opponent_id,
        "mode": mode,
        "bet": bet,
        "created_at": time.time(),
        "status": "pending",
    }
    return {"ok": True, "challenge_id": cid}


def get_challenge(challenge_id: str) -> dict | None:
    _gc_challenges()
    return _pending_challenges.get(challenge_id)


async def accept_challenge(challenge_id: str, accepter_id: int) -> dict:
    challenge = get_challenge(challenge_id)
    if not challenge or challenge["status"] != "pending":
        return {"ok": False, "reason": "not_found_or_expired"}
    if accepter_id != challenge["opponent_id"]:
        return {"ok": False, "reason": "not_your_challenge"}

    bet = challenge["bet"]
    opponent_profile = await economy.get_profile(accepter_id)
    if opponent_profile["mick"] < bet:
        _pending_challenges.pop(challenge_id, None)
        return {"ok": False, "reason": "opponent_insufficient_funds"}

    # Trừ cược cả 2 bên ngay (atomic qua place_bet, đã có lock riêng từng user)
    r1 = await economy.place_bet(challenge["challenger_id"], bet)
    if not r1["ok"]:
        _pending_challenges.pop(challenge_id, None)
        return {"ok": False, "reason": "challenger_funds_changed"}
    r2 = await economy.place_bet(accepter_id, bet)
    if not r2["ok"]:
        # hoàn tiền lại cho challenger vì opponent không đủ tiền vào phút chót
        await economy.add_mick(challenge["challenger_id"], bet)
        _pending_challenges.pop(challenge_id, None)
        return {"ok": False, "reason": "opponent_funds_changed"}

    challenge["status"] = "accepted"
    _pending_challenges.pop(challenge_id, None)
    _active_matches[challenge_id] = challenge
    return {"ok": True, "challenge": challenge}


async def _pet_edge(user_id: int) -> float:
    """Trả về % lợi thế thắng nhỏ (0.0 - 0.10) nếu user có pet khoẻ mạnh."""
    try:
        pet = await pets.get_pet_live(user_id)
        if not pet or pet.get("hunger", 0) <= 0:
            return 0.0
        power = pets.pet_power(pet)
        return min(0.10, power * 0.004)
    except Exception:
        return 0.0


async def resolve_rps(challenge_id: str, challenger_choice: str, opponent_choice: str) -> dict:
    match = _active_matches.get(challenge_id)
    if not match:
        return {"ok": False, "reason": "not_found"}

    if challenger_choice == opponent_choice:
        winner_id = None
    elif _RPS_BEATS[challenger_choice] == opponent_choice:
        winner_id = match["challenger_id"]
    else:
        winner_id = match["opponent_id"]

    return await _finalize_match(challenge_id, winner_id, extra_note=(
        f"{RPS_CHOICES[challenger_choice]} vs {RPS_CHOICES[opponent_choice]}"
    ))


async def resolve_taixiu_duel(challenge_id: str, challenger_choice: str, opponent_choice: str) -> dict:
    """Cả 2 chọn Tài/Xỉu độc lập cho CÙNG 1 lượt xúc xắc chung. Nếu cả 2 chọn
    giống nhau (cùng đúng hoặc cùng sai) -> hoà, hoàn tiền. Nếu khác nhau,
    ai đoán đúng thắng trọn pool."""
    match = _active_matches.get(challenge_id)
    if not match:
        return {"ok": False, "reason": "not_found"}

    dice = [random.randint(1, 6) for _ in range(3)]
    total = sum(dice)
    result = "tai" if total >= 11 else "xiu"
    dice_text = " ".join(f"🎲{d}" for d in dice)

    challenger_correct = challenger_choice == result
    opponent_correct = opponent_choice == result

    if challenger_correct == opponent_correct:
        winner_id = None
    elif challenger_correct:
        winner_id = match["challenger_id"]
    else:
        winner_id = match["opponent_id"]

    note = f"{dice_text} = **{total}** → **{'Tài' if result=='tai' else 'Xỉu'}**"
    return await _finalize_match(challenge_id, winner_id, extra_note=note)


async def _finalize_match(challenge_id: str, winner_id: int | None, extra_note: str = "") -> dict:
    match = _active_matches.pop(challenge_id, None)
    if not match:
        return {"ok": False, "reason": "not_found"}

    bet = match["bet"]
    challenger_id = match["challenger_id"]
    opponent_id = match["opponent_id"]
    pool = bet * 2

    if winner_id is None:
        # Hoà: hoàn tiền cược lại cho cả 2, không thu phí
        await economy.add_mick(challenger_id, bet)
        await economy.add_mick(opponent_id, bet)
        return {
            "ok": True, "result": "draw", "note": extra_note,
            "bet": bet, "winner_id": None,
        }

    fee = round(pool * PVP_FEE_PERCENT / 100)
    payout = pool - fee
    await economy.add_mick(winner_id, payout)
    loser_id = opponent_id if winner_id == challenger_id else challenger_id

    try:
        pet = await pets.get_pet(winner_id)
        if pet:
            pet["battles_won"] = pet.get("battles_won", 0) + 1
            await pets._save_pet(winner_id, {"battles_won": pet["battles_won"]})
        pet_l = await pets.get_pet(loser_id)
        if pet_l:
            pet_l["battles_lost"] = pet_l.get("battles_lost", 0) + 1
            await pets._save_pet(loser_id, {"battles_lost": pet_l["battles_lost"]})
    except Exception:
        pass

    return {
        "ok": True, "result": "win", "note": extra_note,
        "bet": bet, "pool": pool, "fee": fee, "payout": payout,
        "winner_id": winner_id, "loser_id": loser_id,
    }


def build_challenge_container(challenger_name: str, opponent_name: str, mode: str, bet: int) -> discord.ui.Container:
    mode_label = "Oẳn Tù Tì" if mode == "rps" else "Tài Xỉu Đối Đầu"
    return features.build_container(
        title=f"⚔️ Thách đấu PvP · {mode_label}",
        description=(
            f"**{challenger_name}** thách đấu **{opponent_name}**!\n"
            f"Mức cược: **{bet} {CURRENCY_EMOJI} MICK** mỗi bên (người thắng ăn trọn, trừ {PVP_FEE_PERCENT}% phí).\n\n"
            f"{opponent_name} có {CHALLENGE_TTL_SEC}s để bấm **Nhận đấu** hoặc **Từ chối**."
        ),
        color=discord.Color.dark_red(),
    )


def build_result_container(
    challenger_id: int, opponent_id: int, challenger_name: str, opponent_name: str, result: dict
) -> discord.ui.Container:
    if result["result"] == "draw":
        desc = f"{result['note']}\n\n🤝 Hoà! Tiền cược đã được hoàn lại cho cả 2 bên."
        color = discord.Color.light_grey()
    else:
        winner_name = challenger_name if result["winner_id"] == challenger_id else opponent_name
        desc = (
            f"{result['note']}\n\n"
            f"🏆 **{winner_name}** thắng! Nhận **{result['payout']} MICK** "
            f"(pool {result['pool']} - phí {result['fee']})."
        )
        color = discord.Color.gold()
    return features.build_container(title="⚔️ Kết quả PvP", description=desc, color=color)


# ---------------------------------------------------------------------------
# Discord UI Views (Components V2) - gắn nút, dùng trực tiếp trong /pvp
# ---------------------------------------------------------------------------


class ChallengeInviteView(discord.ui.LayoutView):
    """Hiện cho người bị thách đấu: nút Nhận đấu / Từ chối."""

    def __init__(self, challenge_id: str, challenger_id: int, opponent_id: int,
                 challenger_name: str, opponent_name: str, mode: str, bet: int):
        super().__init__(timeout=CHALLENGE_TTL_SEC)
        self.challenge_id = challenge_id
        self.challenger_id = challenger_id
        self.opponent_id = opponent_id
        self.challenger_name = challenger_name
        self.opponent_name = opponent_name
        self.mode = mode
        self.bet = bet

        container = build_challenge_container(challenger_name, opponent_name, mode, bet)
        self.add_item(container)
        row = discord.ui.ActionRow()
        self.add_item(row)

        accept_btn = discord.ui.Button(label="Nhận đấu", emoji="⚔️", style=discord.ButtonStyle.success)
        accept_btn.callback = self._on_accept
        row.add_item(accept_btn)

        decline_btn = discord.ui.Button(label="Từ chối", emoji="🚫", style=discord.ButtonStyle.danger)
        decline_btn.callback = self._on_decline
        row.add_item(decline_btn)

    async def _on_accept(self, interaction: discord.Interaction):
        if interaction.user.id != self.opponent_id:
            await interaction.response.send_message("❌ Lời thách đấu này không dành cho bạn.", ephemeral=True)
            return
        result = await accept_challenge(self.challenge_id, interaction.user.id)
        if not result["ok"]:
            reason_map = {
                "not_found_or_expired": "⌛ Lời thách đấu đã hết hạn hoặc không tồn tại.",
                "opponent_insufficient_funds": "❌ Bạn không đủ MICK để nhận đấu.",
                "challenger_funds_changed": "❌ Người thách đấu không còn đủ MICK, trận đấu bị huỷ.",
                "opponent_funds_changed": "❌ Có lỗi khi trừ tiền cược, trận đấu bị huỷ.",
            }
            await interaction.response.send_message(
                reason_map.get(result["reason"], "❌ Không thể nhận đấu lúc này."), ephemeral=True
            )
            return
        view = MatchPlayView(self.challenge_id, self.challenger_id, self.opponent_id,
                              self.challenger_name, self.opponent_name, self.mode, self.bet)
        await interaction.response.edit_message(view=view)

    async def _on_decline(self, interaction: discord.Interaction):
        if interaction.user.id != self.opponent_id:
            await interaction.response.send_message("❌ Lời thách đấu này không dành cho bạn.", ephemeral=True)
            return
        _pending_challenges.pop(self.challenge_id, None)
        container = features.build_container(
            title="⚔️ Thách đấu bị từ chối",
            description=f"{self.opponent_name} đã từ chối lời thách đấu của {self.challenger_name}.",
            color=discord.Color.light_grey(),
        )
        await interaction.response.edit_message(view=features.SimpleContainerLayout(container))


class MatchPlayView(discord.ui.LayoutView):
    """Sau khi nhận đấu: cả 2 người bấm chọn nước đi riêng (ephemeral), khi cả
    2 đã chọn thì tự động chấm kết quả."""

    def __init__(self, challenge_id: str, challenger_id: int, opponent_id: int,
                 challenger_name: str, opponent_name: str, mode: str, bet: int):
        super().__init__(timeout=180)
        self.challenge_id = challenge_id
        self.challenger_id = challenger_id
        self.opponent_id = opponent_id
        self.challenger_name = challenger_name
        self.opponent_name = opponent_name
        self.mode = mode
        self.bet = bet
        self.choices: dict[int, str] = {}

        mode_label = "Oẳn Tù Tì" if mode == "rps" else "Tài Xỉu Đối Đầu"
        container = features.build_container(
            title=f"⚔️ {mode_label} · Trận đấu bắt đầu!",
            description=(
                f"**{challenger_name}** vs **{opponent_name}** · Cược {bet} MICK/bên.\n"
                f"Mỗi người bấm 1 lựa chọn bên dưới (chỉ mình bạn thấy kết quả bấm)."
            ),
            color=discord.Color.dark_gold(),
        )
        self.add_item(container)
        row = discord.ui.ActionRow()
        self.add_item(row)

        if mode == "rps":
            options = [("bua", "✊ Búa"), ("keo", "✌️ Kéo"), ("bao", "🖐️ Bao")]
        else:
            options = [("tai", "📈 Tài (11-18)"), ("xiu", "📉 Xỉu (3-10)")]

        for value, label in options:
            btn = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary)
            btn.callback = self._make_callback(value)
            row.add_item(btn)

    def _make_callback(self, value: str):
        async def _cb(interaction: discord.Interaction):
            if interaction.user.id not in (self.challenger_id, self.opponent_id):
                await interaction.response.send_message("❌ Bạn không tham gia trận này.", ephemeral=True)
                return
            if interaction.user.id in self.choices:
                await interaction.response.send_message("⏳ Bạn đã chọn rồi, đang chờ đối thủ...", ephemeral=True)
                return
            self.choices[interaction.user.id] = value
            await interaction.response.send_message(f"✅ Đã ghi nhận lựa chọn: **{value}**", ephemeral=True)

            if len(self.choices) == 2:
                challenger_choice = self.choices[self.challenger_id]
                opponent_choice = self.choices[self.opponent_id]
                if self.mode == "rps":
                    result = await resolve_rps(self.challenge_id, challenger_choice, opponent_choice)
                else:
                    result = await resolve_taixiu_duel(self.challenge_id, challenger_choice, opponent_choice)
                if result["ok"]:
                    container = build_result_container(
                        self.challenger_id, self.opponent_id,
                        self.challenger_name, self.opponent_name, result,
                    )
                    try:
                        await interaction.message.edit(view=features.SimpleContainerLayout(container))
                    except Exception:
                        pass
        return _cb
