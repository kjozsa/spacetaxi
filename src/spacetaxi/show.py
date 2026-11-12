"""Text view of a replay: the board and the taxis at one tick of one round.

Reads the JSON-lines replay written by `spacetaxi run --replay`. On the board, `#` is a wall,
`A`-`Z` a pad tile, `.` air (spawn cells too), `@` a waiting passenger (on the cell above its
spot) and `1`-`9` the taxis in player order.
"""

import json


class ReplayError(ValueError):
    """A replay that cannot be read, or a round or tick it does not contain."""


def load_replay(path: str) -> dict[int, dict]:
    """Rounds by number: each `{"start": round_start, "ticks": {tick: tick message}}`."""
    rounds: dict[int, dict] = {}
    current = None
    try:
        with open(path, encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ReplayError(f"{path}:{n}: bad JSON: {exc}") from exc
                kind = msg.get("type") if isinstance(msg, dict) else None
                if kind == "round_start":
                    current = {"start": msg, "ticks": {}}
                    rounds[msg["round"]] = current
                elif kind == "tick" and current is not None:
                    current["ticks"][msg["tick"]] = msg
    except OSError as exc:
        raise ReplayError(f"cannot read replay: {exc}") from exc
    if not rounds:
        raise ReplayError(f"{path}: no rounds in replay")
    return rounds


def get_round(rounds: dict[int, dict], round_no: int) -> dict:
    if round_no not in rounds:
        raise ReplayError(f"round {round_no} not in replay (rounds {min(rounds)}-{max(rounds)})")
    return rounds[round_no]


def get_tick(rnd: dict, tick: int) -> dict:
    ticks = rnd["ticks"]
    if tick not in ticks:
        span = f"ticks {min(ticks)}-{max(ticks)}" if ticks else "no ticks"
        raise ReplayError(f"tick {tick} not in round {rnd['start']['round']} ({span})")
    return ticks[tick]


def taxi_mark(index: int) -> str:
    return str(index + 1) if index < 9 else "*"


def render(start: dict, tick: dict) -> str:
    """The board, then one line per taxi: digit, name, state, `vx,vy`, money, passenger."""
    grid = [list(row.replace("S", ".")) for row in start["map"]]
    pads = {p["id"]: p for p in start["pads"]}
    for p in tick["passengers"]:
        pad = pads.get(p["at"])
        if pad is not None:
            grid[pad["y"] - 1][p["spot_x"]] = "@"
    marks = {}
    for i, taxi in enumerate(tick["taxis"]):
        marks[taxi["id"]] = taxi_mark(i)
        grid[taxi["y"]][taxi["x"]] = taxi_mark(i)
    lines = ["".join(row) for row in grid]

    names = [t["id"] for t in tick["taxis"]]
    width = max(len(n) for n in names) if names else 0
    for taxi in tick["taxis"]:
        p = taxi["passenger"]
        carrying = f"{p['id']}→{p['to']}" if p else "-"
        lines.append(
            f"{marks[taxi['id']]} {taxi['id'].ljust(width)} {taxi['state']:<7} "
            f"{taxi['vx']},{taxi['vy']} {taxi['money']} {carrying}"
        )
    return "\n".join(lines)


def frame(start: dict, tick: dict) -> str:
    """One animation frame: a header line above `render`."""
    return f"round {start['round']} tick {tick['tick']}\n{render(start, tick)}"
