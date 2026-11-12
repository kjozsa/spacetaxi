"""The simulation: taxis, physics and crashes, as described in the Tick order of docs/rules.md.

Everything is integer arithmetic. All randomness comes from one RNG seeded at round start.
"""

import random
from dataclasses import asdict, dataclass

from .maps import PAD_IDS, WALL, Map

FLYING = "flying"
LANDED = "landed"
CRASHED = "crashed"


@dataclass(frozen=True)
class Rules:
    tick_ms: int = 100
    deadline_ms: int = 75
    round_ticks: int = 750
    units_per_cell: int = 32
    gravity: int = 2
    gravity_every: int = 1
    main_thrust: int = 4
    side_thrust: int = 2
    max_speed: int = 24
    safe_landing_vy: int = 6
    safe_landing_vx: int = 3
    taxi_half_width: int = 15
    taxi_half_height: int = 12
    crash_penalty_ticks: int = 30
    crash_cost: int = 25
    passenger_spawn_every: int = 40
    passenger_expires: int = 300
    fare_base: int = 50
    fare_per_cell: int = 2

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Taxi:
    id: str
    fx: int
    fy: int
    vx: int = 0
    vy: int = 0
    state: str = FLYING
    pad: str | None = None
    passenger: dict | None = None
    money: int = 0
    respawn_in: int = 0
    crashes: int = 0
    deliveries: int = 0

    x: int = 0  # cell of the centre, kept in step with fx, fy by World
    y: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "x": self.x,
            "y": self.y,
            "fx": self.fx,
            "fy": self.fy,
            "vx": self.vx,
            "vy": self.vy,
            "state": self.state,
            "pad": self.pad,
            "passenger": dict(self.passenger) if self.passenger else None,
            "money": self.money,
            "respawn_in": self.respawn_in,
        }


@dataclass
class Passenger:
    id: int
    at: str
    spot_x: int
    to: str
    fare: int
    expires_in: int

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "at": self.at,
            "spot_x": self.spot_x,
            "to": self.to,
            "fare": self.fare,
            "expires_in": self.expires_in,
        }


def parse_move(move) -> tuple[int, int]:
    """A move dict to `(ax, ay)`; anything invalid (or None) coasts as `(0, 0)`."""
    if not isinstance(move, dict):
        return 0, 0
    ax, ay = move.get("ax"), move.get("ay")
    for a in (ax, ay):
        if type(a) is not int or a not in (-1, 0, 1):
            return 0, 0
    return ax, ay


def _sign(n: int) -> int:
    return (n > 0) - (n < 0)


def _clamp(n: int, limit: int) -> int:
    return max(-limit, min(limit, n))


def path_steps(vx: int, vy: int):
    """The 4-connected Bresenham steps `(sx, sy)` from the origin to `(vx, vy)`."""
    dx, dy = abs(vx), abs(vy)
    i = j = 0
    while i < dx or j < dy:
        if j >= dy or (i < dx and (2 * i + 1) * dy <= (2 * j + 1) * dx):
            i += 1
            yield _sign(vx), 0
        else:
            j += 1
            yield 0, _sign(vy)


