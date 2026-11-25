"""The agent brief, the missions and the slides: the Markdown renderer, the served pages, and
the missions kept in step with docs/rules.md."""

import asyncio
import re
import urllib.error
import urllib.request
from pathlib import Path

from spacetaxi.maps import load_map
from spacetaxi.markdown import render
from spacetaxi.milestones import MILESTONES
from spacetaxi.server import ArenaServer
from spacetaxi.sim import Rules
from spacetaxi.viewer import parse_missions

DOCS = Path(__file__).resolve().parent.parent / "docs"


def rules_milestones() -> list[str]:
    """The milestone ids of the table in docs/rules.md's Milestones section, in order."""
    text = (DOCS / "rules.md").read_text(encoding="utf-8")
    section = text.split("\n## Milestones\n", 1)[1].split("\n## ", 1)[0]
    return re.findall(r"^\|\s*\d+\s*\|\s*`(\w+)`", section, re.M)


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
goes on, with **bold** and a [link](/brief) & <tag>.

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
            '<a href="/brief">link</a> &amp; &lt;tag&gt;.</p>') in html
    assert "<ul><li>one <code>a&lt;b</code></li><li>two continues here</li></ul>" in html
    assert '<ol><li>first</li><li>second with <code>{"type": "move"}</code></li></ol>' in html
    assert "<pre><code>raw &lt;b&gt; &amp; stuff\n  indented</code></pre>" in html
    assert html.endswith("<p>after</p>")


def test_markdown_renders_the_brief():
    brief = render((DOCS / "brief.md").read_text(encoding="utf-8"))
    assert "```" not in brief and "**" not in brief and "\n#" not in brief
    assert brief.count("<li>") == 15 and brief.count("<ol>") == 1


def get(port: int, path: str) -> tuple[str, str]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as res:
        return res.headers["Content-Type"], res.read().decode("utf-8")


def test_server_serves_the_brief_with_its_address_and_no_card():
    async def main():
        server = ArenaServer({"cavern": load_map("cavern")},
                             Rules(tick_ms=50, deadline_ms=30, round_ticks=20), 60, seed=1, benchmarks={"cavern": {"greedy": 1, "pilot": 1}})
        srv = await server.start("127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        pages = {p: await asyncio.to_thread(get, port, p)
                 for p in ("/brief", "/brief.md", "/", "/standings")}
        try:
            await asyncio.to_thread(get, port, "/card")
            card = 200
        except urllib.error.HTTPError as exc:
            card = exc.code
        srv.close()
        return port, pages, card

    port, pages, card = asyncio.run(main())
    assert card == 404
    kind, brief = pages["/brief"]
    assert kind.startswith("text/html") and "<h1>space taxi</h1>" in brief
    kind, raw = pages["/brief.md"]
    assert kind.startswith("text/markdown")
    assert raw.startswith("# space taxi\n") and f"ws://127.0.0.1:{port}/bot" in raw
    assert 'href="/mission"' in pages["/"][1] and 'href="/mission"' in pages["/standings"][1]
    assert "/card" not in pages["/"][1] + pages["/standings"][1] + brief


def test_the_address_given_wins_over_the_bound_one():
    server = ArenaServer({"cavern": load_map("cavern")}, Rules(), 60, seed=1)
    server.address = "10.0.0.7:8000"
    brief = server.http("/brief")[2].decode("utf-8")
    assert "ws://10.0.0.7:8000/bot" in brief and "HOST:PORT" not in brief


def test_missions_follow_the_rules_and_only_the_first_has_a_prompt():
    missions = parse_missions((DOCS / "missions.md").read_text(encoding="utf-8"))
    assert [m["id"] for m in missions] == rules_milestones() == list(MILESTONES)
    assert [m["id"] for m in missions if m["prompt"]] == ["hello"]
    for m in missions:
        assert m["goal"] and m["hint"] and "\n" not in m["goal"] + m["hint"]
    assert "NAME" in missions[0]["prompt"] and "ws://HOST:PORT/bot" in missions[0]["prompt"]


def test_server_serves_the_mission_page_with_its_address():
    server = ArenaServer({"cavern": load_map("cavern")}, Rules(), 60, seed=1)
    server.address = "10.0.0.7:8000"
    status, kind, body = server.http("/mission")
    page = body.decode("utf-8")
    assert status == 200 and kind.startswith("text/html")
    assert "HOST:PORT" not in page and "ws://10.0.0.7:8000/bot" in page
    assert "http://10.0.0.7:8000/brief.md" in page
    for m in MILESTONES:
        assert f'"id":"{m}"' in page
    assert "/*MISSIONS*/" not in page and "/*FONT*/" not in page
    assert 'href="/mission"' in server.http("/standings")[2].decode("utf-8")


def test_server_serves_the_slides_self_contained_with_its_address():
    async def main():
        server = ArenaServer({"cavern": load_map("cavern")}, Rules(), 60, seed=1,
                             benchmarks={"cavern": {"greedy": 1, "pilot": 1}})
        srv = await server.start("127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        page = await asyncio.to_thread(get, port, "/slides")
        srv.close()
        return port, page

    port, (kind, html) = asyncio.run(main())
    assert kind.startswith("text/html")
    assert "HOST:8000" not in html and f"ws://127.0.0.1:{port}/bot" in html
    assert "../../src/" not in html and 'src="img/' not in html
    assert "data:font/ttf;base64," in html
    assert 'src="data:image/jpeg;base64,' in html and 'src="data:image/png;base64,' in html
