"""The player cookie and user lookup (docs/architecture.md §2, S2).

The cookie carries a random token; the database stores only its SHA-256, so a leaked database
can't be replayed as cookies. Nickname folding and the blocklist come with T16: until then the
nickname is checked by the regex alone and `nickname_key` holds it unchanged.
"""

import hashlib
import re
import secrets
import sqlite3
import uuid
from datetime import datetime

from trur.db import iso
from trur.errors import ApiError

COOKIE = "trur_token"
COOKIE_MAX_AGE = 400 * 86400  # 400 days, so closing the browser doesn't lose the player

# 3–16 characters; spaces only inside, and never two in a row (checked separately).
_NICKNAME = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9 _-]{1,14}[A-Za-z0-9_-]")


def check_nickname(nick: str) -> str:
    if not _NICKNAME.fullmatch(nick) or "  " in nick:
        raise ApiError(
            422, "invalid_nickname",
            "Use 3 to 16 letters, digits, spaces, _ or -, with no space at the start or end and no double spaces.",
        )
    return nick


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_user(conn: sqlite3.Connection, nickname: str, now: datetime) -> str:
    """Store a new player and return their cookie token (256 random bits). 409 if the nickname is taken."""
    token = secrets.token_urlsafe(32)
    try:
        conn.execute(
            "INSERT INTO users (id, token_hash, nickname, nickname_key, created_at) VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), hash_token(token), nickname, nickname, iso(now)),
        )
    except sqlite3.IntegrityError:
        raise ApiError(409, "nickname_taken", "That nickname is already taken.") from None
    return token


def user_for_token(conn: sqlite3.Connection, token: str | None) -> sqlite3.Row | None:
    if not token:
        return None
    return conn.execute("SELECT id, nickname FROM users WHERE token_hash = ?", (hash_token(token),)).fetchone()
