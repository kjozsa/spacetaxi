"""The arena server: bots connect over WebSocket and fly live rounds of up to 4 taxis.

A bot says `hello` on `/bot`, gets `welcome` and waits in one queue (`queued` tells it its
place). A round starts in a new arena as soon as 4 bots are waiting, or when the longest-waiting
bot has waited `fill_seconds`, with however many are waiting. Up to `max_arenas` arenas run side
by side; after `round_end` every bot of the round that is still connected goes back to the end
of the queue.

Each arena ticks every `tick_ms` on its own clock. A `move` counts when it names the tick being
played and arrives within `deadline_ms` of that tick being sent; anything else is dropped and the
taxi coasts. Every message to a bot goes through the bot's own outbox and sender task, so a slow
or vanished bot never holds up its arena or the others; a bot whose outbox backs up loses ticks.

`/` is the leaderboard page and `/leaderboard.json` its data. `/watch` is the neon viewer fed
live from `/spectate`: a spectator socket that gets `spectate` (the number of arena slots), then
every arena's `round_start`, `tick` (in the replay's shape, with `moves`) and `round_end`, each
with `arena` (its slot, 1 to `max_arenas`) and `round` added. A spectator that connects mid-round
gets each running arena's `round_start` and latest `tick` first. Spectators send nothing. With `log`, each `round_end` is
appended to that file as one JSON line (with each taxi's `ticks` and `late` count) and the
leaderboard is rebuilt from it on start. With `replays`, each round is written to that directory
as a replay that `spacetaxi view` opens.
"""

import asyncio
import json
import random
import re
import time
from pathlib import Path

from . import ws
from .maps import Map
from .runner import check_move, clean_debug
from .sim import Rules, World
from .viewer import build_live_html

WEB_DIR = Path(__file__).parent / "web"

NAME = re.compile(r"[A-Za-z0-9_-]{1,12}")
PROTOCOL = 1
ARENA_SIZE = 4
MAX_ARENAS = 3
HELLO_TIMEOUT = 10  # seconds a new connection has to say hello
OUTBOX_TICKS = 8  # a tick is dropped for a bot that has this many messages still unsent


def dumps(message: dict) -> str:
    return json.dumps(message, separators=(",", ":"))


class Bot:
    """One connected bot: its socket, its outbox and where it is (queue or arena)."""

    def __init__(self, name: str, sock: ws.WebSocket, backlog: int = OUTBOX_TICKS):
        self.name = name
        self.backlog = backlog  # droppable messages are dropped at this many unsent
        self.sock = sock
        self.connected = True
        self.queued_at = 0  # perf_counter_ns when it joined the queue
        self.told: tuple[int, int] | None = None  # the last (position, waiting) sent
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

    def __init__(self):
        self.rows: dict[str, dict] = {}

    def add(self, record: dict) -> None:
        for r in record["results"]:
            row = self.rows.setdefault(r["id"], {"rounds": 0, "money": 0, "deliveries": 0,
                                                 "crashes": 0, "ticks": 0, "late": 0})
            row["rounds"] += 1
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
            })
        out.sort(key=lambda r: (-r["avg_money"], -r["total_money"], r["name"]))
        return out


class Arena:
    """One live round: its world, its bots and the moves for the tick being played."""

    def __init__(self, server: "ArenaServer", number: int, bots: list[Bot], seed: int,
                 slot: int = 1):
        self.server = server
        self.number = number
        self.slot = slot  # which of the server's arenas, 1 to max_arenas
        self.bots = bots
        self.names = [b.name for b in bots]
        self.rules = server.rules
        self.world = World(server.map, self.rules, seed=seed, players=self.names)
        self.playing = 0  # the tick whose moves are being collected, 0 before the first
        self.sent_ns = 0
        self.records: dict[str, dict] = {}
        self.late = {name: 0 for name in self.names}
        self.replay = None
        self.shown: dict[str, str] = {}  # the latest message of each type sent to spectators
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
        self.world.step({n: {"ax": r["ax"], "ay": r["ay"]} for n, r in records.items()})

    async def run(self) -> dict:
        """Play the round in real time; returns its log record."""
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
            self.emit(round_start)
            self.broadcast(round_start)
            tick_ns = rules.tick_ms * 1_000_000
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
        finally:
            if self.replay is not None:
                self.replay.close()
        return {
            "type": "round_end",
            "round": self.number,
            "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "results": [{**r, "ticks": rules.round_ticks, "late": self.late[r["id"]]}
                        for r in results],
        }


