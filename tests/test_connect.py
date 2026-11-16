"""`spacetaxi connect`: a local bot flown in the arena over WebSocket."""

import asyncio
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import types
import urllib.request

import pytest

from spacetaxi.cli import NEW_BOT_HEADER, main
from spacetaxi.connect import DEFAULT_SERVER, Client, Fatal, server_url
from spacetaxi.maps import load_map
from spacetaxi.server import ArenaServer
from spacetaxi.sim import Rules

RULES = Rules(tick_ms=50, deadline_ms=40, round_ticks=20)
ENV = {**os.environ, "PYTHONUNBUFFERED": "1"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Lines:
    """A process's stdout, read line by line in a thread so tests can wait with a deadline."""

    def __init__(self, proc: subprocess.Popen):
        self.proc = proc
        self.lines: list[str] = []
        self._q: queue.Queue = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self.proc.stdout:
            self._q.put(line)
        self._q.put(None)

    def wait_for(self, text: str, timeout: float = 20) -> str:
        deadline = time.monotonic() + timeout
        for line in self.lines:
            if text in line:
                return line
        while (left := deadline - time.monotonic()) > 0:
            try:
                line = self._q.get(timeout=left)
            except queue.Empty:
                break
            if line is None:
                break
            self.lines.append(line)
            if text in line:
                return line
        raise AssertionError(f"no line with {text!r} in:\n{''.join(self.lines)}")


def spawn(*args: str) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-m", "spacetaxi", *args], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, env=ENV)


def serve(port: int, fill: str = "0.5") -> subprocess.Popen:
    return spawn("serve", "--port", str(port), "--fill-seconds", fill,
                 "--rule", "tick_ms=50", "--rule", "deadline_ms=40", "--rule", "round_ticks=20")


def stop(*procs: subprocess.Popen) -> None:
    for proc in procs:
        proc.terminate()
    for proc in procs:
        proc.wait(5)


def leaderboard(port: int) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/leaderboard.json", timeout=2) as r:
        return json.loads(r.read())


def test_greedy_and_pilot_connect_and_reach_the_leaderboard():
    port = free_port()
    server = serve(port, fill="3")  # both clients are in the queue before the round fills
    clients = []
    try:
        Lines(server).wait_for("Ctrl-C")
        url = f"ws://127.0.0.1:{port}/bot"
        clients = [spawn("connect", "--bot", "greedy", "--name", "alice", "--server", url),
                   spawn("connect", "--bot", "pilot", "--name", "bob", "--server", url)]
        outs = [Lines(c) for c in clients]
        for out, other in zip(outs, ["bob", "alice"]):
            out.wait_for("queued: position")
            assert other in out.wait_for("starts: you vs")
            assert "(you)" in out.wait_for(" over: 1. ")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            names = {row["name"] for row in leaderboard(port)["leaderboard"]}
            if {"alice", "bob"} <= names:
                break
            time.sleep(0.2)
        assert {"alice", "bob"} <= names
        # the bots answered their ticks: hardly any coasted for being late
        rows = {row["name"]: row for row in leaderboard(port)["leaderboard"]}
        assert rows["alice"]["late_pct"] < 50 and rows["bob"]["late_pct"] < 50
    finally:
        stop(*clients, server)


def test_reconnects_after_the_server_restarts():
    port = free_port()
    server = serve(port)
    client = None
    try:
        Lines(server).wait_for("Ctrl-C")
        client = spawn("connect", "--bot", "greedy", "--name", "carol",
                       "--server", f"ws://127.0.0.1:{port}/bot")
        out = Lines(client)
        out.wait_for("connected to")
        out.wait_for("queued: position")
        stop(server)
        out.wait_for("lost the connection")
        time.sleep(2.5)  # at least one retry finds nobody listening
        server = serve(port)
        Lines(server).wait_for("Ctrl-C")
        n = len(out.lines)
        out.wait_for("starts: you vs")
        assert any("connected to" in line for line in out.lines[n - 3:])
        assert client.poll() is None
    finally:
        stop(*([client] if client else []), server)


def raising_bot() -> types.ModuleType:
    bot = types.ModuleType("raiser")

    def move(state):
        raise RuntimeError(f"boom at {state['tick']}")

    bot.move = move
    return bot


def test_a_bot_that_raises_in_move_keeps_its_connection_and_coasts():
    async def main_():
        server = ArenaServer(load_map("cavern"), RULES, 0.1, seed=1)
        srv = await server.start("127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        said: list[str] = []
        client = Client(f"ws://127.0.0.1:{port}/bot", "dave", raising_bot(), say=said.append)
        task = asyncio.create_task(client.run())
        async with asyncio.timeout(15):
            while sum(" over: " in s for s in said) < 2:
                await asyncio.sleep(0.05)
        connected = "dave" in server.bots
        table = server.leaderboard.table()
        task.cancel()
        srv.close()
        return said, connected, table

    said, connected, table = asyncio.run(main_())
    assert connected
    tracebacks = [s for s in said if "move() raised" in s]
    assert len(tracebacks) == 2  # once per round, not once per tick
    assert "RuntimeError: boom at" in tracebacks[0] and "Traceback" in tracebacks[0]
    row = next(r for r in table if r["name"] == "dave")
    assert row["rounds"] == 2 and row["late_pct"] == 0  # every tick answered, with no thrust
    assert not any("lost the connection" in s for s in said)


def test_a_taken_name_is_said_in_plain_words():
    async def main_():
        server = ArenaServer(load_map("cavern"), RULES, 60, seed=1)
        srv = await server.start("127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        url = f"ws://127.0.0.1:{port}/bot"
        first = Client(url, "alice", raising_bot(), say=lambda s: None)
        task = asyncio.create_task(first.run())
        async with asyncio.timeout(5):
            while "alice" not in server.bots:
                await asyncio.sleep(0.02)
        second = Client(url, "alice", raising_bot(), say=lambda s: None)
        try:
            async with asyncio.timeout(5):
                await second.run()
        except Fatal as exc:
            error = str(exc)
        else:
            error = None
        task.cancel()
        srv.close()
        return error

    error = asyncio.run(main_())
    assert error and "the name alice is taken" in error


def test_server_defaults_to_the_environment_then_localhost(monkeypatch):
    monkeypatch.delenv("SPACETAXI_SERVER", raising=False)
    assert server_url(None) == DEFAULT_SERVER == "ws://localhost:8000/bot"
    monkeypatch.setenv("SPACETAXI_SERVER", "ws://arena:9000/bot")
    assert server_url(None) == "ws://arena:9000/bot"
    assert server_url("ws://other:1/bot") == "ws://other:1/bot"


def test_cli_connect_bad_arguments(monkeypatch):
    monkeypatch.delenv("SPACETAXI_SERVER", raising=False)
    assert main(["connect", "--bot", "nope", "--name", "alice"]) == 2
    assert main(["connect", "--bot", "greedy", "--name", "way-too-long-name"]) == 2
    assert main(["connect", "--bot", "greedy", "--name", "a", "--server", "http://x/"]) == 2
    monkeypatch.setenv("SPACETAXI_SERVER", "http://x/")
    assert main(["connect", "--bot", "greedy", "--name", "a"]) == 2


def test_starter_header_says_how_to_connect():
    assert "spacetaxi connect --bot ./mybot.py --name" in NEW_BOT_HEADER
    assert "SPACETAXI_SERVER" in NEW_BOT_HEADER
