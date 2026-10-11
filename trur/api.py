"""Routes, dependencies and exception handlers (docs/architecture.md §2–§3).

All routes are plain `def`: sqlite3 blocks, so they run in the threadpool. Time comes from
app.state.clock and randomness from app.state.rng, never from the system directly.
"""

import sqlite3
from collections.abc import Iterator
from datetime import datetime
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException

from trur import auth, db
from trur.config import Settings
from trur.errors import ApiError
from trur.security import check_post

router = APIRouter()


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


def post_checks(request: Request) -> None:
    check_post(request, request.app.state.settings)


Conn = Annotated[sqlite3.Connection, Depends(get_conn)]
Cfg = Annotated[Settings, Depends(get_settings)]
Now = Annotated[datetime, Depends(get_now)]
POST = [Depends(post_checks)]  # route-level, so it runs before the body is validated


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


# --- routes ---

@router.get("/healthz")
def healthz(conn: Conn) -> dict:
    conn.execute("SELECT 1")
    return {"ok": True}


@router.post("/api/users", status_code=201, dependencies=POST)
def register(body: NewUser, request: Request, response: Response, conn: Conn, s: Cfg, now: Now) -> dict:
    if auth.user_for_token(conn, request.cookies.get(auth.COOKIE)) is not None:
        raise ApiError(409, "already_registered", "This browser already has a player.")
    nickname = auth.check_nickname(body.nickname)
    token = auth.create_user(conn, nickname, now)
    response.set_cookie(
        auth.COOKIE, token, max_age=auth.COOKIE_MAX_AGE, path="/",
        secure=s.cookie_secure, httponly=True, samesite="lax",
    )
    return {"nickname": nickname}


@router.get("/api/users/me")
def me(player: Player) -> dict:
    return {"nickname": player["nickname"]}


# --- the error envelope ---

def error_response(status: int, code: str, message: str, headers: dict[str, str] | None = None, **extra: object) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, **extra}}, status_code=status, headers=headers)


async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
    return error_response(exc.status, exc.code, exc.message)


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