class ArenaServer:
    """The queue, the running arenas, the leaderboard and the WebSocket handler."""

    def __init__(self, game_map: Map, rules: Rules | None = None, fill_seconds: float = 10,
                 log: str | Path | None = None, replays: str | Path | None = None,
                 seed: int | None = None, max_arenas: int = MAX_ARENAS, say=None):
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
        self.tasks: set[asyncio.Task] = set()
        self.leaderboard = Leaderboard()
        self.rounds = 0
        if self.log and self.log.exists():
            with open(self.log, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    self.leaderboard.add(record)
                    self.rounds = max(self.rounds, record.get("round", 0))
        if self.replays:
            self.replays.mkdir(parents=True, exist_ok=True)

    # --- HTTP

    def http(self, path: str):
        if path in ("/", "/index.html"):
            return 200, "text/html; charset=utf-8", (WEB_DIR / "leaderboard.html").read_bytes()
        if path == "/leaderboard.json":
            data = {"leaderboard": self.leaderboard.table(), "rounds": self.rounds,
                    "connected": len(self.bots), "waiting": len(self.queue),
                    "arenas": sorted([{"round": a.number, "arena": a.slot, "players": a.names,
                                       "tick": a.playing} for a in self.arenas],
                                     key=lambda a: a["round"])}
            return 200, "application/json", json.dumps(data).encode("utf-8")
        if path == "/watch":
            if self.watch_page is None:
                self.watch_page = build_live_html(self.map, self.rules).encode("utf-8")
            return 200, "text/html; charset=utf-8", self.watch_page
        return None

    async def start(self, host: str | None = None, port: int = 8000) -> asyncio.Server:
        server = await ws.serve({"/bot": self.handle, "/spectate": self.spectate}, self.http, host, port)
        self._spawn(self.scheduler())
        return server

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    # --- the queue

    def enqueue(self, bot: Bot) -> None:
        bot.queued_at = time.perf_counter_ns()
        bot.told = None
        self.queue.append(bot)
        self.tell_positions()  # every bot hears `queued`, even one whose round starts now
        self.fill()

    def tell_positions(self) -> None:
        waiting = len(self.queue)
        for i, bot in enumerate(self.queue, 1):
            if bot.told != (i, waiting):
                bot.told = (i, waiting)
                bot.send({"type": "queued", "position": i, "waiting": waiting})

    def fill(self) -> None:
        """Start every round that is due, then tell the waiting bots their places."""
        self.queue = [b for b in self.queue if b.connected]
        now = time.perf_counter_ns()
        while self.queue and len(self.arenas) < self.max_arenas and (
                len(self.queue) >= ARENA_SIZE or now - self.queue[0].queued_at >= self.fill_ns):
            players, self.queue = self.queue[:ARENA_SIZE], self.queue[ARENA_SIZE:]
            self.rounds += 1
            taken = {a.slot for a in self.arenas}
            slot = min(n for n in range(1, self.max_arenas + 1) if n not in taken)
            arena = Arena(self, self.rounds, players, self.rng.getrandbits(63), slot)
            self.arenas.add(arena)
            self.say(f"round {arena.number}: {', '.join(arena.names)}")
            self._spawn(self.play(arena))
        self.tell_positions()

    async def scheduler(self) -> None:
        """Start rounds whose fill time is up."""
        while True:
            await asyncio.sleep(0.02)
            if self.queue:
                self.fill()

    async def play(self, arena: Arena) -> None:
        try:
            record = await arena.run()
            self.leaderboard.add(record)
            if self.log:
                with open(self.log, "a", encoding="utf-8") as f:
                    f.write(dumps(record) + "\n")
            self.say(f"round {arena.number} over: " + ", ".join(
                f"{r['id']} {r['money']}" for r in record["results"]))
        finally:
            self.arenas.discard(arena)
            for bot in arena.bots:
                bot.arena = None
                if bot.connected:
                    self.enqueue(bot)
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
        bot = Bot(name, sock)
        self.bots[name] = bot
        self.say(f"+ {name} joined from {sock.peer}")
        bot.send({"type": "welcome", "id": name, "rules": self.rules.to_dict()})
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
