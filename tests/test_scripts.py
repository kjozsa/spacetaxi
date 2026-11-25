"""scripts/arena.fish end to end: up, house, status, map, kick, ban, unhouse, down on a free port."""

import os
import shutil
import signal
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

from spacetaxi.show import load_replay

ROOT = Path(__file__).resolve().parent.parent
ARENA = ROOT / "scripts" / "arena.fish"

pytestmark = pytest.mark.skipif(shutil.which("fish") is None or shutil.which("uv") is None,
                                reason="needs fish and uv")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def arena(*args, port):
    return subprocess.run(["fish", str(ARENA), *args, "-p", str(port)], cwd=ROOT,
                          capture_output=True, text=True, timeout=60)


def answers(port) -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/leaderboard.json", timeout=1)
        return True
    except OSError:
        return False


def until(check, seconds=30.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.2)
    return False


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def round_maps(replays: Path) -> list[str]:
    maps = []
    for path in sorted(replays.glob("round-*.jsonl")):
        try:
            maps += [r["start"]["map_name"] for r in load_replay(str(path)).values()]
        except Exception:  # a file still being written
            pass
    return maps


def test_arena_script_up_house_status_map_unhouse_down():
    port = free_port()
    folder = ROOT / "out" / "arena" / str(port)
    out = folder.parent / f"serve-{port}.out"
    folder.parent.mkdir(parents=True, exist_ok=True)
    serve = None
    try:
        with open(out, "w") as sink:
            serve = subprocess.Popen(
                ["fish", str(ARENA), "up", "--fresh", "-p", str(port), "-m", "cavern",
                 "-m", "classic", "-r", "round_ticks=30", "-f", "0.5"],
                cwd=ROOT, stdin=subprocess.DEVNULL, stdout=sink, stderr=subprocess.STDOUT)
        assert until(lambda: answers(port)), out.read_text()
        assert (folder / "control").is_fifo()

        again = arena("up", port=port)
        assert again.returncode != 0 and "already answers" in again.stderr

        house = arena("house", port=port)
        assert house.returncode == 0, house.stderr
        assert "ace (pilot)" in house.stdout and "gus (greedy)" in house.stdout
        pids = [int((folder / f"house-{n}.pid").read_text()) for n in ("ace", "gus")]

        def playing():
            status = arena("status", port=port)
            return "house: ace gus" in status.stdout and "0 waiting, 2 playing" in status.stdout

        assert until(playing)
        status = arena("status", port=port).stdout
        assert "map cavern, next cavern" in status
        assert all(len(line) <= 80 for line in status.splitlines())

        sent = arena("map", "classic", port=port)
        assert sent.returncode == 0, sent.stderr
        assert until(lambda: "next wave: classic" in out.read_text(), 10)
        assert until(lambda: "classic" in round_maps(folder / "replays"), 60)
        maps = round_maps(folder / "replays")
        assert maps[0] == "cavern"
        assert maps[maps.index("classic"):] == ["classic"] * (len(maps) - maps.index("classic"))
        assert "next cavern" not in arena("status", port=port).stdout

        for cmd in ("kick", "ban", "unban"):
            sent = arena(cmd, "nobody", port=port)
            assert sent.returncode == 0, sent.stderr
        assert until(lambda: "kick: no bot 'nobody' connected" in out.read_text()
                     and "unbanned nobody" in out.read_text(), 10)
        assert arena("kick", port=port).returncode != 0

        gone = arena("unhouse", "all", port=port)
        assert gone.returncode == 0 and "ace" in gone.stdout and "gus" in gone.stdout
        assert not any(alive(pid) for pid in pids)
        assert not list(folder.glob("house-*.pid"))

        down = arena("down", port=port)
        assert down.returncode == 0, down.stderr
        assert serve.wait(timeout=10) == 0
        assert not answers(port)
        assert not (folder / "control").exists()
        assert arena("map", "cavern", port=port).returncode != 0
    finally:
        for pidfile in folder.glob("house-*.pid"):
            try:
                os.kill(int(pidfile.read_text()), signal.SIGTERM)
            except (OSError, ValueError):
                pass
        if serve is not None and serve.poll() is None:
            serve.kill()
        shutil.rmtree(folder, ignore_errors=True)
        out.unlink(missing_ok=True)
