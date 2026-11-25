"""End-to-end tests of the live viewer (`spacetaxi serve`'s `/watch`) in headless Chromium
(Playwright), against an arena server of scripted bots in its own thread and event loop.
Install the browser once: `uv run playwright install chromium`. Without it these tests skip."""

import asyncio
import json
import re
import threading
import time
import urllib.request

import pytest

from spacetaxi import ws
from spacetaxi.maps import load_map
from spacetaxi.server import ArenaServer
from spacetaxi.sim import Rules

pytest.importorskip("playwright.sync_api")

RULES = Rules(tick_ms=100, deadline_ms=80, round_ticks=40)


async def scripted_bot(port: int, name: str, sock=None) -> None:
    """Joins and answers every tick: up on even ticks, with a debug text and path."""
    sock = sock or await ws.connect(f"ws://127.0.0.1:{port}/bot")
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


def open_watch(browser, base, bot=None, init=None):
    """/watch (`?bot=` when `bot` is given), with `init` run before the page's scripts."""
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    if init:
        page.add_init_script(init)
    page.goto(base + "/watch" + (f"?bot={bot}" if bot else ""))
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


def test_watch_picker_empties_an_arena_the_next_wave_does_not_fill(browser, serve_arena):
    server = ArenaServer({"cavern": load_map("cavern")}, RULES, 1.0, seed=1, max_arenas=3)
    base, server, loop = serve_arena(server, lambda loop, port: [
        loop.create_task(scripted_bot(port, f"bot{i}")) for i in range(8)])
    page, errors = open_watch(browser, base)
    page.wait_for_function("window.spacetaxi.running.length === 2", timeout=15000)
    page.select_option("#round", "2")

    # Half the bots leave between the waves: the next one fills only arena 1.
    page.wait_for_function("window.spacetaxi.idle", timeout=15000)
    two = picker(page)
    for i in range(4, 8):
        loop.call_soon_threadsafe(server.kick, f"bot{i}")
    page.wait_for_function("window.spacetaxi.running.length === 1", timeout=15000)
    arenas = live_arenas(base)
    assert sorted(arenas) == [1] and len(arenas[1]["players"]) == 4
    assert picker(page) == expected_picker(arenas) != two
    assert page.locator("#round").input_value() == "2"   # the picked arena stays picked
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


# --- /watch?bot=NAME follows one bot

def arena_of(base, name):
    return next((k for k, a in live_arenas(base).items() if name in a["players"]), None)


def selected(page):
    return page.evaluate("document.querySelector('#round').selectedOptions[0].textContent")


def test_watch_bot_follows_its_arena_across_waves_silently(browser, two_arenas, shots):
    base, server = two_arenas
    page, errors = open_watch(browser, base, "bot3")
    # no click: the round plays, the sound is off and not set up
    page.wait_for_function("window.spacetaxi.running.length === 2 && window.spacetaxi.tick > 2",
                           timeout=15000)
    s = state(page)
    assert s["started"] and not page.locator("#splash").is_visible()
    assert s["followed"] == "bot3" and not s["follow"] and s["followNote"] is None
    assert s["arena"] == arena_of(base, "bot3")
    assert "bot3" in s["marked"]
    assert s["muted"] and not s["audio"] and page.locator("#sound").inner_text() == "SOUND OFF"
    assert selected(page) == "FOLLOW BOT3" and "FOLLOW LATEST" in picker(page)
    page.wait_for_timeout(300)
    page.screenshot(path=str(shots / "follow-bot.png"))

    # the next wave deals new groups: the view goes where bot3 flies now
    first = live_arenas(base)[s["arena"]]["round"]
    page.wait_for_function("window.spacetaxi.idle", timeout=15000)
    page.wait_for_function(f"window.spacetaxi.running.length === 2 && window.spacetaxi.round > {first}"
                           " && window.spacetaxi.tick > 1", timeout=15000)
    s = state(page)
    assert s["arena"] == arena_of(base, "bot3") and "bot3" in s["marked"]

    # the sound button turns the sound on, creating the audio then
    page.click("#sound")
    s = state(page)
    assert s["audio"] and not s["muted"] and page.locator("#sound").inner_text() == "SOUND ON"

    # picking an arena stops following and keeps that arena
    page.select_option("#round", "1")
    page.wait_for_function("window.spacetaxi.arena === 1 && window.spacetaxi.followed === null")
    page.wait_for_function("window.spacetaxi.idle", timeout=15000)
    page.wait_for_function("window.spacetaxi.running.length === 2 && window.spacetaxi.tick > 1",
                           timeout=15000)
    assert state(page)["arena"] == 1 and state(page)["followed"] is None
    assert "bot3" not in state(page)["marked"]
    page.close()
    assert errors == []


