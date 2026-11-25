"""The arena server: bots connect over WebSocket and fly live rounds of up to 4 taxis.

A bot says `hello` on `/bot`, gets `welcome` and waits (`queued` tells it its place in the plan
of the next wave and `starts_in`, the seconds until its wave starts). Rounds run in waves: a
wave starts `fill_seconds` after the first bot joins an idle server, and again `fill_seconds`
after every arena of the previous wave has ended (the pause for the podium and the leaderboard).
The next wave is planned early: when a wave starts, every connected bot is dealt into up to
`max_arenas` arenas of at most 4, as even as possible (7 bots play 4 and 3, 6 play 3 and 3),
the ones sitting the current wave out first; bots beyond that wait on the plan's waitlist. The
deal is a seeded shuffle that prefers bots who have met least so far and avoids repeating a
group of the previous wave, so groups mix and the leaderboard compares like with like. On an
idle server the plan starts with the first join. Joins and leaves change the plan as little as
they can (`rebalance`), and the next wave plays exactly as planned; after `round_end` every bot
of the round that is still connected waits again. Names ignore case: the first spelling seen
(connected, or in the standings or the leaderboard) keeps the name, and a `hello` with another
spelling of it gets `name_taken` naming that spelling; the server never renames a bot.

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
milestone only, from `docs/missions.md`, polling `/standings.json`. `/slides` is the intro deck
(`docs/slides/intro.html`, self-contained, HOST:8000 filled in). Every HTML page has the
page bar linking the others (`spacetaxi.nav`). The house bots' benchmarks
are simulated in a thread at start, for every map, unless `benchmarks` gives them.

The server can have several maps (`maps`, the first is the start map). `command` takes one line
the operator typed (`map NAME`, `maps`): `map NAME` sets the map of the next wave; the wave in
progress finishes on its map. All arenas of a wave play one map, named in `round_start` as
`map_name`, and their `beat_greedy` / `beat_pilot` compare against that map's benchmarks.
`kick NAME` disconnects a bot; `ban NAME` disconnects it, refuses the name from then on and
leaves it out of the standings, the leaderboard and the plan (bans are kept in the standings
file); `unban NAME` lifts it.

Hostile clients (docs/rules.md, Limits): a bot's message over `BOT_MAX_MESSAGE` bytes gets
`too_big`, more than `FLOOD_MESSAGES` in one tick `flooding`, and the connection is closed;
`max_bots_per_ip` and `max_bots` (None: no cap) cap the bots connected at once (`too_many`,
`full`), house bots aside. An exception in one connection's handler is logged as one line.
"""

import asyncio
import base64
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
from .nav import with_nav
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
BOT_MAX_MESSAGE = 64 * 1024  # bytes: a bigger message from a bot gets `too_big` and is closed
SPECTATE_MAX_MESSAGE = 4 * 1024  # spectators send nothing
FLOOD_MESSAGES = 20  # more messages than this from one bot within one tick: `flooding`
MAX_BOTS_PER_IP = 4  # `serve`'s default for bots connected at once from one IP, house bots aside
MAX_BOTS = 40  # `serve`'s default for bots connected at once, house bots aside
OUTBOX_TICKS = 8  # a tick is dropped for a bot that has this many messages still unsent
DEAL_TRIES = 200  # shuffles tried per deal; the one whose groups have met least wins


# the Markdown documents the server serves: path -> (file in docs/, page title)
DOC_PAGES = {"/card": ("participant-card.md", "Spacetaxi: participant card"),
             "/brief": ("brief.md", "Spacetaxi: brief"),
             "/brief.md": ("brief.md", "")}


def doc_path(name: str) -> Path:
    """A file of `docs/`: packaged next to the code in a wheel, the repo's `docs/` otherwise."""
    for docs in (Path(__file__).parent / "docs", Path(__file__).resolve().parents[2] / "docs"):
        if (docs / name).is_file():
            return docs / name
    raise FileNotFoundError(name)


def doc_text(name: str) -> str:
    return doc_path(name).read_text(encoding="utf-8")


