"""The arena server's HTTP side: its pages and their JSON.

`/` is the leaderboard page and `/leaderboard.json` its data. `/fly` is the page to fly in the
arena by hand: a `/bot` client in the browser with the neon viewer. `/watch` is the neon viewer
fed live from `/spectate`. `/standings` is the projector page of the milestone race and
`/standings.json` its data. `/brief` is the agent brief (`docs/brief.md`,
rendered, with `address` filled in for HOST:PORT; `/brief.md` is the raw Markdown). `/mission` is each participant's own page: their next
milestone only, from `docs/missions.md`, polling `/standings.json`. `/slides` is the intro deck
(`docs/slides/intro.html`, self-contained, HOST:8000 filled in). `/lessons` is the
workshop's lessons (`docs/lessons.md`): each opens when the followed bot reaches its milestone or
the operator reveals it (`lesson N`). The page gets them from `/lessons/NAME.json` (or
`/lessons.json`, following no bot), which sends a locked lesson's number and `after` only. `/lessons?key=KEY` shows all of them
for the facilitator (to review, and on the projector at the end), from
`/lessons-all/KEY.json`; the key is random per start, printed by `serve` and by `lesson`. Every HTML page has the
page bar linking the others (`spacetaxi.nav`).
"""

import base64
import secrets
import json
import re
from pathlib import Path

from .milestones import MILESTONES, POINTS
from .nav import with_nav
from .viewer import (build_doc_html, build_fly_html, build_lessons_html, build_live_html,
                     build_mission_html, build_standings_html, parse_lessons)

WEB_DIR = Path(__file__).parent / "web"
LESSONS_JSON = re.compile(r"/lessons/[A-Za-z0-9_-]{1,12}\.json")  # a bot name, as hello allows

# the Markdown documents the server serves: path -> (file in docs/, page title)
DOC_PAGES = {"/brief": ("brief.md", "Spacetaxi: brief"),
             "/brief.md": ("brief.md", "")}


def doc_path(name: str) -> Path:
    """A file of `docs/`: packaged next to the code in a wheel, the repo's `docs/` otherwise."""
    for docs in (Path(__file__).parent / "docs", Path(__file__).resolve().parents[2] / "docs"):
        if (docs / name).is_file():
            return docs / name
    raise FileNotFoundError(name)


def doc_text(name: str) -> str:
    return doc_path(name).read_text(encoding="utf-8")


def build_slides_html(address: str) -> str:
    """docs/slides/intro.html as one self-contained page: the font and the images inlined,
    HOST:8000 replaced by `address`."""
    def data_uri(path: Path, kind: str) -> str:
        return f"data:{kind};base64," + base64.b64encode(path.read_bytes()).decode("ascii")

    html = doc_text("slides/intro.html")
    html = html.replace("../../src/spacetaxi/web/PressStart2P-Regular.ttf",
                        data_uri(WEB_DIR / "PressStart2P-Regular.ttf", "font/ttf"))
    kinds = {".png": "image/png", ".jpg": "image/jpeg"}
    html = re.sub(r'src="(img/[\w.-]+)"', lambda m: 'src="{}"'.format(
        data_uri(doc_path("slides/" + m[1]), kinds[Path(m[1]).suffix])), html)
    return html.replace("HOST:8000", address)


