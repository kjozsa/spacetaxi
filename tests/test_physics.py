import random
from pathlib import Path

from spacetaxi.maps import load_map
from spacetaxi.sim import Rules, World, parse_move, path_steps

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "maps"
RULES = Rules()
COAST = {"ax": 0, "ay": 0}


def world(name="physics", players=("p",), seed=0):
    path = FIXTURES / f"{name}.txt" if name != "classic" else "classic"
    return World(load_map(path), Rules(), seed, list(players))


def place(w, x, y, vx=0, vy=0, i=0):
    t = w.taxis[i]
    t.x, t.y, t.vx, t.vy = x, y, vx, vy
    return t


def taxi_state(w, i=0):
    return w.state()["taxis"][i]


def test_rules_defaults_match_doc():
    assert Rules().to_dict() == {
        "tick_ms": 200, "deadline_ms": 150, "round_ticks": 375,
        "gravity": 1, "gravity_every": 2, "max_speed": 3, "safe_landing_vy": 1,
        "crash_penalty_ticks": 15, "crash_cost": 25,
        "passenger_spawn_every": 20, "passenger_expires": 150,
        "fare_base": 50, "fare_per_cell": 2,
    }


def test_initial_state_shape():
    w = world()
    s = w.state()
    assert s["type"] == "tick" and s["tick"] == 1
    assert s["passengers"] == [] and s["events"] == []
    assert s["taxis"] == [
        {"id": "p", "x": 2, "y": 1, "vx": 0, "vy": 0, "state": "flying", "pad": None,
         "passenger": None, "money": 0, "respawn_in": 0}
    ]


def test_free_fall_from_rest_follows_gravity_every():
    w = world("falling")
    assert (w.taxis[0].x, w.taxis[0].y) == (3, 1)
    seen = []
    y, vy = 1, 0
    expected = []
    for tick in range(1, 7):
        if tick % RULES.gravity_every == 0:
            vy += RULES.gravity
        y += vy
        expected.append((y, vy))
        w.step({})
        t = taxi_state(w)
        seen.append((t["y"], t["vy"]))
        assert t["state"] == "flying" and t["vx"] == 0
    assert seen == expected == [(1, 0), (2, 1), (3, 1), (5, 2), (7, 2), (10, 3)]


def test_speed_is_clamped():
    w = world("classic")
    place(w, 1, 1)
    vxs = []
    for tick in range(1, 7):
        w.step({"p": {"ax": 1, "ay": -1 if tick % 2 == 0 else 0}})
        vxs.append(taxi_state(w)["vx"])
    assert vxs == [1, 2, 3, 3, 3, 3]
    assert taxi_state(w)["state"] == "flying"

    w = world("falling")
    place(w, 3, 12, vy=-3)
    w.step({"p": {"ax": -1, "ay": -1}})  # tick 1: no gravity
    assert (taxi_state(w)["vx"], taxi_state(w)["vy"]) == (-1, -3)


def test_speed_never_exceeds_max_under_random_moves():
    w = world("classic", players=[f"p{i}" for i in range(6)], seed=7)
    rng = random.Random(1)
    for _ in range(RULES.round_ticks):
        w.step({t.id: {"ax": rng.choice((-1, 0, 1)), "ay": rng.choice((-1, 0, 1))} for t in w.taxis})
        for t in w.state()["taxis"]:
            assert -RULES.max_speed <= t["vx"] <= RULES.max_speed
            assert -RULES.max_speed <= t["vy"] <= RULES.max_speed


def test_path_is_bresenham_x_first_on_ties():
    assert list(path_steps(1, 1)) == [(1, 0), (0, 1)]
    assert list(path_steps(3, 1)) == [(1, 0), (1, 0), (0, 1), (1, 0)]
    assert list(path_steps(-1, -3)) == [(0, -1), (-1, 0), (0, -1), (0, -1)]
    assert list(path_steps(0, 0)) == []


def test_no_tunnelling_horizontal():
    w = world()
    place(w, 2, 3, vx=3)  # wall at (4, 3), end cell (5, 3) is free
    events = w.step({})
    assert events == [{"type": "crash", "taxi": "p", "cause": "wall"}]
    t = taxi_state(w)
    assert (t["x"], t["y"], t["state"]) == (3, 3, "crashed")
    assert (t["vx"], t["vy"]) == (0, 0)


def test_no_tunnelling_diagonal():
    w = world()
    place(w, 2, 2, vx=3, vy=1)  # path (3,2) (4,2) (4,3)=wall ..., end (5,3) free
    assert w.step({}) == [{"type": "crash", "taxi": "p", "cause": "wall"}]
    t = taxi_state(w)
    assert (t["x"], t["y"]) == (4, 2)


def test_landing_soft():
    w = world()
    place(w, 3, 4, vy=RULES.safe_landing_vy)
    assert w.step({}) == [{"type": "landed", "taxi": "p", "pad": "A"}]
    t = taxi_state(w)
    assert (t["x"], t["y"], t["vx"], t["vy"]) == (3, 4, 0, 0)
    assert (t["state"], t["pad"], t["money"]) == ("landed", "A", 0)