class World:
    def __init__(self, map: Map, rules: Rules | None = None, seed: int = 0, players=()):
        self.map = map
        self.rules = rules or Rules()
        self.rng = random.Random(seed)
        self.tick = 1
        self.events: list[dict] = []
        self.taxis = []
        for pid in players:
            taxi = Taxi(pid, 0, 0)
            self._place(taxi, *self._random_spawn())
            self.taxis.append(taxi)
        self.passengers: list[Passenger] = []  # waiting passengers, oldest first
        self.next_passenger_id = 1

    def _random_spawn(self) -> tuple[int, int]:
        return self.rng.choice(self.map.spawns)

    def _cell(self, x: int, y: int) -> str:
        if x < 0 or y < 0 or y >= self.map.height or x >= self.map.width:
            return WALL
        return self.map.rows[y][x]

    def _place(self, taxi: Taxi, cx: int, cy: int) -> None:
        u = self.rules.units_per_cell
        taxi.fx, taxi.fy = cx * u + u // 2, cy * u + u // 2
        self._sync(taxi)

    def _sync(self, taxi: Taxi) -> None:
        u = self.rules.units_per_cell
        taxi.x, taxi.y = taxi.fx // u, taxi.fy // u

    def _box_cells(self, fx: int, fy: int):
        """Rows of `(cell, x, y)` the taxi's box covers, top row first."""
        r, u = self.rules, self.rules.units_per_cell
        x0, x1 = (fx - r.taxi_half_width) // u, (fx + r.taxi_half_width) // u
        y0, y1 = (fy - r.taxi_half_height) // u, (fy + r.taxi_half_height) // u
        return [[(self._cell(x, y), x, y) for x in range(x0, x1 + 1)] for y in range(y0, y1 + 1)]

    def state(self) -> dict:
        """The `tick` message: state after the previous tick and that tick's events."""
        return {
            "type": "tick",
            "tick": self.tick,
            "taxis": [t.to_dict() for t in self.taxis],
            "passengers": [p.to_dict() for p in self.passengers],
            "events": [dict(e) for e in self.events],
        }

    def step(self, moves: dict | None = None) -> list[dict]:
        """Run one tick. `moves` maps a player id to its move dict. Returns the tick's events."""
        moves = moves or {}
        tick = self.tick
        events: list[dict] = []
        crashed_before = [t for t in self.taxis if t.state == CRASHED]

        # 1. apply moves
        for taxi in self.taxis:
            ax, ay = parse_move(moves.get(taxi.id))
            r = self.rules
            if taxi.state == FLYING:
                taxi.vx += ax * r.side_thrust
                if ay == -1:  # there is no down thruster: ay = 1 does nothing
                    taxi.vy -= r.main_thrust
                if tick % r.gravity_every == 0:
                    taxi.vy += r.gravity
                taxi.vx = _clamp(taxi.vx, r.max_speed)
                taxi.vy = _clamp(taxi.vy, r.max_speed)
            elif taxi.state == LANDED and ay == -1:
                events.append({"type": "takeoff", "taxi": taxi.id, "pad": taxi.pad})
                taxi.state, taxi.pad = FLYING, None
                taxi.vx, taxi.vy = 0, r.gravity - r.main_thrust

        # 2. move
        flying = [t for t in self.taxis if t.state == FLYING]
        for taxi in flying:
            self._move(taxi, events)
        landed_now = [t for t in flying if t.state == LANDED]

        # 3. pickups and deliveries
        self._deliver(landed_now, events)
        self._pick_up(landed_now, events)

        # 4.1 respawns
        for taxi in crashed_before:
            taxi.respawn_in -= 1
            if taxi.respawn_in <= 0:
                self._place(taxi, *self._random_spawn())
                taxi.state, taxi.vx, taxi.vy, taxi.respawn_in = FLYING, 0, 0, 0
                events.append({"type": "respawn", "taxi": taxi.id, "x": taxi.x, "y": taxi.y})

        # 4.2 expiry
        for p in list(self.passengers):
            p.expires_in -= 1
            if p.expires_in <= 0:
                self.passengers.remove(p)
                events.append({"type": "expired", "passenger": p.id})

        # 4.3 spawn
        if (
            tick % self.rules.passenger_spawn_every == 0
            and len(self.passengers) < len(self.taxis) + 2
        ):
            p = self._spawn_passenger()
            events.append({"type": "spawn", "passenger": p.id, "at": p.at, "to": p.to})

        self.tick += 1
        self.events = events
        return [dict(e) for e in events]

    def _move(self, taxi: Taxi, events: list[dict]) -> None:
        r = self.rules
        for sx, sy in path_steps(taxi.vx, taxi.vy):
            nx, ny = taxi.fx + sx, taxi.fy + sy
            rows = self._box_cells(nx, ny)
            solid = [(c, x, y) for row in rows for (c, x, y) in row if c == WALL or c in PAD_IDS]
            if solid:
                bottom = rows[-1]
                pads = [c for (c, _, _) in bottom if c in PAD_IDS]
                touchdown = (sy == 1 and pads and all(c in PAD_IDS for (c, _, _) in solid)
                             and all(y == bottom[0][2] for (_, _, y) in solid))
                if not touchdown:
                    self._crash(taxi, "wall", events)
                elif abs(taxi.vx) <= r.safe_landing_vx and taxi.vy <= r.safe_landing_vy:
                    under = self._cell(taxi.fx // r.units_per_cell, bottom[0][2])
                    pad = under if under in PAD_IDS else pads[0]
                    taxi.state, taxi.pad, taxi.vx, taxi.vy = LANDED, pad, 0, 0
                    self._sync(taxi)
                    events.append({"type": "landed", "taxi": taxi.id, "pad": pad})
                else:
                    self._crash(taxi, "hard_landing", events)
                return
            taxi.fx, taxi.fy = nx, ny
            self._sync(taxi)

    def _deliver(self, landed: list[Taxi], events: list[dict]) -> None:
        for taxi in landed:
            p = taxi.passenger
            if p is not None and p["to"] == taxi.pad:
                taxi.money += p["fare"]
                taxi.passenger = None
                taxi.deliveries += 1
                events.append(
                    {"type": "deliver", "taxi": taxi.id, "passenger": p["id"], "fare": p["fare"]}
                )

    def _pick_up(self, landed: list[Taxi], events: list[dict]) -> None:
        # Only taxis that touched down this tick are candidates, and every waiting passenger
        # spawned on an earlier tick (spawns come after this step), so campers never board.
        for p in list(self.passengers):
            candidates = [t for t in landed if t.passenger is None and t.pad == p.at]
            if not candidates:
                continue
            best = min(abs(t.x - p.spot_x) for t in candidates)
            closest = [t for t in candidates if abs(t.x - p.spot_x) == best]
            taxi = closest[0] if len(closest) == 1 else self.rng.choice(closest)
            taxi.passenger = {"id": p.id, "to": p.to, "fare": p.fare}
            self.passengers.remove(p)
            events.append({"type": "pickup", "taxi": taxi.id, "passenger": p.id})

    def _spawn_passenger(self) -> Passenger:
        pads = self.map.pads
        at = self.rng.choice(pads)
        spot_x = self.rng.randrange(at.x, at.x + at.len)
        to = self.rng.choice([pad for pad in pads if pad.id != at.id])
        distance = abs((at.x + at.len // 2) - (to.x + to.len // 2)) + abs(at.y - to.y)
        p = Passenger(
            id=self.next_passenger_id,
            at=at.id,
            spot_x=spot_x,
            to=to.id,
            fare=self.rules.fare_base + self.rules.fare_per_cell * distance,
            expires_in=self.rules.passenger_expires,
        )
        self.next_passenger_id += 1
        self.passengers.append(p)
        return p

    def _crash(self, taxi: Taxi, cause: str, events: list[dict]) -> None:
        taxi.money -= self.rules.crash_cost
        taxi.crashes += 1
        events.append({"type": "crash", "taxi": taxi.id, "cause": cause})
        if taxi.passenger is not None:
            events.append({"type": "lost", "taxi": taxi.id, "passenger": taxi.passenger["id"]})
            taxi.passenger = None
        taxi.state, taxi.vx, taxi.vy = CRASHED, 0, 0
        taxi.respawn_in = self.rules.crash_penalty_ticks
