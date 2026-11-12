"""End-to-end tests of the web viewer in headless Chromium (Playwright).

Install the browser once: `uv run playwright install chromium`. Without it these tests skip.
Set `SPACETAXI_SCREENSHOTS=1` to write the screenshots (tick 0, mid-round, end of round) to
`tests/e2e/screenshots/` instead of a temp dir.
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


@pytest.fixture
def viewer(browser, page_file):
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(page_file.as_uri())
    page.wait_for_function("window.spacetaxi && window.spacetaxi.fps > 0")
    yield page
    page.close()
    assert errors == []


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


def test_paused_at_tick_one_on_load(viewer, shots):
    s = state(viewer)
    assert s["round"] == 1
    first = s["tick"]
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


def test_fps_at_4x_with_4_taxis(viewer):
    viewer.keyboard.press("4")
    viewer.keyboard.press("Space")
    viewer.wait_for_timeout(1500)
    assert state(viewer)["fps"] >= 50


def test_scrub_and_round_selector(viewer, shots):
    scrub_to(viewer, 186)
    viewer.wait_for_timeout(200)
    assert state(viewer)["tick"] == 187
    viewer.screenshot(path=str(shots / "mid.png"))
    viewer.select_option("#round", "1")
    viewer.wait_for_timeout(200)
    s = state(viewer)
    assert s["round"] == 2 and s["tick"] == 1


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
