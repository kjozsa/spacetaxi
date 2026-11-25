"""The greedy house bot: no search, a few straight lines, flown slowly.

Target: the passenger's destination when carrying one, otherwise the nearest waiting passenger
by Manhattan distance (whatever they pay and wherever they go: deliberately weak).

Flight, one function per step, all positions in units (see docs/rules.md): `take_off`; `climb`
(or sink) straight to a cruising height from which the pad is reachable in straight lines;
`cross` at that height with the side jets, braking early; `descend` once over the pad, slower
than `safe_landing_vy`. With no such route from here, it first goes sideways (`detour`).
"""

from math import isqrt

CRUISE_SPEED = 10  # top speed we fly at, in units per tick, across and up or down
MARGIN = 2         # units of air we keep around the taxi's box on the planned route

_me = None      # our taxi id, from `welcome`
_rules = {}     # the rule constants, from `welcome` (and again from `round_start`)
_width = 0      # the map's width in cells, from `round_start`
_air = set()    # the (x, y) cells of the map that are air
_pads = {}      # pad id -> {"id", "x", "y", "len"}


def welcome(info: dict) -> None:
    global _me, _rules
    _me = info["id"]
    _rules = info["rules"]


def start(info: dict) -> None:
    global _rules, _width, _air, _pads
    _rules = info["rules"]
    _width = len(info["map"][0])
    _air = {(x, y) for y, row in enumerate(info["map"]) for x, ch in enumerate(row) if ch in ".S"}
    _pads = {p["id"]: p for p in info["pads"]}


def clear(x1: int, x2: int, y1: int, y2: int, margin: int = MARGIN) -> bool:
    """True if the taxi's box, plus `margin`, touches only air while its centre stays between
    `x1` and `x2` across and `y1` and `y2` down."""
    u, w, h = _rules["units_per_cell"], _rules["taxi_half_width"], _rules["taxi_half_height"]
    left, right = (min(x1, x2) - w - margin) // u, (max(x1, x2) + w + margin) // u
    top, bottom = (min(y1, y2) - h - margin) // u, (max(y1, y2) + h + margin) // u
    return all((x, y) in _air for y in range(top, bottom + 1) for x in range(left, right + 1))


def landing_fy(pad: dict) -> int:
    """Our `fy` when resting on `pad`."""
    return pad["y"] * _rules["units_per_cell"] - 1 - _rules["taxi_half_height"]


