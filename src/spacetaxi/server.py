"""The arena server: bots connect over WebSocket and fly live rounds of up to 4 taxis.

A bot says `hello` on `/bot`, gets `welcome` and waits (`queued` tells it its place in the plan
of the next wave and `starts_in`, the seconds until its wave starts). Rounds run in waves: a
wave starts `fill_seconds` after the first bot joins an idle server, and again `fill_seconds`
after every arena of the previous wave has ended (the pause for the podium and the leaderboard).
The next wave is planned early (`spacetaxi.plan`) and plays exactly as planned; after
`round_end` every bot of the round that is still connected waits again. The hello's checks (names
ignore case) and the hostile-client limits are in `spacetaxi.limits`.

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

The pages and their JSON are in `spacetaxi.pages`. `/spectate` is a spectator socket (the
`/watch` page) that gets `spectate` (the number of arena slots), then
every arena's `round_start`, `tick` (in the replay's shape, with `moves`) and `round_end`, each
with `arena` (its slot, 1 to `max_arenas`) and `round` added. A spectator that connects mid-round
gets each running arena's `round_start` and latest `tick` first. Spectators send nothing. With `log`, each `round_end` is
appended to that file as one JSON line (with each taxi's `ticks` and `late` count) and the
leaderboard (`spacetaxi.leaderboard`) is rebuilt from it on start. With `replays`, each round is
written to that directory as a replay that `spacetaxi view` opens. The milestone race
(docs/rules.md, Milestones) and the house bots' benchmarks are in `spacetaxi.race`.

The server can have several maps (`maps`, the first is the start map). `command` takes one line
the operator typed (`map NAME`, `maps`): `map NAME` sets the map of the next wave; the wave in
progress finishes on its map. All arenas of a wave play one map, named in `round_start` as
`map_name`, and their `beat_greedy` / `beat_pilot` compare against that map's benchmarks.
`lesson N` opens lessons 1 to N
on every `/lessons` page; `room N` is the head count a pit stop needs half of (both kept in the
standings file). `kick NAME` disconnects a bot; `ban NAME` disconnects it, refuses the name from then on and
leaves it out of the standings, the leaderboard and the plan (bans are kept in the standings
file); `unban NAME` lifts it. An exception in one connection's handler is logged as one line.
"""

import asyncio
import json
import random
import time
from pathlib import Path

from . import ws
from .leaderboard import Leaderboard
from .limits import (BOT_MAX_MESSAGE, FLOOD_BYTES, FLOOD_MESSAGES, HELLO_TIMEOUT,
                     MAX_BOTS, MAX_BOTS_PER_IP, NAME, PROTOCOL, SPECTATE_MAX_MESSAGE,  # noqa: F401
                     Admission, Flood, parse)
from .maps import Map
from .milestones import Tracker
from .pages import Pages, doc_text
from .plan import ARENA_SIZE, MAX_ARENAS, Plan, group_sizes  # noqa: F401
from .race import Race
from .runner import check_move, clean_debug
from .sim import Rules, World
from .viewer import parse_lessons

OUTBOX_TICKS = 8  # a tick is dropped for a bot that has this many messages still unsent


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
        self.house = [n for n in self.names if n.lower() in server.house]
        self.rules = server.rules
        self.map_name = server.map_name  # the map of the wave this round belongs to
        self.map = server.maps[self.map_name]
        self.world = World(self.map, self.rules, seed=seed, players=self.names)
        self.playing = 0  # the tick whose moves are being collected, 0 before the first
        self.sent_ns = 0
        self.records: dict[str, dict] = {}
        self.late = {name: 0 for name in self.names}
        self.replay = None
        self.shown: dict[str, str] = {}  # the latest message of each type sent to spectators
        self.tracker = Tracker(self.names, [*self.humans, *self.house])
        # this map's benchmarks as at round start: known ones count
        self.benchmarks = dict(server.map_benchmarks[self.map_name])
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
        game_map = self.map
        round_start = {
            "type": "round_start",
            "round": self.number,
            "map_name": self.map_name,
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
            "map_name": self.map_name,
            "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "results": [{**r, "ticks": rules.round_ticks, "late": self.late[r["id"]],
                         **({"human": True} if r["id"] in self.humans else {})}
                        for r in results],
        }


