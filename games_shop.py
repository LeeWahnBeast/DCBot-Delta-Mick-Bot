"""
6 minigame mới cho /game: Emoji Riddle (Đuổi Hình Bắt Chữ), Tic-Tac-Toe
(Cờ Caro 2 người), Horse Race (Đua Ngựa), Slot Machine, High-Low (Cao/Thấp
bài cào), Minesweeper (Dò Mìn).

Tất cả trừ High-Low là miễn phí (chơi bằng Vé). High-Low hỗ trợ cược MICK
tự do (giống Tài Xỉu).
"""

import asyncio
import random
import string
import time
from io import BytesIO

import discord
from PIL import Image, ImageDraw, ImageFont

import db
import economy
import features
from config import log, GAME_TICKET_COST

_active_games = features._active_games

_EMOJI_RIDDLE_WORDS = [
    ("orange", "🍊"),
    ("cat", "🐱"),
    ("sun", "☀️"),
    ("star", "⭐"),
    ("moon", "🌙"),
    ("heart", "❤️"),
    ("fire", "🔥"),
    ("water", "💧"),
    ("tree", "🌳"),
    ("flower", "🌸"),
    ("apple", "🍎"),
    ("book", "📚"),
    ("music", "🎵"),
    ("dance", "💃"),
    ("laugh", "😂"),
    ("smile", "😊"),
    ("sleep", "😴"),
    ("party", "🎉"),
    ("dream", "💭"),
    ("love", "💕"),
]

_TICTACTOE_EMPTY = "⬜"
_TICTACTOE_X = "❌"
_TICTACTOE_O = "⭕"

_HIGH_LOW_SUITS = "♠♥♦♣"
_HIGH_LOW_RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]

_MINESWEEPER_SIZE = 5
_MINESWEEPER_MINES = 5
_MINESWEEPER_SAFE = "🟩"
_MINESWEEPER_MINE = "💣"
_MINESWEEPER_FLAG = "🚩"
_MINESWEEPER_HIDDEN = "❓"


def _new_game_id() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=8))


def start_emoji_riddle(user_id: int) -> tuple[str, discord.ui.Container]:
    gid = _new_game_id()
    word, emoji = random.choice(_EMOJI_RIDDLE_WORDS)
    _active_games[gid] = {
        "type": "emoji_riddle",
        "owner_id": user_id,
        "status": "playing",
        "created_at": time.time(),
        "word": word,
        "emoji": emoji,
    }
    hint_emojis = "".join(random.choices(_EMOJI_RIDDLE_WORDS, k=3))
    hint = emoji + " " + hint_emojis[:3]
    container = features.build_container(
        title="🎨 Đuổi Hình Bắt Chữ",
        description=f"Đoán từ tiếng Anh từ emoji sau:\n\n{hint}\n\nNhập từ bằng nút bên dưới (viết thường, 5-10 ký tự).",
        color=discord.Color.purple(),
    )
    return gid, container


async def process_emoji_riddle(game_id: str, guess: str) -> discord.ui.Container | None:
    game = _active_games.get(game_id)
    if not game or game["type"] != "emoji_riddle" or game["status"] != "playing":
        return None

    guess = guess.lower().strip()
    correct = guess == game["word"]

    if correct:
        new_balance = await economy.add_mick(game["owner_id"], 50)
        container = features.build_container(
            title="🎉 Đúng rồi!",
            description=f"Từ đúng là **{game['word'].upper()}**! 💰 Bạn nhận **50 MICK**.\nSố dư: **{new_balance} MICK**",
            color=discord.Color.green(),
        )
        game["status"] = "won"
    else:
        container = features.build_container(
            title="❌ Sai rồi!",
            description=f"Từ đúng là **{game['word'].upper()}** (gợi ý: {game['emoji']}).",
            color=discord.Color.red(),
        )
        game["status"] = "lost"

    game["finished_at"] = time.time()
    return container


def start_tictactoe(user_id: int) -> tuple[str, discord.ui.Container]:
    gid = _new_game_id()
    board = [_TICTACTOE_EMPTY] * 9
    _active_games[gid] = {
        "type": "tictactoe",
        "owner_id": user_id,
        "status": "playing",
        "created_at": time.time(),
        "board": board,
        "turn": "X",
    }
    return gid, _render_tictactoe(gid)


