"""The "decent" house bot: plans safe touchdowns with a breadth-first search.

The search runs over flying states `(x, y, vx, vy, tick % gravity_every)`. Physics is
deterministic, so for every pad a backward breadth-first search from the states that touch down
safely on it gives the exact number of ticks to land there from any state, and the best move is
the one whose successor is closest. The tables are built in `start` (once per map) so `move`
only looks up at most nine successors per candidate target.

Target: the passenger's destination when carrying one, otherwise the waiting passenger with the
best fare per planned tick (ticks to reach them plus ticks to fly them home). With nothing to
do, the taxi lands on the nearest pad and waits there.
"""

from collections import deque

WALL = "#"
AIR = ".S"
DEFAULT_RULES = {"gravity": 1, "gravity_every": 2, "max_speed": 3, "safe_landing_vy": 1,
                 "round_ticks": 375}

_me = None
_rules = dict(DEFAULT_RULES)
_planner = None
_cache: dict = {}


class Planner:
    """Ticks-to-land tables for every pad of one map under one set of rules."""

    def __init__(self, rows, pads, rules=None):
        rules = {**DEFAULT_RULES, **(rules or {})}
        self.rows = list(rows)
        self.w = len(self.rows[0])
        self.m = rules["max_speed"]
        self.span = 2 * self.m + 1
        self.nv = self.span * self.span
        self.gp = rules["gravity_every"]
        self.g = rules["gravity"]
        self.safe = rules["safe_landing_vy"]
        self.pads = [p["id"] for p in pads]
        self.pad_index = {pid: k for k, pid in enumerate(self.pads)}
        self.pad_centre = {
            p["id"]: (p["y"] - 1) * self.w + p["x"] + p["len"] // 2 for p in pads
        }
        n_states = len(self.rows) * self.w * self.nv * self.gp
        self._outcome = {}
        preds = [[] for _ in range(n_states)]
        goals = [[] for _ in self.pads]
        for y, row in enumerate(self.rows):
            for x, ch in enumerate(row):
                if ch not in AIR:
                    continue
                cell = y * self.w + x
                for vi in range(self.nv):
                    for p in range(self.gp):
                        s = self.state(cell, vi, p)
                        for nxt in self.successors(cell, vi, p).values():
                            if nxt >= 0:
                                preds[nxt].append(s)
                            elif nxt <= -2:
                                goals[-2 - nxt].append(s)
        self.dist = []
        for k in range(len(self.pads)):
            dist = [-1] * n_states
            queue = deque()
            for s in goals[k]:
                if dist[s] < 0:
                    dist[s] = 1
                    queue.append(s)
            while queue:
                s = queue.popleft()
                d = dist[s] + 1
                for prev in preds[s]:
                    if dist[prev] < 0:
                        dist[prev] = d
                        queue.append(prev)
            self.dist.append(dist)

    def state(self, cell: int, vi: int, p: int) -> int:
        return (cell * self.nv + vi) * self.gp + p

    def vel_index(self, vx: int, vy: int) -> int:
        return (vy + self.m) * self.span + (vx + self.m)

    def _move(self, cell: int, vx: int, vy: int) -> int:
        """Where a flying taxi at `cell` with velocity `(vx, vy)` ends up after step 2: its new
        cell (`>= 0`), `-1` for a crash, or `-2 - k` for a safe landing on pad `k`."""
        key = (cell, vx, vy)
        if key in self._outcome:
            return self._outcome[key]
        x, y = cell % self.w, cell // self.w
        dx, dy = abs(vx), abs(vy)
        sx, sy = (vx > 0) - (vx < 0), (vy > 0) - (vy < 0)
        i = j = 0
        result = None
        while i < dx or j < dy:
            if j >= dy or (i < dx and (2 * i + 1) * dy <= (2 * j + 1) * dx):
                i += 1
                nx, ny, ystep = x + sx, y, False
            else:
                j += 1
                nx, ny, ystep = x, y + sy, True
            ch = self.rows[ny][nx]
            if ch in AIR:
                x, y = nx, ny
                continue
            if ch != WALL and ystep and sy == 1 and vx == 0 and vy <= self.safe:
                result = -2 - self.pad_index[ch]
            else:
                result = -1
            break
        if result is None:
            result = y * self.w + x
        self._outcome[key] = result
        return result

    def successors(self, cell: int, vi: int, p: int) -> dict:
        """`{(ax, ay): outcome}` for a flying state, outcome as in `_move` but with flying
        results turned into state indices."""
        vx, vy = vi % self.span - self.m, vi // self.span - self.m
        grav = self.g if p == 0 else 0
        np_ = (p + 1) % self.gp
        out = {}
        for ax in (-1, 0, 1):
            for ay in (-1, 0, 1):
                nvx = max(-self.m, min(self.m, vx + ax))
                nvy = max(-self.m, min(self.m, vy + ay + grav))
                res = self._move(cell, nvx, nvy)
                if res >= 0:
                    res = self.state(res, self.vel_index(nvx, nvy), np_)
                out[(ax, ay)] = res
        return out

    def takeoff_state(self, cell: int, p: int) -> int:
        """The flying state right after a landed taxi at `cell` takes off on a tick with
        parity `p`: one cell up, velocity `(0, -1)`."""
        return self.state(cell - self.w, self.vel_index(0, -1), (p + 1) % self.gp)

    def plan(self, taxi: dict, tick: int, pad: str):
        """`(ticks, (ax, ay))`: the fastest safe way to land on `pad`, or `None` if there is none.
        A taxi landed on `pad` has to take off and land again."""
        k = self.pad_index[pad]
        dist = self.dist[k]
        p = tick % self.gp
        cell = taxi["y"] * self.w + taxi["x"]
        if taxi["state"] == "landed":
            d = dist[self.takeoff_state(cell, p)]
            return (d + 1, (0, -1)) if d > 0 else None
        if taxi["state"] != "flying":
            return None
        vi = self.vel_index(taxi["vx"], taxi["vy"])
        best = None
        for action, nxt in self.successors(cell, vi, p).items():
            if nxt == -2 - k:
                d = 1
            elif nxt >= 0 and dist[nxt] > 0:
                d = dist[nxt] + 1
            else:
                continue
            if best is None or d < best[0]:
                best = (d, action)
        return best

    def pad_to_pad(self, a: str, b: str, tick: int) -> int:
        """Ticks to fly from landed on the centre of pad `a` to landed on pad `b`, or -1."""
        d = self.dist[self.pad_index[b]][self.takeoff_state(self.pad_centre[a], tick % self.gp)]
        return d + 1 if d > 0 else -1


