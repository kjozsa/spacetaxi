"""End-to-end tests of the web viewer in headless Chromium (Playwright).

Install the browser once: `uv run playwright install chromium`. Without it these tests skip.
Set `SPACETAXI_SCREENSHOTS=1` to write the screenshots (tick 0, mid-round, end of round, an
explosion, a `+$` popup, the podium) to `tests/e2e/screenshots/` instead of a temp dir.
"""

import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from spacetaxi.cli import main
from spacetaxi.show import load_replay
from spacetaxi.viewer import build_html

sync_api = pytest.importorskip("playwright.sync_api")

SCREENSHOTS = Path(__file__).parent / "screenshots"


@pytest.fixture(scope="module")
def replay(tmp_path_factory):
    out = tmp_path_factory.mktemp("replay") / "r.jsonl"
    assert main(["run", "--map", "canyon", "--bots", "lander,greedy,lander,random",
                 "--rounds", "2", "--seed", "1", "--replay", str(out)]) == 0
    return out


@pytest.fixture(scope="module")
def page_file(replay, tmp_path_factory):
    out = tmp_path_factory.mktemp("view") / "v.html"
    assert main(["view", str(replay), "--out", str(out)]) == 0
    return out


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


def open_page(browser, page_file, **kwargs):
    """The viewer, loaded and drawing, with the INSERT COIN splash still up."""
    page = browser.new_page(viewport={"width": 1920, "height": 1080}, **kwargs)
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(page_file.as_uri())
    page.wait_for_function("window.spacetaxi && window.spacetaxi.fps > 0")
    return page, errors


def insert_coin(page):
    """Click the splash (the round starts playing), then pause and go back to the first tick."""
    page.click("#splash")
    page.wait_for_function("window.spacetaxi.started")
    page.keyboard.press("Space")
    scrub_to(page, 0)
    page.wait_for_timeout(100)


@pytest.fixture
def splash_page(browser, page_file):
    page, errors = open_page(browser, page_file)
    yield page
    page.close()
    assert errors == []


@pytest.fixture
def viewer(splash_page):
    insert_coin(splash_page)
    return splash_page


@pytest.fixture(scope="module")
def shots(tmp_path_factory):
    if os.environ.get("SPACETAXI_SCREENSHOTS"):
        SCREENSHOTS.mkdir(exist_ok=True)
        return SCREENSHOTS
    return tmp_path_factory.mktemp("shots")


def state(page):
    return page.evaluate("({...window.spacetaxi})")


def scrub_to(page, index):
    page.evaluate("""i => { const s = document.getElementById('scrub');
                           s.value = String(i); s.dispatchEvent(new Event('input')); }""", index)


def ticks_per_second(page, ms=2000):
    return page.evaluate("""ms => new Promise(done => {
        const t0 = window.spacetaxi.tick, s0 = performance.now();
        setTimeout(() => done((window.spacetaxi.tick - t0) * 1000 / (performance.now() - s0)), ms);
    })""", ms)


def test_out_file_has_no_external_references(page_file):
    assert not re.search(r"https?://", page_file.read_text(encoding="utf-8"))


def test_waits_for_the_click(splash_page, shots):
    s = state(splash_page)
    assert s["round"] == 1 and s["tick"] == 1 and not s["started"]
    splash_page.keyboard.press("Space")
    splash_page.keyboard.press("4")
    splash_page.wait_for_timeout(600)
    s = state(splash_page)
    assert s["tick"] == 1 and not s["started"]
    assert splash_page.locator("#splash").is_visible()
    assert "INSERT COIN" in splash_page.locator("#splash").inner_text()
    assert all(v == 0 for v in s["sfx"].values()) and all(v == 0 for v in s["fx"].values())
    splash_page.screenshot(path=str(shots / "splash.png"))


def test_click_starts_the_round_with_a_jingle(splash_page):
    splash_page.click("#splash")
    assert not splash_page.locator("#splash").is_visible()
    splash_page.wait_for_function("window.spacetaxi.tick > 2")
    s = state(splash_page)
    assert s["started"] and s["sfx"]["jingle"] == 1
    assert splash_page.evaluate("document.getElementById('play').textContent") == "PAUSE"


