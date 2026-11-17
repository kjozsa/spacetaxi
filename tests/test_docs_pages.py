"""The participant card and the agent brief: the Markdown renderer, the served pages, and the
card's milestones kept in step with docs/rules.md."""

import asyncio
import re
import urllib.request
from pathlib import Path

from spacetaxi.maps import load_map
from spacetaxi.markdown import render
from spacetaxi.milestones import MILESTONES
from spacetaxi.server import ArenaServer
from spacetaxi.sim import Rules

DOCS = Path(__file__).resolve().parent.parent / "docs"


def rules_milestones() -> list[str]:
    """The milestone ids of the table in docs/rules.md's Milestones section, in order."""
    text = (DOCS / "rules.md").read_text(encoding="utf-8")
    section = text.split("\n## Milestones\n", 1)[1].split("\n## ", 1)[0]
    return re.findall(r"^\|\s*\d+\s*\|\s*`(\w+)`", section, re.M)


def test_card_lists_every_milestone_of_the_rules_in_order():
    ids = rules_milestones()
    assert ids == list(MILESTONES)
    card = (DOCS / "participant-card.md").read_text(encoding="utf-8")
    section = card.split("## The milestones", 1)[1].split("\n## ", 1)[0]
    listed = re.findall(r"^\d+\.\s+`(\w+)`", section, re.M)
    assert listed == ids


def test_card_has_the_points_rules_of_thumb_and_one_prompt():
    card = (DOCS / "participant-card.md").read_text(encoding="utf-8")
    assert "25, 18, 15, 12, 10, 8, 6, 4, 2, 1" in card
    assert "ws://HOST:PORT/bot" in card and "**your first name**" in card
    for path in ("/brief", "/watch", "/standings"):
        assert f"HOST:PORT{path}" in card
    assert card.count("```") == 2
    assert ("Read brief.md. Write the smallest Python bot that connects as Anna and answers every "
            "tick with no thrust. Run it and leave it running.") in card
    order = [card.index(h) for h in ("## The server", "## The milestones", "## Rules of thumb",
                                      "## Your first prompt")]
    assert order == sorted(order)


def test_brief_does_not_mention_the_race():
    brief = (DOCS / "brief.md").read_text(encoding="utf-8").lower()
    for word in ("milestone", "point", "greedy", "pilot", "house bot"):
        assert word not in brief
    rules = (DOCS / "rules.md").read_text(encoding="utf-8")
    assert "brief.md" in rules.split("\n\n", 2)[1]


def test_markdown_renderer():
    text = """# Title *x*

## Sub `code`

A paragraph that
goes on, with **bold** and a [link](/card) & <tag>.

- one `a<b`
- two
  continues here

1. first
2. second with `{"type": "move"}`

```
raw <b> & stuff
  indented
```
after
"""
    html = render(text)
    assert "<h1>Title *x*</h1>" in html
    assert "<h2>Sub <code>code</code></h2>" in html
    assert ('<p>A paragraph that goes on, with <strong>bold</strong> and a '
            '<a href="/card">link</a> &amp; &lt;tag&gt;.</p>') in html
    assert "<ul><li>one <code>a&lt;b</code></li><li>two continues here</li></ul>" in html
    assert '<ol><li>first</li><li>second with <code>{"type": "move"}</code></li></ol>' in html
    assert "<pre><code>raw &lt;b&gt; &amp; stuff\n  indented</code></pre>" in html
    assert html.endswith("<p>after</p>")


def test_markdown_renders_both_docs():
    for name in ("brief.md", "participant-card.md"):
        html = render((DOCS / name).read_text(encoding="utf-8"))
        assert "```" not in html and "**" not in html and "\n#" not in html
    brief = render((DOCS / "brief.md").read_text(encoding="utf-8"))
    assert brief.count("<li>") == 13 and brief.count("<ol>") == 1
    card = render((DOCS / "participant-card.md").read_text(encoding="utf-8"))
    assert "<pre><code>Read brief.md." in card


def get(port: int, path: str) -> tuple[str, str]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as res:
        return res.headers["Content-Type"], res.read().decode("utf-8")


def test_server_serves_card_and_brief_with_its_address():
    async def main():
        server = ArenaServer(load_map("cavern"), Rules(tick_ms=50, deadline_ms=30, round_ticks=20),
                             60, seed=1, benchmarks={"greedy": 1, "pilot": 1})
        srv = await server.start("127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        pages = {p: await asyncio.to_thread(get, port, p)
                 for p in ("/card", "/brief", "/brief.md", "/", "/standings")}
        srv.close()
        return port, pages

    port, pages = asyncio.run(main())
    kind, card = pages["/card"]
    assert kind.startswith("text/html")
    assert "HOST:PORT" not in card
    assert f"ws://127.0.0.1:{port}/bot" in card and f"http://127.0.0.1:{port}/standings" in card
    assert "<code>beat_pilot</code>" in card
    kind, brief = pages["/brief"]
    assert kind.startswith("text/html") and "<h1>space taxi</h1>" in brief
    kind, raw = pages["/brief.md"]
    assert kind.startswith("text/markdown")
    assert raw.startswith("# space taxi\n") and f"ws://127.0.0.1:{port}/bot" in raw
    assert 'href="/card"' in pages["/"][1] and 'href="/card"' in pages["/standings"][1]


def test_the_address_given_wins_over_the_bound_one():
    server = ArenaServer(load_map("cavern"), Rules(), 60, seed=1)
    server.address = "10.0.0.7:8000"
    card = server.http("/card")[2].decode("utf-8")
    assert "ws://10.0.0.7:8000/bot" in card and "HOST:PORT" not in card
