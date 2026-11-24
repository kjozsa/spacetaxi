"""End-to-end tests of the page bar (spacetaxi.nav) in headless Chromium (Playwright): moving
between the pages, the fading links of /watch, /fly and /slides laid out above the bar, and no
page scrolling sideways on a phone. Without the browser these tests skip."""

import pytest

from spacetaxi.maps import load_map
from spacetaxi.nav import NAV_PAGES
from spacetaxi.server import ArenaServer
from spacetaxi.sim import Rules

pytest.importorskip("playwright.sync_api")


@pytest.fixture
def base(serve_arena):
    server = ArenaServer({"cavern": load_map("cavern")}, Rules(), 600, seed=1,
                         benchmarks={"cavern": {"greedy": 300, "pilot": 450}})
    url, _, _ = serve_arena(server)
    return url


def box(page, selector: str) -> dict:
    return page.locator(selector).first.bounding_box()


def test_from_watch_to_mission_and_back(browser, base):
    page = browser.new_page(viewport={"width": 1280, "height": 720})
    page.goto(base + "/watch")
    current = page.locator('#bar .barnav a[aria-current="page"]')
    assert current.inner_text() == "WATCH"
    page.click('#bar .barnav a[href="/mission"]')
    page.wait_for_url(base + "/mission")
    assert page.locator('nav.sitenav a[aria-current="page"]').inner_text() == "MISSION"
    page.click('nav.sitenav a[href="/watch"]')
    page.wait_for_url(base + "/watch")
    assert page.locator("#bar .barnav").is_visible()
    page.close()


def test_watch_links_fade_out_when_idle_and_return_on_mouse_move(browser, base):
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.goto(base + "/watch")
    nav = page.locator("#bar .barnav")
    page.mouse.move(300, 300)
    assert "idle" not in nav.get_attribute("class")
    page.wait_for_function("document.querySelector('#bar .barnav').classList.contains('idle')",
                           timeout=6000)
    page.wait_for_function("getComputedStyle(document.querySelector('#bar .barnav')).opacity === '0'",
                           timeout=3000)
    # the bar's own controls stay as they are
    assert page.locator("#sound").is_visible() and page.locator("#tickLabel").is_visible()
    page.mouse.move(600, 500)
    page.wait_for_function("!document.querySelector('#bar .barnav').classList.contains('idle')",
                           timeout=2000)
    page.close()


def test_fly_fits_above_the_bar_and_keeps_the_touch_buttons_clear(browser, base):
    context = browser.new_context(viewport={"width": 400, "height": 800}, has_touch=True,
                                  is_mobile=True)
    page = context.new_page()
    page.goto(base + "/fly")
    page.wait_for_function("window.spacetaxi && window.spacetaxi.fly === 'join'")
    nav = box(page, "nav.sitenav")
    assert nav["y"] + nav["height"] == pytest.approx(800, abs=1)
    stage = box(page, "#stage")
    assert stage["y"] + stage["height"] <= nav["y"] + 1
    page.evaluate("document.getElementById('touch').hidden = false")
    for button in page.locator("#touch button").all():
        b = button.bounding_box()
        assert b["y"] + b["height"] <= nav["y"] + 1
    context.close()


def test_slides_sit_above_the_bar_and_keep_their_keys(browser, base):
    page = browser.new_page(viewport={"width": 1280, "height": 720})
    page.goto(base + "/slides")
    nav = box(page, "nav.sitenav")
    deck = box(page, ".reveal")
    assert deck["y"] + deck["height"] <= nav["y"] + 1
    try:
        page.wait_for_function("window.Reveal && Reveal.isReady && Reveal.isReady()", timeout=5000)
    except Exception:
        pytest.skip("reveal.js did not load (no internet?)")
    page.focus('nav.sitenav a[href="/watch"]')
    page.keyboard.press("ArrowRight")
    assert page.evaluate("Reveal.getIndices().h") == 1
    page.keyboard.press("Space")
    assert page.evaluate("Reveal.getIndices().h") == 2
    assert page.url.startswith(base + "/slides")
    page.close()


def test_no_page_scrolls_sideways_on_a_phone(browser, base):
    context = browser.new_context(viewport={"width": 400, "height": 800})
    page = context.new_page()
    for path, _ in NAV_PAGES:
        page.goto(base + path)
        page.wait_for_load_state("load")
        assert page.evaluate("document.documentElement.scrollWidth") <= 400, path
        if path != "/watch":
            nav = page.locator("nav.sitenav")
            assert nav.is_visible(), path
            # the links scroll sideways inside the bar
            assert page.evaluate("(n => n.scrollWidth > n.clientWidth)"
                                 "(document.querySelector('nav.sitenav'))"), path
    context.close()