def plan(fx: int, fy: int, pad: dict):
    """`(pad_fx, cruise_fy)`: where over the pad to land and the height to cross at, or `None`.
    Tries spots from the pad's centre outward and heights from the lowest up, every half cell:
    straight up or down from here, straight across, then straight down onto the pad."""
    u, half = _rules["units_per_cell"], _rules["units_per_cell"] // 2
    centre = (pad["x"] + pad["len"] // 2) * u + half
    spots = range(pad["x"] * u + half, (pad["x"] + pad["len"]) * u - half + 1, half)
    land_fy = landing_fy(pad)
    heights = [h for h in range((land_fy - MARGIN) // half * half, half - 1, -half)
               if clear(fx, fx, min(fy, h - MARGIN), max(fy, h + MARGIN), 0)]
    for pad_fx in sorted(spots, key=lambda s: abs(s - centre)):
        for cruise_fy in heights:
            if not clear(pad_fx, pad_fx, cruise_fy, land_fy - MARGIN):
                break  # no straight way down from this height, nor from any higher
            if clear(fx, pad_fx, cruise_fy, cruise_fy):
                return pad_fx, cruise_fy
    return None


def over(fx: int, fy: int, pad: dict) -> bool:
    """True if we are above `pad`, with clear air all the way down to it."""
    u, half = _rules["units_per_cell"], _rules["units_per_cell"] // 2
    on_pad = pad["x"] * u + half <= fx <= (pad["x"] + pad["len"]) * u - half
    land = landing_fy(pad) - 3  # 3 units of margin, but not into the pad itself
    return on_pad and fy <= landing_fy(pad) and clear(fx, fx, min(fy, land), land, 3)


def speed_for(dist: int, decel: int) -> int:
    """How fast to go `dist` units: slow enough to stop in half of it, braking by `decel`."""
    return min(CRUISE_SPEED, isqrt(decel * abs(dist))) * (1 if dist > 0 else -1)


def side_jets(want_vx: int, vx: int) -> int:
    """`ax`: the side jet that brings `vx` closest to `want_vx`."""
    return min((0, -1, 1), key=lambda ax: abs(vx + ax * _rules["side_thrust"] - want_vx))


def main_engine(want_vy: int, vy: int, tick: int) -> int:
    """`ay`: fire the main engine (-1) if that brings `vy` closer to `want_vy` than falling."""
    if tick % _rules["gravity_every"] == 0:
        vy += _rules["gravity"]
    return -1 if abs(vy - _rules["main_thrust"] - want_vy) < abs(vy - want_vy) else 0


def hold(taxi: dict, fy: int, tick: int) -> int:
    """`ay` that takes us to height `fy` and stops there (the engine brakes against gravity)."""
    brake = _rules["main_thrust"] - _rules["gravity"]
    return main_engine(speed_for(fy - taxi["fy"], brake), taxi["vy"], tick)


def take_off():
    return 0, -1


def climb(taxi: dict, cruise_fy: int, tick: int):
    return side_jets(0, taxi["vx"]), hold(taxi, cruise_fy, tick)


def cross(taxi: dict, to_fx: int, cruise_fy: int, tick: int):
    want_vx = speed_for(to_fx - taxi["fx"], _rules["side_thrust"])
    return side_jets(want_vx, taxi["vx"]), hold(taxi, cruise_fy, tick)


def descend(taxi: dict, pad: dict, tick: int):
    """Stop sideways, sink fast while high, then at half the safe landing speed."""
    brake = _rules["main_thrust"] - _rules["gravity"]
    want_vy = max(_rules["safe_landing_vy"] // 2, speed_for(landing_fy(pad) - taxi["fy"], brake))
    return side_jets(0, taxi["vx"]), main_engine(want_vy, taxi["vy"], tick)


def detour(fx: int, fy: int, pad: dict):
    """The nearest `fx` straight across at height `fy` with a `plan` to `pad`, or `None`."""
    half = _rules["units_per_cell"] // 2
    sides = [-1, 1]  # the directions not yet blocked by a wall
    for dist in range(half, _width * 2 * half, half):
        for side in list(sides):
            if not clear(fx, fx + side * dist, fy, fy, 3):
                sides.remove(side)
            elif plan(fx + side * dist, fy, pad) is not None:
                return fx + side * dist
    return None


def target(taxi: dict, passengers: list):
    """The pad to land on next, or `None`."""
    if taxi["passenger"] is not None:
        return _pads[taxi["passenger"]["to"]]
    if not passengers:
        return None
    nearest = min(passengers, key=lambda p: abs(_pads[p["at"]]["x"] - taxi["x"])
                  + abs(_pads[p["at"]]["y"] - taxi["y"]))
    return _pads[nearest["at"]]


def choose(taxi: dict, passengers: list, tick: int):
    """`(ax, ay, debug)` for our taxi on this tick."""
    pad = target(taxi, passengers)
    if taxi["state"] == "landed":
        # take off for anything to do: even a passenger on this pad needs a fresh touchdown
        return (*take_off(), "takeoff") if pad is not None else (0, 0, "waiting")
    if taxi["state"] != "flying":
        return 0, 0, "crashed"
    fx, fy, u = taxi["fx"], taxi["fy"], _rules["units_per_cell"]
    here_fy = taxi["y"] * u + u // 2  # the centre of our cell
    route = plan(fx, fy, pad) if pad is not None else None
    if route is None:
        wx = detour(fx, here_fy, pad) if pad is not None else None
        if wx is None:
            return (*climb(taxi, here_fy, tick), "hovering")
        return (*cross(taxi, wx, here_fy, tick), f"detour to {pad['id']}")
    if over(fx, fy, pad) and abs(taxi["vx"]) <= _rules["safe_landing_vx"]:
        return (*descend(taxi, pad, tick), f"descending to {pad['id']}")
    pad_fx, cruise_fy = route
    if abs(fy - cruise_fy) > MARGIN:
        return (*climb(taxi, cruise_fy, tick), f"to cruising height {cruise_fy}")
    return (*cross(taxi, pad_fx, cruise_fy, tick), f"crossing to {pad['id']}")


def move(state: dict) -> dict:
    taxi = next(t for t in state["taxis"] if t["id"] == _me)
    ax, ay, debug = choose(taxi, state["passengers"], state["tick"])
    return {"ax": ax, "ay": ay, "debug": debug}
