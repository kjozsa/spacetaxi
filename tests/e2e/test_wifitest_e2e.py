"""End-to-end test of the WiFi test pages in headless Chromium (Playwright): the `/` page joins
and its name shows on `/stats` with its answers counting up.
Install the browser once: `uv run playwright install chromium`. Without it these tests skip."""

import asyncio
import threading

import pytest

from spacetaxi.sim import Rules
from spacetaxi.wifitest import WifiTest

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
def server():
    """A WiFi test server on a free port, in its own thread and event loop."""
    test = WifiTest(Rules(tick_ms=100))
    ready = threading.Event()
    box = {}

    def run() -> None:
        loop = asyncio.new_event_loop()
        box["loop"] = loop
        box["srv"] = loop.run_until_complete(test.start("127.0.0.1", 0))
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert ready.wait(10)
    port = box["srv"].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}", test

    async def shutdown() -> None:
        box["srv"].close()
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run_coroutine_threadsafe(shutdown(), box["loop"]).result(10)
    box["loop"].call_soon_threadsafe(box["loop"].stop)
    thread.join(5)


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
