"""Routes, dependencies and exception handlers (docs/architecture.md §2–§3).

Routes are plain `def`: sqlite3 blocks, so they run in the threadpool. The exception are the
multiplayer routes, which are `async def` so that match state only changes on the event loop;
they never call sqlite3 themselves (§8 item 24). Time comes from app.state.clock and randomness
from app.state.rng, never from the system directly.
"""

import asyncio
import json
import random
import re
import sqlite3
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import date, datetime
from http import HTTPStatus
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Receive, Scope, Send

from trur import auth, daily, db, games, match, rankings
from trur.config import Settings
from trur.errors import ApiError
from trur.lobby import TERMINAL, Item, Lobby, public_results
from trur.security import RateLimiter, check_post, client_ip
from trur.solver import Lexicon, Tile

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


GameMode = Literal["free", "daily"]


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


# Client event types only: a server-only type such as word_accepted matches no member, so it's a 422.
ClientMs = Annotated[int, Field(ge=0, le=2**53)]
GameId = Annotated[str, Field(max_length=36)]


class TileClicked(Strict):
    type: Literal["tile_clicked"]
    game_id: GameId
    row: Coord
    col: Coord
    selected: bool
    client_ms: ClientMs


class SelectionCleared(Strict):
    type: Literal["selection_cleared"]
    game_id: GameId
    client_ms: ClientMs


MAX_EVENTS = 50


class EventBatch(Strict):
    events: Annotated[
        list[Annotated[TileClicked | SelectionCleared, Field(discriminator="type")]],
        Field(min_length=1, max_length=MAX_EVENTS),
    ]


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


_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def parse_day(raw: str) -> date:
    """Exactly YYYY-MM-DD and a real date, else 422. Stricter than Pydantic's date, which also takes timestamps."""
    try:
        if _ISO_DATE.fullmatch(raw):
            return date.fromisoformat(raw)
    except ValueError:
        pass
    raise ApiError(422, "invalid_request", "The date is not valid.")


@router.get("/api/daily/{day}/answers")
def answers(day: str, request: Request, conn: Conn, lex: Lex, now: Now) -> dict:
    player = auth.user_for_token(conn, request.cookies.get(auth.COOKIE))  # optional: adds the caller's finds
    return daily.daily_answers(conn, lex, player["id"] if player else None, parse_day(day), now.date())


_MONTH = re.compile(r"\d{4}-(0[1-9]|1[0-2])")


def parse_month(raw: str) -> str:
    if _MONTH.fullmatch(raw):
        return raw
    raise ApiError(422, "invalid_request", "The month is not valid.")


def get_conn_ro(request: Request) -> Iterator[sqlite3.Connection]:
    """A read-only connection for the board query (S1). It reads after the sweep has committed (§8 item 14)."""
    conn = db.connect_ro(request.app.state.settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


ConnRo = Annotated[sqlite3.Connection, Depends(get_conn_ro)]


@router.get("/api/rankings")
def monthly_board(
    mode: Annotated[GameMode, Query()], conn: Conn, conn_ro: ConnRo, s: Cfg, lex: Lex, now: Now,
    month: Annotated[str | None, Query()] = None,
) -> dict:
    period = now.strftime("%Y-%m") if month is None else parse_month(month)
    games.sweep_abandoned(conn, lex, s, now)
    return {"mode": mode, "month": period, "entries": rankings.monthly_board(conn_ro, mode, period, s.board_limit)}


@router.get("/api/rankings/daily")
def daily_board(
    conn: Conn, conn_ro: ConnRo, s: Cfg, lex: Lex, now: Now,
    day: Annotated[str | None, Query(alias="date")] = None,
) -> dict:
    when = now.date() if day is None else parse_day(day)
    games.sweep_abandoned(conn, lex, s, now)
    return {"date": when.isoformat(), "entries": rankings.daily_board(conn_ro, when, s.board_limit)}


@router.post("/api/events", status_code=204, dependencies=POST)
def events(body: EventBatch, conn: Conn, s: Cfg, player: Player, now: Now, limiter: Limiter) -> Response:
    limiter.check(f"events:{player['id']}", s.rl_events_per_player_s, 1)
    games.log_client_events(conn, player["id"], [e.model_dump() for e in body.events], now)
    return Response(status_code=204)


# --- multiplayer (docs/multiplayer.md §6.1, §6.4, §6.5) ---
#
# async def: each route reads the clock and changes match state with no await in between, so
# submits and ballots are handled one at a time in arrival order, without locks (§7 M3). The lobby
# raises the API errors itself.

def get_match_player(request: Request) -> sqlite3.Row:
    """The player, from a connection that is closed before the route runs, so a stream never holds one."""
    conn = db.connect(request.app.state.settings.db_path)
    try:
        return get_player(request, conn)
    finally:
        conn.close()


MatchPlayer = Annotated[sqlite3.Row, Depends(get_match_player)]


class NoInput(Strict):
    """{}: the route takes no input."""


class Ballot(Strict):
    """Candidate ids from the caller's ballot, best first. The lobby judges them, so a bad ballot of
    any kind (no entry or more than three, a repeated or unknown id, your own word) is 422 invalid_ballot."""

    ranking: list[str]


def _lobby(request: Request) -> Lobby:
    return request.app.state.lobby


def _now(request: Request) -> datetime:
    """Read inside the route, never in a dependency: those run in the threadpool, so the time could be stale."""
    return request.app.state.clock.now()


def _match_id(raw: str) -> str:
    """A canonical UUID, or 404 before any lookup (§6.4)."""
    try:
        if str(uuid.UUID(raw)) == raw:
            return raw
    except ValueError:
        pass
    raise ApiError(404, "not_found", "There is no such match.")


@router.post("/api/match/queue", dependencies=POST)
async def match_queue(body: NoInput, request: Request, player: MatchPlayer) -> dict:
    s: Settings = request.app.state.settings
    request.app.state.limiter.check(f"queue:{player['id']}", s.rl_queue_joins_per_player_hour, 3600)
    return _lobby(request).join(player["id"], player["nickname"], player["nickname_key"], _now(request))


@router.post("/api/match/queue/leave", status_code=204, dependencies=POST)
async def match_leave(body: NoInput, request: Request, player: MatchPlayer) -> Response:
    _lobby(request).leave(player["id"], _now(request))
    return Response(status_code=204)


@router.get("/api/match/current")
async def match_current(request: Request, player: MatchPlayer) -> dict:
    return _lobby(request).view(player["id"], _now(request))


class EventStream(StreamingResponse):
    """text/event-stream that runs `closed` however it ends: after a terminal event, when the
    browser goes away, or when a shutdown cuts it. A generator's own `finally` wouldn't run if the
    browser left before the first chunk."""

    def __init__(self, content: AsyncIterator[str], closed: Callable[[], None]) -> None:
        super().__init__(content, media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})
        self.closed = closed

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.closed()


