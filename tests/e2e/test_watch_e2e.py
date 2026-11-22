"""End-to-end tests of the live viewer (`spacetaxi serve`'s `/watch`) in headless Chromium
(Playwright), against an arena server of scripted bots in its own thread and event loop.
Install the browser once: `uv run playwright install chromium`. Without it these tests skip."""

import asyncio
import json
import threading
import urllib.request

import pytest

from spacetaxi import ws
from spacetaxi.maps import load_map
from spacetaxi.server import ArenaServer
from spacetaxi.sim import Rules

pytest.importorskip("playwright.sync_api")

RULES = Rules(tick_ms=100, deadline_ms=80, round_ticks=40)


async def scripted_bot(port: int, name: str) -> None:
    """Joins and answers every tick: up on even ticks, with a debug text and path."""
    sock = await ws.connect(f"ws://127.0.0.1:{port}/bot")
    await sock.send(json.dumps({"type": "hello", "name": name, "protocol": 1}))
    while (text := await sock.recv()) is not None:
        msg = json.loads(text)
        if msg["type"] == "tick":
            await sock.send(json.dumps({
                "type": "move", "tick": msg["tick"], "ax": 0, "ay": -1 if msg["tick"] % 2 else 0,
                "debug": {"say": f"{name} here", "path": [[3, 3], [4, 3]]}}))


def run_arena(serve_arena, bots: int, fill_seconds: float):
    """An arena server with `bots` scripted bots; returns (base URL, server)."""
    server = ArenaServer(load_map("cavern"), RULES, fill_seconds, seed=1, max_arenas=3)
    base, server, _ = serve_arena(server, lambda loop, port: [
        loop.create_task(scripted_bot(port, f"bot{i}")) for i in range(bots)])
    return base, server


@pytest.fixture
def two_arenas(serve_arena):
    return run_arena(serve_arena, bots=8, fill_seconds=0.2)


@pytest.fixture
def lonely(serve_arena):
    return run_arena(serve_arena, bots=1, fill_seconds=600)


def open_watch(browser, base):
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/watch")
    page.wait_for_function("window.spacetaxi && window.spacetaxi.fps > 0 && window.spacetaxi.live")
    return page, errors


def state(page):
    return page.evaluate("({...window.spacetaxi})")


def live_arenas(base):
    with urllib.request.urlopen(base + "/leaderboard.json", timeout=5) as res:
        return {a["arena"]: a for a in json.load(res)["arenas"]}


def test_watch_follows_switches_and_reports_the_live_tick(browser, two_arenas):
    base, server = two_arenas
    page, errors = open_watch(browser, base)
    page.click("#splash")
    page.wait_for_function("window.spacetaxi.started && window.spacetaxi.running.length === 2",
                           timeout=15000)
    assert not page.locator("#splash").is_visible()

    # Follows the arena whose round started last, and its ticks advance live.
    page.wait_for_function("window.spacetaxi.tick > 3", timeout=10000)
    s = state(page)
    assert s["follow"] and s["round"] == max(a["round"] for a in live_arenas(base).values())
    t0 = s["tick"]
    page.wait_for_function(f"window.spacetaxi.tick >= {t0 + 5}", timeout=5000)

    # The page's tick is the server's live tick, give or take the viewer's lag.
    s = state(page)
    server_tick = live_arenas(base)[s["arena"]]["tick"]
    assert s["round"] == live_arenas(base)[s["arena"]]["round"]
    assert 0 <= server_tick - s["tick"] <= 5 or server_tick == 0

    # The picker switches arena without a reload.
    page.evaluate("window.__same_page = true")
    other = next(k for k in s["running"] if k != s["arena"])
    page.select_option("#round", str(other))
    page.wait_for_function(f"window.spacetaxi.arena === {other} && !window.spacetaxi.follow")
    s2 = state(page)
    assert page.evaluate("window.__same_page") is True
    assert s2["round"] != s["round"] and s2["tick"] > 0
    page.wait_for_function(f"window.spacetaxi.tick >= {s2['tick'] + 3}", timeout=5000)

    # The D overlay shows the bots' debug, which only spectators get.
    page.keyboard.press("d")
    page.wait_for_function("window.spacetaxi.bubbles.some(b => b.endsWith('here'))", timeout=5000)
    # Sound and effects run as in the replay viewer.
    page.wait_for_function("window.spacetaxi.sfx.hum > 0 && window.spacetaxi.fx.flames > 0",
                           timeout=5000)
    assert state(page)["audio"] is True

    # At a round end the leaderboard panel fills in.
    page.wait_for_function("window.spacetaxi.leaderboard.length >= 8", timeout=15000)
    assert page.locator("#lb li").count() >= 8
    assert page.locator("#lb").is_visible()
    page.close()
    assert errors == []


