"""
Minigame:
- Úp ly chọn kẹo (CupGameView): đoán đúng ly có kẹo -> CUP_GAME_REWARD MICK.
- Wordle (start_wordle/process_guess): đoán đúng từ 5 chữ trong
  WORDLE_MAX_GUESSES lượt -> WORDLE_WIN_REWARD MICK.
"""

import random
import string

import discord

import db
import economy
from config import CUP_GAME_REWARD, CUP_GAME_CUP_COUNT, WORDLE_WIN_REWARD, WORDLE_MAX_GUESSES

CUP_EMOJI = "🥤"
CANDY_EMOJI = "🍬"

# ---------------------------------------------------------------------------
# Game 1: Úp ly chọn kẹo
# ---------------------------------------------------------------------------


class CupGameView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=30)
        self.owner_id = owner_id
        self.winning_index = random.randrange(CUP_GAME_CUP_COUNT)
        self.done = False

        for i in range(CUP_GAME_CUP_COUNT):
            self.add_item(self._make_button(i))

    def _make_button(self, index: int) -> discord.ui.Button:
        button = discord.ui.Button(label=f"Ly {index + 1}", emoji=CUP_EMOJI, style=discord.ButtonStyle.secondary)

        async def callback(interaction: discord.Interaction):
            await self._on_pick(interaction, index)

        button.callback = callback
        return button

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Đây không phải lượt chơi của bạn!", ephemeral=True)
            return False
        return True

    async def _on_pick(self, interaction: discord.Interaction, index: int):
        if self.done:
            return
        self.done = True
        for child in self.children:
            child.disabled = True

        won = index == self.winning_index
        reveal = " ".join(
            CANDY_EMOJI if i == self.winning_index else "🚫" for i in range(CUP_GAME_CUP_COUNT)
        )

        try:
            import quests

            finished = await quests.bump_progress(self.owner_id, "play_game_5")
            if finished:
                await interaction.channel.send(
                    f"✅ <@{self.owner_id}> hoàn thành quest **{finished['desc']}**! "
                    f"+**{finished['reward']} MICK** (số dư: {finished['new_balance']})"
                )
        except Exception:
            pass

        if won:
            new_balance = await economy.add_mick(self.owner_id, CUP_GAME_REWARD)
            desc = (
                f"{CANDY_EMOJI} Chính xác! Bạn nhận được **{CUP_GAME_REWARD} MICK**.\n"
                f"Số dư hiện tại: **{new_balance} MICK**."
            )
            color = discord.Color.green()
        else:
            desc = f"Tiếc quá, ly có kẹo là **Ly {self.winning_index + 1}**. Chúc may mắn lần sau!"
            color = discord.Color.red()

        embed = discord.Embed(title="🥤 Úp ly chọn kẹo - Kết quả", description=f"{reveal}\n\n{desc}", color=color)
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


def build_cup_game_embed() -> discord.Embed:
    return discord.Embed(
        title="🥤 Úp ly chọn kẹo",
        description=f"Có {CUP_GAME_CUP_COUNT} ly, 1 ly giấu {CANDY_EMOJI}. Chọn đúng nhận **{CUP_GAME_REWARD} MICK**!",
        color=discord.Color.gold(),
    )


# ---------------------------------------------------------------------------
# Game 2: Wordle
# ---------------------------------------------------------------------------

_WORDLE_WORDS = [
    "apple", "table", "chair", "house", "mouse", "plane", "train", "brick", "cloud", "storm",
    "light", "night", "water", "fruit", "grape", "bread", "sugar", "honey", "spice", "sound",
    "plant", "trees", "grass", "stone", "sandy", "beach", "ocean", "river", "magic", "dream",
    "happy", "smile", "heart", "brain", "music", "dance", "paint", "write", "radio", "phone",
    "video", "movie", "actor", "drama", "novel", "story", "poems", "songs", "piano", "drums",
    "flute", "angle", "shape", "color", "black", "white", "green", "brown", "coral", "ivory",
    "amber", "olive", "lemon", "mango", "peach", "melon", "berry", "robot", "laser", "pixel",
    "cyber", "space", "earth", "venus",
]

