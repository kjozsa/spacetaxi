"""The page bar (spacetaxi.nav): on every HTML page the server serves, its own page marked; not
on the raw Markdown, the JSON endpoints or the files built for file://."""

import re
from pathlib import Path

from spacetaxi.cli import main
from spacetaxi.maps import load_map
from spacetaxi.nav import NAV_PAGES
from spacetaxi.server import ArenaServer
from spacetaxi.sim import Rules

PAGES = [href for href, _ in NAV_PAGES]
REPLAY = Path(__file__).parent / "fixtures" / "show" / "stacked.jsonl"


def server() -> ArenaServer:
    s = ArenaServer({"cavern": load_map("cavern")}, Rules(), 60, seed=1)
    s.address = "10.0.0.7:8000"
    return s


def links(page: str) -> list[tuple[str, bool]]:
    """(href, marked current) of every link of the page bar, in order."""
    nav = re.search(r'<nav class="(?:sitenav|barnav)" aria-label="Pages">(.*?)</nav>', page, re.S)
    assert nav, "no page bar"
    return [(m[1], bool(m[2])) for m in re.finditer(r'<a href="([^"]*)"( aria-current="page")?>',
                                                    nav[1])]


def test_every_served_page_has_the_bar_marking_itself():
    s = server()
    assert PAGES == ["/", "/watch", "/standings", "/mission", "/fly", "/card", "/brief", "/slides"]
    for path in PAGES + ["/index.html"]:
        status, kind, body = s.http(path)
        page = body.decode("utf-8")
        assert status == 200 and kind.startswith("text/html"), path
        current = "/" if path == "/index.html" else path
        assert links(page) == [(href, href == current) for href in PAGES], path
        assert page.count('aria-label="Pages"') == 1, path


def test_watch_puts_the_links_into_its_own_bar():
    page = server().http("/watch")[2].decode("utf-8")
    assert 'class="sitenav"' not in page and "<!--NAV-->" not in page
    bar = page.split('<div id="bar"', 1)[1].split("</div>", 1)[0]
    assert '<nav class="barnav"' in bar and 'id="sound"' in bar and 'id="tickLabel"' in bar


def test_fly_and_the_slides_get_the_fixed_bar():
    s = server()
    for path in ("/fly", "/slides", "/", "/card"):
        page = s.http(path)[2].decode("utf-8")
        assert '<nav class="sitenav"' in page and "<!--NAV-->" not in page, path
        assert page.index('<nav class="sitenav"') < page.rindex("</body>"), path
    assert "--sitenav-h" in s.http("/slides")[2].decode("utf-8").split("<nav", 1)[0]


def test_the_leaderboard_gets_the_pixel_font_for_the_bar():
    page = server().http("/")[2].decode("utf-8")
    assert '@font-face { font-family: "Press Start 2P"; src: url(data:font/ttf;base64,' in page


def test_no_bar_on_raw_markdown_or_json():
    s = server()
    for path in ("/brief.md", "/leaderboard.json", "/standings.json"):
        body = s.http(path)[2].decode("utf-8")
        assert "sitenav" not in body and "barnav" not in body, path


def test_files_for_file_urls_get_no_bar(tmp_path):
    for args in (["view", str(REPLAY)], ["play", "--map", "cavern"]):
        out = tmp_path / "out.html"
        assert main([*args, "--out", str(out)]) == 0
        html = out.read_text(encoding="utf-8")
        assert "<nav" not in html and "<!--NAV-->" not in html, args
