from pathlib import Path

import pytest

from spacetaxi.cli import main
from spacetaxi.show import render

FIXTURES = Path(__file__).parent / "fixtures"
SNAPSHOT = FIXTURES / "show" / "passengers_seed5_tick100.txt"


@pytest.fixture
def replay(tmp_path, capsys):
    out = tmp_path / "out.jsonl"
    assert main(["run", "--map", str(FIXTURES / "maps" / "passengers.txt"), "--bots",
                 "random,idle", "--rounds", "2", "--seed", "5", "--replay", str(out)]) == 0
    capsys.readouterr()
    return str(out)


def test_show_matches_snapshot(replay, capsys):
    assert main(["show", replay, "--tick", "100"]) == 0
    assert capsys.readouterr().out == SNAPSHOT.read_text()


def test_round_option(replay, capsys):
    assert main(["show", replay, "--round", "1", "--tick", "100"]) == 0
    first = capsys.readouterr().out
    assert main(["show", replay, "--round", "2", "--tick", "100"]) == 0
    assert capsys.readouterr().out != first


@pytest.mark.parametrize("extra", [
    ["--tick", "0"], ["--tick", "376"], ["--round", "3", "--tick", "1"],
    ["--round", "0", "--tick", "1"], [],
])
def test_out_of_range_exits_2(replay, capsys, extra):
    assert main(["show", replay] + extra) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error" in captured.err


def test_missing_file_exits_2(tmp_path, capsys):
    assert main(["show", str(tmp_path / "nope.jsonl"), "--tick", "1"]) == 2
    assert "error" in capsys.readouterr().err


def test_render_marks_and_taxi_lines():
    start = {
        "round": 1,
        "map": ["#######", "#S....#", "#.....#", "#AA.BB#", "#######"],
        "pads": [{"id": "A", "x": 1, "y": 3, "len": 2}, {"id": "B", "x": 4, "y": 3, "len": 2}],
    }
    tick = {
        "tick": 7,
        "taxis": [
            {"id": "alice", "x": 1, "y": 2, "vx": 0, "vy": 0, "state": "landed", "pad": "A",
             "passenger": {"id": 3, "to": "B", "fare": 60}, "money": 101, "respawn_in": 0},
            {"id": "bob", "x": 3, "y": 1, "vx": -1, "vy": 2, "state": "flying", "pad": None,
             "passenger": None, "money": -25, "respawn_in": 0},
        ],
        "passengers": [{"id": 4, "at": "B", "spot_x": 5, "to": "A", "fare": 60,
                        "expires_in": 9}],
    }
    assert render(start, tick) == "\n".join([
        "#######",
        "#..2..#",
        "#1...@#",
        "#AA.BB#",
        "#######",
        "1 alice landed  0,0 101 3→B",
        "2 bob   flying  -1,2 -25 -",
    ])


def test_watch_plays_round(replay, capsys, monkeypatch):
    sleeps = []
    monkeypatch.setattr("spacetaxi.cli.time.sleep", sleeps.append)
    assert main(["show", replay, "--watch", "--fps", "50"]) == 0
    out = capsys.readouterr().out
    assert len(sleeps) == 375 and sleeps[0] == pytest.approx(0.02)
    assert "round 1 tick 1\n" in out and "round 1 tick 375\n" in out


def test_watch_ctrl_c_exits_0(replay, capsys, monkeypatch):
    def interrupt(_):
        raise KeyboardInterrupt

    monkeypatch.setattr("spacetaxi.cli.time.sleep", interrupt)
    assert main(["show", replay, "--watch"]) == 0
    assert "round 1 tick 1\n" in capsys.readouterr().out
