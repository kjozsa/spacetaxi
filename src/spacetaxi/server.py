"""The arena server: bots connect over WebSocket and fly live rounds of up to 4 taxis.

A bot says `hello` on `/bot`, gets `welcome` and waits in one queue (`queued` tells it its
place and `starts_in`, the seconds until its round starts). Rounds run in waves: a wave starts
`fill_seconds` after the first bot joins an idle server, and again `fill_seconds` after every
arena of the previous wave has ended (the pause for the podium and the leaderboard). At wave
start the waiting bots are dealt into up to `max_arenas` arenas of at most 4, as even as
possible (7 bots play 4 and 3, 6 play 3 and 3); bots beyond that wait for the next wave. The
deal is a seeded shuffle that prefers bots who have met least so far and avoids repeating a
group of the previous wave, so groups mix and the leaderboard compares like with like. A bot
that joins mid-wave plays from the next wave; after `round_end` every bot of the round that is
still connected goes back to the end of the queue.

All arenas of a wave start together and tick every `tick_ms` on the same clock. A `move` counts when it names the tick being
played and arrives within `deadline_ms` of that tick being sent; anything else is dropped and the
taxi coasts. Every message to a bot goes through the bot's own outbox and sender task, so a slow
or vanished bot never holds up its arena or the others; a bot whose outbox backs up loses ticks.

A client whose `hello` has `"client": "human"` is a person flying by hand (the `/fly` page);
the server treats it as any bot, but marks it as human on the leaderboard (`human: true`), in the
log and to spectators (`humans` in `round_start`). Names given in `house` are the house bots,
there to make rounds and to be beaten: they play like any bot, but never reach a milestone (as
humans), are marked `house: true` on the leaderboard and to spectators (`house` in
`round_start`), and are left out of the standings.

`/` is the leaderboard page and `/leaderboard.json` its data. `/fly` is the page to fly in the
arena by hand: a `/bot` client in the browser with the neon viewer. `/watch` is the neon viewer fed
live from `/spectate`: a spectator socket that gets `spectate` (the number of arena slots), then
every arena's `round_start`, `tick` (in the replay's shape, with `moves`) and `round_end`, each
with `arena` (its slot, 1 to `max_arenas`) and `round` added. A spectator that connects mid-round
gets each running arena's `round_start` and latest `tick` first. Spectators send nothing. With `log`, each `round_end` is
appended to that file as one JSON line (with each taxi's `ticks` and `late` count) and the
leaderboard is rebuilt from it on start. With `replays`, each round is written to that directory
as a replay that `spacetaxi view` opens.

The milestone race (docs/rules.md, Milestones): every `hello`, tick and `round_end` is checked
for milestones (`milestones.Tracker`), and points go to whoever reaches each first
(`milestones.Standings`, saved to `standings` after every change). Ticks of a wave's arenas are
played on one clock, so a milestone reached on the same tick in two arenas is a tie: candidates
wait in `pending` until every arena of their wave has played that tick. Every award is logged
and sent to spectators as `milestone`. `/standings` is the projector page of the race and
`/standings.json` its data. `/card` is the participant card and `/brief` the agent brief
(`docs/participant-card.md`, `docs/brief.md`, rendered, with `address` filled in for HOST:PORT;
`/brief.md` is the raw Markdown). `/mission` is each participant's own page: their next
milestone only, from `docs/missions.md`, polling `/standings.json`. The house bots' benchmarks
are simulated in a thread at start, unless `benchmarks` gives them.
"""

import asyncio
import itertools
import json
import random
import re
import time
from pathlib import Path

from . import ws
from .maps import Map
from .milestones import (BENCHMARK_BOTS, BENCHMARK_ROUNDS, MILESTONES, POINTS, Standings,
                         Tracker)
from .milestones import benchmarks as simulate_benchmarks
from .runner import check_move, clean_debug
from .sim import Rules, World
from .viewer import (build_doc_html, build_fly_html, build_live_html, build_mission_html,
                     build_standings_html)

WEB_DIR = Path(__file__).parent / "web"

