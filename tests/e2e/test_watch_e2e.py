"""End-to-end tests of the live viewer (`spacetaxi serve`'s `/watch`) in headless Chromium
(Playwright), against an arena server of scripted bots in its own thread and event loop.
Install the browser once: `uv run playwright install chromium`. Without it these tests skip."""

import asyncio
import json
import re
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
    server = ArenaServer({"cavern": load_map("cavern")}, RULES, fill_seconds, seed=1, max_arenas=3)
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


def picker(page):
    return page.locator("#round option").all_inner_texts()


def expected_picker(arenas, slots=3):
    return ["FOLLOW LATEST"] + [
        f"ARENA {k} \u00b7 " + (" ".join(n.upper() for n in arenas[k]["players"])
                               if k in arenas else "EMPTY")
        for k in range(1, slots + 1)]


def test_watch_picker_names_each_arenas_players(browser, two_arenas):
    base, server = two_arenas
    page, errors = open_watch(browser, base)
    page.wait_for_function("window.spacetaxi.running.length === 2", timeout=15000)

    arenas = live_arenas(base)
    assert sorted(arenas) == [1, 2] and all(len(a["players"]) == 4 for a in arenas.values())
    first = {k: a["round"] for k, a in arenas.items()}
    page.wait_for_function("document.querySelectorAll('#round option').length === 4")
    assert picker(page) == expected_picker(arenas)
    assert not any(re.search(r"R\d|IDLE", o) for o in picker(page))

    # After the rounds end, the names stay until the next wave starts.
    page.wait_for_function("window.spacetaxi.idle", timeout=15000)
    assert picker(page) == expected_picker(arenas)

    # The next wave deals new groups, and the options follow them.
    page.wait_for_function("window.spacetaxi.running.length === 2", timeout=15000)
    page.wait_for_function("window.spacetaxi.tick > 1", timeout=10000)
    arenas = live_arenas(base)
    assert all(a["round"] > first[k] for k, a in arenas.items())
    assert picker(page) == expected_picker(arenas)
    assert not any(re.search(r"R\d|IDLE", o) for o in picker(page))
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


@pytest.fixture
def two_maps(serve_arena):
    """Two scripted bots on a server with cavern and classic (cavern first)."""
    maps = {"cavern": load_map("cavern"), "classic": load_map("classic")}
    server = ArenaServer(maps, RULES, 0.5, seed=1, benchmarks={"cavern": {"greedy": 1, "pilot": 1},
                                                              "classic": {"greedy": 1, "pilot": 1}})
    base, server, loop = serve_arena(server, lambda loop, port: [
        loop.create_task(scripted_bot(port, f"bot{i}")) for i in range(2)])
    return base, server, loop


def test_watch_shows_the_new_map_after_a_switch_without_a_reload(browser, two_maps):
    base, server, loop = two_maps
    page, errors = open_watch(browser, base)
    page.click("#splash")
    page.wait_for_function("window.spacetaxi.mapName === 'cavern' && window.spacetaxi.tick > 0",
                           timeout=15000)
    assert state(page)["map"] == list(load_map("cavern").rows)
    page.evaluate("window.__same_page = true")

    async def switch():  # typed in serve's terminal; run on the server's loop
        return server.command("map classic")

    answer = asyncio.run_coroutine_threadsafe(switch(), loop).result(5)
    assert answer == "next wave: classic"
    page.wait_for_function("window.spacetaxi.nextMap === 'classic'", timeout=10000)
    assert state(page)["mapName"] == "cavern"  # the wave in progress finishes on its map

    page.wait_for_function("window.spacetaxi.mapName === 'classic'", timeout=20000)
    assert state(page)["map"] == list(load_map("classic").rows)
    assert page.evaluate("window.__same_page") is True
    page.close()
    assert errors == []


def test_insert_coin_names_the_next_map(browser, lonely):
    base, server = lonely
    page, errors = open_watch(browser, base)
    page.wait_for_function("window.spacetaxi.nextMap === 'cavern'", timeout=10000)
    page.click("#splash")
    assert "NEXT MAP: CAVERN" in page.locator("#splash").inner_text()
    page.close()
    assert errors == []


# --- the plan of the next wave on /watch and /mission

@pytest.fixture
def fourteen(serve_arena):
    """14 scripted bots on an idle server whose first wave is 10 minutes away: 12 planned into
    3 arenas, 2 on the waitlist."""
    server = ArenaServer({"cavern": load_map("cavern")}, RULES, 600, seed=1)
    base, server, _ = serve_arena(server, lambda loop, port: [
        loop.create_task(scripted_bot(port, f"bot{i}")) for i in range(14)])
    return base, server


