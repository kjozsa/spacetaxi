"""End-to-end tests of the mission page (`spacetaxi serve`'s `/mission`) in headless Chromium
(Playwright), against an arena server in its own thread whose standings the test awards
directly. Install the browser once: `uv run playwright install chromium`. Without it these
tests skip."""

import asyncio
import json
import os
import threading
from pathlib import Path

import pytest

from spacetaxi.maps import load_map
from spacetaxi.milestones import MILESTONES
from spacetaxi.server import ArenaServer
from spacetaxi.sim import Rules

sync_api = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # the browser is not installed
            pytest.skip(f"headless Chromium unavailable ({exc.__class__.__name__}); "
                        "run `uv run playwright install chromium`")
        yield b
        b.close()


@pytest.fixture
def arena(tmp_path):
    """A server whose standings file has `Anna` with `hello` and `Full` with all nine; yields
    (base URL, award) where `award(milestone, name)` scores on the server's own loop."""
    awards = {"Anna": {"hello": {"place": 3, "points": 15}},
              "Full": {m: {"place": 1, "points": 25} for m in MILESTONES}}
    path = tmp_path / "standings.json"
    path.write_text(json.dumps({"awards": awards}), encoding="utf-8")
    server = ArenaServer(load_map("cavern"), Rules(), 600, seed=1, standings=path,
                         benchmarks={"greedy": 300, "pilot": 450})
    ready = threading.Event()
    box = {}

    def run() -> None:
        loop = asyncio.new_event_loop()
        box["loop"] = loop
        box["srv"] = loop.run_until_complete(server.start("127.0.0.1", 0))
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert ready.wait(10)
    port = box["srv"].sockets[0].getsockname()[1]

    def award(*pairs: tuple[str, str]) -> None:
        async def go() -> None:
            for milestone, name in pairs:
                server.standings.award(milestone, [(name, 0)])
        asyncio.run_coroutine_threadsafe(go(), box["loop"]).result(5)

    yield f"http://127.0.0.1:{port}", award

    async def shutdown() -> None:
        box["srv"].close()
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run_coroutine_threadsafe(shutdown(), box["loop"]).result(10)
    box["loop"].call_soon_threadsafe(box["loop"].stop)
    thread.join(5)


def shots_dir(tmp_path: Path) -> Path:
    return Path(__file__).parent / "screenshots" if os.environ.get("SPACETAXI_SCREENSHOTS") \
        else tmp_path


def st(page) -> dict:
    return page.evaluate("window.spacetaxi")


def test_mission_page_one_milestone_at_a_time(browser, arena, tmp_path):
    base, award = arena
    shots = shots_dir(tmp_path)
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/mission")

    # first visit: the name, validated like a hello name
    assert page.locator("#ask").is_visible()
    assert "The same name your bot connects with" in page.locator("#ask").inner_text()
    page.fill("#askName", "Anna Maria")
    page.click("#ask button")
    assert page.locator("#askErr").inner_text() != ""
    page.fill("#askName", "Anna")
    page.click("#ask button")
    page.wait_for_function("window.spacetaxi.mission === 'liftoff'", timeout=5000)

    assert page.locator("#count").inner_text() == "MISSION 2 OF 9"
    assert page.locator("#mid").inner_text() == "liftoff"
    assert not page.locator("#promptBox").is_visible()
    assert "stay up" in page.locator("#hint").inner_text()
    assert page.locator("#next").inner_text().startswith("+18 IF YOU ARE NEXT")
    assert page.locator("#done .chip").all_inner_texts() == ["hello P3 +15"]
    assert "15 PTS" in page.locator("#score").inner_text()
    assert "touchdown" not in page.locator("#view").inner_text()
    assert st(page)["celebrated"] == []
    page.screenshot(path=str(shots / "mission-390.png"), full_page=True)

    # two awards at once: celebrated one after the other, then the next mission slides in
    award(("liftoff", "Anna"), ("touchdown", "Anna"))
    page.wait_for_function("window.spacetaxi.celebrating === 'liftoff'", timeout=5000)
    assert page.locator("#party").is_visible()
    assert page.locator("#partyPl").inner_text() == "P2 · +18"
    page.wait_for_timeout(500)
    page.screenshot(path=str(shots / "mission-party-390.png"))
    page.wait_for_function("window.spacetaxi.celebrating === 'touchdown'", timeout=5000)
    page.wait_for_function("window.spacetaxi.mission === 'fare' && !window.spacetaxi.celebrating",
                           timeout=5000)
    assert st(page)["celebrated"] == ["liftoff P2 +18", "touchdown P2 +18"]
    page.wait_for_timeout(100)
    assert not page.locator("#party").is_visible()
    assert page.locator("#count").inner_text() == "MISSION 4 OF 9"
    assert not page.locator("#promptBox").is_visible()
    assert page.locator("#done .chip").all_inner_texts() == [
        "hello P3 +15", "liftoff P2 +18", "touchdown P2 +18"]

    # out of order: paid before fare is already ticked when the page gets to it
    award(("paid", "Anna"))
    page.wait_for_function("window.spacetaxi.celebrating === 'paid'", timeout=5000)
    page.wait_for_function("!window.spacetaxi.celebrating", timeout=5000)
    assert st(page)["mission"] == "fare"
    award(("fare", "Anna"))
    page.wait_for_function("window.spacetaxi.mission === 'clean' && !window.spacetaxi.celebrating",
                           timeout=8000)

    # a reload keeps the name and does not celebrate again
    page.reload()
    page.wait_for_function("window.spacetaxi.polls >= 1", timeout=5000)
    assert not page.locator("#ask").is_visible()
    assert st(page)["mission"] == "clean" and st(page)["celebrated"] == []
    page.wait_for_timeout(2500)
    assert st(page)["celebrated"] == []
    context.close()

    # the benchmark to beat, at a laptop's size
    award(("clean", "Anna"), ("hat_trick", "Anna"))
    wide = browser.new_context(viewport={"width": 1440, "height": 900})
    page = wide.new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/mission")
    page.fill("#askName", "Anna")
    page.click("#ask button")
    page.wait_for_function("window.spacetaxi.mission === 'beat_greedy'", timeout=5000)
    assert "$300" in page.locator("#bench").inner_text()
    page.screenshot(path=str(shots / "mission-1440.png"))

    # a name with all nine: the finish screen
    page.click("#rename")
    page.fill("#askName", "Full")
    page.click("#ask button")
    page.wait_for_function("window.spacetaxi.finished", timeout=5000)
    assert page.locator("#finish").is_visible() and not page.locator("#view").is_visible()
    assert "225 PTS" in page.locator("#finish").inner_text()
    assert "RANK 1" in page.locator("#finish").inner_text()
    assert page.locator("#finish a[href='/watch']").count() == 1
    assert st(page)["celebrated"] == []
    page.screenshot(path=str(shots / "mission-finish-1440.png"))
    wide.close()
    assert errors == []


