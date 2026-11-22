"""End-to-end test of flying by hand (`spacetaxi serve`'s `/fly`) in headless Chromium
(Playwright), against an arena server with short rounds and a scripted bot in its own thread
and event loop. Install the browser once: `uv run playwright install chromium`. Without it this
test skips."""

import json
import urllib.request

import pytest

from spacetaxi import ws
from spacetaxi.maps import load_map
from spacetaxi.server import ArenaServer
from spacetaxi.sim import Rules

pytest.importorskip("playwright.sync_api")

RULES = Rules(tick_ms=100, deadline_ms=80, round_ticks=40)


async def scripted_bot(port: int, name: str) -> None:
    """Joins and answers every tick with the main engine on odd ticks."""
    sock = await ws.connect(f"ws://127.0.0.1:{port}/bot")
    await sock.send(json.dumps({"type": "hello", "name": name, "protocol": 1}))
    while (text := await sock.recv()) is not None:
        msg = json.loads(text)
        if msg["type"] == "tick":
            await sock.send(json.dumps({"type": "move", "tick": msg["tick"], "ax": 0,
                                        "ay": -1 if msg["tick"] % 2 else 0}))


@pytest.fixture
def arena(serve_arena):
    """An arena server with one scripted bot; returns (base URL, server)."""
    server = ArenaServer(load_map("cavern"), RULES, 1.0, seed=1, max_arenas=3)
    base, server, _ = serve_arena(server, lambda loop, port: loop.create_task(scripted_bot(port, "robo")))
    return base, server


def open_page(browser, url, **kw):
    page = browser.new_page(**kw)
    errors, requests = [], []
    page.on("console", lambda m: errors.append(m.text) if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("request", lambda r: requests.append(r.url))
    page.goto(url)
    return page, errors, requests


def state(page):
    return page.evaluate("({...window.spacetaxi})")


def leaderboard(base):
    with urllib.request.urlopen(base + "/leaderboard.json", timeout=5) as res:
        return {r["name"]: r for r in json.load(res)["leaderboard"]}


def test_fly_by_hand_against_a_bot(browser, arena):
    base, server = arena
    page, errors, requests = open_page(browser, base + "/fly",
                                       viewport={"width": 1280, "height": 720})
    watch, watch_errors, _ = open_page(browser, base + "/watch",
                                       viewport={"width": 1920, "height": 1080})
    page.wait_for_function("window.spacetaxi && window.spacetaxi.fps > 0 "
                           "&& window.spacetaxi.fly === 'join'")
    assert page.locator("#join").is_visible()

    # A bad name is refused on the page; a good one joins and waits in the queue.
    page.fill("#name", "no spaces")
    page.click("#fly")
    assert "NAME" in page.locator("#joinMsg").inner_text()
    page.fill("#name", "me")
    page.click("#fly")
    page.wait_for_function("window.spacetaxi.fly === 'queued' && window.spacetaxi.queue", timeout=5000)
    s = state(page)
    assert s["id"] == "me" and s["queue"]["position"] >= 1 and s["queue"]["starts_in"] >= 0
    assert "IN THE QUEUE" in page.locator("#status").inner_text()
    assert page.locator("#leave").is_visible() and not page.locator("#join").is_visible()

    # The round starts: the page answers every tick; no key held is ax 0, ay 0.
    page.wait_for_function("window.spacetaxi.fly === 'playing' && window.spacetaxi.me "
                           "&& window.spacetaxi.moves > 2", timeout=10000)
    s = state(page)
    assert s["lastMove"]["ax"] == 0 and s["lastMove"]["ay"] == 0
    players = page.evaluate("window.spacetaxi.you")
    assert players >= 0
    # Hold the main engine for a few ticks: the taxi climbs, vy < 0.
    page.keyboard.down("ArrowUp")
    page.wait_for_function("window.spacetaxi.lastMove.ay === -1 && window.spacetaxi.me.vy < 0",
                           timeout=3000)
    page.keyboard.down("ArrowRight")
    page.wait_for_function("window.spacetaxi.lastMove.ax === 1", timeout=3000)
    page.keyboard.up("ArrowRight")
    page.keyboard.up("ArrowUp")
    page.wait_for_function("window.spacetaxi.lastMove.ax === 0 && window.spacetaxi.lastMove.ay === 0",
                           timeout=3000)
    # Only the player's own taxi carries the YOU marker.
    page.wait_for_function("window.spacetaxi.marked.length > 0", timeout=3000)
    assert state(page)["marked"] == ["me"]

    # /watch shows the human with the HUMAN tag, the bot without.
    watch.click("#splash")
    watch.wait_for_function("window.spacetaxi.humans.includes('me') "
                            "&& window.spacetaxi.marked.includes('me')", timeout=5000)
    assert "robo" not in state(watch)["marked"]

    # The round ends: the results show and the page is back in the queue on its own.
    page.wait_for_function("window.spacetaxi.fly === 'over' && window.spacetaxi.results", timeout=10000)
    assert {r["id"] for r in state(page)["results"]} == {"me", "robo"}
    assert page.locator("#results").is_visible()
    assert "ME (YOU)" in page.locator("#results").inner_text()
    page.wait_for_function("window.spacetaxi.queue !== null", timeout=5000)
    assert "IN THE QUEUE" in page.locator("#status").inner_text()

    # The leaderboard marks the human, on /leaderboard.json, on / and in /watch.
    rows = leaderboard(base)
    assert rows["me"]["human"] is True and rows["robo"]["human"] is False
    watch.wait_for_function("[...document.querySelectorAll('#lb li')].some("
                            "li => li.textContent.includes('ME') && li.textContent.includes('HUMAN'))",
                            timeout=5000)
    board, board_errors, _ = open_page(browser, base + "/")
    board.wait_for_function("document.querySelectorAll('#rows tr').length >= 2", timeout=5000)
    assert board.locator("#rows tr .human").count() == 1
    board.close()

    # The next round starts on its own; LEAVE disconnects.
    page.wait_for_function("window.spacetaxi.fly === 'playing'", timeout=10000)
    page.click("#leave")
    page.wait_for_function("window.spacetaxi.fly === 'join'", timeout=3000)
    assert page.locator("#join").is_visible()
    page.wait_for_timeout(300)
    assert "me" not in server.bots

    # No external requests: every request goes to the server (or is inline data).
    assert all(u.startswith(base) or u.startswith("data:") for u in requests), requests
    page.close()
    watch.close()
    assert errors == [] and watch_errors == [] and board_errors == []
