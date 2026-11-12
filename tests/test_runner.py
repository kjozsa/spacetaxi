import json
import time

from spacetaxi.cli import main
from spacetaxi.maps import load_map
from spacetaxi.runner import player_ids, run

KEYS = {"name", "rounds", "avg_money", "avg_deliveries", "crashes", "errors", "timeouts"}
CMD = ["run", "--map", "classic", "--bots", "random,random,idle,idle", "--rounds", "100",
       "--seed", "1", "--json"]


def test_json_output_and_determinism(capsys):
    start = time.perf_counter()
    assert main(CMD) == 0
    assert time.perf_counter() - start < 60
    first = capsys.readouterr().out
    assert main(CMD) == 0
    assert capsys.readouterr().out == first

    results = json.loads(first)
    assert [r["name"] for r in results] == ["random-1", "random-2", "idle-1", "idle-2"]
    for r in results:
        assert set(r) == KEYS
        assert r["rounds"] == 100
        assert set(r["crashes"]) == {"wall", "hard_landing"}
        assert r["errors"] == 0 and r["timeouts"] == 0


def test_different_seed_differs(capsys):
    main(CMD[:-3] + ["--rounds", "5", "--seed", "1", "--json"])
    a = capsys.readouterr().out
    main(CMD[:-3] + ["--rounds", "5", "--seed", "2", "--json"])
    assert capsys.readouterr().out != a


def test_human_table(capsys):
    assert main(["run", "--bots", "random,idle", "--rounds", "2"]) == 0
    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    assert lines[0].split()[0] == "bot"
    assert lines[1].split()[0] == "random"
    assert lines[2].split()[0] == "idle"


def test_bad_bots_count_errors_and_coast(tmp_path, capsys):
    raises = tmp_path / "raiser.py"
    raises.write_text("def move(state):\n    raise RuntimeError('boom')\n")
    half = tmp_path / "half.py"
    half.write_text("def move(state):\n    return {'ax': 5}\n")
    idle_like = tmp_path / "noop.py"
    idle_like.write_text("def move(state):\n    return {'ax': 0, 'ay': 0}\n")
    bots = f"{raises},{half},{idle_like}"
    assert main(["run", "--bots", bots, "--rounds", "2", "--seed", "3", "--json"]) == 0
    raiser, halfbot, noop = json.loads(capsys.readouterr().out)
    ticks = 2 * 375
    assert raiser["name"] == "raiser" and raiser["errors"] == ticks
    assert halfbot["name"] == "half" and halfbot["errors"] == ticks
    assert noop["errors"] == 0
    # Alone on the map with the same seed, a coasting taxi plays exactly like the idle bot.
    [idle] = run(load_map("classic"), ["idle"], rounds=2, seed=3)
    for path in (raises, half):
        [bad] = run(load_map("classic"), [str(path)], rounds=2, seed=3)
        assert bad["crashes"] == idle["crashes"]
        assert bad["avg_money"] == idle["avg_money"]


def test_slow_bot_times_out(tmp_path):
    slow = tmp_path / "slow.py"
    slow.write_text(
        "import time\n"
        "def move(state):\n"
        "    if state['tick'] == 1:\n"
        "        time.sleep(0.2)\n"
        "    return {'ax': 0, 'ay': -1}\n"
    )
    [r] = run(load_map("classic"), [str(slow)], rounds=1)
    assert r["timeouts"] == 1
    assert r["errors"] == 0


def test_player_ids_number_repeats():
    assert player_ids(["random", "idle", "random", "x/bot.py"]) == [
        "random-1", "idle", "random-2", "bot"
    ]


def test_unknown_bot_exits_2(capsys):
    assert main(["run", "--bots", "nosuchbot", "--rounds", "1"]) == 2
    assert "nosuchbot" in capsys.readouterr().err


def test_replay_line_shapes(tmp_path, capsys):
    out = tmp_path / "out.jsonl"
    assert main(["run", "--bots", "random,idle", "--rounds", "2", "--seed", "4",
                 "--replay", str(out)]) == 0
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(lines) == 2 * (1 + 375 + 1)

    per_round = 1 + 375 + 1
    for r in range(2):
        chunk = lines[r * per_round:(r + 1) * per_round]
        start, ticks, end = chunk[0], chunk[1:-1], chunk[-1]
        assert set(start) == {"type", "round", "map", "pads", "spawns", "players"}
        assert start["type"] == "round_start" and start["round"] == r + 1
        assert start["players"] == ["random", "idle"]
        assert set(start["pads"][0]) == {"id", "x", "y", "len"}
        assert set(start["spawns"][0]) == {"x", "y"}

        assert [t["tick"] for t in ticks] == list(range(1, 376))
        for t in ticks:
            assert set(t) == {"type", "tick", "taxis", "passengers", "events"}
            assert t["type"] == "tick"
            assert [taxi["id"] for taxi in t["taxis"]] == ["random", "idle"]
            for taxi in t["taxis"]:
                assert set(taxi) == {"id", "x", "y", "vx", "vy", "state", "pad", "passenger",
                                     "money", "respawn_in"}
            for p in t["passengers"]:
                assert set(p) == {"id", "at", "spot_x", "to", "fare", "expires_in"}

        assert end["type"] == "round_end" and end["round"] == r + 1
        assert set(end) == {"type", "round", "results"}
        for res in end["results"]:
            assert set(res) == {"id", "money", "deliveries", "crashes"}