def test_mission_page_first_prompt_has_the_name_and_the_address(browser, arena):
    base, _ = arena
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    page.goto(base + "/mission")
    page.fill("#askName", "Bob")
    page.click("#ask button")
    page.wait_for_function("window.spacetaxi.mission === 'hello' && window.spacetaxi.polls >= 1",
                           timeout=5000)
    assert page.locator("#count").inner_text() == "MISSION 1 OF 9"
    prompt = page.locator("#prompt").inner_text()
    host = base.removeprefix("http://")
    assert f"ws://{host}/bot as Bob " in prompt and f"http://{host}/brief.md" in prompt
    assert page.locator("#next").inner_text().startswith("+15 IF YOU ARE NEXT")
    assert "NO RANK YET" in page.locator("#score").inner_text()
    # the copy button puts the prompt with the name on the clipboard (or selects it)
    page.click("#copy")
    page.wait_for_function("['COPIED', 'SELECTED'].includes(document.getElementById('copy').textContent)",
                           timeout=2000)
    context.close()


def test_mission_page_name_form_stays_open_across_polls(browser, arena):
    base, award = arena
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    page.goto(base + "/mission")
    page.fill("#askName", "Anna")
    page.click("#ask button")
    page.wait_for_function("window.spacetaxi.mission === 'liftoff'", timeout=5000)
    page.click("#rename")
    polls = page.evaluate("window.spacetaxi.polls")
    award(("liftoff", "Anna"))
    page.wait_for_function(f"window.spacetaxi.polls > {polls}", timeout=5000)
    page.wait_for_timeout(200)
    assert page.locator("#ask").is_visible()
    assert not page.locator("#view").is_visible() and not page.locator("#finish").is_visible()
    assert not page.locator("#party").is_visible()
    assert page.evaluate("window.spacetaxi.celebrated") == []
    context.close()


def test_mission_page_survives_the_server_going_away(browser, arena):
    base, _ = arena
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    page.goto(base + "/mission")
    page.fill("#askName", "Anna")
    page.click("#ask button")
    page.wait_for_function("window.spacetaxi.mission === 'liftoff'", timeout=5000)
    page.route("**/standings.json", lambda route: route.abort())
    page.wait_for_function(
        "document.getElementById('status').textContent.includes('UNREACHABLE')", timeout=5000)
    assert page.locator("#mid").inner_text() == "liftoff"
    page.unroute("**/standings.json")
    page.wait_for_function("document.getElementById('status').textContent === ''", timeout=5000)
    assert page.locator("#whoName").inner_text() == "Anna"
    context.close()


def test_mission_page_rename_during_a_celebration_keeps_the_award_off_the_new_name(browser, arena):
    base, award = arena
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    page.goto(base + "/mission")
    page.fill("#askName", "Anna")
    page.click("#ask button")
    page.wait_for_function("window.spacetaxi.mission === 'liftoff'", timeout=5000)
    award(("liftoff", "Anna"))
    award(("hello", "Cleo"))
    page.wait_for_function("window.spacetaxi.celebrating === 'liftoff'", timeout=5000)
    # the overlay covers the page, so rename from script while the party is still up
    page.evaluate("""() => {
        document.getElementById('rename').click();
        document.getElementById('askName').value = 'Cleo';
        document.getElementById('ask').requestSubmit();
    }""")
    page.wait_for_function("window.spacetaxi.celebrating === 'liftoff' && "
                           "document.getElementById('whoName').textContent === 'Cleo' && "
                           "window.spacetaxi.mission === 'liftoff'", timeout=2000)
    page.wait_for_function("!window.spacetaxi.celebrating", timeout=5000)
    page.wait_for_timeout(300)
    assert page.evaluate("window.spacetaxi.celebrated") == ["liftoff P2 +18"]
    assert page.evaluate("window.spacetaxi.mission") == "liftoff"
    assert page.locator("#done .chip").all_inner_texts()[0].startswith("hello P")
    context.close()