def _render_tictactoe(game_id: str) -> discord.ui.Container:
    game = _active_games.get(game_id)
    if not game:
        return features.build_container(description="Ván không tồn tại.")
    board = game["board"]
    grid_text = "\n".join([
        " ".join(board[i*3:(i+1)*3])
        for i in range(3)
    ])
    title = "❌ Luợt của bạn (X)" if game["turn"] == "X" else "⭕ Bot đang suy nghĩ (O)..."
    return features.build_container(
        title="🎮 Cờ Caro (Tic-Tac-Toe)",
        description=f"{grid_text}\n\nNhập toạ độ (0-8) để đi nước.",
        color=discord.Color.blurple(),
        footer=title,
    )


async def process_tictactoe(game_id: str, move: str) -> discord.ui.Container | None:
    game = _active_games.get(game_id)
    if not game or game["type"] != "tictactoe" or game["status"] != "playing" or game["turn"] != "X":
        return None

    try:
        pos = int(move.strip())
        if not 0 <= pos <= 8 or game["board"][pos] != _TICTACTOE_EMPTY:
            return None
    except (ValueError, IndexError):
        return None

    game["board"][pos] = _TICTACTOE_X
    if _check_tictactoe_win(game["board"], _TICTACTOE_X):
        game["status"] = "won"
        game["finished_at"] = time.time()
        container = features.build_container(
            title="🎉 Bạn thắng!",
            description="".join([" ".join(game["board"][i*3:(i+1)*3]) + "\n" for i in range(3)]) + "\n💰 Bạn nhận **30 MICK**.",
            color=discord.Color.green(),
        )
        await economy.add_mick(game["owner_id"], 30)
        return container

    if all(x != _TICTACTOE_EMPTY for x in game["board"]):
        game["status"] = "draw"
        game["finished_at"] = time.time()
        container = features.build_container(
            title="🤝 Hòa!",
            description="".join([" ".join(game["board"][i*3:(i+1)*3]) + "\n" for i in range(3)]),
            color=discord.Color.yellow(),
        )
        return container

    bot_move = _find_tictactoe_move(game["board"])
    game["board"][bot_move] = _TICTACTOE_O
    if _check_tictactoe_win(game["board"], _TICTACTOE_O):
        game["status"] = "lost"
        game["finished_at"] = time.time()
        container = features.build_container(
            title="💀 Bot thắng!",
            description="".join([" ".join(game["board"][i*3:(i+1)*3]) + "\n" for i in range(3)]),
            color=discord.Color.red(),
        )
        return container

    if all(x != _TICTACTOE_EMPTY for x in game["board"]):
        game["status"] = "draw"
        game["finished_at"] = time.time()
        container = features.build_container(
            title="🤝 Hòa!",
            description="".join([" ".join(game["board"][i*3:(i+1)*3]) + "\n" for i in range(3)]),
            color=discord.Color.yellow(),
        )
        return container

    game["turn"] = "X"
    return _render_tictactoe(game_id)


def _check_tictactoe_win(board: list, player: str) -> bool:
    lines = [
        [0, 1, 2], [3, 4, 5], [6, 7, 8],
        [0, 3, 6], [1, 4, 7], [2, 5, 8],
        [0, 4, 8], [2, 4, 6],
    ]
    return any(all(board[i] == player for i in line) for line in lines)


def _find_tictactoe_move(board: list) -> int:
    for i, cell in enumerate(board):
        if cell == _TICTACTOE_EMPTY:
            test = board.copy()
            test[i] = _TICTACTOE_O
            if _check_tictactoe_win(test, _TICTACTOE_O):
                return i
    for i, cell in enumerate(board):
        if cell == _TICTACTOE_EMPTY:
            test = board.copy()
            test[i] = _TICTACTOE_X
            if _check_tictactoe_win(test, _TICTACTOE_X):
                return i
    return next(i for i, cell in enumerate(board) if cell == _TICTACTOE_EMPTY)