def test_watch_bot_ignores_case(browser, two_arenas):
    base, server = two_arenas
    page, errors = open_watch(browser, base, "BOT3")
    page.wait_for_function("window.spacetaxi.running.length === 2 && window.spacetaxi.tick > 2",
                           timeout=15000)
    s = state(page)
    assert s["arena"] == arena_of(base, "bot3") and "bot3" in s["marked"]
    assert selected(page) == "FOLLOW BOT3"
    page.close()
    assert errors == []


@pytest.fixture
def thirteen(serve_arena):
    """13 scripted bots on a server with 3 arenas (12 seats): one sits out every wave."""
    return run_arena(serve_arena, bots=13, fill_seconds=0.5)


def test_watch_bot_sits_out_a_wave_on_its_last_arena(browser, thirteen, shots):
    base, server = thirteen
    deadline = time.monotonic() + 15
    while len(live_arenas(base)) < 3:
        assert time.monotonic() < deadline, "the first wave did not start"
        time.sleep(0.05)
    # the plan of the next wave, made at this wave's start, names who sits it out
    sitter = next_wave(base)["waitlist"][0]
    last = arena_of(base, sitter)
    assert last is not None
    page, errors = open_watch(browser, base, sitter)
    page.wait_for_function(f"window.spacetaxi.arena === {last}", timeout=10000)
    assert state(page)["followNote"] is None
    page.wait_for_function("window.spacetaxi.idle", timeout=15000)
    page.wait_for_function("window.spacetaxi.running.length === 3", timeout=15000)
    page.wait_for_function("window.spacetaxi.followNote !== null", timeout=5000)
    s = state(page)
    assert arena_of(base, sitter) is None
    assert s["followNote"] == f"{sitter.upper()} SITS OUT THIS WAVE"
    assert s["arena"] == last
    note = page.locator("#followNote")
    assert note.is_visible() and note.inner_text() == f"{sitter.upper()} SITS OUT THIS WAVE"
    page.screenshot(path=str(shots / "follow-sits-out.png"))
    page.close()
    assert errors == []


def test_watch_bot_splash_names_its_place_on_the_waitlist(browser, serve_arena):
    # 13 bots on 12 seats; the splash returns 5 s after a wave, before the next one starts
    base, server = run_arena(serve_arena, bots=13, fill_seconds=8)
    deadline = time.monotonic() + 20
    while len(live_arenas(base)) < 3:
        assert time.monotonic() < deadline, "the first wave did not start"
        time.sleep(0.05)
    waiter = next_wave(base)["waitlist"][0]
    page, errors = open_watch(browser, base, waiter.upper())
    page.wait_for_function("window.spacetaxi.running.length === 3", timeout=5000)
    page.wait_for_function("window.spacetaxi.idle", timeout=15000)
    page.locator("#splash").wait_for(state="visible", timeout=10000)
    plan = next_wave(base)
    assert plan["waitlist"] == [waiter]
    line = f"{waiter.upper()}: WAITLIST #1, PLAYS IN WAVE {plan['wave'] + 1} AT THE LATEST"
    page.wait_for_function(f"document.querySelector('#seat').textContent === '{line}'", timeout=2000)
    assert page.locator("#seat").is_visible()
    assert page.locator("#plan span.me").count() == 0   # no arena column has it
    assert state(page)["followNote"] is None
    page.close()
    assert errors == []


