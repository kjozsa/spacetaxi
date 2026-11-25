"""Hostile clients against `spacetaxi serve`: huge messages, floods, deep JSON, name spam, kick
and ban. Each attack runs while a probe bot measures the gaps between its ticks: the server
must stay up and the probe's ticks keep their rhythm (every gap under 1.5 ticks)."""

import asyncio
import base64
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from spacetaxi import ws
from spacetaxi.maps import load_map
from spacetaxi.server import BOT_MAX_MESSAGE, MAX_BOTS_PER_IP, ArenaServer, parse
from spacetaxi.sim import Rules

ROOT = Path(__file__).resolve().parents[1]
TICK_MS = 50


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Serve:
    """`spacetaxi serve` in a subprocess on a short tick, its output in a file."""

    def __init__(self, tmp: Path, *extra: str, round_ticks: int = 100, fill: float = 1.0):
        self.port = free_port()
        self.tmp = tmp
        self.out = tmp / f"serve-{self.port}.out"
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        with open(self.out, "w") as sink:
            self.proc = subprocess.Popen(
                [sys.executable, "-m", "spacetaxi", "serve", "--port", str(self.port),
                 "--rule", f"tick_ms={TICK_MS}", "--rule", "deadline_ms=30",
                 "--rule", f"round_ticks={round_ticks}", "--fill-seconds", str(fill),
                 "--seed", "1", "--benchmark", "greedy=1", "--benchmark", "pilot=1", *extra],
                stdin=subprocess.DEVNULL, stdout=sink, stderr=subprocess.STDOUT, env=env)
        for _ in range(200):
            try:
                self.get("/leaderboard.json")
                return
            except OSError:
                time.sleep(0.05)
        raise AssertionError(self.output())

    def get(self, path: str) -> dict:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=2) as r:
            return json.load(r)

    def output(self) -> str:
        return self.out.read_text()

    def stop(self) -> str:
        assert self.proc.poll() is None, "the server died:\n" + self.output()
        self.proc.terminate()
        self.proc.wait(timeout=10)
        out = self.output()
        assert "Traceback" not in out, out
        return out


@pytest.fixture
def serve(tmp_path):
    servers = []

    def make(*extra, **kw):
        servers.append(Serve(tmp_path, *extra, **kw))
        return servers[-1]

    yield make
    for s in servers:
        if s.proc.poll() is None:
            s.proc.kill()