# user_id -> {"answer": str, "guesses": [str], "feedback": [str]}
_active_games: dict[int, dict] = {}


def has_active_wordle(user_id: int) -> bool:
    return user_id in _active_games


def _feedback_row(answer: str, guess: str) -> str:
    """🟩 đúng vị trí, 🟨 đúng chữ sai vị trí, ⬛ không có trong từ (xử lý đúng chữ trùng lặp)."""
    result = ["⬛"] * 5
    answer_chars = list(answer)

    for i in range(5):
        if guess[i] == answer[i]:
            result[i] = "🟩"
            answer_chars[i] = None

    for i in range(5):
        if result[i] == "🟩":
            continue
        if guess[i] in answer_chars:
            result[i] = "🟨"
            answer_chars[answer_chars.index(guess[i])] = None

    return "".join(result)


def _render_board(game: dict) -> str:
    lines = []
    for guess, fb in zip(game["guesses"], game["feedback"]):
        lines.append(f"{fb}   `{guess.upper()}`")
    remaining = WORDLE_MAX_GUESSES - len(game["guesses"])
    lines.append(f"\nCòn **{remaining}** lượt đoán. Gõ thẳng 1 từ 5 chữ vào kênh để đoán.")
    return "\n".join(lines) if game["guesses"] else "Gõ thẳng 1 từ tiếng Anh 5 chữ vào kênh để bắt đầu đoán!"


def start_wordle(user_id: int) -> discord.Embed:
    answer = random.choice(_WORDLE_WORDS)
    _active_games[user_id] = {"answer": answer, "guesses": [], "feedback": []}
    embed = discord.Embed(
        title="🟩 Wordle",
        description=_render_board(_active_games[user_id]),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text=f"Đoán đúng nhận {WORDLE_WIN_REWARD} MICK · Tối đa {WORDLE_MAX_GUESSES} lượt")
    return embed


def is_valid_guess(text: str) -> bool:
    return len(text) == 5 and all(c in string.ascii_letters for c in text)


async def process_guess(user_id: int, guess: str) -> tuple[discord.Embed, bool]:
    """Trả về (embed, finished). finished=True nghĩa là ván đã kết thúc (thắng/thua)."""
    game = _active_games[user_id]
    guess = guess.lower()
    answer = game["answer"]

    fb = _feedback_row(answer, guess)
    game["guesses"].append(guess)
    game["feedback"].append(fb)

    won = guess == answer
    out_of_tries = len(game["guesses"]) >= WORDLE_MAX_GUESSES

    if won:
        new_balance = await economy.add_mick(user_id, WORDLE_WIN_REWARD)
        user = await db.get_user(user_id)
        wordle_wins = user.get("wordle_wins", 0) + 1
        await db.save_user(user_id, {"wordle_wins": wordle_wins})
        embed = discord.Embed(
            title="🎉 Wordle - Thắng!",
            description=(
                f"{_render_board(game)}\n\n"
                f"Chính xác là **{answer.upper()}**! Bạn nhận **{WORDLE_WIN_REWARD} MICK**.\n"
                f"Số dư hiện tại: **{new_balance} MICK**."
            ),
            color=discord.Color.green(),
        )
        del _active_games[user_id]
        return embed, True

    if out_of_tries:
        embed = discord.Embed(
            title="💀 Wordle - Hết lượt",
            description=f"{_render_board(game)}\n\nTừ đúng là **{answer.upper()}**. Chúc may mắn lần sau!",
            color=discord.Color.red(),
        )
        del _active_games[user_id]
        return embed, True

    embed = discord.Embed(title="🟩 Wordle", description=_render_board(game), color=discord.Color.blurple())
    embed.set_footer(text=f"Đoán đúng nhận {WORDLE_WIN_REWARD} MICK · Tối đa {WORDLE_MAX_GUESSES} lượt")
    return embed, False