def next_wave(base):
    with urllib.request.urlopen(base + "/leaderboard.json", timeout=5) as res:
        return json.load(res)["next_wave"]


def wait_for_plan(page, base, size):
    page.wait_for_function(
        f"window.spacetaxi.nextWave && window.spacetaxi.nextWave.arenas.flat().length"
        f" + window.spacetaxi.nextWave.waitlist.length === {size}", timeout=15000)
    return next_wave(base)


def test_watch_splash_shows_the_next_waves_arenas_and_waitlist(browser, fourteen, shots):
    base, server = fourteen
    page, errors = open_watch(browser, base)
    plan = wait_for_plan(page, base, 14)
    assert [len(g) for g in plan["arenas"]] == [4, 4, 4] and len(plan["waitlist"]) == 2
    splash = page.locator("#splash")
    assert splash.is_visible()
    text = splash.inner_text()
    assert re.search(r"NEXT WAVE 1 · CAVERN · IN \d+ S", text)
    cols = page.locator("#plan .col")
    assert cols.count() == 3
    for i, names in enumerate(plan["arenas"]):
        col = cols.nth(i).inner_text()
        assert f"ARENA {i + 1}" in col and all(n.upper() in col for n in names)
    assert page.locator("#plan .wait").inner_text() == (
        "WAITING: " + ", ".join(n.upper() for n in plan["waitlist"]))
    # the compact box under the leaderboard has one line per arena and one for the waitlist
    assert page.locator("#lbNext div").count() == 1 + 3 + 1
    page.screenshot(path=str(shots / "next-wave.png"))
    page.close()
    assert errors == []


def test_watch_leaderboard_shows_all_eighteen_names(browser, serve_arena, tmp_path, shots):
    names = [f"name{i:02d}" for i in range(18)]
    log = tmp_path / "rounds.jsonl"
    log.write_text(json.dumps({"type": "round_end", "round": 1, "wave": 1, "results": [
        {"id": n, "money": 100 - i, "deliveries": 1, "crashes": 0, "ticks": 40, "late": 0}
        for i, n in enumerate(names)]}) + "\n", encoding="utf-8")
    server = ArenaServer({"cavern": load_map("cavern")}, RULES, 600, seed=1, log=log)
    base, server, _ = serve_arena(server)
    page, errors = open_watch(browser, base)
    page.wait_for_function("window.spacetaxi.leaderboard.length === 18", timeout=10000)
    page.click("#splash")
    rows = page.locator("#lbRows li")
    assert rows.count() == 18
    box = page.locator("#lbRows").bounding_box()
    for i in range(18):
        r = rows.nth(i).bounding_box()
        assert r["height"] > 0 and r["y"] >= box["y"] - 1
        assert r["y"] + r["height"] <= box["y"] + box["height"] + 1
    assert rows.nth(17).inner_text().split("\n")[1] == "NAME17"
    page.screenshot(path=str(shots / "leaderboard-18.png"))
    page.close()
    assert errors == []


def test_mission_shows_next_for_a_planned_name_and_waitlist_for_a_waiting_one(browser, fourteen):
    base, server = fourteen
    page = browser.new_page(viewport={"width": 390, "height": 844})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/watch")  # only to wait for the plan of all 14
    plan = wait_for_plan(page, base, 14)
    planned, others = plan["arenas"][1][0], plan["arenas"][1][1:]
    waiting = plan["waitlist"][1]

    page.goto(base + "/mission")
    page.fill("#askName", planned)
    page.click("#ask button")
    page.wait_for_function("window.spacetaxi.where", timeout=10000)
    where = page.locator("#where").inner_text()
    assert re.fullmatch(rf"NEXT: wave 1, arena 2 with {', '.join(others)} — in ~\d+ s", where)

    page.click("#rename")
    page.fill("#askName", waiting)
    page.click("#ask button")
    page.wait_for_function("window.spacetaxi.where && window.spacetaxi.where.startsWith('WAITLIST')",
                           timeout=10000)
    assert page.locator("#where").inner_text() == "WAITLIST #2: plays in wave 2 at the latest"

    page.click("#rename")
    page.fill("#askName", "nobody")
    page.click("#ask button")
    page.wait_for_function("window.spacetaxi.where === 'not connected'", timeout=10000)
    page.close()
    assert errors == []
