"""The local match sandbox (scripts/match_sandbox.py): a dev tool over trur/match.py."""

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator

import pytest

from scripts import match_sandbox
from scripts.match_sandbox import Sandbox
from trur.match import AlreadyVoted, NotRunning, NotVoting
from trur.solver import Lexicon

GE, AR, DE, ER, RU, NS = [4, 0], [5, 1], [4, 3], [5, 4], [1, 2], [0, 5]


@pytest.fixture
def box(lex: Lexicon) -> Sandbox:
    b = Sandbox(lex, 4, min_words=1, seed=0)
    b.new_board("garden", "summer")
    return b


def test_full_four_player_game(box: Sandbox) -> None:
    assert box.submit("1", [GE, AR]) == {"status": "accepted", "word": "GEAR"}
    assert box.submit("2", [GE, AR]) == {"status": "accepted", "word": "GEAR"}
    assert box.submit("1", [GE, AR])["status"] == "duplicate"
    assert box.submit("3", [RU, AR])["status"] == "rejected"
    box.showcase("3", [DE, ER])
    box.showcase("4", [RU, NS])
    s = box.state("2")
    assert [(f["nickname"], f["letters"]) for f in s["feed"]] == [("Ana", 4), ("Cleo", None), ("Dan", None)]
    assert s["found"] == ["GEAR"] and s["pct"][4][0] == 25 and s["pct"][4][3] == 100 and s["vote"] is None
    box.end_play()
    with pytest.raises(NotRunning):
        box.submit("1", [DE, ER])
    s = box.state("3")
    assert s["phase"] == "vote" and [c["word"] for c in s["vote"]["candidates"]] == ["RUNS"]
    cand = {box.state(u)["vote"]["candidates"][0]["word"]: box.state(u)["vote"]["candidates"][0]["id"] for u in "34"}
    for uid in "123":
        ids = [c["id"] for c in box.state(uid)["vote"]["candidates"] if c["word"] == "RUNS"]
        box.vote(uid, ids)
    with pytest.raises(AlreadyVoted):
        box.vote("1", [cand["RUNS"]])
    box.vote("4", [cand["DEER"]])
    res = box.state("1")["results"]
    assert box.phase == "results" and "user_id" not in res["players"][0]
    rows = {p["nickname"]: (p["score"], p["showcase"]) for p in res["players"]}
    assert rows["Dan"] == (5, {"word": "RUNS", "votes": 15, "bonus": 5})
    assert rows["Ana"] == (4, None) and rows["Ben"] == (2, None)


def test_vote_skipped_and_end_vote(box: Sandbox) -> None:
    box.end_play()
    assert box.phase == "results"
    with pytest.raises(NotVoting):
        box.end_vote()
    with pytest.raises(NotRunning):
        box.end_play()
    box.new_board()
    assert box.phase == "play" and box.result is None


def test_end_vote_early(box: Sandbox) -> None:
    box.showcase("1", [GE, AR])
    box.showcase("2", [DE, ER])
    box.end_play()
    box.end_vote()
    assert box.phase == "results"


def test_bad_input(lex: Lexicon, box: Sandbox) -> None:
    with pytest.raises(ValueError):
        Sandbox(lex, 7, 1)
    with pytest.raises(ValueError):
        box.new_board("GAR<DEN", "SUMMER")
    with pytest.raises(KeyError):
        box.state("9")


@pytest.fixture
def server(box: Sandbox) -> Iterator[str]:
    srv = match_sandbox.serve(box, 0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


def call(url: str, body: object = None, ctype: str = "application/json") -> tuple[int, dict | bytes]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data, {"Content-Type": ctype} if data is not None else {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            raw, status = r.read(), r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw


def test_http(server: str) -> None:
    status, page = call(server + "/")
    assert status == 200 and b"match sandbox" in page
    assert call(server + "/api/submit", {"p": "1", "tiles": [GE, AR]}) == (200, {"status": "accepted", "word": "GEAR"})
    assert call(server + "/api/showcase", {"p": "1", "tiles": [DE, ER]})[1]["status"] == "showcased"
    assert call(server + "/api/submit", {"p": "1", "tiles": [GE, DE]})[1]["error"] == "tile_locked"
    assert call(server + "/api/submit", {"p": "1", "tiles": [GE]})[0] == 422
    assert call(server + "/api/submit", {"p": "9", "tiles": [GE, AR]})[0] == 404
    assert call(server + "/api/state?p=2")[1]["feed"][0]["letters"] == 4
    assert call(server + "/api/vote", {"p": "1", "ranking": ["c1"]})[1]["error"] == "match_not_voting"
    assert call(server + "/api/end", {}) == (200, {})
    assert call(server + "/api/end-vote", {})[0] == 409
    assert call(server + "/api/new", {"word1": "GARDEN", "word2": "SUMMER"}) == (200, {})
    assert call(server + "/api/end", {}, "text/plain")[0] == 415
    assert call(server + "/api/nope", {})[0] == 404
    assert call(server + "/nope")[0] == 404


def test_main_starts_and_stops(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    class Stopped:
        server_port = 1

        def serve_forever(self) -> None:
            raise KeyboardInterrupt

    monkeypatch.setattr(match_sandbox, "serve", lambda box, port: Stopped())
    from tests.conftest import FIXTURES

    match_sandbox.main(["--data", str(FIXTURES), "--words", "GARDEN,SUMMER", "--min-words", "1"])
    assert "GARDEN × SUMMER" in capsys.readouterr().out
