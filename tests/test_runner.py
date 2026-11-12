import json
import time

from spacetaxi.cli import main
from spacetaxi.maps import load_map
from spacetaxi.runner import player_ids, run
from spacetaxi.sim import Rules

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
    ticks = 2 * Rules().round_ticks
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
    per_round = 1 + Rules().round_ticks + 1
    assert len(lines) == 2 * per_round

    for r in range(2):
        chunk = lines[r * per_round:(r + 1) * per_round]
        start, ticks, end = chunk[0], chunk[1:-1], chunk[-1]
        assert set(start) == {"type", "round", "map", "pads", "spawns", "players", "rules"}
        assert start["rules"] == Rules().to_dict()
        assert start["type"] == "round_start" and start["round"] == r + 1
        assert start["players"] == ["random", "idle"]
        assert set(start["pads"][0]) == {"id", "x", "y", "len"}
        assert set(start["spawns"][0]) == {"x", "y"}

        assert [t["tick"] for t in ticks] == list(range(1, Rules().round_ticks + 1))
        for t in ticks:
            assert set(t) == {"type", "tick", "taxis", "passengers", "events", "moves"}
            assert list(t["moves"]) == ["random", "idle"]
            for m in t["moves"].values():
                assert {"ax", "ay", "coast"} <= set(m) <= {"ax", "ay", "coast", "debug"}
            assert t["type"] == "tick"
            assert [taxi["id"] for taxi in t["taxis"]] == ["random", "idle"]
            for taxi in t["taxis"]:
                assert set(taxi) == {"id", "x", "y", "fx", "fy", "vx", "vy", "state", "pad",
                                     "passenger", "money", "respawn_in"}
            for p in t["passengers"]:
                assert set(p) == {"id", "at", "spot_x", "to", "fare", "expires_in"}

        assert end["type"] == "round_end" and end["round"] == r + 1
        assert set(end) == {"type", "round", "results"}
        for res in end["results"]:
            assert set(res) == {"id", "money", "deliveries", "crashes"}


RULE_CMD = ["run", "--map", "classic", "--bots", "lander,random", "--rounds", "20", "--seed", "1",
            "--json"]
OVERRIDES = ["--rule", "max_speed=4", "--rule", "crash_cost=50"]


def test_rule_overrides_change_results(capsys):
    assert main(RULE_CMD) == 0
    plain = capsys.readouterr().out
    assert main(RULE_CMD + OVERRIDES) == 0
    assert capsys.readouterr().out != plain


def test_replay_records_overridden_rules(tmp_path, capsys):
    out = tmp_path / "out.jsonl"
    assert main(["run", "--bots", "idle", "--rounds", "2", "--replay", str(out)] + OVERRIDES) == 0
    starts = [m for m in map(json.loads, out.read_text().splitlines())
              if m["type"] == "round_start"]
    assert len(starts) == 2
    expected = Rules(max_speed=4, crash_cost=50).to_dict()
    for start in starts:
        assert start["rules"] == expected


def test_bots_receive_overridden_rules(tmp_path, capsys):
    log = tmp_path / "seen.jsonl"
    spy = tmp_path / "spy.py"
    spy.write_text(
        "import json\n"
        f"LOG = {str(log)!r}\n"
        "def _write(info):\n"
        "    with open(LOG, 'a') as f:\n"
        "        f.write(json.dumps(info) + '\\n')\n"
        "welcome = start = _write\n"
        "def move(state):\n"
        "    return {'ax': 0, 'ay': 0}\n"
    )
    assert main(["run", "--bots", str(spy), "--rounds", "1", "--rule", "round_ticks=5",
                 "--rule", "max_speed=4"]) == 0
    welcome, start = map(json.loads, log.read_text().splitlines())
    expected = Rules(round_ticks=5, max_speed=4).to_dict()
    assert welcome["type"] == "welcome" and welcome["rules"] == expected
    assert start["type"] == "round_start" and start["rules"] == expected


