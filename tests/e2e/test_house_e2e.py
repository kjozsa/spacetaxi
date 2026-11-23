"""End-to-end test of house bots (`spacetaxi serve --house NAME`) in headless Chromium: a house
bot and a participant play a round; the house bot never scores a milestone and has no standings
row, but is marked as house on the leaderboard. Without the browser this test skips."""

import json
import urllib.request

import pytest

from spacetaxi.maps import load_map
from spacetaxi.server import ArenaServer
from spacetaxi.sim import Rules
from test_fly_e2e import scripted_bot

pytest.importorskip("playwright.sync_api")

RULES = Rules(tick_ms=100, deadline_ms=80, round_ticks=30)


def get_json(base, path):
    with urllib.request.urlopen(base + path, timeout=5) as res:
        return json.load(res)


def test_house_bot_is_marked_and_never_scores(browser, serve_arena):
    server = ArenaServer({"cavern": load_map("cavern")}, RULES, 1.0, seed=1, max_arenas=3,
                         house=["ace"])

    def start(loop, port):
        return [loop.create_task(scripted_bot(port, "ace")),
                loop.create_task(scripted_bot(port, "guest"))]

    base, server, _ = serve_arena(server, start)

    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/")
    page.wait_for_function("document.querySelectorAll('#rows tr').length >= 2", timeout=15000)
    assert page.locator("#rows tr .house").count() == 1
    assert page.locator("#rows tr .human").count() == 0
    row = page.locator("#rows tr", has=page.locator(".house"))
    assert "ace" in row.inner_text()
    page.close()

    rows = {r["name"]: r for r in get_json(base, "/leaderboard.json")["leaderboard"]}
    assert rows["ace"]["house"] is True and rows["guest"]["house"] is False

    data = get_json(base, "/standings.json")
    names = [r["name"] for r in data["standings"]]
    assert "ace" not in names and "guest" in names
    assert "ace" not in server.standings.awards

    watch = browser.new_page(viewport={"width": 1920, "height": 1080})
    watch.goto(base + "/watch")
    watch.wait_for_function("[...document.querySelectorAll('#lb li')].some("
                            "li => li.textContent.includes('ACE') && li.textContent.includes('HOUSE'))",
                            timeout=10000)
    watch.close()
    assert errors == []
