import json
from pathlib import Path

from spacetaxi.bots.lander import Planner, choose
from spacetaxi.cli import main
from spacetaxi.maps import load_map
from spacetaxi.sim import CRASHED, LANDED, Rules, World

FIXTURES = Path(__file__).parent / "fixtures" / "maps"


def fly(world: World, planner: Planner, pad: str, limit: int = 60) -> list[dict]:
    """Fly taxi 0 with the planner toward `pad` until it lands or crashes; return all events."""
    taxi = world.taxis[0]
    events = []
    for _ in range(limit):
        plan = planner.plan(taxi.to_dict(), world.tick, pad)
        assert plan is not None
        ticks, (ax, ay) = plan
        before = world.tick
        events += world.step({taxi.id: {"ax": ax, "ay": ay}})
        if taxi.state != "flying":
            assert world.tick - before == 1 and ticks == 1
            break
    return events


def test_planner_lands_safely_on_each_pad_from_fixed_start():
    m = load_map(str(FIXTURES / "physics.txt"))
    planner = Planner(m.rows, [p.to_dict() for p in m.pads], Rules().to_dict())
    for pad in ("A", "B"):
        world = World(m, players=["t"])
        taxi = world.taxis[0]
        assert (taxi.x, taxi.y) == (2, 1)
        first = planner.plan(taxi.to_dict(), world.tick, pad)[0]
        events = fly(world, planner, pad)
        assert taxi.state == LANDED and taxi.pad == pad
        assert not [e for e in events if e["type"] == "crash"]
        # the plan's tick count is exact: physics is deterministic
        assert world.tick - 1 == first


def test_planner_takes_off_and_relands_on_same_pad():
    m = load_map(str(FIXTURES / "physics.txt"))
    planner = Planner(m.rows, [p.to_dict() for p in m.pads], Rules().to_dict())
    world = World(m, players=["t"])
    fly(world, planner, "A")
    taxi = world.taxis[0]
    assert taxi.state == LANDED
    ticks, action = planner.plan(taxi.to_dict(), world.tick, "A")
    assert action == (0, -1)
    events = fly(world, planner, "A")
    assert taxi.state == LANDED and taxi.pad == "A"
    assert [e["type"] for e in events][0] == "takeoff"
    assert not [e for e in events if e["type"] == "crash"]


def test_choose_targets_destination_when_carrying():
    m = load_map(str(FIXTURES / "passengers.txt"))
    planner = Planner(m.rows, [p.to_dict() for p in m.pads], Rules().to_dict())
    world = World(m, players=["t"])
    taxi = world.taxis[0]
    taxi.passenger = {"id": 1, "to": "C", "fare": 60}
    for _ in range(80):
        ax, ay, _debug = choose(planner, taxi.to_dict(), [], world.tick, 375)
        world.step({"t": {"ax": ax, "ay": ay}})
        if taxi.state != "flying":
            break
    assert taxi.state != CRASHED
    assert taxi.deliveries == 1 and taxi.money == 60


def test_lander_beats_random_bots_on_classic(capsys):
    cmd = ["run", "--map", "classic", "--bots", "lander,random,random,random", "--rounds",
           "100", "--seed", "1", "--json"]
    assert main(cmd) == 0
    lander, *randoms = json.loads(capsys.readouterr().out)
    assert lander["name"] == "lander"
    assert lander["avg_deliveries"] >= 3
    crashes = lander["crashes"]["wall"] + lander["crashes"]["hard_landing"]
    assert crashes <= 0.5 * lander["rounds"]
    assert all(lander["avg_money"] > r["avg_money"] for r in randoms)


def test_four_landers_have_no_errors_or_timeouts(capsys):
    cmd = ["run", "--map", "classic", "--bots", "lander,lander,lander,lander", "--rounds", "20",
           "--json"]
    assert main(cmd) == 0
    for r in json.loads(capsys.readouterr().out):
        assert r["errors"] == 0 and r["timeouts"] == 0
