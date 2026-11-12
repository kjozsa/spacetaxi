"""The flight lab: fly one taxi by hand in the browser, on the real map and rules.

`build_html` embeds the map and the rules into one self-contained page (JS, CSS and font
inline, no external requests); `spacetaxi play` writes it or serves it with
`spacetaxi.viewer.make_server`. The page's physics is a port of `World.step` for one taxi, and
`window.spacetaxi.simulate(moves)` runs it headless so tests can compare it with `simulate`
below.
"""

import base64
import dataclasses
import json

from spacetaxi.maps import Map
from spacetaxi.sim import World, Rules
from spacetaxi.viewer import WEB_DIR


def fixed_spawn(game_map: Map, spawn: tuple[int, int] | None = None) -> Map:
    """`game_map` with only one spawn cell (its first, or `spawn`), so every spawn and respawn
    of a `World` on it lands there, as the page's `simulate` does."""
    return dataclasses.replace(game_map, spawns=(spawn or game_map.spawns[0],))


def simulate(game_map: Map, rules: Rules, moves: list[dict],
             spawn: tuple[int, int] | None = None) -> list[dict]:
    """One taxi flown with `moves` from a fixed spawn: its `fx, fy, vx, vy, state` after each."""
    world = World(fixed_spawn(game_map, spawn), rules, players=["you"])
    taxi = world.taxis[0]
    states = []
    for move in moves:
        world.step({"you": move})
        states.append({"fx": taxi.fx, "fy": taxi.fy, "vx": taxi.vx, "vy": taxi.vy,
                       "state": taxi.state})
    return states


def build_html(game_map: Map, rules: Rules) -> str:
    """The flight lab page for `game_map` with `rules` as its defaults."""
    data = json.dumps({"map": list(game_map.rows), "rules": rules.to_dict()},
                      separators=(",", ":"))
    data = data.replace("<", "\\u003c")
    font = base64.b64encode((WEB_DIR / "PressStart2P-Regular.ttf").read_bytes()).decode("ascii")
    template = (WEB_DIR / "play.html").read_text(encoding="utf-8")
    return template.replace("/*DATA*/", data).replace("/*FONT*/", font)
