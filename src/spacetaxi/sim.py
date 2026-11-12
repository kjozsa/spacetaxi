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
    tick_ms: int = 200
    deadline_ms: int = 150
    round_ticks: int = 375
    gravity: int = 1
    gravity_every: int = 2
    max_speed: int = 3
    safe_landing_vy: int = 1
    crash_penalty_ticks: int = 15
    crash_cost: int = 25
    passenger_spawn_every: int = 20
    passenger_expires: int = 150
    fare_base: int = 50
    fare_per_cell: int = 2

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Taxi:
    id: str
    x: int
    y: int
    vx: int = 0
    vy: int = 0
    state: str = FLYING
    pad: str | None = None
    passenger: dict | None = None
    money: int = 0
    respawn_in: int = 0
    crashes: int = 0
    deliveries: int = 0
    landed_on: int = 0  # the tick of the latest landing

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "x": self.x,
            "y": self.y,
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
    appeared: int = 0  # the tick the passenger spawned on

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
        self.taxis = [Taxi(pid, *self._random_spawn()) for pid in players]
        self.passengers: list[Passenger] = []  # waiting passengers, oldest first
        self.next_passenger_id = 1

    def _random_spawn(self) -> tuple[int, int]:
        return self.rng.choice(self.map.spawns)

    def _cell(self, x: int, y: int) -> str:
        return self.map.rows[y][x]

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
            if taxi.state == FLYING:
                taxi.vx += ax
                taxi.vy += ay
                if tick % self.rules.gravity_every == 0:
                    taxi.vy += self.rules.gravity
                taxi.vx = _clamp(taxi.vx, self.rules.max_speed)
                taxi.vy = _clamp(taxi.vy, self.rules.max_speed)
            elif taxi.state == LANDED and ay == -1:
                events.append({"type": "takeoff", "taxi": taxi.id, "pad": taxi.pad})
                taxi.state, taxi.pad, taxi.vx, taxi.vy = FLYING, None, 0, -1

        # 2. move
        for taxi in self.taxis:
            if taxi.state == FLYING:
                self._move(taxi, events)
        landed_now = [t for t in self.taxis if t.state == LANDED and t.landed_on == tick]

        # 3. pickups and deliveries
        self._deliver(landed_now, events)
        self._pick_up(landed_now, tick, events)

        # 4.1 respawns
        for taxi in crashed_before:
            taxi.respawn_in -= 1
            if taxi.respawn_in <= 0:
                taxi.x, taxi.y = self._random_spawn()
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
            p = self._spawn_passenger(tick)
            events.append({"type": "spawn", "passenger": p.id, "at": p.at, "to": p.to})

        self.tick += 1
        self.events = events
        return [dict(e) for e in events]

    def _move(self, taxi: Taxi, events: list[dict]) -> None:
        for sx, sy in path_steps(taxi.vx, taxi.vy):
            nx, ny = taxi.x + sx, taxi.y + sy
            cell = self._cell(nx, ny)
            if cell == WALL:
                self._crash(taxi, "wall", events)
                return
            if cell in PAD_IDS:
                if sy != 1:
                    self._crash(taxi, "wall", events)
                elif taxi.vx == 0 and taxi.vy <= self.rules.safe_landing_vy:
                    taxi.state, taxi.pad, taxi.vx, taxi.vy = LANDED, cell, 0, 0
                    taxi.landed_on = self.tick
                    events.append({"type": "landed", "taxi": taxi.id, "pad": cell})
                else:
                    self._crash(taxi, "hard_landing", events)
                return
            taxi.x, taxi.y = nx, ny

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

    def _pick_up(self, landed: list[Taxi], tick: int, events: list[dict]) -> None:
        for p in sorted(self.passengers, key=lambda p: p.id):
            if p.appeared >= tick:
                continue
            candidates = [t for t in landed if t.passenger is None and t.pad == p.at]
            if not candidates:
                continue
            best = min(abs(t.x - p.spot_x) for t in candidates)
            closest = [t for t in candidates if abs(t.x - p.spot_x) == best]
            taxi = closest[0] if len(closest) == 1 else self.rng.choice(closest)
            taxi.passenger = {"id": p.id, "to": p.to, "fare": p.fare}
            self.passengers.remove(p)
            events.append({"type": "pickup", "taxi": taxi.id, "passenger": p.id})

    def _spawn_passenger(self, tick: int) -> Passenger:
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
            appeared=tick,
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