class Raw:
    """A blocking WebSocket client that can send anything, frame by frame."""

    def __init__(self, port: int, path: str = "/bot"):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall(f"GET {path} HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\n"
                       f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                       f"Sec-WebSocket-Version: 13\r\n\r\n".encode())
        self.buf = b""
        while b"\r\n\r\n" not in self.buf:
            self.buf += self.s.recv(4096)
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        assert b" 101 " in head

    def send(self, message, opcode: int = ws.TEXT, fin: bool = True) -> None:
        text = message if isinstance(message, (str, bytes)) else json.dumps(message)
        data = text.encode() if isinstance(text, str) else text
        self.s.sendall(ws.encode_frame(opcode, data, mask=True, fin=fin))

    def hello(self, name: str) -> None:
        self.send({"type": "hello", "name": name, "protocol": 1})

    def header(self, n: int, opcode: int = ws.TEXT) -> None:
        """Only a frame header claiming `n` payload bytes."""
        self.s.sendall(bytes([0x80 | opcode, 0x80 | 127]) + struct.pack("!Q", n) + os.urandom(4))

    def _take(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.s.recv(65536)
            if not chunk:
                raise EOFError
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv(self) -> dict | None:
        """The next message from the server; None once it closes."""
        try:
            b0, b1 = self._take(2)
            n = b1 & 0x7F
            if n == 126:
                (n,) = struct.unpack("!H", self._take(2))
            elif n == 127:
                (n,) = struct.unpack("!Q", self._take(8))
            payload = self._take(n)
        except (EOFError, ConnectionError, TimeoutError):
            return None
        if b0 & 0x0F == ws.CLOSE:
            return None
        return json.loads(payload)

    def until(self, kind: str) -> dict | None:
        while (msg := self.recv()) is not None:
            if msg["type"] in (kind, "error"):
                return msg
        return None

    def closed(self) -> bool:
        """Whether the server ends the connection (after anything still in flight)."""
        while (msg := self.recv()) is not None:
            if msg["type"] == "error":
                return False
        return True

    def close(self) -> None:
        self.s.close()


class Probe(threading.Thread):
    """A good bot in its own thread and event loop: joins, plays one round, notes when each
    tick arrives."""

    def __init__(self, port: int, name: str = "probe"):
        super().__init__(daemon=True)
        self.port, self.name = port, name
        self.started = threading.Event()
        self.times: list[float] = []
        self.error = None

    def run(self) -> None:
        try:
            asyncio.run(self.main())
        except Exception as exc:  # seen by the test through max_gap
            self.error = exc

    async def main(self) -> None:
        sock = await ws.connect(f"ws://127.0.0.1:{self.port}/bot")
        await sock.send(json.dumps({"type": "hello", "name": self.name, "protocol": 1}))
        while (text := await sock.recv()) is not None:
            msg = json.loads(text)
            if msg["type"] == "round_start":
                self.started.set()
            elif msg["type"] == "tick":
                self.times.append(time.perf_counter())
                await sock.send(json.dumps({"type": "move", "tick": msg["tick"], "ax": 0,
                                            "ay": 0}))
            elif msg["type"] in ("round_end", "error"):
                break
        await sock.close()

    def max_gap_ms(self) -> float:
        self.join(timeout=30)
        assert self.error is None, self.error
        assert len(self.times) > 10, self.times
        return 1000 * max(b - a for a, b in zip(self.times, self.times[1:]))


def wait_round(probe: Probe) -> None:
    assert probe.started.wait(10), "the round did not start"


def assert_rhythm(probe: Probe) -> None:
    gap = probe.max_gap_ms()
    assert gap < 1.5 * TICK_MS, f"max tick gap {gap:.0f} ms"


def test_a_frame_or_message_over_64_kb_gets_too_big_and_a_64_kb_move_is_accepted(serve, tmp_path):
    srv = serve("--max-bots-per-ip", "10", "--replays", str(tmp_path / "replays"))
    big, frag, fine = Raw(srv.port), Raw(srv.port), Raw(srv.port)
    for name, sock in (("big", big), ("frag", frag), ("fine", fine)):
        sock.hello(name)
        assert sock.until("welcome")["type"] == "welcome"
    probe = Probe(srv.port)
    probe.start()
    wait_round(probe)
    tick = fine.until("tick")["tick"]
    move = {"type": "move", "tick": tick, "ax": 1, "ay": 0, "debug": ""}
    move["debug"] = "x" * (BOT_MAX_MESSAGE - len(json.dumps(move)))
    fine.send(move)
    assert len(json.dumps(move)) == BOT_MAX_MESSAGE

    big.header(16 * 1024 * 1024)  # the 16 MB payload never follows: the header is enough
    assert big.until("error") == {"type": "error", "code": "too_big",
                                  "message": f"messages are at most {BOT_MAX_MESSAGE} bytes"}
    assert big.closed()
    piece = "y" * 40000
    frag.send(piece, fin=False)
    frag.send(piece, opcode=ws.CONT, fin=False)  # 80 000 bytes so far
    assert frag.until("error")["code"] == "too_big"
    assert frag.closed()
    assert fine.until("tick")["type"] == "tick"  # still in the round
    assert_rhythm(probe)
    fine.close()
    srv.stop()
    replay = (tmp_path / "replays" / "round-00001.jsonl").read_text().splitlines()
    played = [json.loads(line) for line in replay if f'"tick":{tick},' in line]
    assert played[0]["moves"]["fine"]["ax"] == 1  # the 64 KB move counted
    assert played[0]["moves"]["fine"]["debug"] == "x" * 200


def test_the_header_alone_decides_too_big():
    async def main():
        reader = asyncio.StreamReader()
        reader.feed_data(bytes([0x81, 0x80 | 127]) + struct.pack("!Q", 16 * 1024 * 1024))

        class Writer:
            def get_extra_info(self, key):
                return None
        sock = ws.WebSocket(reader, Writer(), client=False, max_message=BOT_MAX_MESSAGE)
        async with asyncio.timeout(1):  # no payload was fed: reading it would hang
            assert await sock.recv() is None
        return sock.too_big

    assert asyncio.run(main())


def test_a_long_debug_reaches_spectators_and_the_replay_cut_to_200(serve, tmp_path):
    srv = serve("--replays", str(tmp_path / "replays"))
    spec = Raw(srv.port, "/spectate")
    bot = Raw(srv.port)
    bot.hello("talker")
    probe = Probe(srv.port)
    probe.start()
    wait_round(probe)
    tick = bot.until("tick")["tick"]
    bot.send({"type": "move", "tick": tick, "ax": 0, "ay": 0, "debug": "d" * 10000})
    nxt = bot.until("tick")["tick"]
    bot.send({"type": "move", "tick": nxt, "ax": 0, "ay": 0,
              "debug": {"say": "s" * 10000, "path": [[1, 1]] * 1000}})
    seen = {}
    while len(seen) < 2:
        msg = spec.recv()
        assert msg is not None
        if msg["type"] == "tick" and msg["tick"] in (tick, nxt):
            seen[msg["tick"]] = msg["moves"]["talker"]["debug"]
    assert seen[tick] == "d" * 200
    assert seen[nxt] == {"say": "s" * 200, "path": [[1, 1]] * 200}
    assert_rhythm(probe)
    srv.stop()
    replay = (tmp_path / "replays" / "round-00001.jsonl").read_text()
    assert "d" * 201 not in replay and "d" * 200 in replay
    assert "s" * 201 not in replay


def test_a_move_flood_gets_flooding_and_is_closed(serve):
    srv = serve()
    bot = Raw(srv.port)
    bot.hello("flood")
    probe = Probe(srv.port)
    probe.start()
    wait_round(probe)
    tick = bot.until("tick")["tick"]
    move = ws.encode_frame(ws.TEXT, json.dumps(
        {"type": "move", "tick": tick, "ax": 0, "ay": 0}).encode(), mask=True)
    try:
        bot.s.sendall(move * 5000)
    except ConnectionError:
        pass
    assert bot.until("error")["code"] == "flooding"
    assert bot.closed()
    assert_rhythm(probe)
    assert "- flood left" in srv.stop()


def test_deeply_nested_json_is_a_bad_message_without_a_traceback(serve):
    deep = "[" * 200_000 + "]" * 200_000
    assert parse(deep) is None
    assert parse('{"a":' * 200_000 + "1" + "}" * 200_000) is None
    srv = serve("--max-bots-per-ip", "10")
    fits = "[" * 30_000 + "]" * 30_000  # deep enough to recurse too far, under 64 KB
    hello = Raw(srv.port)
    hello.send(fits)
    assert hello.until("error")["code"] == "bad_message"
    huge = Raw(srv.port)
    try:
        huge.send(deep)  # 400 KB: over the size limit first
    except ConnectionError:  # the server stops reading after the header
        pass
    assert huge.until("error")["code"] == "too_big"
    mover = Raw(srv.port)
    mover.hello("deep")
    probe = Probe(srv.port)
    probe.start()
    wait_round(probe)
    mover.until("tick")
    mover.send(fits)
    assert mover.until("error")["code"] == "bad_message"
    assert mover.closed()
    assert_rhythm(probe)
    out = srv.stop()
    assert "RecursionError" not in out


def test_at_most_4_bots_from_one_address_house_bots_aside(serve):
    srv = serve("--house", "gus")  # the default cap
    assert MAX_BOTS_PER_IP == 4
    socks = []
    for i in range(4):
        socks.append(Raw(srv.port))
        socks[-1].hello(f"b{i}")
        assert socks[-1].until("welcome")["type"] == "welcome"
    fifth = Raw(srv.port)
    fifth.hello("b4")
    err = fifth.until("error")
    assert err["code"] == "too_many"
    house = Raw(srv.port)
    house.hello("GUS")  # names ignore case
    assert house.until("welcome")["type"] == "welcome"
    socks[0].close()
    for _ in range(50):  # the server notices the leave, then a seat is free again
        if "- b0 left" in srv.output():
            break
        time.sleep(0.05)
    again = Raw(srv.port)
    again.hello("b4")
    assert again.until("welcome")["type"] == "welcome"
    srv.stop()


def test_max_bots_caps_the_total_house_bots_aside():
    async def main():
        server = ArenaServer({"cavern": load_map("cavern")}, Rules(tick_ms=50), 60, seed=1,
                             house=["gus"], max_bots=2)
        srv = await server.start("127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        got, socks = [], []
        for name in ("a", "b", "c", "gus"):
            socks.append(sock := await ws.connect(f"ws://127.0.0.1:{port}/bot"))
            await sock.send(json.dumps({"type": "hello", "name": name, "protocol": 1}))
            got.append(json.loads(await sock.recv()))
        srv.close()
        return got

    got = asyncio.run(main())
    assert [m["type"] for m in got] == ["welcome", "welcome", "error", "welcome"]
    assert got[2]["code"] == "full"


def test_kick_and_ban_from_the_control_pipe(serve, tmp_path):
    pipe = tmp_path / "control"
    standings = tmp_path / "standings.json"
    srv = serve("--control", str(pipe), "--standings", str(standings), "--log",
                str(tmp_path / "log.jsonl"), round_ticks=10, fill=0.3)

    def command(line):
        with open(pipe, "w") as p:
            p.write(line + "\n")

    spam, kid = Raw(srv.port), Raw(srv.port)
    spam.hello("Spam")
    kid.hello("kid")
    assert spam.until("round_end")["type"] == "round_end"
    names = lambda path, key: [r["name"] for r in srv.get(path)[key]]  # noqa: E731
    assert "Spam" in names("/standings.json", "standings")
    assert "Spam" in names("/leaderboard.json", "leaderboard")

    command("kick kid")
    assert kid.until("error")["code"] == "kicked"
    assert kid.closed()
    kid = Raw(srv.port)
    kid.hello("kid")  # a kicked bot may come back
    assert kid.until("welcome")["type"] == "welcome"

    command("ban spam")  # names ignore case
    assert spam.until("error")["code"] == "banned"
    assert spam.closed()
    back = Raw(srv.port)
    back.hello("Spam")
    assert back.until("error")["code"] == "banned"
    assert "Spam" not in names("/standings.json", "standings")
    kid_row = [r for r in srv.get("/standings.json")["standings"] if r["name"] == "kid"][0]
    assert kid_row["milestones"]["hello"]["place"] == 1  # Spam's place went to the next
    assert "Spam" not in names("/leaderboard.json", "leaderboard")
    board = srv.get("/leaderboard.json")
    assert "Spam" not in board["next_wave"]["waitlist"] + sum(board["next_wave"]["arenas"], [])
    assert json.loads(standings.read_text())["banned"] == ["spam"]
    kid.close()
    out = srv.stop()
    assert "kicked kid" in out and "banned spam and disconnected it" in out

    srv = serve("--control", str(pipe), "--standings", str(standings), "--log",
                str(tmp_path / "log.jsonl"), round_ticks=10, fill=0.3)
    again = Raw(srv.port)
    again.hello("Spam")  # the ban survives a restart
    assert again.until("error")["code"] == "banned"
    assert "Spam" not in names("/leaderboard.json", "leaderboard")
    command("unban Spam")
    for _ in range(50):
        if "unbanned Spam" in srv.output():
            break
        time.sleep(0.05)
    welcome = Raw(srv.port)
    welcome.hello("Spam")
    assert welcome.until("welcome")["type"] == "welcome"
    srv.stop()


def test_a_never_reading_bot_and_50_never_reading_spectators_do_not_move_the_ticks(serve):
    srv = serve()
    lazy = Raw(srv.port)
    lazy.hello("lazy")
    lazy.s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    spectators = [Raw(srv.port, "/spectate") for _ in range(50)]
    probe = Probe(srv.port)
    probe.start()
    wait_round(probe)
    assert_rhythm(probe)
    for s in [lazy, *spectators]:
        s.close()
    srv.stop()


def test_a_message_of_endless_empty_fragments_does_not_stall_the_ticks(serve):
    srv = serve()
    bot = Raw(srv.port)
    bot.hello("shards")
    probe = Probe(srv.port)
    probe.start()
    wait_round(probe)
    start = ws.encode_frame(ws.TEXT, b"", mask=True, fin=False)
    piece = ws.encode_frame(ws.CONT, b"", mask=True, fin=False)
    try:
        bot.s.sendall(start + piece * 20_000)  # 120 KB on the wire, no payload at all
    except OSError:  # the server may close on it
        pass
    assert_rhythm(probe)
    srv.stop()


def test_kick_disconnects_a_bot_that_does_not_read(serve, tmp_path):
    pipe = tmp_path / "control"
    srv = serve("--control", str(pipe))
    deaf = Raw(srv.port)
    deaf.hello("deaf")
    assert deaf.until("welcome")["type"] == "welcome"
    deaf.s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    deaf.s.settimeout(1)
    pings = ws.encode_frame(ws.PING, b"p" * 125, mask=True) * 100
    try:  # pings whose pongs are never read, until the server's send buffer is full
        for _ in range(1000):
            deaf.s.sendall(pings)
    except OSError:
        pass
    with open(pipe, "w") as p:
        p.write("kick deaf\n")
    for _ in range(100):
        if "- deaf left" in srv.output():
            break
        time.sleep(0.05)
    assert "- deaf left" in srv.output(), "kicked, but still connected 5 s later"
    deaf.close()
    srv.stop()


# connects to /spectate and streams empty text messages (masked with a zero key) until killed,
# connecting again whenever the server drops it
SPECTATOR_FLOOD = """
import socket, sys, time
chunk = bytes([0x81, 0x80, 0, 0, 0, 0]) * 40000
while True:
    try:
        s = socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=5)
        s.sendall(b"GET /spectate HTTP/1.1\\r\\nHost: x\\r\\nUpgrade: websocket\\r\\n"
                  b"Connection: Upgrade\\r\\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\\r\\n"
                  b"Sec-WebSocket-Version: 13\\r\\n\\r\\n")
        s.recv(4096)
        while True:
            s.sendall(chunk)
    except OSError:
        time.sleep(0.05)
"""


def test_spectators_streaming_messages_do_not_stall_the_ticks(serve):
    srv = serve()
    bot = Raw(srv.port)
    bot.hello("rider")
    probe = Probe(srv.port)
    probe.start()
    wait_round(probe)
    floods = [subprocess.Popen([sys.executable, "-c", SPECTATOR_FLOOD, str(srv.port)])
              for _ in range(4)]  # processes of their own, so they cannot slow the probe's loop
    try:
        assert_rhythm(probe)
    finally:
        for p in floods:
            p.kill()
            p.wait(timeout=5)
    srv.stop()