class ArenaServer(Plan, Race, Admission, Pages):
    """The queue, the waves of arenas, the leaderboard and the WebSocket handler; the plan of
    the next wave (`Plan`), the milestone race (`Race`), the hello checks (`Admission`) and the
    HTTP pages (`Pages`) are mixed in."""

    def __init__(self, maps: dict[str, Map], rules: Rules | None = None,
                 fill_seconds: float = 10,
                 log: str | Path | None = None, replays: str | Path | None = None,
                 seed: int | None = None, max_arenas: int = MAX_ARENAS, say=None,
                 standings: str | Path | None = None,
                 benchmarks: dict[str, dict[str, int]] | None = None, house=(),
                 max_bots_per_ip: int | None = None, max_bots: int | None = None):
        """`maps` are the maps to choose from by name (the first starts); `benchmarks` gives
        known ones as `{map name: {bot: money}}`."""
        self.max_bots_per_ip = max_bots_per_ip
        self.max_bots = max_bots
        self.maps = dict(maps)
        self.map_name = next(iter(self.maps))  # the map of the current (or last) wave
        self.next_map = self.map_name  # the map the next wave plays
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
        self.tasks: set[asyncio.Task] = set()
        # the house bots' names, lowercased (names ignore case): they never score milestones
        self.house = {n.lower() for n in house}
        self.init_race(standings, benchmarks)  # the standings, the benchmarks, `pending`
        self.leaderboard = Leaderboard(self.house, self.standings.banned)
        self.rounds = 0
        self.wave = 0
        self.wave_t0 = 0  # perf_counter_ns when the running wave's clock started
        self.next_wave_ns: int | None = None  # when the next wave starts; None while a wave runs
        self.init_plan()  # the next wave's arenas (names) and waitlist, every connected bot
        self.init_pages()
        self.address: str | None = None  # HOST:PORT for the brief and mission; set by start()
        if self.log and self.log.exists():
            self.rounds, self.wave = self.leaderboard.load(self.log)
        if self.replays:
            self.replays.mkdir(parents=True, exist_ok=True)

    # --- the operator's commands

    def command(self, line: str) -> str | None:
        """One line typed in `serve`'s terminal; returns the answer to print (None for an
        empty line). `map NAME` sets the next wave's map, `maps` lists them."""
        words = line.split()
        if not words:
            return None
        if words[0] == "maps" and len(words) == 1:
            def tags(name):
                t = [w for w, on in (("current", name == self.map_name),
                                     ("next", name == self.next_map)) if on]
                return f" ({', '.join(t)})" if t else ""
            return "maps: " + ", ".join(name + tags(name) for name in self.maps)
        if words[0] == "map" and len(words) == 2:
            name = words[1]
            if name not in self.maps:
                return f"unknown map {name!r}; maps: {', '.join(self.maps)}"
            self.next_map = name
            self.plan_changed()
            return f"next wave: {name}"
        if words[0] in ("kick", "ban", "unban") and len(words) == 2:
            return getattr(self, words[0])(words[1])
        if words[0] == "lesson" and len(words) <= 2:
            return self.lesson(words[1] if len(words) == 2 else None)
        if words[0] == "room" and len(words) <= 2:
            return self.room(words[1] if len(words) == 2 else None)
        return ("commands: map NAME (the next wave's map), maps (list them), kick NAME, "
                "ban NAME, unban NAME, lesson N (reveal lessons 1 to N to everyone, 0 hides), "
                "room N (the participants in the room, for the pit stops; 0 counts the standings)")

    def lesson(self, arg: str | None) -> str:
        """`lesson N`: lessons 1 to N open on every /lessons page; `lesson` alone tells how many."""
        total = len(parse_lessons(doc_text("lessons.md")))
        if arg is None:
            return (f"lessons revealed to everyone: {self.standings.lessons} of {total}; "
                    f"all of them for you: {self.lessons_url()}")
        if not arg.isdigit() or int(arg) > total:
            return f"lesson: a number from 0 to {total}"
        self.standings.lessons = int(arg)
        self.standings.save()
        return f"lessons revealed to everyone: {self.standings.lessons} of {total}"

    def room(self, arg: str | None) -> str:
        """`room N`: the head count in the room; a pit stop is due at half of it, or of the
        participants in the standings when they are more. `room` alone tells both."""
        if arg is not None:
            if not arg.isdigit() or int(arg) > 999:
                return "room: the number of participants in the room (0: count the standings)"
            self.standings.room = int(arg)
            self.standings.save()
        rows = len(self.standings.table(self.house | self.standings.banned))
        room = self.standings.room
        said = f"room: {room} participants" if room else "room: not set"
        stop = self.pitstop()
        need = f"; pit stop {stop['number']} is due at {stop['need']}" if stop else ""
        return f"{said}, {rows} in the standings{need}"

    def connected(self, name: str) -> Bot | None:
        """The connected bot of `name`, ignoring case."""
        return next((b for n, b in self.bots.items() if n.lower() == name.lower()), None)

    def disconnect(self, bot: Bot, code: str, message: str) -> None:
        """Tell `bot` why and drop its connection; its handler then cleans up."""
        async def drop():
            await bot.stop()
            await self.error(bot.sock, code, message)
            await bot.sock.abort(1008)
        self._spawn(drop())

    def kick(self, name: str) -> str:
        bot = self.connected(name)
        if bot is None:
            return f"kick: no bot {name!r} connected"
        self.disconnect(bot, "kicked", "the operator disconnected you")
        return f"kicked {bot.name}"

    def ban(self, name: str) -> str:
        key = name.lower()
        if key in self.standings.banned:
            return f"ban: {name!r} is already banned"
        bot = self.connected(name)
        self.standings.banned.add(key)
        self.standings.remove(name)
        self.standings.save()
        if bot is not None:
            self.disconnect(bot, "banned", "the operator banned this name")
            self.plan_leave(bot.name)
        return f"banned {name}" + (" and disconnected it" if bot else "")

    def unban(self, name: str) -> str:
        key = name.lower()
        if key not in self.standings.banned:
            return f"unban: {name!r} is not banned"
        self.standings.banned.discard(key)
        self.standings.save()
        return f"unbanned {name}"

    async def start(self, host: str | None = None, port: int = 8000) -> asyncio.Server:
        server = await ws.serve({"/bot": self.handle, "/spectate": self.spectate}, self.http,
                                host, port, {"/bot": BOT_MAX_MESSAGE,
                                             "/spectate": SPECTATE_MAX_MESSAGE})
        if self.address is None:
            self.address = f"{host or 'localhost'}:{server.sockets[0].getsockname()[1]}"
        self._spawn(self.scheduler())
        self.start_benchmarks()
        return server

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    # --- the queue, the plan of the next wave and the waves

    def enqueue(self, bot: Bot) -> None:
        bot.told = None
        self.queue.append(bot)
        if self.next_wave_ns is None and not self.arenas:
            self.next_wave_ns = time.perf_counter_ns() + self.fill_ns
        self.plan_join(bot.name)
        self.fill()
        self.tell_positions()  # every bot hears `queued`, even one whose round starts now

    def round_ns(self) -> int:
        return (self.rules.round_ticks + 1) * self.rules.tick_ms * 1_000_000

    def next_wave_at(self) -> int | None:
        """perf_counter_ns when the next wave starts (an estimate while a wave runs: a pause
        after its rounds end); None on an idle server."""
        if self.next_wave_ns is not None:
            return self.next_wave_ns
        if self.arenas:
            return self.wave_t0 + self.round_ns() + self.fill_ns
        return None

    @staticmethod
    def seconds(at: int | None) -> int | None:
        """Whole seconds from now until `at` (perf_counter_ns), rounded up, at least 0."""
        if at is None:
            return None
        return max(0, -(-(at - time.perf_counter_ns()) // 1_000_000_000))

    def tell_positions(self) -> None:
        """Send `queued` to every waiting bot whose place or start time changed: its place in
        the plan, the plan's size and the start of the wave it is planned into."""
        first = self.next_wave_at()
        if first is None:
            return
        places = self.places()
        period = self.round_ns() + self.fill_ns
        for bot in self.queue:
            i, later = places[bot.name]
            told = (i, len(places), self.seconds(first + later * period))
            if bot.told != told:
                bot.told = told
                bot.send({"type": "queued", "position": told[0], "waiting": told[1],
                          "starts_in": told[2]})

    def fill(self) -> None:
        """Start the next wave when it is due, exactly as planned: its arenas start on one
        clock, then the plan of the wave after is made from every connected bot, the ones
        that sit this wave out seated first."""
        self.queue = [b for b in self.queue if b.connected]
        if self.next_wave_ns is None or time.perf_counter_ns() < self.next_wave_ns:
            return
        groups = self.plan_arenas
        if not groups:
            self.next_wave_ns = None
            return
        self.played(groups)
        dealt = {name for g in groups for name in g}
        sitters = list(self.plan_waitlist)
        self.queue = [b for b in self.queue if b.name not in dealt]
        self.wave += 1
        self.next_wave_ns = None
        self.map_name = self.next_map  # the whole wave plays one map
        self.say(f"wave {self.wave} on {self.map_name}: "
                 + " | ".join(", ".join(g) for g in groups))
        arenas = []
        for slot, group in enumerate(groups, 1):
            self.rounds += 1
            arena = Arena(self, self.rounds, [self.bots[n] for n in group],
                          self.rng.getrandbits(63), slot, self.wave)
            self.arenas.add(arena)
            arenas.append(arena)
            self.say(f"round {arena.number}: {', '.join(arena.names)}")
        self.wave_t0 = time.perf_counter_ns()
        for arena in arenas:  # every round_start goes out now, before any arena's first tick
            self._spawn(self.play(arena, self.wave_t0))
        self.replan(sitters + [n for g in groups for n in g])
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
                self.next_wave_ns = (time.perf_counter_ns() + self.fill_ns
                                     if self.plan_arenas else None)
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
            # spectators send nothing: one that does is dropped at once, without draining it
            if await sock.recv() is not None:
                await sock.abort(1008)
        finally:
            self.spectators.discard(spectator)
            await spectator.stop()

    async def error(self, sock: ws.WebSocket, code: str, message: str) -> None:
        """Send an `error`; a peer that does not read gets `ws.CLOSE_TIMEOUT` seconds."""
        try:
            async with asyncio.timeout(ws.CLOSE_TIMEOUT):
                await sock.send(dumps({"type": "error", "code": code, "message": message}))
        except (ConnectionError, OSError, TimeoutError):
            pass

    async def handle(self, sock: ws.WebSocket, path: str) -> None:
        """A bot's connection: its hello, then its moves until it leaves. Any exception is
        logged as one line, not raised."""
        try:
            bot = await self.greet(sock)
        except Exception as exc:
            return self.crashed(sock.peer, exc)
        if bot is None:
            return
        try:
            await self.listen(bot)
        except Exception as exc:
            self.crashed(bot.name, exc)
        finally:
            await bot.stop()
            if self.bots.get(bot.name) is bot:
                del self.bots[bot.name]
            if bot in self.queue:
                self.queue.remove(bot)
            self.plan_leave(bot.name)
            self.tell_positions()
            self.say(f"- {bot.name} left")

    def crashed(self, who: str, exc: Exception) -> None:
        self.say(f"! {who}: {type(exc).__name__} {str(exc)[:200]}")

    async def too_big(self, sock: ws.WebSocket) -> None:
        await self.error(sock, "too_big", f"messages are at most {BOT_MAX_MESSAGE} bytes")

    async def greet(self, sock: ws.WebSocket) -> Bot | None:
        """The hello of a new connection: the bot, welcomed and queued, or None after an
        `error`."""
        try:
            async with asyncio.timeout(HELLO_TIMEOUT):
                text = await sock.recv()
        except TimeoutError:
            await self.error(sock, "bad_message", f"no hello within {HELLO_TIMEOUT} s")
            return None
        if text is None:
            if sock.too_big:
                await self.too_big(sock)
            return None
        hello = parse(text)
        refusal = self.refuse(sock, hello)
        if refusal is not None:
            await self.error(sock, *refusal)
            return None
        name = hello["name"]
        human = hello.get("client") == "human"
        house = name.lower() in self.house
        bot = Bot(name, sock, human=human)
        self.bots[name] = bot
        self.say(f"+ {name} joined from {sock.peer}" + (" (human)" if human else "")
                 + (" (house)" if house else ""))
        bot.send({"type": "welcome", "id": name, "rules": self.rules.to_dict()})
        if not human and not house:
            self.award("hello", [(name, 0)])
        self.enqueue(bot)
        return bot

    async def listen(self, bot: Bot) -> None:
        """A bot's moves until it leaves, or breaks a limit or the protocol (an `error`)."""
        sock = bot.sock
        flood = Flood(self.rules.tick_ms * 1_000_000)
        while (text := await sock.recv()) is not None:
            now = time.perf_counter_ns()
            if flood.add(text, now):
                await bot.stop()
                await self.error(sock, "flooding", f"at most {FLOOD_MESSAGES} messages and "
                                                   f"{FLOOD_BYTES} bytes within one tick")
                return
            msg = parse(text)
            if msg is None or msg.get("type") != "move":
                await bot.stop()
                await self.error(sock, "bad_message",
                                 "expected a move" if msg else "not a JSON object")
                return
            if bot.arena is not None:
                bot.arena.on_move(bot, msg, now)
        if sock.too_big:
            await bot.stop()
            await self.too_big(sock)