def test_unknown_rule_key_exits_2(capsys):
    assert main(["run", "--bots", "idle", "--rounds", "1", "--rule", "warp=9"]) == 2
    err = capsys.readouterr().err
    assert "warp" in err
    for key in Rules().to_dict():
        assert key in err


def test_non_integer_rule_value_exits_2(capsys):
    assert main(["run", "--bots", "idle", "--rounds", "1", "--rule", "max_speed=fast"]) == 2
    err = capsys.readouterr().err
    assert "max_speed" in err and "fast" in err
    for key in Rules().to_dict():
        assert key in err


def test_rule_without_equals_exits_2(capsys):
    assert main(["run", "--bots", "idle", "--rounds", "1", "--rule", "max_speed"]) == 2
    assert "max_speed" in capsys.readouterr().err


def test_replay_moves_match_bot_answers(tmp_path):
    scripted = tmp_path / "scripted.py"
    scripted.write_text(
        "def move(state):\n"
        "    t = state['tick']\n"
        "    if t % 3 == 0:\n"
        "        return {'ax': 1, 'ay': -1, 'debug': f'tick {t}'}\n"
        "    return {'ax': t % 2 * -1, 'ay': 0}\n"
    )
    raiser = tmp_path / "raiser.py"
    raiser.write_text("def move(state):\n    raise RuntimeError('boom')\n")
    out = tmp_path / "out.jsonl"
    with out.open("w") as f:
        run(load_map("classic"), [str(scripted), str(raiser)], rounds=1, seed=5,
            rules=Rules(round_ticks=10), replay=f)
    ticks = [m for m in map(json.loads, out.read_text().splitlines()) if m["type"] == "tick"]
    assert len(ticks) == 10
    for t in ticks:
        n = t["tick"]
        if n % 3 == 0:
            expected = {"ax": 1, "ay": -1, "coast": False, "debug": f"tick {n}"}
        else:
            expected = {"ax": n % 2 * -1, "ay": 0, "coast": False}
        assert t["moves"]["scripted"] == expected
        assert t["moves"]["raiser"] == {"ax": 0, "ay": 0, "coast": True}


def test_bots_never_see_moves(tmp_path):
    log = tmp_path / "keys.txt"
    spy = tmp_path / "spy.py"
    spy.write_text(
        f"LOG = {str(log)!r}\n"
        "def move(state):\n"
        "    with open(LOG, 'a') as f:\n"
        "        f.write(','.join(sorted(state)) + '\\n')\n"
        "    return {'ax': 0, 'ay': 0, 'debug': 'hi'}\n"
    )
    out = tmp_path / "out.jsonl"
    with out.open("w") as f:
        run(load_map("classic"), [str(spy), str(spy)], rounds=1, rules=Rules(round_ticks=5),
            replay=f)
    seen = log.read_text().splitlines()
    assert len(seen) == 10
    for keys in seen:
        assert "moves" not in keys.split(",")
    assert all("moves" in json.loads(line) for line in out.read_text().splitlines()
               if json.loads(line)["type"] == "tick")


def test_replay_keeps_object_debug(tmp_path):
    bot = tmp_path / "planner.py"
    bot.write_text(
        "def move(state):\n"
        "    t = state['tick']\n"
        "    if t == 1:\n"
        "        return {'ax': 0, 'ay': 0, 'debug': {'say': 'hi', 'path': [[5, 5], (6, 5)], 'x': 1}}\n"
        "    if t == 2:\n"
        "        return {'ax': 0, 'ay': 0, 'debug': {'say': 3, 'path': [[1, True]]}}\n"
        "    return {'ax': 0, 'ay': 0, 'debug': 42}\n"
    )
    out = tmp_path / "out.jsonl"
    with out.open("w") as f:
        run(load_map("classic"), [str(bot)], rounds=1, rules=Rules(round_ticks=3), replay=f)
    moves = [m["moves"]["planner"] for m in map(json.loads, out.read_text().splitlines())
             if m["type"] == "tick"]
    assert moves[0]["debug"] == {"say": "hi", "path": [[5, 5], [6, 5]]}
    assert moves[1]["debug"] == {}
    assert "debug" not in moves[2]