def test_watch_bot_splash_shows_its_seat_in_the_next_wave(browser, lonely, shots):
    base, server = lonely
    page, errors = open_watch(browser, base, "bot0")
    plan = wait_for_plan(page, base, 1)
    page.wait_for_function("document.querySelector('#seat').textContent !== ''", timeout=10000)
    i = next(k for k, names in enumerate(plan["arenas"]) if "bot0" in names)
    assert page.locator("#splash").is_visible()
    assert "INSERT COIN" not in page.locator("#splash").inner_text()
    assert page.locator("#seat").inner_text() == f"BOT0: ARENA {i + 1} IN WAVE {plan['wave']}"
    me = page.locator("#plan .col").nth(i).locator("span.me")
    assert me.count() == 1 and me.inner_text().endswith("BOT0")
    assert state(page)["followNote"] is None
    page.wait_for_timeout(300)
    page.screenshot(path=str(shots / "follow-seat.png"))
    page.close()
    assert errors == []


def test_watch_bot_splash_after_a_wave_names_its_next_seat(browser, serve_arena):
    # the splash comes back 5 s after the last round ends, so the next wave must be further away
    base, server = run_arena(serve_arena, bots=8, fill_seconds=8)
    page, errors = open_watch(browser, base, "bot3")
    page.wait_for_function("window.spacetaxi.running.length === 2", timeout=25000)
    page.wait_for_function("window.spacetaxi.idle", timeout=15000)
    page.locator("#splash").wait_for(state="visible", timeout=10000)
    plan = next_wave(base)
    i = next(k for k, names in enumerate(plan["arenas"]) if "bot3" in names)
    line = f"BOT3: ARENA {i + 1} IN WAVE {plan['wave']}"
    page.wait_for_function(f"document.querySelector('#seat').textContent === '{line}'", timeout=5000)
    assert page.locator("#plan .col").nth(i).locator("span.me").inner_text().endswith("BOT3")
    assert page.locator("#plan span.me").count() == 1
    assert "INSERT COIN" not in page.locator("#splash").inner_text()
    assert state(page)["followNote"] is None
    # and the next wave is shown from the arena the splash named
    page.wait_for_function("window.spacetaxi.running.length === 2 && window.spacetaxi.tick > 1",
                           timeout=15000)
    assert state(page)["arena"] == i + 1 == arena_of(base, "bot3")
    page.close()
    assert errors == []


def test_watch_bot_not_connected_plays_like_plain_watch(browser, two_arenas):
    base, server = two_arenas
    page, errors = open_watch(browser, base, "nobody")
    page.wait_for_function("window.spacetaxi.followNote === 'NOBODY IS NOT CONNECTED'", timeout=10000)
    page.wait_for_function("window.spacetaxi.running.length === 2 && window.spacetaxi.tick > 2",
                           timeout=15000)
    s = state(page)
    assert s["followed"] == "nobody" and s["started"]
    assert s["round"] == max(a["round"] for a in live_arenas(base).values())
    assert page.locator("#followNote").inner_text() == "NOBODY IS NOT CONNECTED"
    assert selected(page) == "FOLLOW NOBODY"
    page.close()
    assert errors == []


def test_plain_watch_offers_the_stored_name_without_following(browser, two_arenas):
    base, server = two_arenas
    page, errors = open_watch(browser, base, None,
                               init="localStorage.setItem('spacetaxi.name', 'bot5')")
    page.wait_for_function("document.querySelectorAll('#round option').length === 5")
    s = state(page)
    assert not s["started"] and s["follow"] and s["followed"] is None and not s["muted"]
    assert page.locator("#splash").is_visible()
    assert "INSERT COIN" in page.locator("#splash").inner_text()
    assert picker(page)[:2] == ["FOLLOW LATEST", "FOLLOW BOT5"] and selected(page) == "FOLLOW LATEST"
    page.click("#splash")
    page.wait_for_function("window.spacetaxi.running.length === 2 && window.spacetaxi.tick > 2",
                           timeout=15000)
    page.select_option("#round", "bot")
    page.wait_for_function("window.spacetaxi.followed === 'bot5'")
    s = state(page)
    assert s["arena"] == arena_of(base, "bot5") and not s["follow"]
    page.wait_for_function("window.spacetaxi.marked.includes('bot5')", timeout=5000)
    assert not state(page)["muted"] and state(page)["audio"]   # the sound stays as it was
    page.close()
    assert errors == []


