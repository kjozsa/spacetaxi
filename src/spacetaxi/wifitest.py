"""Venue WiFi test: real-size `tick` messages at the game's rhythm, answers timed on the server.

The server (`WifiTest`) records one 4-pilot round on `cavern` when it starts and replays its
`tick` messages (renumbered, so each client's ticks count up from 0) to every client on `/bot`,
one every `tick_ms`. Clients say `hello` and answer every `tick` with a `move` for that tick.
For each tick the server measures the round trip, from sending the `tick` to receiving its
`move`, and when the next tick goes out it judges the previous one:

- late: no answer within `deadline_ms` (in the game the taxi coasts); missing ones count too.
- missing: no answer before the next tick.

`/` is a client page for any browser, `/stats` a dashboard for a projector, `/stats.json` its
data. Once a second every client also gets its own numbers as a `stats` message (a test-only
message, not part of the game protocol). `run_clients` runs headless clients from Python.
"""

import asyncio
import io
import json
import re
import socket
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import ws
from .maps import load_map
from .runner import run
from .sim import Rules

WEB_DIR = Path(__file__).parent / "web"

NAME = re.compile(r"[A-Za-z0-9_-]{1,12}")
PROTOCOL = 1
KEEP_PENDING = 100  # ticks remembered for answers that come after the next tick


def record_ticks(rules: Rules | None = None) -> list[str]:
    """The `tick` messages of one 4-pilot round on `cavern`, as JSON text without the
    spectator-only `moves`."""
    buf = io.StringIO()
    run(load_map("cavern"), ["pilot"] * 4, 1, seed=0, rules=rules, replay=buf)
    out = []
    for line in buf.getvalue().splitlines():
        message = json.loads(line)
        if message["type"] == "tick":
            message.pop("moves", None)
            out.append(json.dumps(message, separators=(",", ":")))
    return out


def tick_templates(ticks: list[str]) -> list[tuple[str, str]]:
    """Each tick message split around its tick number: `prefix + str(n) + suffix`."""
    out = []
    for text in ticks:
        m = re.match(r'(\{"type":"tick","tick":)\d+(.*)\Z', text, re.S)
        if not m:
            raise ValueError("unexpected tick message layout")
        out.append((m.group(1), m.group(2)))
    return out


