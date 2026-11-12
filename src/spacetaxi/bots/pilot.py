"""The strong house bot: flies the fine lander physics with a cost field and a safety check.

In `start`, for every pad a Dijkstra search from the cell above the pad's centre gives a cost
field over the air cells (walls nearby cost extra, so routes keep to open air). In `move`:

- follow the field: the waypoint is the farthest cell along it, a few cells ahead, that the
  taxi's box can reach in a straight line;
- steer each axis toward a desired velocity, capped by the stopping distance (side jets
  horizontally, main engine against gravity vertically);
- over the pad, hold `vx` near 0 and descend slower than `safe_landing_vy`;
- before answering, check the move: simulate it, then braking to a stop, with the exact
  physics, and fall back to the next best move when that would crash.

Target: the passenger's destination when carrying one, otherwise the waiting passenger with the
best fare per estimated tick. With nothing to do, the taxi lands on the nearest pad and waits.
"""

from heapq import heappop, heappush

from spacetaxi.sim import path_steps

AIR = ".S"
STEPS = ((1, 0, 10), (-1, 0, 10), (0, 1, 10), (0, -1, 10),
         (1, 1, 14), (1, -1, 14), (-1, 1, 14), (-1, -1, 14))
NEAR_WALL = 30      # extra cost of a cell next to a solid one (10 per cell of distance)
LOOK_AHEAD = 10     # cells along the field the waypoint may be ahead
MARGIN = 3          # units of clearance the straight line to the waypoint keeps
CRUISE = 16         # top speed we steer for, in units per tick
ALIGN = 6           # units off the pad's centre column from which we descend onto it
BRAKE_TICKS = 30    # ticks the safety check follows the braking after a move
TICKS_PER_COST = 3  # estimated ticks per 10 cost units (one cell)
LAND_TICKS = 12     # estimated ticks for a takeoff, approach and touchdown
CRASH = "crash"

_me = None
_rules: dict = {}
_pilot = None


