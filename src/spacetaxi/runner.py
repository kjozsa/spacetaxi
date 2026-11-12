"""Headless runner: plays rounds in lockstep with in-process Python bots, no real-time waiting.

A bot is a Python module with `move(state: dict) -> dict`, where `state` is the `tick` message
from docs/rules.md. Optional hooks: `welcome(info)` gets the `welcome` message once, and
`start(info)` gets the `round_start` message once per round. Each player loads its own copy of
the module, so two players using the same bot share no module state.

Each `move` call is timed; one that takes longer than `deadline_ms` counts as a timeout and the
taxi coasts. A move that raises or returns an invalid answer counts as an error and coasts.
"""

import importlib.util
import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

from .maps import Map
from .sim import World, Rules

BOTS_DIR = Path(__file__).resolve().parent / "bots"

COAST = {"ax": 0, "ay": 0}


class BotError(ValueError):
    """A bot that cannot be found or loaded."""


def bot_path(name_or_path: str) -> Path:
    """A bundled bot name (e.g. `random`) or a path to a `.py` file."""
    if "/" in name_or_path or "\\" in name_or_path or name_or_path.endswith(".py"):
        path = Path(name_or_path)
    else:
        path = BOTS_DIR / f"{name_or_path}.py"
        if name_or_path.startswith("_") or not path.is_file():
            raise BotError(f"unknown bot {name_or_path!r}")
    if not path.is_file():
        raise BotError(f"bot file not found: {name_or_path}")
    return path


_load_count = 0


