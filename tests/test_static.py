"""AST guards over trur/ and scripts/ (docs/test-strategy.md §1–§2)."""

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SOURCES = sorted(p for d in ("trur", "scripts") for p in (ROOT / d).rglob("*.py"))

CLOCK_FILE = Path("trur/clock.py")
TIME_READS = {("datetime", "now"), ("datetime", "utcnow"), ("date", "today"), ("time", "time"), ("time", "time_ns")}
# Only these modules may import web-framework packages (docs/architecture.md §2).
FRAMEWORK_MODULES = {"fastapi", "starlette", "pydantic"}
FRAMEWORK_FILES = {Path("trur/api.py"), Path("trur/app.py"), Path("trur/security.py")}


def _rel(path: Path) -> Path:
    return path.relative_to(ROOT)


def time_reads(source: str) -> list[str]:
    """Calls that read the system time: datetime.now(), date.today(), time.time(), …"""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            value = node.func.value
            owner = value.attr if isinstance(value, ast.Attribute) else getattr(value, "id", None)
            if (owner, node.func.attr) in TIME_READS:
                found.append(f"line {node.lineno}: {owner}.{node.func.attr}()")
        elif isinstance(node, ast.ImportFrom) and node.module == "time":
            found += [f"line {node.lineno}: from time import {a.name}" for a in node.names if a.name in ("time", "time_ns")]
    return found


def global_random(source: str) -> list[str]:
    """Uses of the shared module-level RNG. Only random.Random(...) instances are allowed."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "random"
            and node.attr != "Random"
        ):
            found.append(f"line {node.lineno}: random.{node.attr}")
        elif isinstance(node, ast.ImportFrom) and node.module == "random":
            found += [f"line {node.lineno}: from random import {a.name}" for a in node.names if a.name != "Random"]
    return found


def framework_imports(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        else:
            continue
        found += [f"line {node.lineno}: {n}" for n in names if n.split(".")[0] in FRAMEWORK_MODULES]
    return found


@pytest.mark.parametrize("path", [p for p in SOURCES if _rel(p) != CLOCK_FILE], ids=lambda p: str(_rel(p)))
def test_time_is_read_only_in_clock(path: Path) -> None:
    assert time_reads(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(_rel(p)))
def test_no_global_random(path: Path) -> None:
    assert global_random(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("path", [p for p in SOURCES if _rel(p) not in FRAMEWORK_FILES], ids=lambda p: str(_rel(p)))
def test_framework_only_in_web_layer(path: Path) -> None:
    assert framework_imports(path.read_text(encoding="utf-8")) == []


EXECUTE = {"execute", "executemany", "executescript"}


def built_sql(source: str) -> list[str]:
    """execute*() calls whose SQL is built with an f-string, %, + or .format() (S1).

    db.migrate is the one exception: it runs constant migration SQL with executescript.
    """
    found = []
    tree = ast.parse(source)
    allowed = {
        id(node)
        for fn in ast.walk(tree)
        if isinstance(fn, ast.FunctionDef) and fn.name == "migrate"
        for node in ast.walk(fn)
    }
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in EXECUTE):
            continue
        if node.func.attr == "executescript" and id(node) not in allowed:
            found.append(f"line {node.lineno}: executescript outside db.migrate")
        if not node.args or id(node) in allowed:
            continue
        sql = node.args[0]
        formatted = isinstance(sql, ast.Call) and isinstance(sql.func, ast.Attribute) and sql.func.attr == "format"
        if isinstance(sql, (ast.JoinedStr, ast.BinOp)) or formatted:
            found.append(f"line {node.lineno}: SQL built from strings")
    return found


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(_rel(p)))
def test_sql_is_never_built_from_strings(path: Path) -> None:
    assert built_sql(path.read_text(encoding="utf-8")) == []


def test_guards_catch_planted_code() -> None:
    planted = (
        "import datetime, time, random\n"
        "from time import time as t\n"
        "from random import choice\n"
        "from fastapi import FastAPI\n"
        "a = datetime.datetime.now()\n"
        "b = datetime.date.today()\n"
        "c = time.time()\n"
        "d = random.choice([1, 2])\n"
        "e = random.Random(1).choice([1, 2])\n"
    )
    assert len(time_reads(planted)) == 4
    assert len(global_random(planted)) == 2  # random.choice and `from random import choice`; Random(1) is fine
    assert len(framework_imports(planted)) == 1
    sql = (
        "conn.execute(f'SELECT * FROM users WHERE nickname = {nick}')\n"
        "conn.execute('SELECT * FROM games WHERE id = ' + game_id)\n"
        "conn.execute('SELECT %s' % x)\n"
        "conn.execute('SELECT {}'.format(x))\n"
        "conn.executescript(user_sql)\n"
        "conn.execute('SELECT * FROM games WHERE id = ?', (game_id,))\n"
        "def migrate(conn):\n    conn.executescript(f'BEGIN;{SQL}COMMIT;')\n"
    )
    assert len(built_sql(sql)) == 5  # the placeholder query and db.migrate's constant SQL are fine


# --- the frontend in web/ (CSP: default-src 'self'; S8: text only through textContent) ---

WEB = ROOT / "web"
HTML_FILES = sorted(WEB.rglob("*.html"))
JS_FILES = sorted(WEB.rglob("*.js"))


@pytest.mark.parametrize("path", HTML_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_html_has_no_inline_script_or_style(path: Path) -> None:
    html = path.read_text(encoding="utf-8")
    assert re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html) is None, "inline <script>"
    assert re.search(r"<style\b", html) is None, "<style> element"
    assert re.search(r"\sstyle=", html) is None, "style attribute"
    assert re.search(r"\son[a-z]+=", html) is None, "inline event handler"
    assert re.search(r"(src|href)=\"(https?:)?//", html) is None, "external resource"


@pytest.mark.parametrize("path", JS_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_js_never_parses_html(path: Path) -> None:
    js = path.read_text(encoding="utf-8")
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert sink not in js, sink


def test_web_guards_catch_planted_code(tmp_path: Path) -> None:
    assert re.search(r"<script(?![^>]*\bsrc=)[^>]*>", "<script>alert(1)</script>")
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", '<script type="module" src="js/main.js"></script>')
