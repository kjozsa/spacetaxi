"""The milestone race of the arena (docs/rules.md, Milestones): what each milestone is, how the
server spots it, the F1 points of the standings and the house bots' benchmarks.

`Tracker` watches one round and names who reached which milestone, tick by tick and at the
round end; `Standings` turns those into places and points (the first name to reach a milestone
scores most) and keeps them in a JSON file across restarts; `benchmarks` simulates the house
bots on the served map for the money that `beat_greedy` and `beat_pilot` must beat.
"""

import json
import os
from dataclasses import replace
from pathlib import Path

from .maps import Map
from .sim import FLYING, Rules

MILESTONES = ("hello", "liftoff", "touchdown", "fare", "paid", "clean", "hat_trick",
              "beat_greedy", "beat_pilot")
ROUND_END = ("clean", "hat_trick", "beat_greedy", "beat_pilot")  # decided at round_end
POINTS = (25, 18, 15, 12, 10, 8, 6, 4, 2, 1)  # for the 1st to 10th name; later ones score 0
LIFTOFF_TICKS = 50  # ticks flying in one round, before the next crash, for `liftoff`
HAT_TRICK = 3  # deliveries in one round for `hat_trick`
MIN_TAXIS = 2  # taxis a round needs for the round-end milestones to count
BENCHMARK_BOTS = ("greedy", "pilot")
BENCHMARK_ROUNDS = 20
EVENT_MILESTONES = {"landed": "touchdown", "pickup": "fare", "deliver": "paid"}


def points(place: int) -> int:
    """The points for reaching a milestone in `place` (from 1)."""
    return POINTS[place - 1] if place <= len(POINTS) else 0


class Tracker:
    """The milestones reached in one round, by the round's non-human players."""

    def __init__(self, names: list[str], humans=()):
        self.taxis = len(names)
        self.names = [n for n in names if n not in set(humans)]
        self.streak = {n: 0 for n in self.names}  # flown ticks since round start or last crash

    def tick(self, states: dict[str, str], events: list[dict]) -> list[tuple[str, str]]:
        """`(milestone, name)` for every tick milestone reached in a tick, from each taxi's state
        after it and its events."""
        found = []
        crashed = {e.get("taxi") for e in events if e["type"] == "crash"}
        for name in self.names:
            if name in crashed:
                self.streak[name] = 0
            elif states.get(name) == FLYING:
                self.streak[name] += 1
            if self.streak[name] == LIFTOFF_TICKS:
                found.append(("liftoff", name))
        for event in events:
            milestone = EVENT_MILESTONES.get(event["type"])
            name = event.get("taxi")
            if milestone and name in self.streak and (milestone, name) not in found:
                found.append((milestone, name))
        return found

    def round_end(self, results: list[dict], benchmarks: dict) -> list[tuple[str, str, int]]:
        """`(milestone, name, money)` for every round-end milestone reached, from the
        `round_end` results; none in a round of fewer than `MIN_TAXIS` taxis. `benchmarks`
        maps `greedy` and `pilot` to their money, or to None while not known yet."""
        if self.taxis < MIN_TAXIS:
            return []
        found = []
        for r in results:
            name, money = r["id"], r["money"]
            if name not in self.streak:
                continue
            if r["crashes"] == 0 and r["deliveries"] >= 1:
                found.append(("clean", name, money))
            if r["deliveries"] >= HAT_TRICK:
                found.append(("hat_trick", name, money))
            for bot in BENCHMARK_BOTS:
                if benchmarks.get(bot) is not None and money > benchmarks[bot]:
                    found.append((f"beat_{bot}", name, money))
        return found


class Standings:
    """Who reached which milestone, in which place and for how many points; saved to `path`
    (JSON) after every change and loaded from it when it exists."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self.awards: dict[str, dict[str, dict]] = {}  # name -> milestone -> {place, points}
        if self.path and self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            try:
                for name, got in data["awards"].items():
                    self.awards[name] = {m: {"place": int(a["place"]),
                                             "points": int(a["points"])}
                                         for m, a in got.items()}
            except (AttributeError, KeyError, TypeError) as exc:
                raise ValueError(f"{self.path}: not a standings file ({exc!r})") from None

    def reached(self, milestone: str) -> int:
        """How many names have reached `milestone`."""
        return sum(milestone in got for got in self.awards.values())

    def has(self, name: str, milestone: str) -> bool:
        return milestone in self.awards.get(name, {})

    def award(self, milestone: str, entries: list[tuple[str, int]]) -> list[dict]:
        """Score names reaching `milestone` at the same moment: `entries` is `(name, key)`,
        a higher key placing first (the round money for round-end milestones, 0 otherwise).
        Names with the same key share the place and its points, and the next place is skipped.
        Names that already have it are left out. Returns the awards, best place first."""
        best: dict[str, int] = {}
        for name, key in entries:
            if not self.has(name, milestone):
                best[name] = max(key, best.get(name, key))
        place = self.reached(milestone) + 1
        out = []
        for key in sorted(set(best.values()), reverse=True):
            group = sorted(n for n, k in best.items() if k == key)
            for name in group:
                award = {"place": place, "points": points(place)}
                self.awards.setdefault(name, {})[milestone] = award
                out.append({"name": name, "milestone": milestone, **award})
            place += len(group)
        return out

    def save(self) -> None:
        if not self.path:
            return
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps({"awards": self.awards}, indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def table(self, leave_out=()) -> list[dict]:
        """One row per name but those in `leave_out`: most points first, then most milestones,
        then by name; equal points and milestones share the rank."""
        rows = [{"name": name, "points": sum(a["points"] for a in got.values()),
                 "milestones": {m: got[m] for m in MILESTONES if m in got}}
                for name, got in self.awards.items() if name not in leave_out]
        rows.sort(key=lambda r: (-r["points"], -len(r["milestones"]), r["name"]))
        for i, row in enumerate(rows):
            before = rows[i - 1] if i else None
            same = before and (before["points"], len(before["milestones"])) == (
                row["points"], len(row["milestones"]))
            row["rank"] = before["rank"] if same else i + 1
        return rows


def benchmarks(game_map: Map, rules: Rules, bots=BENCHMARK_BOTS,
               rounds: int = BENCHMARK_ROUNDS) -> dict[str, int]:
    """Each house bot's best money in one round (at least 1) over `rounds` seeded rounds on
    `game_map` against 3 greedy copies. Moves are not timed, so a busy machine does not make
    the house bots coast."""
    from .runner import run

    rules = replace(rules, deadline_ms=10 ** 9)
    out = {}
    for bot in bots:
        rounds_money = _RoundMoney()
        run(game_map, [bot, "greedy", "greedy", "greedy"], rounds, 0, rules, rounds_money)
        out[bot] = max([1, *rounds_money.money])
    return out


class _RoundMoney:
    """A replay stream for `run` that keeps the first player's money from each `round_end`."""

    def __init__(self):
        self.money: list[int] = []

    def write(self, line: str) -> None:
        if line.startswith('{"type":"round_end"'):
            self.money.append(json.loads(line)["results"][0]["money"])
