"""Web viewer of a replay: one self-contained HTML page (JS, CSS, font and replay embedded).

`build_html` turns a replay written by `spacetaxi run --replay` into the page; `serve` serves it
with the standard library's `http.server`. The page makes no external requests, so it works
opened via `file://` and on a LAN without internet.
"""

import base64
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from spacetaxi.show import load_replay
from spacetaxi.sim import Rules

WEB_DIR = Path(__file__).parent / "web"

STATES = {"flying": 0, "landed": 1, "crashed": 2}


def compact_tick(players: list[str], tick: dict) -> dict:
    """One tick for the page: `t` tick number, `x` one `[x, y, vx, state, carrying, money]` per
    player (in `players` order), `p` waiting passengers as `[pad, spot_x]`, `e` the events,
    `m` each player's move as `[ax, ay]` and `d` each player's `debug` (text, an object with
    `say` and/or `path`, or null)."""
    by_id = {t["id"]: t for t in tick["taxis"]}
    moves = tick.get("moves") or {}
    taxis = []
    for name in players:
        t = by_id.get(name)
        if t is None:
            taxis.append([0, 0, 0, STATES["crashed"], 0, 0])
            continue
        taxis.append([t["x"], t["y"], t["vx"], STATES.get(t["state"], 0),
                      1 if t["passenger"] else 0, t["money"]])
    return {
        "t": tick["tick"],
        "x": taxis,
        "p": [[p["at"], p["spot_x"]] for p in tick.get("passengers", [])],
        "e": tick.get("events", []),
        "m": [[moves.get(name, {}).get("ax", 0), moves.get(name, {}).get("ay", 0)]
              for name in players],
        "d": [moves.get(name, {}).get("debug") for name in players],
    }


def replay_data(path: str) -> list[dict]:
    """Every round of the replay, as the page reads them."""
    rounds = []
    for number, rnd in sorted(load_replay(path).items()):
        start = rnd["start"]
        ticks = [rnd["ticks"][n] for n in sorted(rnd["ticks"])]
        players = start.get("players") or [t["id"] for t in (ticks[0]["taxis"] if ticks else [])]
        rounds.append({
            "round": number,
            "map": start["map"],
            "pads": start["pads"],
            "players": players,
            "rules": {**Rules().to_dict(), **start.get("rules", {})},
            "ticks": [compact_tick(players, t) for t in ticks],
        })
    return rounds


def build_html(path: str) -> str:
    """The viewer page for the replay at `path`. Raises `ReplayError` for a bad replay."""
    data = json.dumps(replay_data(path), separators=(",", ":"))
    # Inside <script> the JSON must not close the tag: escape every `<`.
    data = data.replace("<", "\\u003c")
    font = base64.b64encode((WEB_DIR / "PressStart2P-Regular.ttf").read_bytes()).decode("ascii")
    template = (WEB_DIR / "viewer.html").read_text(encoding="utf-8")
    script = (WEB_DIR / "viewer.js").read_text(encoding="utf-8")
    return (template
            .replace("/*FONT*/", font)
            .replace("/*SCRIPT*/", script)
            .replace("/*DATA*/", data))


def make_server(html: str, port: int, host: str = "") -> ThreadingHTTPServer:
    """An HTTP server that answers `/` (and `/index.html`) with `html`."""
    body = html.encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path.split("?", 1)[0] not in ("/", "/index.html"):
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:
            pass

    return ThreadingHTTPServer((host, port), Handler)