def start_horse_race(user_id: int) -> tuple[str, discord.ui.Container]:
    gid = _new_game_id()
    horses = [f"🐴 Ngựa {i+1}" for i in range(5)]
    _active_games[gid] = {
        "type": "horse_race",
        "owner_id": user_id,
        "status": "betting",
        "created_at": time.time(),
        "horses": horses,
        "bets": {},
    }
    return gid, features.build_container(
        title="🏇 Đua Ngựa",
        description="\n".join(horses) + "\n\nChọn ngựa để cược (gõ số 1-5).",
        color=discord.Color.orange(),
    )


async def process_horse_race_bet(game_id: str, horse_num: str, amount: int) -> tuple[str, discord.ui.Container | None]:
    game = _active_games.get(game_id)
    if not game or game["type"] != "horse_race" or game["status"] != "betting":
        return "", None

    try:
        num = int(horse_num.strip()) - 1
        if not 0 <= num <= 4:
            return "", None
    except ValueError:
        return "", None

    user_id = game["owner_id"]
    result = await economy.add_mick(user_id, -amount)
    if result < 0:
        return "", None

    if user_id not in game["bets"]:
        game["bets"][user_id] = []
    game["bets"][user_id].append((num, amount))
    return f"Đã cược **{amount} MICK** vào 🐴 Ngựa {num+1}!", None


def finish_horse_race(game_id: str) -> discord.ui.Container | None:
    game = _active_games.get(game_id)
    if not game or game["type"] != "horse_race":
        return None

    winner = random.randint(0, 4)
    game["status"] = "finished"
    game["winner"] = winner
    game["finished_at"] = time.time()

    msg = f"🏇 Ngựa {winner+1} thắng!\n\n"
    if game["owner_id"] in game["bets"]:
        for horse, amount in game["bets"][game["owner_id"]]:
            if horse == winner:
                reward = amount * 5
                asyncio.create_task(economy.add_mick(game["owner_id"], reward))
                msg += f"💰 Bạn thắng **{reward} MICK**!"
    return features.build_container(title="🏁 Kết quả", description=msg, color=discord.Color.gold())


def start_slot_machine(user_id: int) -> tuple[str, discord.ui.Container]:
    gid = _new_game_id()
    emojis = ["🍎", "🍊", "🍋", "🍌", "🍇", "🎁"]
    reels = [random.choice(emojis) for _ in range(3)]
    _active_games[gid] = {
        "type": "slot",
        "owner_id": user_id,
        "status": "finished",
        "created_at": time.time(),
        "reels": reels,
    }
    msg = f"   {reels[0]} {reels[1]} {reels[2]}\n\n"
    if reels[0] == reels[1] == reels[2]:
        reward = 100
        asyncio.create_task(economy.add_mick(user_id, reward))
        msg += f"🎉 Jackpot! Bạn thắng **{reward} MICK**!"
    elif reels[0] == reels[1] or reels[1] == reels[2]:
        reward = 30
        asyncio.create_task(economy.add_mick(user_id, reward))
        msg += f"✨ 2 giống! Bạn thắng **{reward} MICK**!"
    else:
        msg += "❌ Không trùng, may lần sau!"
    return gid, features.build_container(title="🎰 Slot Machine", description=msg, color=discord.Color.pink())


def start_high_low(user_id: int) -> tuple[str, discord.ui.Container]:
    gid = _new_game_id()
    current_card = _draw_card()
    _active_games[gid] = {
        "type": "high_low",
        "owner_id": user_id,
        "status": "playing",
        "created_at": time.time(),
        "cards": [current_card],
        "streak": 0,
        "current_bet": 0,
    }
    return gid, features.build_container(
        title="🃏 Cao/Thấp Bài Cào",
        description=f"Lá bài đầu: **{current_card}**\n\nĐoán lá tiếp theo cao hơn hay thấp hơn? (bấm nút hoặc gõ 'cao'/'thấp')",
        color=discord.Color.blue(),
    )


def _draw_card() -> str:
    return random.choice(_HIGH_LOW_RANKS) + random.choice(_HIGH_LOW_SUITS)


def _card_value(card: str) -> int:
    rank = card[:-1]
    return _HIGH_LOW_RANKS.index(rank)