async def _events(q: asyncio.Queue[Item], keepalive_s: float) -> AsyncIterator[str]:
    """The stream's events as SSE, with a ": ping" after keepalive_s without one. It ends after a
    terminal event, or without an event when the player leaves the queue (None)."""
    while True:
        try:
            item = await asyncio.wait_for(q.get(), keepalive_s)
        except TimeoutError:
            yield ": ping\n\n"
            continue
        if item is None:
            return
        event, data = item
        yield f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"
        if event in TERMINAL:
            return


@router.get("/api/match/stream")
async def match_stream(request: Request, player: MatchPlayer) -> Response:
    lobby, clock, uid = _lobby(request), request.app.state.clock, player["id"]
    q = lobby.connect(uid, clock.now())
    if q is None:
        return Response(status_code=204)  # idle: EventSource doesn't reconnect after a 204
    keepalive_s = request.app.state.settings.sse_keepalive_s
    return EventStream(_events(q, keepalive_s), lambda: lobby.hub.unsubscribe(uid, q, clock.now()))


def _tiles(body: Submission) -> list[Tile]:
    return [(row, col) for row, col in body.tiles]


def _changes(changes: tuple[match.Change, ...]) -> list[dict]:
    return [{"row": r, "col": c, "pct": pct} for (r, c), pct in changes]


def _submit_gap(request: Request, player: sqlite3.Row) -> None:
    """MATCH_SUBMIT_MIN_GAP_S between a player's submits and showcase words, checked before anything changes."""
    gap = request.app.state.settings.match_submit_min_gap_s
    request.app.state.limiter.check(f"match-submit:{player['id']}", 1, gap)


@router.post("/api/match/{match_id}/submit", dependencies=POST)
async def match_submit(match_id: str, body: Submission, request: Request, player: MatchPlayer) -> dict:
    mid = _match_id(match_id)
    _submit_gap(request, player)
    out = _lobby(request).submit(player["id"], mid, _tiles(body), _now(request))
    return {"status": out.status, "word": out.word, "changes": _changes(out.changes)}  # never the points


@router.post("/api/match/{match_id}/showcase", dependencies=POST)
async def match_showcase(match_id: str, body: Submission, request: Request, player: MatchPlayer) -> dict:
    mid = _match_id(match_id)
    _submit_gap(request, player)
    word = _lobby(request).showcase(player["id"], mid, _tiles(body), _now(request))
    return {"status": "showcased", "word": word}


@router.post("/api/match/{match_id}/vote", status_code=204, dependencies=POST)
async def match_vote(match_id: str, body: Ballot, request: Request, player: MatchPlayer) -> Response:
    _lobby(request).vote(player["id"], _match_id(match_id), body.ranking, _now(request))
    return Response(status_code=204)


def _saved_results(s: Settings, match_id: str, user_id: str) -> dict | None:
    conn = db.connect_ro(s.db_path)
    try:
        return db.match_results(conn, match_id, user_id)
    finally:
        conn.close()


@router.get("/api/match/{match_id}/results")
async def match_results(match_id: str, request: Request, player: MatchPlayer) -> dict:
    """From memory until the save is done, then from the DB. Both check that the caller played."""
    mid = _match_id(match_id)
    res = _lobby(request).results(mid, player["id"])
    if res is None:
        res = await asyncio.to_thread(_saved_results, request.app.state.settings, mid, player["id"])
    if res is None:
        raise ApiError(404, "not_found", "There is no such match.")
    return public_results(res)


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