def test_paused_at_tick_one_after_the_coin(viewer, shots):
    s = state(viewer)
    assert s["round"] == 1
    first = s["tick"]
    assert first == 1
    viewer.wait_for_timeout(500)
    assert state(viewer)["tick"] == first
    viewer.screenshot(path=str(shots / "tick0.png"))


def test_space_plays_until_the_end_of_the_round(viewer, shots):
    viewer.keyboard.press("4")
    viewer.keyboard.press("Space")
    seen = [state(viewer)["tick"]]
    for _ in range(5):
        viewer.wait_for_timeout(200)
        seen.append(state(viewer)["tick"])
    assert seen == sorted(seen) and seen[-1] > seen[0]
    scrub_to(viewer, 340)  # skip ahead so the test does not watch the whole round
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        tick = state(viewer)["tick"]
        assert tick >= seen[-1]
        seen.append(tick)
        if tick == 375:
            break
        viewer.wait_for_timeout(100)
    assert seen[-1] == 375
    viewer.wait_for_timeout(300)
    assert state(viewer)["tick"] == 375  # stops at the end of the round
    viewer.screenshot(path=str(shots / "end.png"))


def test_speed_keys(viewer):
    viewer.keyboard.press("Space")
    viewer.keyboard.press("2")
    slow = ticks_per_second(viewer)
    viewer.keyboard.press("4")
    fast = ticks_per_second(viewer)
    assert 4.0 <= slow <= 6.0  # 1x: one tick per tick_ms (200 ms)
    assert 3.2 <= fast / slow <= 4.8


def test_scrub_and_round_selector(viewer, shots):
    scrub_to(viewer, 186)
    viewer.wait_for_timeout(200)
    assert state(viewer)["tick"] == 187
    viewer.screenshot(path=str(shots / "mid.png"))
    viewer.select_option("#round", "1")
    viewer.wait_for_timeout(200)
    s = state(viewer)
    assert s["round"] == 2 and s["tick"] == 1


FX_EVENTS = {"explosions": "crash", "dust": "landed", "pickups": "pickup", "cash": "deliver"}


def round_one(replay):
    rnd = load_replay(str(replay))[1]
    return [rnd["ticks"][n] for n in sorted(rnd["ticks"])]