async def process_high_low_guess(game_id: str, guess: str) -> discord.ui.Container | None:
    game = _active_games.get(game_id)
    if not game or game["type"] != "high_low" or game["status"] != "playing":
        return None

    guess = guess.lower().strip()
    if guess not in ("cao", "thấp"):
        return None

    current = game["cards"][-1]
    next_card = _draw_card()
    game["cards"].append(next_card)

    current_val = _card_value(current)
    next_val = _card_value(next_card)

    correct = (guess == "cao" and next_val > current_val) or (guess == "thấp" and next_val < current_val)

    if correct:
        game["streak"] += 1
        msg = f"✅ Đúng! Lá tiếp: **{next_card}**\n\nSau {game['streak']} lượt. Tiếp tục hoặc dừng để lấy thưởng?"
        return features.build_container(title="🃏 Cao/Thấp Bài Cào", description=msg, color=discord.Color.blue())
    else:
        reward = game["streak"] * 10 if game["streak"] > 0 else 0
        await economy.add_mick(game["owner_id"], reward)
        game["status"] = "finished"
        game["finished_at"] = time.time()
        msg = f"❌ Sai rồi! Lá đó là **{next_card}**.\n\n💰 Bạn thắng **{reward} MICK** ({game['streak']} vòng)!"
        return features.build_container(title="💀 Thua!", description=msg, color=discord.Color.red())


def start_minesweeper(user_id: int) -> tuple[str, discord.ui.Container]:
    gid = _new_game_id()
    mines = set(random.sample(range(_MINESWEEPER_SIZE * _MINESWEEPER_SIZE), _MINESWEEPER_MINES))
    _active_games[gid] = {
        "type": "minesweeper",
        "owner_id": user_id,
        "status": "playing",
        "created_at": time.time(),
        "mines": mines,
        "opened": set(),
        "flagged": set(),
    }
    return gid, _render_minesweeper(gid)


def _render_minesweeper(game_id: str) -> discord.ui.Container:
    game = _active_games.get(game_id)
    if not game:
        return features.build_container(description="Ván không tồn tại.")

    grid = []
    for i in range(_MINESWEEPER_SIZE * _MINESWEEPER_SIZE):
        if i in game["flagged"]:
            grid.append(_MINESWEEPER_FLAG)
        elif i in game["opened"]:
            if i in game["mines"]:
                grid.append(_MINESWEEPER_MINE)
            else:
                grid.append(_MINESWEEPER_SAFE)
        else:
            grid.append(_MINESWEEPER_HIDDEN)

    grid_text = "\n".join([
        " ".join(grid[i*_MINESWEEPER_SIZE:(i+1)*_MINESWEEPER_SIZE])
        for i in range(_MINESWEEPER_SIZE)
    ])
    return features.build_container(
        title="💣 Dò Mìn",
        description=f"{grid_text}\n\nNhập toạ độ 0-24 để mở ô (bấm nút).",
        color=discord.Color.dark_red(),
    )


async def process_minesweeper(game_id: str, pos: str) -> discord.ui.Container | None:
    game = _active_games.get(game_id)
    if not game or game["type"] != "minesweeper" or game["status"] != "playing":
        return None

    try:
        idx = int(pos.strip())
        if not 0 <= idx < _MINESWEEPER_SIZE * _MINESWEEPER_SIZE or idx in game["opened"] or idx in game["flagged"]:
            return None
    except ValueError:
        return None

    game["opened"].add(idx)

    if idx in game["mines"]:
        game["status"] = "lost"
        game["finished_at"] = time.time()
        return _render_minesweeper(game_id)

    if len(game["opened"]) == _MINESWEEPER_SIZE * _MINESWEEPER_SIZE - _MINESWEEPER_MINES:
        game["status"] = "won"
        game["finished_at"] = time.time()
        reward = len(game["opened"]) * 5
        await economy.add_mick(game["owner_id"], reward)
        container = _render_minesweeper(game_id)
        container.add_item(discord.ui.TextDisplay(f"💰 Bạn thắng **{reward} MICK**!"))
        return container

    return _render_minesweeper(game_id)