class Pages:
    """The HTTP answers of `ArenaServer`, mixed into it: they read the server's state (its
    leaderboard, standings, arenas, plan, maps and `address`). The class mixing it in calls
    `init_pages` first."""

    def init_pages(self) -> None:
        self.watch_pages: dict[str, bytes] = {}  # per map, built on the first request
        self.fly_pages: dict[str, bytes] = {}
        self.leaderboard_page: bytes | None = None
        self.standings_page: bytes | None = None
        self.slides_page: bytes | None = None
        self.lessons_page: bytes | None = None
        self.lessons_key = secrets.token_hex(8)  # /lessons?key=...: all of them, for the facilitator

    def lessons_url(self) -> str:
        return f"http://{self.address or 'HOST:PORT'}/lessons?key={self.lessons_key}"

    def lessons_for(self, name: str | None, everything: bool = False) -> dict:
        """The lessons as `name`'s page sees them: an open one whole, a locked one as its
        number and `after` only. Open: `after: start`, revealed by the operator (the first
        `revealed`), or `after` a milestone the bot `name` reached."""
        key = (name or "").lower()
        got = next((m for n, m in self.standings.awards.items() if n.lower() == key), {})
        revealed = self.standings.lessons
        out = []
        for i, lesson in enumerate(parse_lessons(doc_text("lessons.md"))):
            after = lesson["after"]
            if everything or after == "start" or i < revealed or (after != "reveal" and after in got):
                out.append({**lesson, "open": True})
            else:
                out.append({"n": lesson["n"], "after": after, "open": False})
        return {"name": name, "revealed": revealed, "all": everything, "lessons": out}

    def doc(self, name: str) -> str:
        """A file of `docs/` with the server's address filled in for HOST:PORT."""
        return doc_text(name).replace("HOST:PORT", self.address or "HOST:PORT")

    def http(self, path: str):
        """The HTTP answer to GET `path`: (status, content type, body), or None. Every HTML page
        is built with the page bar (`spacetaxi.nav`)."""
        if path in ("/", "/index.html"):
            if self.leaderboard_page is None:
                page = (WEB_DIR / "leaderboard.html").read_text(encoding="utf-8")
                self.leaderboard_page = with_nav(page, "/").encode("utf-8")
            return 200, "text/html; charset=utf-8", self.leaderboard_page
        if path == "/leaderboard.json":
            data = {"leaderboard": self.leaderboard.table(), "rounds": self.rounds,
                    "wave": self.wave, "next_map": self.next_map,
                    "connected": len(self.bots), "waiting": len(self.queue),
                    "arenas": sorted([{"round": a.number, "arena": a.slot, "players": a.names,
                                       "tick": a.playing, "ends_in": self.seconds(self.wave_t0 + self.round_ns())}
                                      for a in self.arenas],
                                     key=lambda a: a["round"]),
                    "next_wave": {"wave": self.wave + 1, "map": self.next_map,
                                  "starts_in": self.seconds(self.next_wave_at()),
                                  "arenas": [list(g) for g in self.plan_arenas],
                                  "waitlist": list(self.plan_waitlist)}}
            return 200, "application/json", json.dumps(data).encode("utf-8")
        if path == "/standings":
            if self.standings_page is None:
                self.standings_page = with_nav(build_standings_html(), path).encode("utf-8")
            return 200, "text/html; charset=utf-8", self.standings_page
        if path == "/standings.json":
            data = {"standings": self.standings.table(self.house | self.standings.banned),
                    "milestones": list(MILESTONES), "points": list(POINTS),
                    "map_name": self.map_name, "benchmarks": self.benchmarks,
                    "reached": {m: self.standings.reached(m) for m in MILESTONES}}
            return 200, "application/json", json.dumps(data).encode("utf-8")
        if path == "/slides":
            if self.slides_page is None:
                page = build_slides_html(self.address or "HOST:8000")
                self.slides_page = with_nav(page, path).encode("utf-8")
            return 200, "text/html; charset=utf-8", self.slides_page
        if path == "/lessons":
            if self.lessons_page is None:
                self.lessons_page = with_nav(build_lessons_html(), path).encode("utf-8")
            return 200, "text/html; charset=utf-8", self.lessons_page
        if path == f"/lessons-all/{self.lessons_key}.json":
            return 200, "application/json", json.dumps(self.lessons_for(None, True)).encode("utf-8")
        if path == "/lessons.json" or LESSONS_JSON.fullmatch(path):
            name = path[len("/lessons/"):-len(".json")] if path != "/lessons.json" else None
            data = json.dumps(self.lessons_for(name)).encode("utf-8")
            return 200, "application/json", data
        if path == "/mission":
            text = self.doc("missions.md")
            page = with_nav(build_mission_html(text), path)
            return 200, "text/html; charset=utf-8", page.encode("utf-8")
        if path in DOC_PAGES:
            name, title = DOC_PAGES[path]
            text = self.doc(name)
            if path.endswith(".md"):
                return 200, "text/markdown; charset=utf-8", text.encode("utf-8")
            page = with_nav(build_doc_html(text, title), path)
            return 200, "text/html; charset=utf-8", page.encode("utf-8")
        if path in ("/watch", "/fly"):  # the empty map shown before a round: the next one
            pages, build = ((self.watch_pages, build_live_html) if path == "/watch"
                            else (self.fly_pages, build_fly_html))
            name = self.next_map
            if name not in pages:
                pages[name] = with_nav(build(self.maps[name], self.rules), path).encode("utf-8")
            return 200, "text/html; charset=utf-8", pages[name]
        return None
