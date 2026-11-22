"""End-to-end test of the WiFi test pages in headless Chromium (Playwright): the `/` page joins
and its name shows on `/stats` with its answers counting up.
Install the browser once: `uv run playwright install chromium`. Without it these tests skip."""

import pytest

from spacetaxi.sim import Rules
from spacetaxi.wifitest import WifiTest

pytest.importorskip("playwright.sync_api")


@pytest.fixture
def server(serve_arena):
    """A WiFi test server on a free port, in its own thread and event loop."""
    base, test, _ = serve_arena(WifiTest(Rules(tick_ms=100)))
    return base, test


def test_page_joins_and_shows_on_stats(browser, server):
    base, test = server
    errors = []
    page = browser.new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/")
    page.fill("#name", "phone1")
    page.click("#go")
    page.wait_for_function("window.wifitest.received >= 5", timeout=10_000)
    assert "connected as phone1" in page.text_content("#status")

    stats = browser.new_page()
    stats.on("pageerror", lambda e: errors.append(str(e)))
    stats.goto(base + "/stats")
    stats.wait_for_function(
        "window.lastStats && window.lastStats.clients.some(c => c.name === 'phone1')")
    first = stats.evaluate("window.lastStats.clients[0].answers")
    stats.wait_for_function(f"window.lastStats.clients[0].answers > {first + 5}",
                            timeout=10_000)
    row = stats.locator("#rows tr").first
    assert row.locator("td").first.text_content() == "phone1"
    assert int(row.locator("td").nth(2).text_content()) > first
    assert "GO" in stats.text_content("#verdict")
    page.wait_for_function("document.getElementById('late').textContent.endsWith('%')")
    assert errors == []
