"""Switching the map between waves on a running server (`serve --map A --map B`, `map NAME`)."""

import asyncio
import io
import json
import os
import stat
import sys

import pytest

from spacetaxi import ws
from spacetaxi.cli import (RuleError, load_maps, main, make_pipe, parse_benchmarks, read_commands,
                           read_control)
from spacetaxi.maps import load_map
from spacetaxi.server import ArenaServer
from spacetaxi.show import load_replay
from spacetaxi.sim import Rules

RULES = Rules(tick_ms=50, deadline_ms=30, round_ticks=20)
UNBEATABLE = {"greedy": 10 ** 6, "pilot": 10 ** 6}
BEATEN = {"greedy": -10 ** 6, "pilot": -10 ** 6}


def two_maps(**kw) -> ArenaServer:
    maps = {"cavern": load_map("cavern"), "classic": load_map("classic")}
    return ArenaServer(maps, RULES, kw.pop("fill", 0.2), seed=1, **kw)


async def recv(sock, timeout=5.0):
    async with asyncio.timeout(timeout):
        text = await sock.recv()
    return None if text is None else json.loads(text)


async def join(port, name):
    sock = await ws.connect(f"ws://127.0.0.1:{port}/bot")
    await sock.send(json.dumps({"type": "hello", "name": name, "protocol": 1}))
    return sock


async def play(sock, on_start=lambda msg: None):
    """Coast through one round; returns its round_start."""
    start = None
    while True:
        msg = await recv(sock)
        assert msg is not None
        if msg["type"] == "round_start":
            start = msg
            on_start(msg)
        elif msg["type"] == "tick":
            await sock.send(json.dumps({"type": "move", "ax": 0, "ay": 0, "tick": msg["tick"]}))
        elif msg["type"] == "round_end":
            return start