def test_landing_too_fast_crashes():
    w = world()
    place(w, 3, 3, vy=RULES.safe_landing_vy + 1)
    assert w.step({}) == [{"type": "crash", "taxi": "p", "cause": "hard_landing"}]
    t = taxi_state(w)
    assert (t["x"], t["y"], t["state"]) == (3, 4, "crashed")


def test_landing_with_sideways_speed_crashes():
    w = world()
    place(w, 2, 4, vx=1, vy=1)
    assert w.step({}) == [{"type": "crash", "taxi": "p", "cause": "hard_landing"}]
    t = taxi_state(w)
    assert (t["x"], t["y"], t["state"]) == (3, 4, "crashed")


def test_pad_entered_sideways_is_a_wall():
    w = world()
    place(w, 5, 5)  # the air gap between pad A and pad B
    w.taxis[0].vx = -1
    assert w.step({}) == [{"type": "crash", "taxi": "p", "cause": "wall"}]


def landed_world():
    w = world()
    place(w, 3, 4, vy=1)
    w.step({})
    assert taxi_state(w)["state"] == "landed"
    return w


def test_takeoff():
    w = landed_world()
    assert w.step({"p": {"ax": 1, "ay": -1}}) == [{"type": "takeoff", "taxi": "p", "pad": "A"}]
    t = taxi_state(w)
    assert (t["x"], t["y"], t["vx"], t["vy"]) == (3, 3, 0, -1)
    assert (t["state"], t["pad"]) == ("flying", None)


def test_other_moves_stay_landed():
    w = landed_world()
    for move in (COAST, {"ax": 1, "ay": 0}, {"ax": -1, "ay": 1}, {"ax": 0, "ay": 1}, None,
                 {"ax": 0, "ay": True}, {"ax": 0, "ay": -2}, "junk"):
        assert w.step({"p": move}) == []
        t = taxi_state(w)
        assert (t["x"], t["y"], t["vx"], t["vy"], t["state"], t["pad"]) == (3, 4, 0, 0, "landed", "A")


def test_parse_move_rejects_invalid():
    assert parse_move({"ax": -1, "ay": 1}) == (-1, 1)
    for bad in (None, {}, {"ax": 1}, {"ax": True, "ay": 0}, {"ax": 1.0, "ay": 0}, {"ax": 2, "ay": 0}):
        assert parse_move(bad) == (0, 0)


def test_crash_and_respawn():
    w = world()
    place(w, 2, 3, vx=3)
    w.step({})
    t = taxi_state(w)
    assert (t["state"], t["money"], t["respawn_in"]) == ("crashed", -RULES.crash_cost, 15)
    for n in range(RULES.crash_penalty_ticks - 1):
        assert w.step({"p": {"ax": 0, "ay": -1}}) == []
        t = taxi_state(w)
        assert t["state"] == "crashed" and t["respawn_in"] == RULES.crash_penalty_ticks - 1 - n
        assert (t["x"], t["y"]) == (3, 3)
    events = w.step({})
    assert events == [{"type": "respawn", "taxi": "p", "x": 2, "y": 1}]
    t = taxi_state(w)
    assert (t["x"], t["y"], t["vx"], t["vy"]) == (2, 1, 0, 0)
    assert (t["state"], t["respawn_in"], t["money"]) == ("flying", 0, -RULES.crash_cost)
    assert w.state()["events"] == events


def test_ghosts_do_not_collide():
    moves = [{"ax": 1, "ay": 0}, COAST, {"ax": -1, "ay": -1}, COAST, {"ax": 0, "ay": 1}, COAST]
    solo = world("classic")
    pair = world("classic", players=("a", "b"))
    place(solo, 10, 5)
    place(pair, 10, 5, i=0)
    place(pair, 10, 5, i=1)
    for m in moves:
        solo.step({"p": m})
        pair.step({"a": m, "b": m})
        s = {k: v for k, v in taxi_state(solo).items() if k != "id"}
        for i in (0, 1):
            assert {k: v for k, v in taxi_state(pair, i).items() if k != "id"} == s

    # head-on through each other: both pass unharmed
    w = world("classic", players=("a", "b"))
    place(w, 10, 5, vx=2, i=0)
    place(w, 12, 5, vx=-2, i=1)
    w.step({"a": {"ax": 0, "ay": -1}, "b": {"ax": 0, "ay": -1}})
    a, b = taxi_state(w, 0), taxi_state(w, 1)
    assert (a["x"], a["y"], a["state"]) == (12, 4, "flying")
    assert (b["x"], b["y"], b["state"]) == (10, 4, "flying")


def run(seed, move_seed):
    w = world("classic", players=("a", "b", "c"), seed=seed)
    rng = random.Random(move_seed)
    states = [w.state()]
    for _ in range(200):
        w.step({p: {"ax": rng.choice((-1, 0, 1)), "ay": rng.choice((-1, 0, 1))} for p in "abc"})
        states.append(w.state())
    return states


def test_determinism():
    assert run(42, 3) == run(42, 3)
    assert run(42, 3) != run(43, 3)