NAME = re.compile(r"[A-Za-z0-9_-]{1,12}")
PROTOCOL = 1
ARENA_SIZE = 4
MAX_ARENAS = 3
HELLO_TIMEOUT = 10  # seconds a new connection has to say hello
OUTBOX_TICKS = 8  # a tick is dropped for a bot that has this many messages still unsent
DEAL_TRIES = 200  # shuffles tried per deal; the one whose groups have met least wins


# the Markdown documents the server serves: path -> (file in docs/, page title)
DOC_PAGES = {"/card": ("participant-card.md", "Spacetaxi: participant card"),
             "/brief": ("brief.md", "Spacetaxi: brief"),
             "/brief.md": ("brief.md", "")}


def doc_text(name: str) -> str:
    """A file of `docs/`: packaged next to the code in a wheel, the repo's `docs/` otherwise."""
    for docs in (Path(__file__).parent / "docs", Path(__file__).resolve().parents[2] / "docs"):
        if (docs / name).is_file():
            return (docs / name).read_text(encoding="utf-8")
    raise FileNotFoundError(name)


def group_sizes(waiting: int, max_arenas: int = MAX_ARENAS) -> list[int]:
    """Arena sizes for a wave with `waiting` bots: at most 4 each, as even as possible, largest
    first; bots beyond `max_arenas` full arenas wait for the next wave."""
    n = min(waiting, ARENA_SIZE * max_arenas)
    if n <= 0:
        return []
    k = -(-n // ARENA_SIZE)
    return [n // k + (1 if i < n % k else 0) for i in range(k)]


def dumps(message: dict) -> str:
    return json.dumps(message, separators=(",", ":"))


class Bot:
    """One connected bot: its socket, its outbox and where it is (queue or arena)."""

    def __init__(self, name: str, sock: ws.WebSocket, backlog: int = OUTBOX_TICKS,
                 human: bool = False):
        self.name = name
        self.human = human  # a person flying by hand (`"client": "human"` in hello)
        self.backlog = backlog  # droppable messages are dropped at this many unsent
        self.sock = sock
        self.connected = True
        self.told: tuple[int, int, int] | None = None  # the last (position, waiting, starts_in)
        self.arena: "Arena | None" = None
        self.outbox: asyncio.Queue[str] = asyncio.Queue()
        self.sender = asyncio.create_task(self._send_loop())

    def send(self, message: dict | str, droppable: bool = False) -> None:
        """Queue a message for the bot without waiting. A `droppable` one (a tick) is dropped
        while the bot is behind."""
        if not self.connected or (droppable and self.outbox.qsize() >= self.backlog):
            return
        self.outbox.put_nowait(message if isinstance(message, str) else dumps(message))

    async def _send_loop(self) -> None:
        while True:
            text = await self.outbox.get()
            try:
                await self.sock.send(text)
            except (ConnectionError, OSError):
                return

    async def stop(self) -> None:
        self.connected = False
        self.sender.cancel()
        try:
            await self.sender
        except (asyncio.CancelledError, ConnectionError, OSError):
            pass


class Leaderboard:
    """Totals per name, from `round_end` log records."""

    def __init__(self, house=()):
        self.rows: dict[str, dict] = {}
        self.house = set(house)  # names marked as house bots

    def add(self, record: dict) -> None:
        for r in record["results"]:
            row = self.rows.setdefault(r["id"], {"rounds": 0, "money": 0, "deliveries": 0,
                                                 "crashes": 0, "ticks": 0, "late": 0})
            row["rounds"] += 1
            if r.get("human"):
                row["human"] = True
            for key in ("money", "deliveries", "crashes", "ticks", "late"):
                row[key] += r.get(key, 0)

    def table(self) -> list[dict]:
        """One row per name, best average money first."""
        out = []
        for name, row in self.rows.items():
            out.append({
                "name": name,
                "rounds": row["rounds"],
                "total_money": row["money"],
                "avg_money": round(row["money"] / row["rounds"], 2),
                "deliveries": row["deliveries"],
                "crashes": row["crashes"],
                "late_pct": round(100 * row["late"] / row["ticks"], 1) if row["ticks"] else 0.0,
                "human": row.get("human", False),
                "house": name in self.house,
            })
        out.sort(key=lambda r: (-r["avg_money"], -r["total_money"], r["name"]))
        return out


class Arena:
    """One live round: its world, its bots and the moves for the tick being played."""

    def __init__(self, server: "ArenaServer", number: int, bots: list[Bot], seed: int,
                 slot: int = 1, wave: int = 0):
        self.server = server
        self.number = number
        self.wave = wave
        self.slot = slot  # which of the server's arenas, 1 to max_arenas
        self.bots = bots
        self.names = [b.name for b in bots]
        self.humans = [b.name for b in bots if b.human]
        self.house = [n for n in self.names if n in server.house]
        self.rules = server.rules
        self.world = World(server.map, self.rules, seed=seed, players=self.names)
        self.playing = 0  # the tick whose moves are being collected, 0 before the first
        self.sent_ns = 0
        self.records: dict[str, dict] = {}
        self.late = {name: 0 for name in self.names}
        self.replay = None
        self.shown: dict[str, str] = {}  # the latest message of each type sent to spectators
        self.tracker = Tracker(self.names, [*self.humans, *self.house])
        self.benchmarks = dict(server.benchmarks)  # as at round start: known ones count
        self.stepped = 0  # the last tick played (round_ticks + 1 once the round has ended)
        for bot in bots:
            bot.arena = self

    def on_move(self, bot: Bot, move: dict, now: int) -> None:
        """A `move` from one of this arena's bots; kept only if it is the first valid, on-time
        answer to the tick being played."""
        if bot.name in self.records or not self.playing:
            return
        if now - self.sent_ns > self.rules.deadline_ms * 1_000_000:
            return
        if check_move(move, self.playing) != "ok":
            return
        record = {"ax": move["ax"], "ay": move["ay"], "coast": False}
        debug = clean_debug(move.get("debug"))
        if debug is not None:
            record["debug"] = debug
        self.records[bot.name] = record

    def emit(self, message: dict) -> None:
        """A message for the replay and the spectators (ticks with `moves`)."""
        if self.replay is not None:
            self.replay.write(dumps(message) + "\n")
        text = dumps({**message, "arena": self.slot, "round": self.number})
        self.shown[message["type"]] = text
        for spectator in self.server.spectators:
            spectator.send(text, droppable=message["type"] == "tick")

    def broadcast(self, message: dict, droppable: bool = False) -> None:
        text = dumps(message)
        for bot in self.bots:
            bot.send(text, droppable)

    def step(self, state: dict) -> None:
        """Play the tick `state` was sent for, with the moves that came in time."""
        records = {}
        for name in self.names:
            record = self.records.get(name)
            if record is None:
                self.late[name] += 1
                record = {"ax": 0, "ay": 0, "coast": True}
            records[name] = record
        self.emit({**state, "moves": records})
        events = self.world.step({n: {"ax": r["ax"], "ay": r["ay"]} for n, r in records.items()})
        found = self.tracker.tick({t.id: t.state for t in self.world.taxis}, events)
        for milestone, name in found:
            self.server.pending.append((self.wave, state["tick"], milestone, name, 0))
        self.stepped = state["tick"]
        self.server.flush(self.wave)

    async def run(self, t0: int | None = None) -> dict:
        """Play the round in real time, tick `k` at `t0 + k * tick_ms` (perf_counter_ns; the
        wave's shared clock); returns its log record."""
        rules = self.rules
        game_map = self.server.map
        round_start = {
            "type": "round_start",
            "round": self.number,
            "map": list(game_map.rows),
            "pads": [p.to_dict() for p in game_map.pads],
            "spawns": [{"x": x, "y": y} for x, y in game_map.spawns],
            "players": list(self.names),
            "rules": rules.to_dict(),
        }
        if self.server.replays is not None:
            path = self.server.replays / f"round-{self.number:05d}.jsonl"
            self.replay = open(path, "w", encoding="utf-8")
        try:
            self.emit({**round_start, "humans": list(self.humans),
                       "house": list(self.house)})
            self.broadcast(round_start)
            tick_ns = rules.tick_ms * 1_000_000
            if t0 is None:
                t0 = time.perf_counter_ns()
            state = None
            for k in range(1, rules.round_ticks + 2):
                delay = t0 + k * tick_ns - time.perf_counter_ns()
                if delay > 0:
                    await asyncio.sleep(delay / 1e9)
                if state is not None:
                    self.step(state)
                if k > rules.round_ticks:
                    break
                state = self.world.state()
                self.playing = state["tick"]
                self.records = {}
                self.sent_ns = time.perf_counter_ns()
                self.broadcast(state, droppable=True)
            self.playing = 0
            results = [{"id": t.id, "money": t.money, "deliveries": t.deliveries,
                        "crashes": t.crashes} for t in self.world.taxis]
            round_end = {"type": "round_end", "round": self.number, "results": results}
            self.emit(round_end)
            self.broadcast(round_end)
            for milestone, name, money in self.tracker.round_end(results, self.benchmarks):
                self.server.pending.append(
                    (self.wave, rules.round_ticks + 1, milestone, name, money))
            self.stepped = rules.round_ticks + 1
            self.server.flush(self.wave)
        finally:
            if self.replay is not None:
                self.replay.close()
        return {
            "type": "round_end",
            "round": self.number,
            "wave": self.wave,
            "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "results": [{**r, "ticks": rules.round_ticks, "late": self.late[r["id"]],
                         **({"human": True} if r["id"] in self.humans else {}),
                         **({"house": True} if r["id"] in self.house else {})}
                        for r in results],
        }