def test_watch_shows_insert_coin_with_the_queue_between_rounds(browser, lonely):
    base, server = lonely
    page, errors = open_watch(browser, base)
    page.wait_for_function("window.spacetaxi.waiting === 1 && window.spacetaxi.connected",
                           timeout=10000)
    page.click("#splash")
    page.wait_for_timeout(300)
    s = state(page)
    assert s["started"] and s["idle"] and s["running"] == []
    splash = page.locator("#splash")
    assert splash.is_visible()
    assert "INSERT COIN" in splash.inner_text() and "1 BOT IN THE QUEUE" in splash.inner_text()
    page.close()
    assert errors == []


def test_watch_leaderboard_waits_for_the_first_round(browser, lonely):
    base, server = lonely
    page, errors = open_watch(browser, base)
    page.wait_for_function("window.spacetaxi.connected", timeout=10000)
    page.wait_for_function("document.querySelector('#lbRows').textContent.trim() !== ''",
                           timeout=10000)
    assert state(page)["leaderboard"] == []
    assert page.locator("#lbRows").inner_text().strip() == "WAITING FOR THE FIRST ROUND"
    assert page.locator("#lb").is_visible()
    page.close()
    assert errors == []


def test_watch_pops_up_milestones_and_standings_render(browser, lonely, shots):
    base, server = lonely
    page, errors = open_watch(browser, base)
    page.click("#splash")
    page.wait_for_function("window.spacetaxi.connected", timeout=10000)

    async def hello(name: str) -> None:
        sock = await ws.connect(base.replace("http", "ws") + "/bot")
        await sock.send(json.dumps({"type": "hello", "name": name, "protocol": 1}))
        await sock.recv()  # welcome: the award has gone out
        await sock.close()

    t = threading.Thread(target=asyncio.run, args=(hello("anna"),))  # Playwright owns this loop
    t.start()
    t.join(10)
    page.wait_for_function("window.spacetaxi.milestones.length >= 1", timeout=5000)
    assert state(page)["milestones"] == ["ANNA · HELLO · P2 · +18"]
    assert state(page)["sfx"]["fanfare"] == 1
    toast = page.locator("#toasts .toast")
    assert toast.is_visible() and toast.inner_text() == "ANNA · HELLO · P2 · +18"
    page.wait_for_timeout(400)
    page.screenshot(path=str(shots / "milestone.png"))
    page.close()

    board = browser.new_page(viewport={"width": 1920, "height": 1080})
    board.on("pageerror", lambda e: errors.append(str(e)))
    board.goto(base + "/standings")
    board.wait_for_function("window.spacetaxi.standings && window.spacetaxi.standings.standings.length === 2",
                            timeout=5000)
    rows = board.locator("#rows tr")
    assert rows.count() == 2
    assert "BOT0" in rows.nth(0).inner_text() and "25" in rows.nth(0).inner_text()
    assert board.locator("#rows .badge.on").count() == 2
    board.screenshot(path=str(shots / "standings.png"))
    board.close()
    assert errors == []