def percentile(sorted_values: list[int], pct: int) -> int:
    """Nearest-rank percentile of an ascending list; 0 for an empty one."""
    if not sorted_values:
        return 0
    rank = max(1, -(-pct * len(sorted_values) // 100))
    return sorted_values[rank - 1]


def summary(name: str, ticks: int, closed: int, answers: int, late: int, missing: int,
            rtts_ns: list[int], longest: int) -> dict:
    """One stats row; percentages of judged ticks, times in ms."""
    rtts = sorted(rtts_ns)

    def ms(ns: int) -> float:
        return round(ns / 1e6, 1)

    return {
        "name": name,
        "ticks": ticks,
        "answers": answers,
        "late_pct": round(100 * late / closed, 2) if closed else 0.0,
        "missing_pct": round(100 * missing / closed, 2) if closed else 0.0,
        "rtt_p50_ms": ms(percentile(rtts, 50)),
        "rtt_p95_ms": ms(percentile(rtts, 95)),
        "rtt_p99_ms": ms(percentile(rtts, 99)),
        "rtt_max_ms": ms(rtts[-1] if rtts else 0),
        "longest_bad_run": longest,
    }


@dataclass
class Client:
    name: str
    peer: str
    deadline_ns: int
    connected: bool = True
    joined: float = field(default_factory=time.time)
    sent: int = 0          # ticks sent
    closed: int = 0        # ticks judged (the next tick went out, or the client left)
    answers: int = 0
    late: int = 0
    missing: int = 0
    run: int = 0           # current run of late or missing ticks
    longest: int = 0
    rtts: list[int] = field(default_factory=list)
    pending: dict[int, int] = field(default_factory=dict)  # tick -> sent at (ns)
    current: int | None = None       # the last tick sent, not judged yet
    current_rtt: int | None = None   # its round trip, once answered

    def judge(self, bad: bool, missing: bool) -> None:
        self.closed += 1
        if bad:
            self.late += 1
            self.missing += missing
            self.run += 1
            self.longest = max(self.longest, self.run)
        else:
            self.run = 0

    def close_current(self) -> None:
        if self.current is None:
            return
        rtt = self.current_rtt
        self.judge(rtt is None or rtt > self.deadline_ns, rtt is None)
        self.current = None

    def on_sent(self, tick: int, now: int) -> None:
        self.close_current()
        self.sent += 1
        self.current, self.current_rtt = tick, None
        self.pending[tick] = now
        while len(self.pending) > KEEP_PENDING:
            del self.pending[next(iter(self.pending))]

    def on_skipped(self) -> None:
        """A tick that could not be sent in time (the socket was backed up): missing."""
        self.close_current()
        self.sent += 1
        self.judge(True, True)

    def on_move(self, tick: int, now: int) -> None:
        sent_at = self.pending.pop(tick, None)
        if sent_at is None:
            return
        rtt = now - sent_at
        self.answers += 1
        self.rtts.append(rtt)
        if tick == self.current:
            self.current_rtt = rtt

    def on_leave(self, now: int, tick_ns: int) -> None:
        """The client left: judge its last tick, unless it was answered by nobody and had
        less than a tick to be answered (then it never counted)."""
        self.connected = False
        if self.current is not None and self.current_rtt is None and \
                now - self.pending.get(self.current, 0) < tick_ns:
            self.sent -= 1
            self.pending.pop(self.current, None)
            self.current = None
        self.close_current()

    def row(self) -> dict:
        return {**summary(self.name, self.sent, self.closed, self.answers, self.late,
                          self.missing, self.rtts, self.longest),
                "connected": self.connected, "peer": self.peer}


class WifiTest:
    """The test server's state and its WebSocket handler."""

    def __init__(self, rules: Rules | None = None, ticks: list[str] | None = None,
                 log=None):
        self.rules = rules or Rules()
        self.templates = tick_templates(ticks if ticks is not None else record_ticks(self.rules))
        self.clients: list[Client] = []
        self.started = time.time()
        self.clock0 = time.perf_counter_ns()
        self.log = log

    @property
    def tick_ns(self) -> int:
        return self.rules.tick_ms * 1_000_000

    def say(self, text: str) -> None:
        if self.log:
            print(text, file=self.log, flush=True)

    def stats(self) -> dict:
        rows = [c.row() for c in self.clients]
        cs = self.clients
        total = summary("total", sum(c.sent for c in cs), sum(c.closed for c in cs),
                        sum(c.answers for c in cs), sum(c.late for c in cs),
                        sum(c.missing for c in cs), [r for c in cs for r in c.rtts],
                        max((c.longest for c in cs), default=0))
        return {
            "tick_ms": self.rules.tick_ms,
            "deadline_ms": self.rules.deadline_ms,
            "tick_bytes": len(self.templates[0][0]) + len(self.templates[0][1]) + 1,
            "started": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.started)),
            "seconds": int(time.time() - self.started),
            "connected": sum(c.connected for c in cs),
            "clients": rows,
            "total": total,
        }

    def report(self) -> dict:
        """Every client's numbers, for `--report`; clients still connected are judged up to
        their last tick sent."""
        now = time.perf_counter_ns()
        for c in self.clients:
            if c.connected:
                c.on_leave(now, self.tick_ns)
        return self.stats()

    def http(self, path: str):
        if path in ("/", "/index.html"):
            return 200, "text/html; charset=utf-8", (WEB_DIR / "wifitest.html").read_bytes()
        if path == "/stats":
            return 200, "text/html; charset=utf-8", (WEB_DIR / "wifistats.html").read_bytes()
        if path == "/stats.json":
            return 200, "application/json", json.dumps(self.stats()).encode("utf-8")
        return None

    async def start(self, host: str | None = None, port: int = 8000) -> asyncio.Server:
        return await ws.serve({"/bot": self.handle}, self.http, host, port)

    async def error(self, sock: ws.WebSocket, code: str, message: str) -> None:
        try:
            await sock.send(json.dumps({"type": "error", "code": code, "message": message}))
        except ConnectionError:
            pass

    async def handle(self, sock: ws.WebSocket, path: str) -> None:
        try:
            async with asyncio.timeout(10):
                text = await sock.recv()
        except TimeoutError:
            return await self.error(sock, "bad_message", "no hello within 10 s")
        if text is None:
            return
        try:
            hello = json.loads(text)
        except ValueError:
            hello = None
        if not isinstance(hello, dict) or hello.get("type") != "hello":
            return await self.error(sock, "bad_message", "the first message must be hello")
        name = hello.get("name")
        if not isinstance(name, str) or not NAME.fullmatch(name):
            return await self.error(sock, "bad_name",
                                    "name: 1 to 12 characters of A-Z a-z 0-9 _ -")
        if hello.get("protocol") != PROTOCOL:
            return await self.error(sock, "bad_protocol", f"this server speaks protocol {PROTOCOL}")
        if any(c.connected and c.name == name for c in self.clients):
            return await self.error(sock, "name_taken", f"name {name!r} is already connected")
        client = Client(name, sock.peer, self.rules.deadline_ms * 1_000_000)
        self.clients.append(client)
        self.say(f"+ {name} joined from {sock.peer}")
        try:
            await sock.send(json.dumps({"type": "welcome", "id": name,
                                        "rules": self.rules.to_dict()}))
        except ConnectionError:
            client.on_leave(time.perf_counter_ns(), self.tick_ns)
            return
        ticker = asyncio.create_task(self.ticker(sock, client))
        try:
            while (text := await sock.recv()) is not None:
                now = time.perf_counter_ns()
                try:
                    msg = json.loads(text)
                except ValueError:
                    continue
                if isinstance(msg, dict) and msg.get("type") == "move" and \
                        type(msg.get("tick")) is int:
                    client.on_move(msg["tick"], now)
        finally:
            ticker.cancel()
            try:
                await ticker
            except (asyncio.CancelledError, ConnectionError, OSError):
                pass
            client.on_leave(time.perf_counter_ns(), self.tick_ns)
            row = client.row()
            self.say(f"- {name} left: {row['ticks']} ticks, late {row['late_pct']} %, "
                     f"missing {row['missing_pct']} %")

    async def ticker(self, sock: ws.WebSocket, client: Client) -> None:
        """Send this client a tick every `tick_ms`, on the server's common clock, and its
        numbers once a second."""
        tick_ns = self.tick_ns
        slot = (time.perf_counter_ns() - self.clock0) // tick_ns + 1
        n = 0
        last_stats = time.perf_counter_ns()
        while True:
            due = self.clock0 + slot * tick_ns
            delay = due - time.perf_counter_ns()
            if delay > 0:
                await asyncio.sleep(delay / 1e9)
            now = time.perf_counter_ns()
            behind = (now - due) // tick_ns  # whole slots missed: the socket was backed up
            for _ in range(behind):
                client.on_skipped()
                n += 1
            slot += behind + 1
            if now - last_stats >= 1_000_000_000:  # before the tick, so it is all answered
                last_stats = now
                await sock.send(json.dumps({"type": "stats", **client.row()}))
            prefix, suffix = self.templates[n % len(self.templates)]
            text = f"{prefix}{n}{suffix}"
            client.on_sent(n, time.perf_counter_ns())
            await sock.send(text)
            n += 1


