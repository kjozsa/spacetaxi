"""The arena server's milestone race (docs/rules.md, Milestones) and its benchmarks.

Every `hello`, tick and `round_end` is checked for milestones (`milestones.Tracker`), and points
go to whoever reaches each first (`milestones.Standings`, saved to `standings` after every
change). Ticks of a wave's arenas are played on one clock, so a milestone reached on the same
tick in two arenas is a tie: candidates wait in `pending` until every arena of their wave has
played that tick. Every award is logged and sent to spectators as `milestone`. The house bots'
benchmarks are simulated in a thread at start, for every map, unless `benchmarks` gives them.
"""

import asyncio
import json
from pathlib import Path

from .milestones import BENCHMARK_BOTS, BENCHMARK_ROUNDS, MILESTONES, Standings
from .milestones import benchmarks as simulate_benchmarks


class Race:
    """The standings, the pending milestones and the benchmarks of `ArenaServer`, mixed into it.
    The class mixing it in sets `maps`, `map_name`, `rules`, `say`, `arenas` and `spectators`,
    and has `_spawn`; it calls `init_race` first."""

    def init_race(self, standings: str | Path | None,
                  benchmarks: dict[str, dict[str, int]] | None) -> None:
        self.standings = Standings(standings)
        # per map, the money beat_greedy / beat_pilot must beat; None until simulated
        self.map_benchmarks: dict[str, dict[str, int | None]] = {
            name: {bot: None for bot in BENCHMARK_BOTS} for name in self.maps}
        for name, values in (benchmarks or {}).items():
            self.map_benchmarks[name].update(values)
        self.pending: list[tuple] = []  # (wave, tick, milestone, name, key) not yet scored

    @property
    def benchmarks(self) -> dict[str, int | None]:
        """The benchmarks of the current (or last) wave's map."""
        return self.map_benchmarks[self.map_name]

    def start_benchmarks(self) -> None:
        """Print the given benchmarks and start simulating the missing ones."""
        for name, values in self.map_benchmarks.items():
            if all(v is not None for v in values.values()):
                self.say_benchmarks(name, "given")
        if any(v is None for values in self.map_benchmarks.values() for v in values.values()):
            self._spawn(self.find_benchmarks())

    def say_benchmarks(self, name: str, how: str) -> None:
        self.say(f"benchmarks on {name} ({how}): " + " ".join(
            f"{bot}={money}" for bot, money in self.map_benchmarks[name].items()))

    async def find_benchmarks(self) -> None:
        """Simulate the missing benchmarks in a thread, map by map, so the arena runs
        meanwhile."""
        for name, values in self.map_benchmarks.items():
            missing = [bot for bot, money in values.items() if money is None]
            if not missing:
                continue
            self.say(f"benchmarks: simulating {', '.join(missing)} on {name} for the best of "
                     f"{BENCHMARK_ROUNDS} rounds ...")
            found = await asyncio.to_thread(simulate_benchmarks, self.maps[name], self.rules,
                                            missing)
            values.update(found)
            self.say_benchmarks(name, f"simulated, best round of {BENCHMARK_ROUNDS}")

    def award(self, milestone: str, entries: list[tuple[str, int]]) -> None:
        """Score `entries` reaching `milestone` together (see `Standings.award`), log every
        award, tell the spectators and save the standings."""
        entries = [e for e in entries if e[0].lower() not in self.standings.banned]
        awards = self.standings.award(milestone, entries)
        for a in awards:
            self.say(f"* milestone {a['milestone']}: {a['name']} P{a['place']} +{a['points']}")
            text = json.dumps({"type": "milestone", **a}, separators=(",", ":"))
            for spectator in self.spectators:
                spectator.send(text)
        if awards:
            self.standings.save()

    def flush(self, wave: int) -> None:
        """Score the pending milestones of `wave` from every tick that all of its running
        arenas have played, tick by tick: the names of one tick and milestone tie."""
        running = [a.stepped for a in self.arenas if a.wave == wave]
        upto = min(running) if running else None
        ready = [p for p in self.pending if p[0] == wave and (upto is None or p[1] <= upto)]
        if not ready:
            return
        self.pending = [p for p in self.pending if p not in ready]
        moments = sorted({(p[1], MILESTONES.index(p[2])) for p in ready})
        for tick, m in moments:
            milestone = MILESTONES[m]
            self.award(milestone, [(p[3], p[4]) for p in ready
                                   if p[1] == tick and p[2] == milestone])