def build_slides_html(address: str) -> str:
    """docs/slides/intro.html as one self-contained page: the font and the images inlined,
    HOST:8000 replaced by `address`."""
    def data_uri(path: Path, kind: str) -> str:
        return f"data:{kind};base64," + base64.b64encode(path.read_bytes()).decode("ascii")

    html = doc_text("slides/intro.html")
    html = html.replace("../../src/spacetaxi/web/PressStart2P-Regular.ttf",
                        data_uri(WEB_DIR / "PressStart2P-Regular.ttf", "font/ttf"))
    kinds = {".png": "image/png", ".jpg": "image/jpeg"}
    html = re.sub(r'src="(img/[\w.-]+)"', lambda m: 'src="{}"'.format(
        data_uri(doc_path("slides/" + m[1]), kinds[Path(m[1]).suffix])), html)
    return html.replace("HOST:8000", address)


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

    def __init__(self, house=(), banned=None):
        self.rows: dict[str, dict] = {}
        self.house = {n.lower() for n in house}  # names marked as house bots, ignoring case
        self.banned = banned if banned is not None else set()  # lowercased, left out of table

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
            if name.lower() in self.banned:
                continue
            out.append({
                "name": name,
                "rounds": row["rounds"],
                "total_money": row["money"],
                "avg_money": round(row["money"] / row["rounds"], 2),
                "deliveries": row["deliveries"],
                "crashes": row["crashes"],
                "late_pct": round(100 * row["late"] / row["ticks"], 1) if row["ticks"] else 0.0,
                "human": row.get("human", False),
                "house": name.lower() in self.house,
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


class ArenaServer:
    """The queue, the waves of arenas, the leaderboard and the WebSocket handler."""

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
        self.watch_pages: dict[str, bytes] = {}  # per map, built on the first request
        self.fly_pages: dict[str, bytes] = {}
        self.tasks: set[asyncio.Task] = set()
        # the house bots' names, lowercased (names ignore case): they never score milestones
        self.house = {n.lower() for n in house}
        self.standings = Standings(standings)
        self.leaderboard = Leaderboard(self.house, self.standings.banned)
        self.rounds = 0
        self.wave = 0
        self.wave_t0 = 0  # perf_counter_ns when the running wave's clock started
        self.next_wave_ns: int | None = None  # when the next wave starts; None while a wave runs
        # the plan of the next wave: its arenas (names) and its waitlist, every connected bot
        self.plan_arenas: list[list[str]] = []
        self.plan_waitlist: list[str] = []
        self.said_plan = ""  # the plan as last printed
        self.met: dict[frozenset, int] = {}  # rounds each pair of names has shared
        self.last_groups: set[frozenset] = set()  # the previous wave's groups
        # per map, the money beat_greedy / beat_pilot must beat; None until simulated
        self.map_benchmarks: dict[str, dict[str, int | None]] = {
            name: {bot: None for bot in BENCHMARK_BOTS} for name in self.maps}
        for name, values in (benchmarks or {}).items():
            self.map_benchmarks[name].update(values)
        self.pending: list[tuple] = []  # (wave, tick, milestone, name, key) not yet scored
        self.leaderboard_page: bytes | None = None
        self.standings_page: bytes | None = None
        self.slides_page: bytes | None = None
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

    @property
    def benchmarks(self) -> dict[str, int | None]:
        """The benchmarks of the current (or last) wave's map."""
        return self.map_benchmarks[self.map_name]

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
        return ("commands: map NAME (the next wave's map), maps (list them), kick NAME, "
                "ban NAME, unban NAME")

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
        self.standings.save()
        if bot is not None:
            self.disconnect(bot, "banned", "the operator banned this name")
        self.plan_order_drop(key)
        return f"banned {name}" + (" and disconnected it" if bot else "")

    def unban(self, name: str) -> str:
        key = name.lower()
        if key not in self.standings.banned:
            return f"unban: {name!r} is not banned"
        self.standings.banned.discard(key)
        self.standings.save()
        return f"unbanned {name}"

    def plan_order_drop(self, key: str) -> None:
        """Take a (banned) name out of the plan of the next wave, ignoring case."""
        for name in self.plan_order():
            if name.lower() == key:
                self.plan_leave(name)

    # --- HTTP

    def doc(self, name: str) -> str:
        """A file of `docs/` with the server's address filled in for HOST:PORT."""
        return doc_text(name).replace("HOST:PORT", self.address or "HOST:PORT")

    def http(self, path: str):
        """The HTTP answer to GET `path`: (status, content type, body), or None. Every HTML page
        is built with the page bar (`spacetaxi.nav`)."""
        if path in ("/", "/index.html"):
            if self.leaderboard_page is None:
                page = (WEB_DIR / "leaderboard.html").read_text(encoding="utf-8")
                self.leaderboard_page = with_nav(page, "/").encode("utf-8")
            return 200, "text/html; charset=utf-8", self.leaderboard_page
        if path == "/leaderboard.json":
            data = {"leaderboard": self.leaderboard.table(), "rounds": self.rounds,
                    "wave": self.wave, "next_map": self.next_map,
                    "connected": len(self.bots), "waiting": len(self.queue),
                    "arenas": sorted([{"round": a.number, "arena": a.slot, "players": a.names,
                                       "tick": a.playing, "ends_in": self.seconds(self.wave_t0 + self.round_ns())}
                                      for a in self.arenas],
                                     key=lambda a: a["round"]),
                    "next_wave": {"wave": self.wave + 1, "map": self.next_map,
                                  "starts_in": self.seconds(self.next_wave_at()),
                                  "arenas": [list(g) for g in self.plan_arenas],
                                  "waitlist": list(self.plan_waitlist)}}
            return 200, "application/json", json.dumps(data).encode("utf-8")
        if path == "/standings":
            if self.standings_page is None:
                self.standings_page = with_nav(build_standings_html(), path).encode("utf-8")
            return 200, "text/html; charset=utf-8", self.standings_page
        if path == "/standings.json":
            data = {"standings": self.standings.table(self.house | self.standings.banned),
                    "milestones": list(MILESTONES), "points": list(POINTS),
                    "map_name": self.map_name, "benchmarks": self.benchmarks,
                    "reached": {m: self.standings.reached(m) for m in MILESTONES}}
            return 200, "application/json", json.dumps(data).encode("utf-8")
        if path == "/slides":
            if self.slides_page is None:
                page = build_slides_html(self.address or "HOST:8000")
                self.slides_page = with_nav(page, path).encode("utf-8")
            return 200, "text/html; charset=utf-8", self.slides_page
        if path == "/mission":
            text = self.doc("missions.md")
            page = with_nav(build_mission_html(text), path)
            return 200, "text/html; charset=utf-8", page.encode("utf-8")
        if path in DOC_PAGES:
            name, title = DOC_PAGES[path]
            text = self.doc(name)
            if path.endswith(".md"):
                return 200, "text/markdown; charset=utf-8", text.encode("utf-8")
            page = with_nav(build_doc_html(text, title), path)
            return 200, "text/html; charset=utf-8", page.encode("utf-8")
        if path in ("/watch", "/fly"):  # the empty map shown before a round: the next one
            pages, build = ((self.watch_pages, build_live_html) if path == "/watch"
                            else (self.fly_pages, build_fly_html))
            name = self.next_map
            if name not in pages:
                pages[name] = with_nav(build(self.maps[name], self.rules), path).encode("utf-8")
            return 200, "text/html; charset=utf-8", pages[name]
        return None

    async def start(self, host: str | None = None, port: int = 8000) -> asyncio.Server:
        server = await ws.serve({"/bot": self.handle, "/spectate": self.spectate}, self.http,
                                host, port, {"/bot": BOT_MAX_MESSAGE,
                                             "/spectate": SPECTATE_MAX_MESSAGE})
        if self.address is None:
            self.address = f"{host or 'localhost'}:{server.sockets[0].getsockname()[1]}"
        self._spawn(self.scheduler())
        for name, values in self.map_benchmarks.items():
            if all(v is not None for v in values.values()):
                self.say_benchmarks(name, "given")
        if any(v is None for values in self.map_benchmarks.values() for v in values.values()):
            self._spawn(self.find_benchmarks())
        return server

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

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    # --- the milestone race

    def award(self, milestone: str, entries: list[tuple[str, int]]) -> None:
        """Score `entries` reaching `milestone` together (see `Standings.award`), log every
        award, tell the spectators and save the standings."""
        entries = [e for e in entries if e[0].lower() not in self.standings.banned]
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

    # The plan: the next wave's arenas and waitlist, made from every connected bot when a wave
    # starts (and from the first join on an idle server), then changed only by joins and leaves.

    def plan_order(self) -> list[str]:
        """The plan's names in order: the next wave's arenas first, then the waitlist."""
        return [n for g in self.plan_arenas for n in g] + self.plan_waitlist

    def replan(self, names: list[str]) -> None:
        """A new plan from `names`, the first ones seated first: dealt as a wave (`deal`), the
        names beyond its seats wait in order."""
        self.plan_arenas = self.deal(names)
        seated = {n for g in self.plan_arenas for n in g}
        self.plan_waitlist = [n for n in names if n not in seated]
        self.plan_changed()

    def plan_join(self, name: str) -> None:
        """A joiner takes a free seat, or the end of the waitlist."""
        self.plan_waitlist.append(name)
        self.rebalance()

    def plan_leave(self, name: str) -> None:
        """A leaver's seat goes to the first on the waitlist."""
        for g in self.plan_arenas:
            if name in g:
                g.remove(name)
        if name in self.plan_waitlist:
            self.plan_waitlist.remove(name)
        self.rebalance()

    def rebalance(self) -> None:
        """Fit the plan to `group_sizes` of its names, moving as few as possible: the arenas
        get the sizes largest to largest, an arena over its size gives up its last names, and
        free seats are filled by those first, then from the head of the waitlist."""
        arenas = [list(g) for g in self.plan_arenas]
        sizes = group_sizes(len(self.plan_order()), self.max_arenas)
        target = [0] * len(arenas)
        for rank, i in enumerate(sorted(range(len(arenas)), key=lambda i: -len(arenas[i]))):
            target[i] = sizes[rank] if rank < len(sizes) else 0
        for size in sizes[len(arenas):]:
            arenas.append([])
            target.append(size)
        pool = []
        for g, size in zip(arenas, target):
            while len(g) > size:
                pool.append(g.pop())
        pool += self.plan_waitlist
        for g, size in zip(arenas, target):
            while len(g) < size:
                g.append(pool.pop(0))
        self.plan_arenas = [g for g in arenas if g]
        self.plan_waitlist = pool
        self.plan_changed()

    def plan_changed(self) -> None:
        """Print the plan when it differs from the one printed last."""
        if not self.plan_arenas:
            return
        text = (f"next wave {self.wave + 1} on {self.next_map}: "
                + " | ".join(", ".join(g) for g in self.plan_arenas))
        if self.plan_waitlist:
            text += "  waiting: " + ", ".join(self.plan_waitlist)
        if text != self.said_plan:
            self.said_plan = text
            self.say(text)

    def tell_positions(self) -> None:
        """Send `queued` to every waiting bot whose place or start time changed: its place in
        the plan, the plan's size and the start of the wave it is planned into."""
        order = self.plan_order()
        first = self.next_wave_at()
        if first is None:
            return
        place = {name: i for i, name in enumerate(order, 1)}
        seats = sum(len(g) for g in self.plan_arenas)
        period = self.round_ns() + self.fill_ns
        for bot in self.queue:
            i = place[bot.name]
            later = 0 if i <= seats else 1 + (i - seats - 1) // seats
            told = (i, len(order), self.seconds(first + later * period))
            if bot.told != told:
                bot.told = told
                bot.send({"type": "queued", "position": told[0], "waiting": told[1],
                          "starts_in": told[2]})

    def deal(self, names: list[str]) -> list[list[str]]:
        """Split `names` into a wave's groups (`group_sizes`; names beyond them are left out,
        they wait): of `DEAL_TRIES` seeded shuffles, the one whose groups repeat fewest of the
        previous wave's groups, then hold the most pairs who never met, then the fewest past
        meetings."""
        sizes = group_sizes(len(names), self.max_arenas)
        names = names[:sum(sizes)]  # the first ones play
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
        return best

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
        for g in groups:
            for pair in itertools.combinations(g, 2):
                key = frozenset(pair)
                self.met[key] = self.met.get(key, 0) + 1
        self.last_groups = {frozenset(g) for g in groups}
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
            while await sock.recv() is not None:
                pass
        except Exception as exc:
            self.say(f"! spectator {sock.peer}: {type(exc).__name__} {str(exc)[:200]}")
        finally:
            self.spectators.discard(spectator)
            await spectator.stop()

    async def error(self, sock: ws.WebSocket, code: str, message: str) -> None:
        try:
            await sock.send(dumps({"type": "error", "code": code, "message": message}))
        except (ConnectionError, OSError):
            pass

    def spelling(self, name: str) -> str | None:
        """The spelling the server knows `name` by, ignoring case: a connected bot's, a
        standings row's or a leaderboard row's; None for a new name."""
        key = name.lower()
        for known in (*self.bots, *self.standings.awards, *self.leaderboard.rows):
            if known.lower() == key:
                return known
        return None

    async def handle(self, sock: ws.WebSocket, path: str) -> None:
        """A bot's connection: its hello, then its moves until it leaves. Any exception is
        logged as one line, not raised."""
        try:
            await self.serve_bot(sock)
        except Exception as exc:
            self.say(f"! {getattr(sock, 'bot_name', None) or sock.peer}: "
                     f"{type(exc).__name__} {str(exc)[:200]}")

    async def serve_bot(self, sock: ws.WebSocket) -> None:
        try:
            async with asyncio.timeout(HELLO_TIMEOUT):
                text = await sock.recv()
        except TimeoutError:
            return await self.error(sock, "bad_message", f"no hello within {HELLO_TIMEOUT} s")
        if text is None:
            if sock.too_big:
                await self.error(sock, "too_big", f"messages are at most {BOT_MAX_MESSAGE} bytes")
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
        if name.lower() in self.standings.banned:
            return await self.error(sock, "banned", f"name {name!r} is banned")
        if name in self.bots:
            return await self.error(sock, "name_taken", f"name {name!r} is already connected")
        spelling = self.spelling(name)
        if spelling is not None and spelling != name:
            return await self.error(sock, "name_taken", f"name {name!r} is taken as {spelling!r}; "
                                                        f"connect as {spelling!r}")
        house = name.lower() in self.house
        if not house:
            guests = [b for n, b in self.bots.items() if n.lower() not in self.house]
            if self.max_bots is not None and len(guests) >= self.max_bots:
                return await self.error(sock, "full", f"the arena is full ({self.max_bots} bots)")
            if self.max_bots_per_ip is not None and sum(
                    b.sock.ip == sock.ip for b in guests) >= self.max_bots_per_ip:
                return await self.error(sock, "too_many", f"at most {self.max_bots_per_ip} bots "
                                                          "from one address at once")
        human = hello.get("client") == "human"
        bot = Bot(name, sock, human=human)
        sock.bot_name = name
        self.bots[name] = bot
        self.say(f"+ {name} joined from {sock.peer}" + (" (human)" if human else "")
                 + (" (house)" if house else ""))
        bot.send({"type": "welcome", "id": name, "rules": self.rules.to_dict()})
        if not human and not house:
            self.award("hello", [(name, 0)])
        self.enqueue(bot)
        tick_ns = self.rules.tick_ms * 1_000_000
        window, count = 0, 0  # the start of the current tick-long window, messages in it
        try:
            while (text := await sock.recv()) is not None:
                now = time.perf_counter_ns()
                if now - window >= tick_ns:
                    window, count = now, 0
                count += 1
                if count > FLOOD_MESSAGES:
                    await bot.stop()
                    await self.error(sock, "flooding", f"more than {FLOOD_MESSAGES} messages "
                                                       "within one tick")
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
                await self.error(sock, "too_big", f"messages are at most {BOT_MAX_MESSAGE} bytes")
        finally:
            await bot.stop()
            if self.bots.get(name) is bot:
                del self.bots[name]
            if bot in self.queue:
                self.queue.remove(bot)
            self.plan_leave(name)
            self.tell_positions()
            self.say(f"- {name} left")


def parse(text) -> dict | None:
    """A message as a dict; None when it is not a JSON object."""
    if not isinstance(text, str):
        return None
    try:
        msg = json.loads(text)
    except (ValueError, RecursionError):  # deeply nested JSON is no message either
        return None
    return msg if isinstance(msg, dict) else None
