"""Routes, dependencies and exception handlers (docs/architecture.md §2–§3).

All routes are plain `def`: sqlite3 blocks, so they run in the threadpool. Time comes from
app.state.clock and randomness from app.state.rng, never from the system directly.
"""

import random
import sqlite3
from collections.abc import Iterator
from datetime import datetime
from http import HTTPStatus
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from trur import auth, db, games
from trur.config import Settings
from trur.errors import ApiError
from trur.security import RateLimiter, check_post, client_ip
from trur.solver import Lexicon

# --- dependencies ---

def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    """One read-write connection per request, closed after it."""
    conn = db.connect(request.app.state.settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


def get_now(request: Request) -> datetime:
    return request.app.state.clock.now()


def get_lexicon(request: Request) -> Lexicon:
    return request.app.state.lexicon


def get_blocklist(request: Request) -> frozenset[str]:
    return request.app.state.blocklist


def get_rng(request: Request) -> random.Random:
    return request.app.state.rng


def get_limiter(request: Request) -> RateLimiter:
    return request.app.state.limiter


def post_checks(request: Request) -> None:
    check_post(request, request.app.state.settings)


def ip_backstop(request: Request) -> None:
    """Every API request: at most RL_REQUESTS_PER_IP_MIN per IP and minute (S7)."""
    s: Settings = request.app.state.settings
    get_limiter(request).check(f"ip:{client_ip(request, s.trust_fly_client_ip)}", s.rl_requests_per_ip_min, 60)


Conn = Annotated[sqlite3.Connection, Depends(get_conn)]
Cfg = Annotated[Settings, Depends(get_settings)]
Now = Annotated[datetime, Depends(get_now)]
Lex = Annotated[Lexicon, Depends(get_lexicon)]
Rng = Annotated[random.Random, Depends(get_rng)]
Limiter = Annotated[RateLimiter, Depends(get_limiter)]
POST = [Depends(post_checks)]  # route-level, so it runs before the body is validated

router = APIRouter(dependencies=[Depends(ip_backstop)])


def get_player(request: Request, conn: Conn) -> sqlite3.Row:
    """The player behind the cookie, or 401. No route ever takes a user id as input (S2)."""
    player = auth.user_for_token(conn, request.cookies.get(auth.COOKIE))
    if player is None:
        raise ApiError(401, "no_player", "Choose a nickname first.")
    return player


Player = Annotated[sqlite3.Row, Depends(get_player)]


# --- request models: unknown fields are refused, so a client can't send a score (S5) ---

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class NewUser(Strict):
    nickname: str


GameMode = Literal["free"]  # "daily" is accepted from T21 on


class NewGame(Strict):
    mode: GameMode


Coord = Annotated[int, Field(ge=0, le=9)]
TilePair = Annotated[list[Coord], Field(min_length=2, max_length=2)]  # [row, col]


class EndGame(Strict):
    """An empty body: ending a game takes no input, and a client can't send a score (S5)."""


class Submission(Strict):
    """Tile coordinates only, never a word or a score: the server rebuilds the word (S5)."""

    tiles: Annotated[list[TilePair], Field(min_length=2, max_length=50)]

    @field_validator("tiles")
    @classmethod
    def no_repeats(cls, tiles: list[list[int]]) -> list[list[int]]:
        if len({tuple(t) for t in tiles}) != len(tiles):
            raise ValueError("a tile is used twice")
        return tiles


# --- routes ---

@router.get("/healthz")
def healthz(conn: Conn) -> dict:
    conn.execute("SELECT 1")
    return {"ok": True}


@router.post("/api/users", status_code=201, dependencies=POST)
def register(
    body: NewUser, request: Request, response: Response, conn: Conn, s: Cfg, now: Now,
    blocklist: Annotated[frozenset[str], Depends(get_blocklist)], limiter: Limiter,
) -> dict:
    if auth.user_for_token(conn, request.cookies.get(auth.COOKIE)) is not None:
        raise ApiError(409, "already_registered", "This browser already has a player.")
    nickname = auth.check_nickname(body.nickname, blocklist)
    # Counted only for valid nicknames, so typos don't use up the hour; a taken nickname still counts.
    limiter.check(f"players:{client_ip(request, s.trust_fly_client_ip)}", s.rl_new_players_per_ip_hour, 3600)
    token = auth.create_user(conn, nickname, now)
    response.set_cookie(
        auth.COOKIE, token, max_age=auth.COOKIE_MAX_AGE, path="/",
        secure=s.cookie_secure, httponly=True, samesite="lax",
    )
    return {"nickname": nickname}


@router.get("/api/users/me")
def me(player: Player) -> dict:
    return {"nickname": player["nickname"]}


@router.post("/api/games", status_code=201, dependencies=POST)
def new_game(
    body: NewGame, response: Response, conn: Conn, s: Cfg, lex: Lex, rng: Rng, player: Player, now: Now, limiter: Limiter,
) -> dict:
    limiter.check(f"games:{player['id']}", s.rl_games_per_player_hour, 3600)
    state, created = games.create_game(conn, lex, s, rng, player["id"], body.mode, now)
    response.status_code = 201 if created else 200
    return state


@router.post("/api/games/{game_id}/submit", dependencies=POST)
def submit(
    game_id: str, body: Submission, conn: Conn, s: Cfg, lex: Lex, player: Player, now: Now, limiter: Limiter,
) -> dict:
    # The submit gap (S4), in memory before the transaction, so a submit that comes too fast costs no try.
    limiter.check(f"submit:{player['id']}:{game_id}", 1, s.submit_min_gap_s)
    tiles = [(row, col) for row, col in body.tiles]
    return games.submit(conn, lex, s, player["id"], game_id, tiles, now)


@router.post("/api/games/{game_id}/end", dependencies=POST)
def end(game_id: str, body: EndGame, conn: Conn, s: Cfg, lex: Lex, player: Player, now: Now) -> dict:
    return games.end_by_player(conn, lex, s, player["id"], game_id, now)


@router.get("/api/games/current")
def current(mode: Annotated[GameMode, Query()], conn: Conn, s: Cfg, lex: Lex, player: Player, now: Now) -> dict:
    return {"game": games.current_game(conn, lex, s, player["id"], mode, now)}


# --- the error envelope ---

def error_response(status: int, code: str, message: str, headers: dict[str, str] | None = None, **extra: object) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, **extra}}, status_code=status, headers=headers)


async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
    retry_after = exc.extra.get("retry_after")
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else None
    return error_response(exc.status, exc.code, exc.message, headers=headers)


async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
    return error_response(exc.status_code, code, HTTPStatus(exc.status_code).phrase + ".", headers=exc.headers)


async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """422 invalid_request naming the bad fields. It never echoes input, so unknown keys aren't named."""
    fields = sorted({_field(err["loc"]) for err in exc.errors() if err["type"] != "extra_forbidden"})
    return error_response(422, "invalid_request", "The request is not valid.", fields=fields)


def _field(loc: tuple) -> str:
    """('body', 'tiles', 0, 1) → 'tiles'; ('query', 'mode') → 'mode'; ('body', 17) → 'body'."""
    names = [part for part in loc[1:] if isinstance(part, str)]
    return names[0] if names else str(loc[0])


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