def wait_for_round_end(page, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if page.evaluate("window.spacetaxi.tick === 375 && "
                         "document.getElementById('play').textContent === 'PLAY'"):
            return
        page.wait_for_timeout(200)
    raise AssertionError(f"round did not end, at tick {state(page)['tick']}")


def test_effect_counters_follow_the_replay_events(viewer, replay, shots):
    ticks = round_one(replay)
    expected = {fx: sum(e["type"] == kind for t in ticks for e in t["events"])
                for fx, kind in FX_EVENTS.items()}
    thrust = any(m["ax"] or m["ay"] for t in ticks for m in t["moves"].values())
    assert all(expected.values()) and thrust  # the canyon replay has every kind of effect
    before = state(viewer)["fx"]
    viewer.wait_for_timeout(200)
    assert state(viewer)["fx"]["flames"] == before["flames"]  # paused: no flames
    viewer.keyboard.press("4")
    viewer.keyboard.press("Space")
    wait_for_round_end(viewer)
    s = state(viewer)
    after = s["fx"]
    assert {fx: after[fx] - before[fx] for fx in FX_EVENTS} == expected
    assert after["flames"] > 0
    assert after["shakes"] == expected["explosions"]
    # the podium: the top 3 by money at the end of the round
    last = {t["id"]: t["money"] for t in ticks[-1]["taxis"]}
    players = load_replay(str(replay))[1]["start"]["players"]
    top3 = sorted(players, key=lambda p: (-last[p], players.index(p)))[:3]
    assert s["podium"] == top3
    viewer.screenshot(path=str(shots / "podium.png"))


def first_tick_with(page, kind):
    return page.evaluate("""kind => JSON.parse(document.getElementById('replay').textContent)[0]
        .ticks.findIndex(t => t.e.some(e => e.type === kind))""", kind)


def play_into(page, kind, counter):
    """Scrub to just before the first `kind` event, play at 1x until it fires."""
    index = first_tick_with(page, kind)
    assert index > 0
    scrub_to(page, index - 1)
    before = state(page)["fx"][counter]
    page.keyboard.press("2")
    page.keyboard.press("Space")
    page.wait_for_function(f"window.spacetaxi.fx.{counter} > {before}", timeout=5000)


def test_explosion_and_cash_popup(viewer, shots):
    play_into(viewer, "crash", "explosions")
    viewer.wait_for_timeout(60)
    viewer.screenshot(path=str(shots / "explosion.png"))
    viewer.keyboard.press("Space")
    assert state(viewer)["fx"]["shakes"] >= 1
    play_into(viewer, "deliver", "cash")
    viewer.wait_for_timeout(250)
    viewer.screenshot(path=str(shots / "cash.png"))


def test_reduced_motion_turns_screen_shake_off(browser, page_file):
    page, _ = open_page(browser, page_file, reduced_motion="reduce")
    try:
        insert_coin(page)
        play_into(page, "crash", "explosions")
        assert state(page)["fx"]["shakes"] == 0
    finally:
        page.close()


SFX_EVENTS = {"boom": "crash", "chime": "pickup", "cash": "deliver"}


def test_sound_counters_follow_the_replay_events(splash_page, replay):
    """From the click to the end of round 1: one sound per crash, pickup and delivery, the hum
    while taxis thrust, a jingle at the start and the end, and no audio errors."""
    ticks = round_one(replay)
    expected = {s: sum(e["type"] == kind for t in ticks for e in t["events"])
                for s, kind in SFX_EVENTS.items()}
    assert all(expected.values())
    splash_page.click("#splash")
    assert splash_page.evaluate("typeof AudioContext") == "function"
    splash_page.keyboard.press("4")
    wait_for_round_end(splash_page)
    sfx = state(splash_page)["sfx"]
    assert {s: sfx[s] for s in SFX_EVENTS} == expected
    assert sfx["hum"] > 0 and sfx["jingle"] == 2
    assert state(splash_page)["thrusting"] == 0  # stopped: the hum is off


def test_m_toggles_mute(viewer):
    assert state(viewer)["muted"] is False
    viewer.keyboard.press("m")
    assert state(viewer)["muted"] is True
    play_into(viewer, "crash", "explosions")  # muted sounds still count
    assert state(viewer)["sfx"]["boom"] >= 1
    viewer.keyboard.press("M")
    assert state(viewer)["muted"] is False


def test_c_toggles_the_scanlines(viewer):
    crt = viewer.locator("#crt")
    assert state(viewer)["scanlines"] is True and crt.is_visible()
    viewer.keyboard.press("c")
    assert state(viewer)["scanlines"] is False and not crt.is_visible()
    viewer.keyboard.press("C")
    assert state(viewer)["scanlines"] is True and crt.is_visible()


def test_fps_at_4x_with_4_taxis_and_all_effects(viewer):
    viewer.keyboard.press("4")
    viewer.keyboard.press("Space")
    viewer.wait_for_timeout(3000)
    s = state(viewer)
    assert s["scanlines"] and s["fx"]["flames"] > 0
    assert s["fps"] >= 50


@pytest.fixture
def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_view_serves_the_same_page(replay, free_port):
    proc = subprocess.Popen(
        [sys.executable, "-m", "spacetaxi", "view", str(replay), "--port", str(free_port)],
        stdout=subprocess.PIPE, text=True)
    try:
        line = proc.stdout.readline()
        assert f"http://localhost:{free_port}/" in line
        with urllib.request.urlopen(f"http://localhost:{free_port}/", timeout=10) as resp:
            assert resp.read().decode("utf-8") == build_html(str(replay))
    finally:
        proc.terminate()
        proc.wait(timeout=10)