def test_map_command_switches_the_next_wave_only(tmp_path):
    log, replays, lines = tmp_path / "log.jsonl", tmp_path / "replays", []
    answers, standings = [], []

    async def main_():
        server = two_maps(log=log, replays=replays, say=lines.append,
                          benchmarks={"cavern": UNBEATABLE, "classic": BEATEN})
        srv = await server.start("127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        a, b = await join(port, "a"), await join(port, "b")

        def mid_wave(msg):  # the wave has started on cavern: switch now
            if not answers:
                standings.append(json.loads(server.http("/standings.json")[2]))
                answers.append(server.command("map classic"))
                answers.append(server.command("maps"))

        first = await asyncio.gather(play(a, mid_wave), play(b))
        after_first = server.standings.awards
        beat_after_first = {n for n, got in after_first.items() if "beat_greedy" in got}
        second = await asyncio.gather(play(a), play(b))
        await asyncio.sleep(0.1)
        standings.append(json.loads(server.http("/standings.json")[2]))
        for s in (a, b):
            await s.close()
        srv.close()
        return server, first, second, beat_after_first

    server, first, second, beat_after_first = asyncio.run(main_())
    assert answers == ["next wave: classic", "maps: cavern (current), classic (next)"]
    assert [s["map_name"] for s in first] == ["cavern", "cavern"]
    assert first[0]["map"] == list(load_map("cavern").rows)
    assert [s["map_name"] for s in second] == ["classic", "classic"]
    assert second[0]["map"] == list(load_map("classic").rows)
    assert second[0]["pads"] == [p.to_dict() for p in load_map("classic").pads]
    # beat_greedy compares against the benchmark of the round's map
    assert beat_after_first == set()
    assert {n for n, got in server.standings.awards.items() if "beat_greedy" in got} == {"a", "b"}
    assert standings[0]["map_name"] == "cavern" and standings[0]["benchmarks"] == UNBEATABLE
    assert standings[1]["map_name"] == "classic" and standings[1]["benchmarks"] == BEATEN
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert [r["map_name"] for r in records] == ["cavern", "classic"]
    for n, name in ((1, "cavern"), (2, "classic")):
        replay = load_replay(str(replays / f"round-{n:05d}.jsonl"))
        assert replay[n]["start"]["map_name"] == name
    assert any(line.startswith("wave 2 on classic: ") for line in lines)


def test_unknown_map_is_an_error_and_keeps_the_next_map():
    server = two_maps()
    assert server.command("map nowhere") == "unknown map 'nowhere'; maps: cavern, classic"
    assert server.next_map == "cavern"
    assert server.command("maps") == "maps: cavern (current, next), classic"
    assert server.command("") is None
    assert server.command("hello").startswith("commands: ")
    assert json.loads(server.http("/leaderboard.json")[2])["next_map"] == "cavern"


def test_watch_and_fly_pages_show_the_next_map():
    server = two_maps()
    cavern = server.http("/watch")[2]
    server.command("map classic")
    classic = server.http("/watch")[2]
    assert cavern != classic
    assert json.dumps(load_map("classic").rows[1]).encode() in classic
    assert json.dumps(load_map("classic").rows[1]).encode() in server.http("/fly")[2]


def test_benchmarks_of_every_map_are_simulated_and_printed():
    lines = []

    async def main_():
        server = two_maps(say=lines.append, benchmarks={"cavern": {"greedy": 5}})
        srv = await server.start("127.0.0.1", 0)
        for _ in range(600):
            if all(v is not None for m in server.map_benchmarks.values() for v in m.values()):
                break
            await asyncio.sleep(0.1)
        srv.close()
        return server

    server = asyncio.run(main_())
    assert server.map_benchmarks["cavern"]["greedy"] == 5
    assert type(server.map_benchmarks["classic"]["greedy"]) is int
    assert any(line.startswith("benchmarks on cavern (simulated") for line in lines)
    assert any(line.startswith("benchmarks on classic (simulated") for line in lines)


def test_parse_benchmarks_first_map_and_by_name():
    got = parse_benchmarks(["greedy=1", "classic:greedy=2", "cavern:pilot=3"],
                           ["cavern", "classic"])
    assert got == {"cavern": {"greedy": 1, "pilot": 3}, "classic": {"greedy": 2}}
    with pytest.raises(RuleError, match="no map 'canyon'"):
        parse_benchmarks(["canyon:greedy=1"], ["cavern", "classic"])
    with pytest.raises(RuleError):
        parse_benchmarks(["classic:bogus=1"], ["cavern", "classic"])


def test_load_maps_keeps_order_and_refuses_duplicates(tmp_path):
    path = tmp_path / "mine.txt"
    path.write_text("\n".join(load_map("classic").rows) + "\n")
    assert list(load_maps(["classic", str(path), "cavern"])) == ["classic", "mine", "cavern"]
    with pytest.raises(RuleError, match="already given"):
        load_maps(["cavern", "cavern"])


def test_serve_rejects_a_benchmark_for_a_map_not_served(capsys):
    assert main(["serve", "--map", "cavern", "--benchmark", "classic:greedy=1"]) == 2
    assert "no map 'classic'" in capsys.readouterr().err


def test_commands_are_not_read_without_a_terminal(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("map classic\n"))

    async def main_():
        return read_commands(two_maps())

    assert asyncio.run(main_()) is False


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="named pipes need POSIX")
def test_control_pipe_switches_the_map_and_survives_a_writer_closing(tmp_path, capsys):
    pipe = tmp_path / "control"
    pipe.write_bytes(b"")
    with pytest.raises(OSError, match="not a named pipe"):
        make_pipe(str(pipe))
    pipe.unlink()
    os.mkfifo(pipe)  # a stale pipe is replaced
    make_pipe(str(pipe))
    assert stat.S_ISFIFO(os.lstat(pipe).st_mode)

    async def write(text):
        def blocking():
            with open(pipe, "w") as f:  # waits for the reader
                f.write(text)
        await asyncio.to_thread(blocking)

    async def until(check):
        for _ in range(100):
            if check():
                return True
            await asyncio.sleep(0.02)
        return False

    async def main_():
        server = two_maps()
        read_control(server, str(pipe))
        await write("map classic\n")
        switched = await until(lambda: server.next_map == "classic")
        await write("maps\nmap cavern\n")  # a second writer after the first closed
        back = await until(lambda: server.next_map == "cavern")
        return switched, back

    assert asyncio.run(main_()) == (True, True)
    out = capsys.readouterr().out
    assert "next wave: classic" in out
    assert "maps: cavern (current), classic (next)" in out
