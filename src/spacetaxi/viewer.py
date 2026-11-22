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


def compact_tick(players: list[str], tick: dict,
                 units_per_cell: int = Rules().units_per_cell) -> dict:
    """One tick for the page: `t` tick number, `x` one `[x, y, vx, state, carrying, money, to]`
    per player (in `players` order), `p` waiting passengers as `[pad, spot_x, to, fare]`, `e`
    the events, `m` each player's move as `[ax, ay]` and `d` each player's `debug` (text, an
    object with `say` and/or `path`, or null).

    A taxi's `x`, `y` is its fine position in cells (`fx / units_per_cell`, the centre of its
    box), or the centre of its cell in replays from before fine positions; `to` is the
    destination pad of the passenger it carries, or null. `to` and `fare` of a passenger are
    null when the replay does not have them."""
    by_id = {t["id"]: t for t in tick["taxis"]}
    moves = tick.get("moves") or {}
    taxis = []
    for name in players:
        t = by_id.get(name)
        if t is None:
            taxis.append([0.5, 0.5, 0, STATES["crashed"], 0, 0, None])
            continue
        if "fx" in t and "fy" in t:
            x, y = t["fx"] / units_per_cell, t["fy"] / units_per_cell
        else:
            x, y = t["x"] + 0.5, t["y"] + 0.5
        passenger = t["passenger"]
        taxis.append([x, y, t["vx"], STATES.get(t["state"], 0), 1 if passenger else 0,
                      t["money"], passenger.get("to") if passenger else None])
    return {
        "t": tick["tick"],
        "x": taxis,
        "p": [[p["at"], p["spot_x"], p.get("to"), p.get("fare")]
              for p in tick.get("passengers", [])],
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
        rules = {**Rules().to_dict(), **start.get("rules", {})}
        rounds.append({
            "round": number,
            "map": start["map"],
            "pads": start["pads"],
            "players": players,
            "humans": start.get("humans", []),
            "rules": rules,
            "ticks": [compact_tick(players, t, rules["units_per_cell"]) for t in ticks],
        })
    return rounds


def build_html(path: str) -> str:
    """The viewer page for the replay at `path`. Raises `ReplayError` for a bad replay."""
    return page(replay_data(path), "replay")


def build_live_html(game_map, rules: Rules) -> str:
    """The live viewer page (`spacetaxi serve`'s `/watch`): it reads the rounds from the
    `/spectate` socket. Its one embedded round is the arena's empty map, shown before any."""
    blank = {"round": 0, "map": list(game_map.rows), "pads": [p.to_dict() for p in game_map.pads],
             "players": [], "rules": rules.to_dict(), "ticks": []}
    return page([blank], "live").replace("<title>Spacetaxi replay</title>",
                                         "<title>Spacetaxi live</title>")


def build_fly_html(game_map, rules: Rules) -> str:
    """The page to fly in the arena by hand (`spacetaxi serve`'s `/fly`): the live viewer in
    `fly` mode, a `/bot` client that answers every tick with the keys held."""
    blank = {"round": 0, "map": list(game_map.rows), "pads": [p.to_dict() for p in game_map.pads],
             "players": [], "rules": rules.to_dict(), "ticks": []}
    return page([blank], "fly").replace("<title>Spacetaxi replay</title>",
                                        "<title>Spacetaxi: fly</title>")


def build_standings_html() -> str:
    """The projector page of the milestone race (`spacetaxi serve`'s `/standings`), in the
    viewer's neon style; it polls `/standings.json`."""
    font = base64.b64encode((WEB_DIR / "PressStart2P-Regular.ttf").read_bytes()).decode("ascii")
    return (WEB_DIR / "standings.html").read_text(encoding="utf-8").replace("/*FONT*/", font)


def build_doc_html(text: str, title: str) -> str:
    """A Markdown document (`spacetaxi serve`'s `/card` and `/brief`) as a page in the
    viewer's neon style."""
    import html

    from spacetaxi.markdown import render
    font = base64.b64encode((WEB_DIR / "PressStart2P-Regular.ttf").read_bytes()).decode("ascii")
    return ((WEB_DIR / "doc.html").read_text(encoding="utf-8").replace("/*FONT*/", font)
            .replace("/*TITLE*/", html.escape(title)).replace("/*BODY*/", render(text)))


def parse_missions(text: str) -> list[dict]:
    """The missions of `docs/missions.md`: one `{id, goal, hint, prompt}` per `## id` section,
    in file order, from its `Goal:`, `Hint:` and `Prompt:` lines (`prompt` None without one)."""
    out = []
    for section in text.split("\n## ")[1:]:
        head, *lines = section.splitlines()
        fields = {"id": head.strip(), "goal": "", "hint": "", "prompt": None}
        for line in lines:
            key, sep, value = line.partition(":")
            if sep and key in ("Goal", "Hint", "Prompt"):
                fields[key.lower()] = value.strip()
        out.append(fields)
    return out


def build_mission_html(text: str) -> str:
    """The participant's mission page (`spacetaxi serve`'s `/mission`), with the missions of
    `text` (`docs/missions.md`, HOST:PORT already filled in) embedded; it polls
    `/standings.json`."""
    data = json.dumps(parse_missions(text), separators=(",", ":")).replace("<", "\\u003c")
    font = base64.b64encode((WEB_DIR / "PressStart2P-Regular.ttf").read_bytes()).decode("ascii")
    return ((WEB_DIR / "mission.html").read_text(encoding="utf-8").replace("/*FONT*/", font)
            .replace("/*MISSIONS*/", data))


def page(rounds: list[dict], mode: str) -> str:
    """The viewer page with `rounds` embedded; `mode` is `replay`, `live` or `fly`."""
    data = json.dumps(rounds, separators=(",", ":"))
    # Inside <script> the JSON must not close the tag: escape every `<`.
    data = data.replace("<", "\\u003c")
    font = base64.b64encode((WEB_DIR / "PressStart2P-Regular.ttf").read_bytes()).decode("ascii")
    template = (WEB_DIR / "viewer.html").read_text(encoding="utf-8")
    script = (WEB_DIR / "viewer.js").read_text(encoding="utf-8")
    return (template
            .replace("/*MODE*/", mode)
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