def load_bot(name_or_path: str) -> ModuleType:
    """Load a fresh copy of a bot module."""
    global _load_count
    path = bot_path(name_or_path)
    _load_count += 1
    spec = importlib.util.spec_from_file_location(f"_spacetaxi_bot_{_load_count}", path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise BotError(f"bot {name_or_path!r} failed to load: {exc!r}") from exc
    if not callable(getattr(module, "move", None)):
        raise BotError(f"bot {name_or_path!r} has no move(state) function")
    return module


def is_valid_move(move) -> bool:
    """True if `move` is a dict whose `ax` and `ay` are each one of the ints -1, 0, 1."""
    if not isinstance(move, dict):
        return False
    for key in ("ax", "ay"):
        a = move.get(key)
        if type(a) is not int or a not in (-1, 0, 1):
            return False
    return True


def player_ids(names: list[str]) -> list[str]:
    """Taxi ids from bot names: the file stem, numbered `-1`, `-2`, ... when repeated."""
    stems = [Path(n).stem if n.endswith(".py") else Path(n).name for n in names]
    ids = []
    for i, stem in enumerate(stems):
        if stems.count(stem) > 1:
            ids.append(f"{stem}-{stems[:i + 1].count(stem)}")
        else:
            ids.append(stem)
    return ids


@dataclass
class Stats:
    name: str
    rounds: int = 0
    money: int = 0
    deliveries: int = 0
    wall: int = 0
    hard_landing: int = 0
    errors: int = 0
    timeouts: int = 0

    def to_dict(self) -> dict:
        n = self.rounds or 1
        return {
            "name": self.name,
            "rounds": self.rounds,
            "avg_money": round(self.money / n, 2),
            "avg_deliveries": round(self.deliveries / n, 2),
            "crashes": {"wall": self.wall, "hard_landing": self.hard_landing},
            "errors": self.errors,
            "timeouts": self.timeouts,
        }


@dataclass
class Player:
    id: str
    bot: ModuleType
    stats: Stats = field(init=False)

    def __post_init__(self):
        self.stats = Stats(self.id)

    def call(self, hook: str, info: dict) -> None:
        fn = getattr(self.bot, hook, None)
        if callable(fn):
            try:
                fn(info)
            except Exception:
                self.stats.errors += 1

    def ask(self, state: dict, deadline_ms: int) -> dict:
        """The move to apply, as `{"ax", "ay", "coast", "debug"?}`. `coast` is true when the
        answer was missing, late or invalid, and the move is then `0,0`. `debug` is the
        answer's debug text, when it has one."""
        start = time.perf_counter_ns()
        try:
            move = self.bot.move(state)
        except Exception:
            move = None
            failed = True
        else:
            failed = not is_valid_move(move)
        elapsed_ms = (time.perf_counter_ns() - start) // 1_000_000
        if elapsed_ms > deadline_ms:
            self.stats.timeouts += 1
            record = {**COAST, "coast": True}
        elif failed:
            self.stats.errors += 1
            record = {**COAST, "coast": True}
        else:
            record = {"ax": move["ax"], "ay": move["ay"], "coast": False}
        if isinstance(move, dict) and isinstance(move.get("debug"), str):
            record["debug"] = move["debug"]
        return record


def run(
    game_map: Map,
    bots: list[str],
    rounds: int,
    seed: int = 0,
    rules: Rules | None = None,
    replay=None,
) -> list[dict]:
    """Play `rounds` rounds and return one stats dict per bot. `replay` is an optional text
    stream that receives one JSON line per `round_start`, `tick` and `round_end` message; each
    `tick` line also has the spectator-only `moves` applied in that tick."""
    rules = rules or Rules()
    ids = player_ids(bots)
    players = [Player(pid, load_bot(name)) for pid, name in zip(ids, bots)]
    master = random.Random(seed)

    def emit(message: dict) -> None:
        if replay is not None:
            replay.write(json.dumps(message, separators=(",", ":")) + "\n")

    for player in players:
        player.call("welcome", {"type": "welcome", "id": player.id, "rules": rules.to_dict()})

    for round_no in range(1, rounds + 1):
        world = World(game_map, rules, seed=master.getrandbits(64), players=ids)
        random.seed(master.getrandbits(64))  # bots using the global random module replay too
        round_start = {
            "type": "round_start",
            "round": round_no,
            "map": list(game_map.rows),
            "pads": [p.to_dict() for p in game_map.pads],
            "spawns": [{"x": x, "y": y} for x, y in game_map.spawns],
            "players": list(ids),
            "rules": rules.to_dict(),
        }
        emit(round_start)
        for player in players:
            player.call("start", json.loads(json.dumps(round_start)))

        for _ in range(rules.round_ticks):
            state = world.state()
            records = {}
            for player in players:
                records[player.id] = player.ask(world.state(), rules.deadline_ms)
            emit({**state, "moves": records})
            moves = {pid: {"ax": r["ax"], "ay": r["ay"]} for pid, r in records.items()}
            for event in world.step(moves):
                if event["type"] == "crash":
                    stats = players[ids.index(event["taxi"])].stats
                    if event["cause"] == "wall":
                        stats.wall += 1
                    else:
                        stats.hard_landing += 1

        results = []
        for player, taxi in zip(players, world.taxis):
            player.stats.rounds += 1
            player.stats.money += taxi.money
            player.stats.deliveries += taxi.deliveries
            results.append(
                {"id": taxi.id, "money": taxi.money, "deliveries": taxi.deliveries,
                 "crashes": taxi.crashes}
            )
        emit({"type": "round_end", "round": round_no, "results": results})

    return [p.stats.to_dict() for p in players]


def format_table(results: list[dict]) -> str:
    headers = ["bot", "rounds", "avg_money", "avg_deliv", "wall", "hard_land", "errors",
               "timeouts"]
    rows = [
        [r["name"], str(r["rounds"]), f"{r['avg_money']:.2f}", f"{r['avg_deliveries']:.2f}",
         str(r["crashes"]["wall"]), str(r["crashes"]["hard_landing"]), str(r["errors"]),
         str(r["timeouts"])]
        for r in results
    ]
    widths = [max(len(row[i]) for row in [headers] + rows) for i in range(len(headers))]

    def line(cells):
        first = cells[0].ljust(widths[0])
        rest = [c.rjust(w) for c, w in zip(cells[1:], widths[1:])]
        return "  ".join([first] + rest)

    return "\n".join([line(headers)] + [line(row) for row in rows])