class ArenaServer:
    """The queue, the waves of arenas, the leaderboard and the WebSocket handler."""

    def __init__(self, game_map: Map, rules: Rules | None = None, fill_seconds: float = 10,
                 log: str | Path | None = None, replays: str | Path | None = None,
                 seed: int | None = None, max_arenas: int = MAX_ARENAS, say=None,
                 standings: str | Path | None = None, benchmarks: dict | None = None,
                 house=()):
        self.map = game_map
        self.rules = rules or Rules()
        self.fill_ns = int(fill_seconds * 1e9)
        self.log = Path(log) if log else None
        self.replays = Path(replays) if replays else None
        self.max_arenas = max_arenas
        self.rng = random.Random(seed) if seed is not None else random.SystemRandom()
        self.say = say or (lambda text: None)
        self.bots: dict[str, Bot] = {}  # connected bots by name
        self.queue: list[Bot] = []
        self.arenas: set[Arena] = set()
        self.spectators: set[Bot] = set()
        self.watch_page: bytes | None = None  # built on the first request
        self.fly_page: bytes | None = None
        self.tasks: set[asyncio.Task] = set()
        self.house = set(house)  # the house bots' names: they never score milestones
        self.leaderboard = Leaderboard(self.house)
        self.rounds = 0
        self.wave = 0
        self.wave_t0 = 0  # perf_counter_ns when the running wave's clock started
        self.next_wave_ns: int | None = None  # when the next wave starts; None while a wave runs
        self.met: dict[frozenset, int] = {}  # rounds each pair of names has shared
        self.last_groups: set[frozenset] = set()  # the previous wave's groups
        self.standings = Standings(standings)
        # the money beat_greedy / beat_pilot must beat; None until simulated
        self.benchmarks: dict[str, int | None] = {bot: None for bot in BENCHMARK_BOTS}
        self.benchmarks.update(benchmarks or {})
        self.pending: list[tuple] = []  # (wave, tick, milestone, name, key) not yet scored
        self.standings_page: bytes | None = None
        self.address: str | None = None  # HOST:PORT for the card and brief; set by start()
        if self.log and self.log.exists():
            with open(self.log, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    self.leaderboard.add(record)
                    self.rounds = max(self.rounds, record.get("round", 0))
                    self.wave = max(self.wave, record.get("wave", 0))
        if self.replays:
            self.replays.mkdir(parents=True, exist_ok=True)

    # --- HTTP

    def doc(self, name: str) -> str:
        """A file of `docs/` with the server's address filled in for HOST:PORT."""
        return doc_text(name).replace("HOST:PORT", self.address or "HOST:PORT")

    def http(self, path: str):
        if path in ("/", "/index.html"):
            return 200, "text/html; charset=utf-8", (WEB_DIR / "leaderboard.html").read_bytes()
        if path == "/leaderboard.json":
            data = {"leaderboard": self.leaderboard.table(), "rounds": self.rounds,
                    "wave": self.wave, "connected": len(self.bots), "waiting": len(self.queue),
                    "arenas": sorted([{"round": a.number, "arena": a.slot, "players": a.names,
                                       "tick": a.playing} for a in self.arenas],
                                     key=lambda a: a["round"])}
            return 200, "application/json", json.dumps(data).encode("utf-8")
        if path == "/standings":
            if self.standings_page is None:
                self.standings_page = build_standings_html().encode("utf-8")
            return 200, "text/html; charset=utf-8", self.standings_page
        if path == "/standings.json":
            data = {"standings": self.standings.table(self.house),
                    "milestones": list(MILESTONES), "points": list(POINTS),
                    "benchmarks": self.benchmarks,
                    "reached": {m: self.standings.reached(m) for m in MILESTONES}}
            return 200, "application/json", json.dumps(data).encode("utf-8")
        if path == "/mission":
            text = self.doc("missions.md")
            return 200, "text/html; charset=utf-8", build_mission_html(text).encode("utf-8")
        if path in DOC_PAGES:
            name, title = DOC_PAGES[path]
            text = self.doc(name)
            if path.endswith(".md"):
                return 200, "text/markdown; charset=utf-8", text.encode("utf-8")
            return 200, "text/html; charset=utf-8", build_doc_html(text, title).encode("utf-8")
        if path == "/watch":
            if self.watch_page is None:
                self.watch_page = build_live_html(self.map, self.rules).encode("utf-8")
            return 200, "text/html; charset=utf-8", self.watch_page
        if path == "/fly":
            if self.fly_page is None:
                self.fly_page = build_fly_html(self.map, self.rules).encode("utf-8")
            return 200, "text/html; charset=utf-8", self.fly_page
        return None

    async def start(self, host: str | None = None, port: int = 8000) -> asyncio.Server:
        server = await ws.serve({"/bot": self.handle, "/spectate": self.spectate}, self.http, host, port)
        if self.address is None:
            self.address = f"{host or 'localhost'}:{server.sockets[0].getsockname()[1]}"
        self._spawn(self.scheduler())
        if any(v is None for v in self.benchmarks.values()):
            self._spawn(self.find_benchmarks())
        else:
            self.say_benchmarks("given")
        return server

    def say_benchmarks(self, how: str) -> None:
        self.say("benchmarks (" + how + "): " + " ".join(
            f"{bot}={money}" for bot, money in self.benchmarks.items()))

    async def find_benchmarks(self) -> None:
        """Simulate the missing benchmarks in a thread, so the arena runs meanwhile."""
        missing = [bot for bot, money in self.benchmarks.items() if money is None]
        self.say(f"benchmarks: simulating {', '.join(missing)} on the map for the best of "
                 f"{BENCHMARK_ROUNDS} rounds ...")
        found = await asyncio.to_thread(simulate_benchmarks, self.map, self.rules, missing)
        self.benchmarks.update(found)
        self.say_benchmarks(f"simulated, best round of {BENCHMARK_ROUNDS}")

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    # --- the milestone race

    def award(self, milestone: str, entries: list[tuple[str, int]]) -> None:
        """Score `entries` reaching `milestone` together (see `Standings.award`), log every
        award, tell the spectators and save the standings."""
        awards = self.standings.award(milestone, entries)
        for a in awards:
            self.say(f"* milestone {a['milestone']}: {a['name']} P{a['place']} +{a['points']}")
            text = dumps({"type": "milestone", **a})
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

    # --- the queue and the waves

    def enqueue(self, bot: Bot) -> None:
        bot.told = None
        self.queue.append(bot)
        if self.next_wave_ns is None and not self.arenas:
            self.next_wave_ns = time.perf_counter_ns() + self.fill_ns
        self.fill()
        self.tell_positions()  # every bot hears `queued`, even one whose round starts now

    def round_ns(self) -> int:
        return (self.rules.round_ticks + 1) * self.rules.tick_ms * 1_000_000

    def tell_positions(self) -> None:
        """Send `queued` to every waiting bot whose place or start time changed."""
        waiting = len(self.queue)
        now = time.perf_counter_ns()
        if self.next_wave_ns is not None:
            first = self.next_wave_ns
        else:  # a wave is running: the next starts a pause after its rounds end
            first = self.wave_t0 + self.round_ns() + self.fill_ns
        per_wave = ARENA_SIZE * self.max_arenas
        for i, bot in enumerate(self.queue, 1):
            at = first + (i - 1) // per_wave * (self.round_ns() + self.fill_ns)
            starts_in = max(0, -(-(at - now) // 1_000_000_000))
            told = (i, waiting, starts_in)
            if bot.told != told:
                bot.told = told
                bot.send({"type": "queued", "position": i, "waiting": waiting,
                          "starts_in": starts_in})

    def deal(self, names: list[str]) -> list[list[str]]:
        """Split `names` into the wave's groups (`group_sizes`; names beyond them are left
        out, they wait): of `DEAL_TRIES` seeded shuffles, the one whose groups repeat fewest of the
        previous wave's groups, then hold the most pairs who never met, then the fewest past
        meetings. Counts the new meetings."""
        sizes = group_sizes(len(names), self.max_arenas)
        names = names[:sum(sizes)]  # the longest waiting play
        best, best_cost = None, None
        for _ in range(DEAL_TRIES):
            order = sorted(names)  # the shuffle depends on the seed, not on the queue order
            self.rng.shuffle(order)
            groups, at = [], 0
            for size in sizes:
                groups.append(order[at:at + size])
                at += size
            repeats = sum(frozenset(g) in self.last_groups for g in groups)
            met = [self.met.get(frozenset(pair), 0)
                   for g in groups for pair in itertools.combinations(g, 2)]
            cost = (repeats, -met.count(0), sum(met))
            if best_cost is None or cost < best_cost:
                best, best_cost = groups, cost
        for g in best:
            for pair in itertools.combinations(g, 2):
                key = frozenset(pair)
                self.met[key] = self.met.get(key, 0) + 1
        self.last_groups = {frozenset(g) for g in best}
        return best

    def fill(self) -> None:
        """Start the next wave when it is due: every waiting bot (up to the arenas' room) is
        dealt into arenas that all start on one clock."""
        self.queue = [b for b in self.queue if b.connected]
        if self.next_wave_ns is None or time.perf_counter_ns() < self.next_wave_ns:
            return
        if not self.queue:
            self.next_wave_ns = None
            return
        by_name = {b.name: b for b in self.queue}
        groups = self.deal([b.name for b in self.queue])
        dealt = {name for g in groups for name in g}
        self.queue = [b for b in self.queue if b.name not in dealt]
        self.wave += 1
        self.next_wave_ns = None
        self.say(f"wave {self.wave}: " + " | ".join(", ".join(g) for g in groups))
        arenas = []
        for slot, group in enumerate(groups, 1):
            self.rounds += 1
            arena = Arena(self, self.rounds, [by_name[n] for n in group],
                          self.rng.getrandbits(63), slot, self.wave)
            self.arenas.add(arena)
            arenas.append(arena)
            self.say(f"round {arena.number}: {', '.join(arena.names)}")
        self.wave_t0 = time.perf_counter_ns()
        for arena in arenas:  # every round_start goes out now, before any arena's first tick
            self._spawn(self.play(arena, self.wave_t0))
        self.tell_positions()

    async def scheduler(self) -> None:
        """Start the next wave when its time is up."""
        while True:
            await asyncio.sleep(0.02)
            if self.next_wave_ns is not None:
                self.fill()

    async def play(self, arena: Arena, t0: int | None = None) -> None:
        try:
            record = await arena.run(t0)
            self.leaderboard.add(record)
            if self.log:
                with open(self.log, "a", encoding="utf-8") as f:
                    f.write(dumps(record) + "\n")
            self.say(f"round {arena.number} over: " + ", ".join(
                f"{r['id']} {r['money']}" for r in record["results"]))
        finally:
            self.arenas.discard(arena)
            self.flush(arena.wave)
            for bot in arena.bots:
                bot.arena = None
                if bot.connected:
                    bot.told = None
                    self.queue.append(bot)
            self.queue = [b for b in self.queue if b.connected]
            if not self.arenas:  # the wave is over: the next one after the pause
                self.next_wave_ns = time.perf_counter_ns() + self.fill_ns if self.queue else None
            self.tell_positions()
            self.fill()

    # --- the WebSocket handlers

    async def spectate(self, sock: ws.WebSocket, path: str) -> None:
        """A spectator (the `/watch` page): every arena's messages, nothing expected back."""
        spectator = Bot("", sock, backlog=OUTBOX_TICKS * self.max_arenas)
        spectator.send({"type": "spectate", "arenas": self.max_arenas})
        for arena in sorted(self.arenas, key=lambda a: a.number):
            for kind in ("round_start", "tick", "round_end"):
                if kind in arena.shown:
                    spectator.send(arena.shown[kind])
        self.spectators.add(spectator)
        try:
            while await sock.recv() is not None:
                pass
        finally:
            self.spectators.discard(spectator)
            await spectator.stop()

    async def error(self, sock: ws.WebSocket, code: str, message: str) -> None:
        try:
            await sock.send(dumps({"type": "error", "code": code, "message": message}))
        except ConnectionError:
            pass

    async def handle(self, sock: ws.WebSocket, path: str) -> None:
        try:
            async with asyncio.timeout(HELLO_TIMEOUT):
                text = await sock.recv()
        except TimeoutError:
            return await self.error(sock, "bad_message", f"no hello within {HELLO_TIMEOUT} s")
        if text is None:
            return
        hello = parse(text)
        if hello is None or hello.get("type") != "hello":
            return await self.error(sock, "bad_message", "the first message must be hello")
        name = hello.get("name")
        if not isinstance(name, str) or not NAME.fullmatch(name):
            return await self.error(sock, "bad_name", "name: 1 to 12 characters of A-Z a-z 0-9 _ -")
        if type(hello.get("protocol")) is not int or hello["protocol"] != PROTOCOL:
            return await self.error(sock, "bad_protocol",
                                    f"this server speaks protocol {PROTOCOL}")
        if name in self.bots:
            return await self.error(sock, "name_taken", f"name {name!r} is already connected")
        human = hello.get("client") == "human"
        bot = Bot(name, sock, human=human)
        self.bots[name] = bot
        house = name in self.house
        self.say(f"+ {name} joined from {sock.peer}" + (" (human)" if human else "")
                 + (" (house)" if house else ""))
        bot.send({"type": "welcome", "id": name, "rules": self.rules.to_dict()})
        if not human and not house:
            self.award("hello", [(name, 0)])
        self.enqueue(bot)
        try:
            while (text := await sock.recv()) is not None:
                now = time.perf_counter_ns()
                msg = parse(text)
                if msg is None or msg.get("type") != "move":
                    await bot.stop()
                    await self.error(sock, "bad_message",
                                     "expected a move" if msg else "not a JSON object")
                    return
                if bot.arena is not None:
                    bot.arena.on_move(bot, msg, now)
        finally:
            await bot.stop()
            if self.bots.get(name) is bot:
                del self.bots[name]
            if bot in self.queue:
                self.queue.remove(bot)
                self.tell_positions()
            self.say(f"- {name} left")


def parse(text) -> dict | None:
    """A message as a dict; None when it is not a JSON object."""
    if not isinstance(text, str):
        return None
    try:
        msg = json.loads(text)
    except ValueError:
        return None
    return msg if isinstance(msg, dict) else None
