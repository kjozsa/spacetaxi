"""The "simple" house bot: no search, just a few straight lines.

Target: the passenger's destination when carrying one, otherwise the nearest waiting passenger
by Manhattan distance (whatever they pay and wherever they go: deliberately weak).

Flight: climb (or sink) to a cruising row from which the target is reachable in straight lines,
fly across to the column above the target, then descend over the pad slowly enough to land.
"""

_me = None      # our taxi id, from `welcome`
_rules = {}     # the rule constants, from `welcome` (and again from `round_start`)
_rows = []      # the map rows, from `round_start`
_pads = {}      # pad id -> {"id", "x", "y", "len"}


def welcome(info: dict) -> None:
    global _me, _rules
    _me = info["id"]
    _rules = info["rules"]


def start(info: dict) -> None:
    global _rules, _rows, _pads
    _rules = info["rules"]
    _rows = info["map"]
    _pads = {p["id"]: p for p in info["pads"]}


def is_air(x: int, y: int) -> bool:
    return _rows[y][x] in ".S"


def clear_column(x: int, y1: int, y2: int) -> bool:
    return all(is_air(x, y) for y in range(min(y1, y2), max(y1, y2) + 1))


def clear_row(y: int, x1: int, x2: int) -> bool:
    return all(is_air(x, y) for x in range(min(x1, x2), max(x1, x2) + 1))


def cruise_row(x: int, y: int, tx: int, ty: int):
    """The lowest row we can reach straight up (or down) from here, cross on to column `tx`
    and descend from to `ty`; `None` if there is no such row."""
    for r in range(min(y, ty), 0, -1):
        if clear_column(x, y, r) and clear_row(r, x, tx) and clear_column(tx, r, ty):
            return r
    return None


def top_speed(dist: int, braking: int) -> int:
    """The fastest speed from which we can still stop within `dist` cells: braking from `v`
    takes about `v * (v + 1) // 2 * braking` cells (`braking` is 2 when gravity fights it)."""
    v = 0
    while v < _rules["max_speed"] and (v + 1) * (v + 2) // 2 * braking <= dist:
        v += 1
    return v


def thrust(want: int, coast: int) -> int:
    """The thrust (-1, 0 or 1) that brings the velocity closest to `want`, given the velocity
    `coast` it would have without thrust."""
    return max(-1, min(1, want - coast))


def target(taxi: dict, passengers: list):
    """`(pad, x)`: the pad to land on and the column we would like best, or `None`."""
    if taxi["passenger"] is not None:
        pad = _pads[taxi["passenger"]["to"]]
        return pad, pad["x"] + pad["len"] // 2
    if not passengers:
        return None
    nearest = min(passengers, key=lambda p: abs(p["spot_x"] - taxi["x"])
                  + abs(_pads[p["at"]]["y"] - 1 - taxi["y"]))
    return _pads[nearest["at"]], nearest["spot_x"]


def choose(taxi: dict, passengers: list, tick: int):
    """`(ax, ay, debug)` for our taxi on this tick."""
    goal = target(taxi, passengers)
    if taxi["state"] == "landed":
        # take off for anything to do: even a passenger on this pad needs a fresh touchdown
        return (0, -1, "takeoff") if goal is not None else (0, 0, "waiting")
    if taxi["state"] != "flying":
        return 0, 0, "crashed"
    x, y, vx, vy = taxi["x"], taxi["y"], taxi["vx"], taxi["vy"]
    gravity = _rules["gravity"] if tick % _rules["gravity_every"] == 0 else 0
    if goal is None:
        return thrust(0, vx), thrust(0, vy + gravity), "hovering"
    pad, best_x = goal
    ty = pad["y"] - 1
    columns = sorted(range(pad["x"], pad["x"] + pad["len"]), key=lambda c: abs(c - best_x))

    if x in columns and vx == 0 and y <= ty and clear_column(x, y, ty):
        # over the pad: sink, at 1 cell per tick (the safe landing speed) for the last cells
        want = max(_rules["safe_landing_vy"], top_speed(ty - y, 2))
        return thrust(0, vx), thrust(want, vy + gravity), f"descending to {pad['id']}"

    # the pad column closest to the one we like best that has a straight way in
    tx, r = best_x, None
    for c in columns:
        r = cruise_row(x, y, c, ty)
        if r is not None:
            tx = c
            break
    if r is None:
        # no straight way in from here: creep sideways toward the target and look again,
        # or just hover when a wall is in the way (walls and overhangs beat this bot)
        step = 1 if tx > x else -1
        want = step if vy == 0 and is_air(x + 2 * step, y) else 0
        return thrust(want, vx), thrust(0, vy + gravity), f"no straight way to {pad['id']}"
    if y != r or vy != 0:
        # get to the cruising row first, without drifting sideways
        dist = y - r
        if dist > 0:
            want = -top_speed(dist, 1)
        else:
            want = top_speed(-dist, 2)
        return thrust(0, vx), thrust(want, vy + gravity), f"to cruising row {r}"
    # on the cruising row: across to the target column, braking in time to stop on it
    want = top_speed(abs(tx - x), 1) * (1 if tx > x else -1)
    return thrust(want, vx), thrust(0, vy + gravity), f"cruising to column {tx}"


def move(state: dict) -> dict:
    taxi = next(t for t in state["taxis"] if t["id"] == _me)
    ax, ay, debug = choose(taxi, state["passengers"], state["tick"])
    return {"ax": ax, "ay": ay, "debug": debug}
