"""End-to-end tests of the flight lab (`spacetaxi play`) in headless Chromium (Playwright).

The parity tests fly the same 200 moves through the page's `window.spacetaxi.simulate` and
through `World` (via `spacetaxi.play.simulate`, which pins the spawn) and compare every tick.
Install the browser once: `uv run playwright install chromium`. Without it these tests skip.
"""

import random
import re

import pytest

from spacetaxi.cli import main
from spacetaxi.maps import load_map
from spacetaxi.play import simulate
from spacetaxi.sim import CRASHED, FLYING, LANDED, Rules

pytest.importorskip("playwright.sync_api")


def moves(seed: int) -> list[dict]:
    """200 moves in runs of 1-6 ticks, `ay = 1` (does nothing) included."""
    rng = random.Random(seed)
    out = []
    while len(out) < 200:
        move = {"ax": rng.choice((-1, 0, 0, 1)), "ay": rng.choice((-1, -1, 0, 0, 0, 1))}
        out += [move] * rng.randint(1, 6)
    return out[:200]


# Each seed was picked so its flight lands, takes off again, crashes and respawns.
CASES = {"defaults": ([], 23), "gravity=3": (["--rule", "gravity=3"], 220)}


def page_file(tmp_path, rule_args):
    out = tmp_path / "p.html"
    assert main(["play", "--map", "cavern", *rule_args, "--out", str(out)]) == 0
    return out


def open_page(browser, path, **kwargs):
    page = browser.new_page(**kwargs)
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(path.as_uri())
    page.wait_for_function("window.spacetaxi && window.spacetaxi.tick > 1")
    return page, errors


@pytest.mark.parametrize("case", CASES)
def test_simulate_matches_world(browser, tmp_path, case):
    rule_args, seed = CASES[case]
    rules = Rules(gravity=3) if rule_args else Rules()
    path = page_file(tmp_path, rule_args)
    assert not re.search(r"https?://", path.read_text(encoding="utf-8"))
    page, errors = open_page(browser, path)
    assert page.evaluate("window.spacetaxi.rules().gravity") == rules.gravity
    flight = moves(seed)
    expected = simulate(load_map("cavern"), rules, flight)
    got = page.evaluate("m => window.spacetaxi.simulate(m)", flight)
    assert len(got) == 200
    for tick, (g, e) in enumerate(zip(got, expected), start=1):
        assert g == e, f"tick {tick}: page {g} != World {e}"
    states = [s["state"] for s in expected]
    assert LANDED in states and CRASHED in states
    assert FLYING in states[states.index(LANDED):] and FLYING in states[states.index(CRASHED):]
    page.close()
    assert errors == []


def test_gravity_rule_changes_the_page(browser, tmp_path):
    flight = moves(23)
    runs = []
    for name, (rule_args, _) in CASES.items():
        (tmp_path / name).mkdir()
        page, _ = open_page(browser, page_file(tmp_path / name, rule_args))
        runs.append(page.evaluate("m => window.spacetaxi.simulate(m)", flight))
        page.close()
    assert runs[0] != runs[1]
    assert runs[1] == simulate(load_map("cavern"), Rules(gravity=3), flight)


def test_arrow_keys_fly_the_taxi(browser, tmp_path):
    page, errors = open_page(browser, page_file(tmp_path, []))
    page.keyboard.down("ArrowUp")
    page.keyboard.down("ArrowRight")
    page.wait_for_timeout(600)
    t = page.evaluate("window.spacetaxi.taxi()")
    page.keyboard.up("ArrowUp")
    page.keyboard.up("ArrowRight")
    assert t["state"] == FLYING and t["vy"] < 0 and t["vx"] > 0
    page.close()
    assert errors == []


def test_touch_buttons_fly_the_taxi(browser, tmp_path):
    page, errors = open_page(browser, page_file(tmp_path, []), viewport={"width": 390, "height": 844},
                             has_touch=True, is_mobile=True)
    up = page.locator('.pad-controls button[data-key="ArrowUp"]')
    up.scroll_into_view_if_needed()
    assert up.is_visible()
    box = up.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.wait_for_timeout(600)
    t = page.evaluate("window.spacetaxi.taxi()")
    page.mouse.up()
    assert t["vy"] < 0
    page.close()
    assert errors == []
