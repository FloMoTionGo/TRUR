"""The player cookie and user lookup (docs/architecture.md §2, S2).

The cookie carries a random token; the database stores only its SHA-256, so a leaked database
can't be replayed as cookies. Nicknames are unique by their folded form, so FIoMo can't pose as
FloMo (S8).
"""

import hashlib
import re
import secrets
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from trur.db import iso
from trur.errors import ApiError

COOKIE = "trur_token"
COOKIE_MAX_AGE = 400 * 86400  # 400 days, so closing the browser doesn't lose the player

# 3–16 characters; spaces only inside, and never two in a row (checked separately).
_NICKNAME = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9 _-]{1,14}[A-Za-z0-9_-]")


_FOLD = str.maketrans("i10", "llo")

# Blocklist words this short match only a whole part of the nickname, never inside one: substring
# matching would refuse Cassandra, Titus and Cucumber (owner, 2026-10-11).
SHORT_WORD = 4
# Except these, which hide in few real words: FuckYou and BigDick are refused, at the cost of
# Dickens and Scunthorpe (owner, 2026-10-11). They only apply while they are on the blocklist.
ANYWHERE = frozenset({"fuck", "shit", "cunt", "porn", "slut", "twat", "wank", "jizz", "dick"})


def fold_nickname(nick: str) -> str:
    """lower(); 'i', '1' → 'l'; '0' → 'o'. FIoMo, F1oMo, Fl0M0 and FLOMO all fold to "flomo"."""
    return nick.lower().translate(_FOLD)


_ANYWHERE_FOLDED = frozenset(fold_nickname(w) for w in ANYWHERE)


def load_blocklist(data_dir: Path) -> frozenset[str]:
    """data/blocklist.txt, folded like nicknames. Blank lines and # comments are ignored."""
    text = (data_dir / "blocklist.txt").read_text(encoding="utf-8")
    lines = (line.split("#", 1)[0].strip() for line in text.splitlines())
    return frozenset(fold_nickname(line) for line in lines if line)


def _blocked(nick: str, blocklist: frozenset[str]) -> bool:
    """Long words and ANYWHERE words match anywhere in the folded nickname with separators removed. Other
    short words match the whole of it, or one part between separators: a_s_s and ass_king are blocked,
    Cassandra isn't."""
    parts = re.split(r"[ _-]+", fold_nickname(nick))
    squashed = "".join(parts)
    if blocklist & {squashed, *parts}:
        return True
    return any(word in squashed for word in blocklist if len(word) > SHORT_WORD or word in _ANYWHERE_FOLDED)


def check_nickname(nick: str, blocklist: frozenset[str]) -> str:
    if not _NICKNAME.fullmatch(nick) or "  " in nick:
        raise ApiError(
            422, "invalid_nickname",
            "Use 3 to 16 letters, digits, spaces, _ or -, with no space at the start or end and no double spaces.",
        )
    if _blocked(nick, blocklist):
        raise ApiError(422, "nickname_not_allowed", "That nickname isn't allowed. Please pick another one.")
    return nick


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_user(conn: sqlite3.Connection, nickname: str, now: datetime) -> str:
    """Store a new player and return their cookie token (256 random bits). 409 if the nickname is taken."""
    token = secrets.token_urlsafe(32)
    try:
        conn.execute(
            "INSERT INTO users (id, token_hash, nickname, nickname_key, created_at) VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), hash_token(token), nickname, fold_nickname(nickname), iso(now)),
        )
    except sqlite3.IntegrityError:
        raise ApiError(409, "nickname_taken", "That nickname is already taken.") from None
    return token


def user_for_token(conn: sqlite3.Connection, token: str | None) -> sqlite3.Row | None:
    if not token:
        return None
    return conn.execute("SELECT id, nickname FROM users WHERE token_hash = ?", (hash_token(token),)).fetchone()
