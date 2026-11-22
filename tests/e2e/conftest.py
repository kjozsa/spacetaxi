"""Shared fixtures of the e2e tests: headless Chromium, the screenshots directory and an arena
server in its own thread and event loop.

Install the browser once: `uv run playwright install chromium`. Without it the tests skip.
Set `SPACETAXI_SCREENSHOTS=1` to write the screenshots to `tests/e2e/screenshots/` instead of
a temp dir."""

import asyncio
import os
import threading
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

import pytest

SCREENSHOTS = Path(__file__).parent / "screenshots"


@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # the browser is not installed
            pytest.skip(f"headless Chromium unavailable ({exc.__class__.__name__}); "
                        "run `uv run playwright install chromium`")
        yield b
        b.close()


@pytest.fixture(scope="module")
def shots(tmp_path_factory) -> Path:
    if os.environ.get("SPACETAXI_SCREENSHOTS"):
        SCREENSHOTS.mkdir(exist_ok=True)
        return SCREENSHOTS
    return tmp_path_factory.mktemp("shots")


@contextmanager
def _serving(server, on_start: Callable[[asyncio.AbstractEventLoop, int], object]) -> Iterator[tuple]:
    ready = threading.Event()
    box = {}

    def run() -> None:
        loop = asyncio.new_event_loop()
        box["loop"] = loop
        box["srv"] = loop.run_until_complete(server.start("127.0.0.1", 0))
        box["started"] = on_start(loop, box["srv"].sockets[0].getsockname()[1])
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert ready.wait(10)
    port = box["srv"].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}", server, box["loop"]
    finally:
        async def shutdown() -> None:
            box["srv"].close()
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        asyncio.run_coroutine_threadsafe(shutdown(), box["loop"]).result(10)
        box["loop"].call_soon_threadsafe(box["loop"].stop)
        thread.join(5)


@pytest.fixture
def serve_arena():
    """`serve_arena(server, on_start)` starts `server` (an `ArenaServer`, or anything with its
    `start(host, port)`) on a free port in its own thread and event loop, calls
    `on_start(loop, port)` on that loop once it listens (what it returns is kept: the loop
    holds only weak references to tasks) and returns (base URL, server, loop). The server
    shuts down when the test ends."""
    with ExitStack() as stack:
        def serve(server, on_start=lambda loop, port: None):
            return stack.enter_context(_serving(server, on_start))
        yield serve