def test_watch_bot_m_key_creates_the_audio_and_the_picker_returns_to_following(browser, two_arenas):
    base, server = two_arenas
    page, errors = open_watch(browser, base, "bot3")
    page.wait_for_function("window.spacetaxi.running.length === 2 && window.spacetaxi.tick > 2",
                           timeout=15000)
    page.keyboard.press("m")
    s = state(page)
    assert s["audio"] and not s["muted"] and page.locator("#sound").inner_text() == "SOUND ON"

    page.select_option("#round", "follow")
    page.wait_for_function("window.spacetaxi.follow && window.spacetaxi.followed === null")
    assert selected(page) == "FOLLOW LATEST" and page.locator("#seat").text_content() == ""
    page.select_option("#round", "bot")
    page.wait_for_function("window.spacetaxi.followed === 'bot3' && !window.spacetaxi.follow")
    page.wait_for_function("window.spacetaxi.marked.includes('bot3')", timeout=10000)
    assert state(page)["arena"] == arena_of(base, "bot3") and not state(page)["muted"]
    page.close()
    assert errors == []


def test_watch_bot_follows_a_late_joiner_until_it_leaves(browser, serve_arena):
    server = ArenaServer({"cavern": load_map("cavern")}, RULES, 0.5, seed=1, max_arenas=3)
    ports = []

    def start(loop, port):
        ports.append(port)
        return [loop.create_task(scripted_bot(port, f"bot{i}")) for i in range(4)]

    base, server, loop = serve_arena(server, start)
    page, errors = open_watch(browser, base, "late")
    page.wait_for_function("window.spacetaxi.followNote === 'LATE IS NOT CONNECTED'", timeout=10000)

    def on_server(coro):
        return asyncio.run_coroutine_threadsafe(coro, loop)

    sock = on_server(ws.connect(f"ws://127.0.0.1:{ports[0]}/bot")).result(10)
    on_server(scripted_bot(ports[0], "Late", sock))
    # following starts with its next round
    page.wait_for_function("window.spacetaxi.marked.includes('Late')", timeout=20000)
    s = state(page)
    assert s["followNote"] is None and s["arena"] == arena_of(base, "Late")
    on_server(sock.abort(1000)).result(10)
    page.wait_for_function("window.spacetaxi.followNote === 'LATE IS NOT CONNECTED'", timeout=20000)
    page.close()
    assert errors == []


def test_watch_bot_banner_never_flashes_while_its_bot_flies(browser, two_arenas):
    base, server = two_arenas
    watch = """document.addEventListener('DOMContentLoaded', () => {
        const el = document.querySelector('#followNote');
        window.__notes = [];
        new MutationObserver(() => { if (!el.hidden) window.__notes.push(el.textContent); })
          .observe(el, {attributes: true, childList: true});
    })"""
    page, errors = open_watch(browser, base, "bot3", init=watch)
    # arenas start and end one message at a time, over several waves
    page.wait_for_function("window.spacetaxi.round >= 5 && window.spacetaxi.tick > 1",
                           timeout=40000)
    assert page.evaluate("window.__notes") == []
    page.close()
    assert errors == []


def test_watch_bot_picker_moves_the_plan_highlight_and_keeps_the_countdown(browser, lonely):
    base, server = lonely
    page, errors = open_watch(browser, base, "bot0")
    wait_for_plan(page, base, 1)
    page.wait_for_function("document.querySelector('#plan span.me') !== null", timeout=10000)
    left = ("Number(document.querySelector('#plan').firstChild.textContent"
            ".match(/IN (\\d+) S/)[1])")
    before = page.evaluate(left)
    page.wait_for_timeout(1200)
    page.select_option("#round", "1")
    assert page.locator("#plan span.me").count() == 0
    assert page.locator("#seat").text_content() == ""
    page.select_option("#round", "bot")
    assert page.locator("#plan span.me").count() == 1
    page.wait_for_timeout(300)
    assert page.evaluate(left) < before
    page.close()
    assert errors == []


def test_watch_bot_ignores_a_value_no_bot_could_be_named(browser, lonely):
    base, server = lonely
    page, errors = open_watch(browser, base, "x" * 300)
    page.wait_for_function("window.spacetaxi.connected", timeout=10000)
    s = state(page)
    assert s["followed"] is None and s["followNote"] is None and not s["started"]
    assert picker(page)[0] == "FOLLOW LATEST" and not any(o.startswith("FOLLOW X") for o in picker(page))
    page.close()
    assert errors == []
