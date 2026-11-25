"""End-to-end test of the viewer script split by mode, in headless Chromium (Playwright): a
saved replay runs the player only, `/watch` the player and live mode, `/fly` all three, each
from one self-contained page without a console error. Without the browser (`uv run playwright
install chromium`) this test skips."""

from pathlib import Path

import pytest

from spacetaxi.maps import load_map
from spacetaxi.server import ArenaServer
from spacetaxi.sim import Rules
from spacetaxi.viewer import build_html

pytest.importorskip("playwright.sync_api")

STACKED = Path(__file__).parent.parent / "fixtures" / "show" / "stacked.jsonl"
# One function of each script, as the page sees it.
PROBE = "[typeof draw, typeof connectLive, typeof startFly]"


@pytest.fixture
def pages(serve_arena, tmp_path):
    base, _, _ = serve_arena(ArenaServer({"cavern": load_map("cavern")}, Rules(), 1.0, seed=1))
    replay = tmp_path / "replay.html"
    replay.write_text(build_html(str(STACKED)), encoding="utf-8")
    return {"replay": replay.as_uri(), "watch": base + "/watch", "fly": base + "/fly"}


@pytest.mark.parametrize("name, ready, modes", [
    ("replay", "window.spacetaxi.round === 1", ["function", "undefined", "undefined"]),
    ("watch", "window.spacetaxi.connected", ["function", "function", "undefined"]),
    ("fly", "window.spacetaxi.fly === 'join'", ["function", "function", "function"]),
])
def test_each_page_runs_only_its_modes(browser, pages, name, ready, modes):
    page = browser.new_page()
    errors, scripts = [], []
    page.on("console", lambda m: errors.append(m.text) if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("request", lambda r: scripts.append(r.url) if r.resource_type == "script" else None)
    page.goto(pages[name])
    page.wait_for_function(f"window.spacetaxi && window.spacetaxi.fps > 0 && {ready}")
    assert page.evaluate(PROBE) == modes
    assert errors == [] and scripts == []
    page.close()