class Pilot:
    """Cost fields and exact physics for one map under one set of rules."""

    def __init__(self, rows, pads, rules: dict):
        self.rows = list(rows)
        self.h, self.w = len(self.rows), len(self.rows[0])
        self.solid = [[ch not in AIR for ch in row] for row in self.rows]
        r = rules
        self.u = r["units_per_cell"]
        self.hw, self.hh = r["taxi_half_width"], r["taxi_half_height"]
        self.g, self.ge = r["gravity"], r["gravity_every"]
        self.mt, self.st, self.m = r["main_thrust"], r["side_thrust"], r["max_speed"]
        self.svx, self.svy = r["safe_landing_vx"], r["safe_landing_vy"]
        self.pads = {p["id"]: p for p in pads}
        self.goal = {p["id"]: (p["x"] + p["len"] // 2, p["y"] - 1) for p in pads}
        self.cost = {}
        self.next = {}
        for pid, goal in self.goal.items():
            self.cost[pid], self.next[pid] = self._field(goal)
        self._stop = {}

    # ---- the cost fields

    def air(self, x: int, y: int) -> bool:
        return 0 <= x < self.w and 0 <= y < self.h and not self.solid[y][x]

    def _near_wall(self, x: int, y: int) -> bool:
        return any(not self.air(x + dx, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1))

    def _field(self, goal):
        """`(cost, next)`: the cost to the goal cell from every air cell, and the next cell on
        the way, both keyed by `(x, y)`."""
        cost = {goal: 0}
        nxt = {goal: None}
        heap = [(0, goal)]
        while heap:
            c, (x, y) = heappop(heap)
            if c > cost[(x, y)]:
                continue
            for dx, dy, step in STEPS:
                px, py = x + dx, y + dy
                if not self.air(px, py):
                    continue
                if dx and dy and not (self.air(px, y) and self.air(x, py)):
                    continue
                pc = c + step + (NEAR_WALL if self._near_wall(px, py) else 0)
                if pc < cost.get((px, py), pc + 1):
                    cost[(px, py)] = pc
                    nxt[(px, py)] = (x, y)
                    heappush(heap, (pc, (px, py)))
        return cost, nxt

    def route(self, pid: str, cell) -> list:
        """The cells from `cell` to the goal of pad `pid` along its field, `cell` excluded."""
        nxt = self.next[pid]
        out = []
        cell = nxt.get(cell)
        while cell is not None and len(out) < 4 * (self.w + self.h):
            out.append(cell)
            cell = nxt[cell]
        return out

    def ticks(self, pid: str, cell) -> int:
        """Estimated ticks to land on pad `pid` from `cell`, or -1 if it cannot be reached."""
        c = self.cost[pid].get(cell)
        return -1 if c is None else c * TICKS_PER_COST // 10 + LAND_TICKS

    # ---- the exact physics

    def box_free(self, x0: int, y0: int, x1: int, y1: int) -> bool:
        """True if the units `x0..x1` by `y0..y1` overlap no solid cell."""
        u = self.u
        for cy in range(y0 // u, y1 // u + 1):
            for cx in range(x0 // u, x1 // u + 1):
                if not self.air(cx, cy):
                    return False
        return True

    def _contact(self, fx: int, fy: int, sy: int, vx: int, vy: int):
        """None if the box at `(fx, fy)` is free, else the step's outcome: a pad id for a safe
        landing or CRASH."""
        u, hw, hh = self.u, self.hw, self.hh
        y0, y1 = (fy - hh) // u, (fy + hh) // u
        x0, x1 = (fx - hw) // u, (fx + hw) // u
        solid = [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)
                 if not self.air(x, y)]
        if not solid:
            return None
        bottom = self.rows[y1] if 0 <= y1 < self.h else ""
        touchdown = sy == 1 and all(y == y1 and 0 <= x < self.w and bottom[x] in self.pads
                                    for x, y in solid)
        if not touchdown or abs(vx) > self.svx or vy > self.svy:
            return CRASH
        under = bottom[fx // u]
        return under if under in self.pads else bottom[solid[0][0]]

    def velocity(self, vx: int, vy: int, ax: int, ay: int, tick: int):
        vx += ax * self.st
        if ay == -1:
            vy -= self.mt
        if tick % self.ge == 0:
            vy += self.g
        m = self.m
        return max(-m, min(m, vx)), max(-m, min(m, vy))

    def step(self, fx, fy, vx, vy, ax, ay, tick):
        """One flying tick: `(fx, fy, vx, vy, outcome)`, outcome None while flying, a pad id
        after a safe landing, CRASH after a crash."""
        vx, vy = self.velocity(vx, vy, ax, ay, tick)
        hw, hh = self.hw, self.hh
        if self.box_free(min(fx, fx + vx) - hw, min(fy, fy + vy) - hh,
                         max(fx, fx + vx) + hw, max(fy, fy + vy) + hh):
            return fx + vx, fy + vy, vx, vy, None
        for sx, sy in path_steps(vx, vy):
            out = self._contact(fx + sx, fy + sy, sy, vx, vy)
            if out is not None:
                return fx, fy, vx, vy, out
            fx, fy = fx + sx, fy + sy
        return fx, fy, vx, vy, None

    def brake(self, vx: int, vy: int):
        """The braking move: side jets against `vx`, main engine while falling."""
        ax = min((-1, 0, 1), key=lambda a: abs(vx + a * self.st))
        return ax, (-1 if vy > 0 else 0)

    def survives(self, fx, fy, vx, vy, ax, ay, tick) -> bool:
        """True if the move, then braking until stopped, neither crashes."""
        fx, fy, vx, vy, out = self.step(fx, fy, vx, vy, ax, ay, tick)
        for k in range(1, BRAKE_TICKS + 1):
            if out is not None:
                return out != CRASH
            if 2 * abs(vx) <= self.st and vy == 0:
                return True
            bx, by = self.brake(vx, vy)
            fx, fy, vx, vy, out = self.step(fx, fy, vx, vy, bx, by, tick + k)
        return out != CRASH

    def stop_speed(self, dist: int, decel: int, end: int = 0) -> int:
        """The fastest speed (a multiple of `decel` above `end`) from which braking by `decel`
        per tick gets down to `end` within `dist` units."""
        key = (dist, decel, end)
        if key not in self._stop:
            v = end
            while v < self.m:
                d, w = 0, v + decel
                while w > end:
                    w -= decel
                    d += w
                if d > dist:
                    break
                v += decel
            self._stop[key] = v
        return self._stop[key]

    # ---- flying

    def visible(self, fx: int, fy: int, cell) -> bool:
        """True if the box, with MARGIN to spare, can fly straight to the centre of `cell`."""
        u, half = self.u, self.u // 2
        tx, ty = cell[0] * u + half, cell[1] * u + half
        n = max(abs(tx - fx), abs(ty - fy)) // 8 + 1
        hw, hh = self.hw + MARGIN, self.hh + MARGIN
        for i in range(n + 1):
            x = fx + (tx - fx) * i // n
            y = fy + (ty - fy) * i // n
            if not self.box_free(x - hw, y - hh, x + hw, y + hh):
                return False
        return True

    def fly(self, taxi: dict, pid: str, tick: int):
        """`(ax, ay, path)` to land on pad `pid` from a flying taxi."""
        u, half = self.u, self.u // 2
        fx, fy, vx, vy = taxi["fx"], taxi["fy"], taxi["vx"], taxi["vy"]
        cell = (taxi["x"], taxi["y"])
        goal = self.goal[pid]
        path = self.route(pid, cell)
        wp = path[0] if path else goal
        for c in reversed(path[:LOOK_AHEAD]):
            if self.visible(fx, fy, c):
                wp = c
                break
        tx, ty = wp[0] * u + half, wp[1] * u + half
        up = max(1, self.g // self.ge)  # braking a climb: gravity alone
        down = self.mt - self.g  # braking a fall: main engine against gravity
        if wp == goal and (cell == goal or self.visible(fx, fy, goal)):
            if abs(fx - tx) <= ALIGN and abs(vx) <= self.svx:
                # over the pad: sink slowly enough to touch down
                land = (goal[1] + 1) * u - 1 - self.hh
                want_vy = min(CRUISE, self.stop_speed(max(0, land - fy), down,
                                                      self.svy - down))
                want_vx = self._want(tx - fx, self.st)
                return (*self._choose(taxi, tick, want_vx, want_vy, tx - fx, 1), path)
            if self.air(goal[0], goal[1] - 1):
                ty -= u  # line up one cell above the pad first
        want_vx = self._want(tx - fx, self.st)
        dy = ty - fy
        want_vy = self._want(dy, down if dy > 0 else up)
        return (*self._choose(taxi, tick, want_vx, want_vy, tx - fx, dy), path)

    def _want(self, d: int, decel: int) -> int:
        v = min(CRUISE, self.stop_speed(abs(d), decel))
        return v if d > 0 else -v

    def _choose(self, taxi, tick, want_vx, want_vy, dx, dy):
        """The move closest to the wanted velocity that the safety check passes."""
        fx, fy, vx, vy = taxi["fx"], taxi["fy"], taxi["vx"], taxi["vy"]
        scored = []
        for ax in (-1, 0, 1):
            for ay in (-1, 0):
                nvx, nvy = self.velocity(vx, vy, ax, ay, tick)
                score = (abs(nvx - want_vx) + abs(nvy - want_vy),
                         (nvx * dx < 0) + (nvy * dy < 0), abs(ax))
                scored.append((score, ax, ay))
        scored.sort()
        for _, ax, ay in scored:
            if self.survives(fx, fy, vx, vy, ax, ay, tick):
                return ax, ay
        return self.brake(vx, vy)


def welcome(info: dict) -> None:
    global _me, _rules
    _me = info["id"]
    _rules = info["rules"]


def start(info: dict) -> None:
    """Build the fields, or keep the last ones when the map and rules are the same."""
    global _pilot, _rules
    _rules = info["rules"]
    if _pilot is None or _pilot.rows != info["map"] or _pilot.rules != _rules:
        _pilot = Pilot(info["map"], info["pads"], _rules)
        _pilot.rules = _rules


def target(pilot: Pilot, taxi: dict, passengers: list, tick: int):
    """`(pad, say)`: the pad to land on next and why, or `(None, say)`."""
    cell = (taxi["x"], taxi["y"])
    if taxi["passenger"] is not None:
        to = taxi["passenger"]["to"]
        return to, f"delivering {taxi['passenger']['id']} to {to}"
    left = _rules["round_ticks"] - tick + 1
    best = None  # (fare, ticks, passenger)
    for pas in passengers:
        there = pilot.ticks(pas["at"], cell)
        if there < 0 or there >= pas["expires_in"]:
            continue
        home = pilot.ticks(pas["to"], pilot.goal[pas["at"]])
        if home < 0 or there + home > left:
            continue
        ticks = there + home
        # best fare per tick, compared without division
        if best is None or pas["fare"] * best[1] > best[0] * ticks:
            best = (pas["fare"], ticks, pas)
    if best is not None:
        pas = best[2]
        return pas["at"], f"picking up {pas['id']} at {pas['at']}"
    if taxi["state"] == "landed":
        return None, "waiting"
    reachable = [(pilot.ticks(pid, cell), pid) for pid in pilot.pads]
    reachable = [r for r in reachable if r[0] >= 0]
    if not reachable:
        return None, "no pad in reach"
    return min(reachable)[1], "parking"


def choose(pilot: Pilot, taxi: dict, passengers: list, tick: int):
    """`(ax, ay, debug)` for one taxi."""
    if taxi["state"] == "crashed":
        return 0, 0, {"say": "crashed"}
    pid, say = target(pilot, taxi, passengers, tick)
    if pid is None:
        if taxi["state"] == "flying":
            vx, vy = taxi["vx"], taxi["vy"]
            ax, ay = pilot._choose(taxi, tick, 0, 0, -vx, -vy)
            return ax, ay, {"say": say}
        return 0, 0, {"say": say}
    if taxi["state"] == "landed":
        return 0, -1, {"say": f"takeoff, {say}"}
    ax, ay, path = pilot.fly(taxi, pid, tick)
    return ax, ay, {"say": say, "path": [[x, y] for x, y in path]}


def move(state: dict) -> dict:
    taxi = next(t for t in state["taxis"] if t["id"] == _me)
    ax, ay, debug = choose(_pilot, taxi, state["passengers"], state["tick"])
    return {"ax": ax, "ay": ay, "debug": debug}