def welcome(info: dict) -> None:
    global _me, _rules
    _me = info.get("id")
    _rules = {**DEFAULT_RULES, **info.get("rules", {})}


def start(info: dict) -> None:
    global _planner
    key = (tuple(info["map"]), tuple(sorted(_rules.items())))
    if key not in _cache:
        _cache[key] = Planner(info["map"], info["pads"], _rules)
    _planner = _cache[key]


def choose(planner: Planner, taxi: dict, passengers: list, tick: int, round_ticks: int):
    """`(ax, ay, debug)` for one taxi."""
    left = round_ticks - tick + 1
    if taxi["passenger"] is not None:
        to = taxi["passenger"]["to"]
        plan = planner.plan(taxi, tick, to)
        if plan is not None:
            return (*plan[1], f"delivering to {to} in {plan[0]}")
    else:
        best = None  # (fare, ticks, plan, passenger)
        for pas in passengers:
            plan = planner.plan(taxi, tick, pas["at"])
            if plan is None or plan[0] >= pas["expires_in"] + 1:
                continue
            home = planner.pad_to_pad(pas["at"], pas["to"], tick + plan[0])
            if home < 0 or plan[0] + home > left:
                continue
            ticks = plan[0] + home
            # best fare per tick, compared without division
            if best is None or pas["fare"] * best[1] > best[0] * ticks:
                best = (pas["fare"], ticks, plan, pas)
        if best is not None:
            pas = best[3]
            return (*best[2][1], f"picking up {pas['id']} at {pas['at']} in {best[2][0]}")
    if taxi["state"] != "flying":
        return 0, 0, "waiting"
    nearest = None
    for pad in planner.pads:
        plan = planner.plan(taxi, tick, pad)
        if plan is not None and (nearest is None or plan[0] < nearest[0]):
            nearest = plan
    if nearest is None:
        return 0, 0, "no safe landing"
    return (*nearest[1], "parking")


def move(state: dict) -> dict:
    if _planner is None:
        return {"ax": 0, "ay": 0}
    taxis = state["taxis"]
    taxi = next((t for t in taxis if t["id"] == _me), taxis[0] if len(taxis) == 1 else None)
    if taxi is None:
        return {"ax": 0, "ay": 0}
    ax, ay, debug = choose(_planner, taxi, state["passengers"], state["tick"],
                           _rules["round_ticks"])
    return {"ax": ax, "ay": ay, "debug": debug}
