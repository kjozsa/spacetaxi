import json

import pytest

from spacetaxi.bots.pilot import Pilot, choose
from spacetaxi.cli import main
from spacetaxi.maps import load_map
from spacetaxi.runner import clean_debug
from spacetaxi.sim import CRASHED, LANDED, Rules, World


def pilot_for(m) -> Pilot:
    return Pilot(m.rows, [p.to_dict() for p in m.pads], Rules().to_dict())


def fly(world: World, pilot: Pilot, pad: str, limit: int = 300) -> list[dict]:
    """Fly taxi 0 toward `pad` (as if carrying a passenger there) until it lands, crashes or
    runs out of ticks; return all events."""
    taxi = world.taxis[0]
    events = []
    for _ in range(limit):
        state = taxi.to_dict()
        state["passenger"] = {"id": 0, "to": pad, "fare": 0}
        ax, ay, _debug = choose(pilot, state, [], world.tick)
        events += world.step({taxi.id: {"ax": ax, "ay": ay}})
        if taxi.state != "flying":
            break
    return events


@pytest.mark.parametrize("pad", list("ABCDEFG"))
def test_pilot_lands_safely_on_each_cavern_pad_from_each_spawn(pad):
    m = load_map("cavern")
    pilot = pilot_for(m)
    for spawn in m.spawns:
        world = World(m, players=["t"])
        world._place(world.taxis[0], *spawn)
        events = fly(world, pilot, pad)
        taxi = world.taxis[0]
        assert taxi.state == LANDED and taxi.pad == pad, (spawn, pad, taxi)
        assert world.tick - 1 <= 300
        assert not [e for e in events if e["type"] == "crash"]


def test_pilot_takes_off_and_relands_on_same_pad():
    m = load_map("cavern")
    pilot = pilot_for(m)
    world = World(m, players=["t"])
    fly(world, pilot, "A")
    taxi = world.taxis[0]
    assert taxi.state == LANDED
    events = fly(world, pilot, "A", limit=30)
    assert taxi.state == LANDED and taxi.pad == "A"
    assert [e["type"] for e in events][0] == "takeoff"
    assert not [e for e in events if e["type"] == "crash"]


def test_choose_targets_destination_when_carrying():
    m = load_map("cavern")
    pilot = pilot_for(m)
    world = World(m, players=["t"])
    taxi = world.taxis[0]
    taxi.passenger = {"id": 1, "to": "F", "fare": 60}
    for _ in range(300):
        ax, ay, _debug = choose(pilot, taxi.to_dict(), [], world.tick)
        world.step({"t": {"ax": ax, "ay": ay}})
        if taxi.state != "flying":
            break
    assert taxi.state != CRASHED
    assert taxi.deliveries == 1 and taxi.money == 60


def test_debug_says_what_it_does_and_draws_its_path():
    m = load_map("cavern")
    pilot = pilot_for(m)
    world = World(m, players=["t"])
    taxi = world.taxis[0].to_dict()
    taxi["passenger"] = {"id": 1, "to": "F", "fare": 60}
    _ax, _ay, debug = choose(pilot, taxi, [], world.tick)
    assert clean_debug(debug) == debug
    assert "F" in debug["say"]
    pad = next(p for p in m.pads if p.id == "F")
    assert debug["path"][-1] == [pad.x + pad.len // 2, pad.y - 1]


def test_pilot_delivers_alone_on_cavern(capsys):
    cmd = ["run", "--map", "cavern", "--bots", "pilot,idle,idle,idle", "--rounds", "100",
           "--seed", "1", "--json"]
    assert main(cmd) == 0
    pilot = json.loads(capsys.readouterr().out)[0]
    assert pilot["name"] == "pilot"
    assert pilot["avg_deliveries"] >= 5
    crashes = pilot["crashes"]["wall"] + pilot["crashes"]["hard_landing"]
    assert crashes <= 0.3 * pilot["rounds"]
    assert pilot["errors"] == 0 and pilot["timeouts"] == 0


def test_pilot_beats_random_bots_on_classic(capsys):
    cmd = ["run", "--map", "classic", "--bots", "pilot,random,random,random", "--rounds",
           "100", "--seed", "1", "--json"]
    assert main(cmd) == 0
    pilot, *randoms = json.loads(capsys.readouterr().out)
    assert pilot["avg_deliveries"] >= 3
    crashes = pilot["crashes"]["wall"] + pilot["crashes"]["hard_landing"]
    assert crashes <= 0.5 * pilot["rounds"]
    assert all(pilot["avg_money"] > r["avg_money"] for r in randoms)


@pytest.mark.parametrize("game_map", ["cavern", "canyon"])
def test_four_pilots_have_no_errors_or_timeouts(capsys, game_map):
    cmd = ["run", "--map", game_map, "--bots", "pilot,pilot,pilot,pilot", "--rounds", "20",
           "--json"]
    assert main(cmd) == 0
    for r in json.loads(capsys.readouterr().out):
        assert r["errors"] == 0 and r["timeouts"] == 0
