import json

import pytest

from spacetaxi.bots import greedy
from spacetaxi.cli import main
from spacetaxi.maps import load_map
from spacetaxi.runner import BOTS_DIR
from spacetaxi.sim import FLYING, LANDED, Rules, World


def run_json(capsys, *args) -> list[dict]:
    assert main(["run", "--json", *args]) == 0
    return json.loads(capsys.readouterr().out)


def strip_names(results: list[dict]) -> list[dict]:
    return [{k: v for k, v in r.items() if k != "name"} for r in results]


@pytest.mark.parametrize("map_name", ["cavern", "classic"])
def test_greedy_delivers_and_earns_alone(capsys, map_name):
    greedy = run_json(capsys, "--map", map_name, "--bots", "greedy,idle,idle,idle",
                      "--rounds", "100", "--seed", "1")[0]
    assert greedy["avg_deliveries"] >= 2
    assert greedy["avg_money"] > 0
    crashes = sum(greedy["crashes"].values())
    assert crashes <= greedy["rounds"]  # at most 1 crash per round
    assert crashes >= 1  # deliberately not crash-free: the starter leaves participants a flaw to fix
    assert greedy["errors"] == 0 and greedy["timeouts"] == 0


def test_greedy_lands_on_each_cavern_pad_from_some_spawn():
    # some spawns crash on purpose (it cruises too close to the middle bar), so one landing per pad
    m = load_map("cavern")
    rules = Rules().to_dict()
    greedy.welcome({"id": "t", "rules": rules})
    greedy.start({"rules": rules, "map": m.rows, "pads": [p.to_dict() for p in m.pads]})
    for pad in m.pads:
        landed = []
        for spawn in m.spawns:
            world = World(m, players=["t"])
            taxi = world.taxis[0]
            world._place(taxi, *spawn)
            while taxi.state == FLYING and world.tick <= 300:
                state = taxi.to_dict()
                state["passenger"] = {"id": 0, "to": pad.id, "fare": 0}
                ax, ay, _debug = greedy.choose(state, [], world.tick)
                world.step({"t": {"ax": ax, "ay": ay}})
            landed.append(taxi.state == LANDED and taxi.pad == pad.id)
        assert any(landed), pad.id


def test_pilot_beats_every_greedy(capsys):
    results = run_json(capsys, "--map", "cavern", "--bots", "pilot,greedy,greedy,greedy",
                       "--rounds", "100", "--seed", "1")
    pilot, greedies = results[0], results[1:]
    assert all(pilot["avg_money"] > g["avg_money"] for g in greedies)


def test_greedy_stays_small():
    lines = (BOTS_DIR / "greedy.py").read_text().splitlines()
    assert len([line for line in lines if line.strip()]) <= 150


def test_new_bot_copy_plays_like_greedy(tmp_path, capsys):
    path = tmp_path / "x" / "mybot.py"
    assert main(["new-bot", str(path)]) == 0
    capsys.readouterr()
    text = path.read_text()
    assert text.endswith((BOTS_DIR / "greedy.py").read_text())
    for needle in ("docs/rules.md", "spacetaxi run --bots ./mybot.py,pilot --rounds 100 --json",
                   "--replay", "spacetaxi show"):
        assert needle in text

    common = ["--map", "cavern", "--rounds", "5", "--seed", "1"]
    mine = run_json(capsys, "--bots", f"{path},idle", *common)
    greedy = run_json(capsys, "--bots", "greedy,idle", *common)
    assert mine[0]["name"] == "mybot"
    assert strip_names(mine) == strip_names(greedy)


def test_new_bot_never_overwrites(tmp_path, capsys):
    path = tmp_path / "mybot.py"
    path.write_text("# mine\n")
    assert main(["new-bot", str(path)]) == 2
    assert "already exists" in capsys.readouterr().err
    assert path.read_text() == "# mine\n"
