"""Screenshots of every screen on the test devices, for design review (docs/mobile.md, P9).

    python -m scripts.mobile_shots [--device ID]... [--screen NAME]... [--theme light|dark|both] [--out DIR]
    python -m scripts.mobile_shots --list

Serves web/ on a local port and answers the API with the tests' FakeApi and fixture dictionary, then drives
each screen with the recipes the tests use (tests/e2e/screens.py) on the device table of tests/e2e/devices.py.
Saves <out>/<device>/<screen>-<theme>.png; the default out is var/shots/<git sha>. A development tool: it
needs the dev requirements and Playwright's Chromium.
"""

import argparse
import subprocess
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tests.e2e.devices import BY_ID, DEVICES, Device
from tests.e2e.screens import PHONE_STATES, STATES, State, reach

ROOT = Path(__file__).resolve().parent.parent
SCREENS = {**STATES, **PHONE_STATES}
THEMES = ("light", "dark")
# Windows can map .js to text/plain from the registry, and browsers refuse such module scripts.
TYPES = {".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".woff2": "font/woff2"}


class _Static(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, **TYPES}

    def log_message(self, *args) -> None:  # quiet
        pass


def git_sha() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    except OSError:
        return "local"
    return out.stdout.strip() or "local"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m scripts.mobile_shots", description=__doc__.split("\n", 1)[0])
    parser.add_argument("--device", action="append", choices=list(BY_ID), metavar="ID",
                        help="a device id from --list; repeat for more (default: every device)")
    parser.add_argument("--screen", action="append", choices=list(SCREENS), metavar="NAME",
                        help="a screen from --list; repeat for more (default: every screen)")
    parser.add_argument("--theme", choices=[*THEMES, "both"], default="both", help="colour scheme (default: both)")
    parser.add_argument("--out", type=Path, help="output folder (default: var/shots/<git sha>)")
    parser.add_argument("--list", action="store_true", help="print the device ids and screen names, then exit")
    return parser.parse_args(argv)


def plan(args: argparse.Namespace) -> list[tuple[Device, str, str]]:
    """(device, screen, theme) for every shot. Phone-only screens are skipped on desktop devices."""
    devices = [BY_ID[i] for i in args.device] if args.device else DEVICES
    screens = args.screen or list(SCREENS)
    themes = THEMES if args.theme == "both" else (args.theme,)
    return [(d, s, t) for d in devices for s in screens for t in themes if d.touch or s not in PHONE_STATES]


def listing() -> str:
    lines = ["devices:"]
    lines += [f"  {d.id:<11} {d.width}x{d.height}{'' if d.touch else '  (mouse)'}" for d in DEVICES]
    lines += ["screens:"] + [f"  {name}" for name in SCREENS]
    return "\n".join(lines)


def shoot(shots: list[tuple[Device, str, str]], out: Path) -> list[Path]:
    from playwright.sync_api import sync_playwright  # the dev requirements only

    from trur.solver import load_lexicon

    lex = load_lexicon(ROOT / "tests" / "fixtures")
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Static, directory=str(ROOT / "web")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    saved = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            for device, screen, theme in shots:
                # reduced motion, as in D1: shots show screens at rest, not mid slide-in
                context = browser.new_context(**{**device.context_args(playwright), "color_scheme": theme,
                                                 "reduced_motion": "reduce"})
                page = context.new_page()
                state: State = SCREENS[screen]
                reach(page, base_url, lex, state)
                path = out / device.id / f"{screen}-{theme}.png"
                path.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(path))
                saved.append(path)
                context.close()
            browser.close()
    finally:
        server.shutdown()
    return saved


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.list:
        print(listing())
        return 0
    out = args.out or ROOT / "var" / "shots" / git_sha()
    saved = shoot(plan(args), out)
    print(f"{len(saved)} screenshots in {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