def lan_addresses() -> list[str]:
    """The machine's IPv4 addresses other than loopback, best effort, standard library only;
    the address of the default route first, when there is one."""
    found: set[str] = set()
    default = None
    try:  # Linux: ask every interface
        import fcntl
        import struct
        for _, ifname in socket.if_nameindex():
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                try:
                    packed = fcntl.ioctl(s.fileno(), 0x8915,  # SIOCGIFADDR
                                         struct.pack("256s", ifname[:15].encode()))
                    found.add(socket.inet_ntoa(packed[20:24]))
                except OSError:
                    pass
    except (ImportError, OSError, AttributeError):
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    try:  # the address of the default route (no packet is sent)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 1))
            default = s.getsockname()[0]
            found.add(default)
    except OSError:
        pass
    found = {a for a in found if not a.startswith("127.") and a != "0.0.0.0"}
    return sorted(found, key=lambda a: (a != default, tuple(int(p) for p in a.split("."))))


def format_row(row: dict) -> str:
    return (f"{row['name']}: ticks {row['ticks']}, answers {row['answers']}, "
            f"late {row['late_pct']} %, missing {row['missing_pct']} %, "
            f"rtt p50 {row['rtt_p50_ms']} p95 {row['rtt_p95_ms']} p99 {row['rtt_p99_ms']} "
            f"max {row['rtt_max_ms']} ms, longest late run {row['longest_bad_run']}")


async def run_client(url: str, name: str, seconds: float) -> dict:
    """One headless client: answers every tick at once for `seconds`, then waits for the
    server's next `stats` message and returns it, with `received` (ticks seen here) and
    `error` (None, or why it stopped early)."""
    result = {"name": name, "received": 0, "error": None, "stats": None}
    try:
        sock = await ws.connect(url)
    except (OSError, ValueError, TimeoutError) as exc:
        result["error"] = f"cannot connect: {exc}"
        return result
    try:
        await sock.send(json.dumps({"type": "hello", "name": name, "protocol": PROTOCOL}))
        end = time.monotonic() + seconds
        stop_by = end + 3  # after the time is up, wait this long for the last stats
        while True:
            now = time.monotonic()
            if now >= stop_by:
                break
            try:
                async with asyncio.timeout(stop_by - now):
                    text = await sock.recv()
            except TimeoutError:
                break
            if text is None:
                result["error"] = result["error"] or "the server closed the connection"
                break
            msg = json.loads(text)
            kind = msg.get("type")
            if kind == "tick":
                result["received"] += 1
                await sock.send(f'{{"type":"move","tick":{msg["tick"]},"ax":0,"ay":0}}')
            elif kind == "stats":
                result["stats"] = msg
                if time.monotonic() >= end:
                    break
            elif kind == "error":
                result["error"] = f"{msg.get('code')}: {msg.get('message')}"
                break
    except ConnectionError as exc:
        result["error"] = str(exc) or "connection lost"
    finally:
        await sock.close()
    return result


def client_names(name: str, count: int) -> list[str]:
    if count == 1:
        return [name[:12]]
    return [f"{name[:12 - 1 - len(str(count))]}-{i}" for i in range(1, count + 1)]


async def run_clients(url: str, count: int, seconds: float, name: str = "lab") -> list[dict]:
    """`count` headless clients in parallel, named `name-1` ... `name-N`."""
    return await asyncio.gather(*(run_client(url, n, seconds)
                                  for n in client_names(name, count)))


def print_client_results(results: list[dict], out=None) -> None:
    out = out or sys.stdout
    for r in results:
        if r["stats"]:
            line = format_row({**r["stats"], "name": r["name"]})
        else:
            line = f"{r['name']}: no numbers from the server"
        line += f" (received {r['received']} ticks)"
        if r["error"]:
            line += f" [stopped: {r['error']}]"
        print(line, file=out)
