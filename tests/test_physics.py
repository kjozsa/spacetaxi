import random
from pathlib import Path

from spacetaxi.maps import load_map
from spacetaxi.sim import Rules, World, parse_move, path_steps

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "maps"
RULES = Rules()
U = RULES.units_per_cell
COAST = {"ax": 0, "ay": 0}
THRUST = {"ax": 0, "ay": -1}

# physics.txt: S at (2, 1), a wall at (4, 3), pad A x=2..4 and pad B x=6..7 on row 5.
#   #########
#   #.S.....#
#   #.......#
#   #...#...#
#   #.......#
#   #.AAA.BB#
#   #########


def world(name="physics", players=("p",), seed=0):
    path = FIXTURES / f"{name}.txt" if name != "classic" else "classic"
    return World(load_map(path), Rules(), seed, list(players))


def mid(cell):
    """The unit at the centre of a cell."""
    return cell * U + U // 2


def resting_fy(pad_row):
    """The `fy` whose box bottom is the unit just above a pad on row `pad_row`."""
    return pad_row * U - RULES.taxi_half_height - 1


def place(w, fx, fy, vx=0, vy=0, i=0):
    t = w.taxis[i]
    t.fx, t.fy, t.vx, t.vy = fx, fy, vx, vy
    t.x, t.y = fx // U, fy // U
    return t


def taxi_state(w, i=0):
    return w.state()["taxis"][i]


def test_rules_defaults_match_doc():
    assert Rules().to_dict() == {
        "tick_ms": 100, "deadline_ms": 75, "round_ticks": 750, "units_per_cell": 32,
        "gravity": 2, "gravity_every": 1, "main_thrust": 4, "side_thrust": 2, "max_speed": 24,
        "safe_landing_vy": 8, "safe_landing_vx": 3, "taxi_half_width": 15,
        "taxi_half_height": 12, "crash_penalty_ticks": 30, "crash_cost": 25,
        "passenger_spawn_every": 40, "passenger_expires": 300,
        "fare_base": 50, "fare_per_cell": 2,
    }


def test_initial_state_is_the_centre_of_the_spawn_cell():
    w = world()
    s = w.state()
    assert s["type"] == "tick" and s["tick"] == 1
    assert s["passengers"] == [] and s["events"] == []
    assert s["taxis"] == [{
        "id": "p", "x": 2, "y": 1, "fx": mid(2), "fy": mid(1), "vx": 0, "vy": 0,
        "state": "flying", "pad": None, "passenger": None, "money": 0, "respawn_in": 0,
    }]


def test_free_fall_from_rest():
    w = world("falling")
    t = taxi_state(w)
    assert (t["x"], t["y"], t["fx"], t["fy"]) == (3, 1, 112, 48)
    # by hand: vy grows by gravity (2) every tick, fy by the new vy
    expected = [(2, 50), (4, 54), (6, 60), (8, 68), (10, 78), (12, 90)]
    seen = []
    for _ in range(6):
        w.step({})
        t = taxi_state(w)
        assert t["state"] == "flying" and (t["vx"], t["fx"], t["x"]) == (0, 112, 3)
        assert t["y"] == t["fy"] // U
        seen.append((t["vy"], t["fy"]))
    assert seen == expected


def test_hovering_alternating_main_engine_and_coast():
    w = world("falling")
    place(w, mid(3), mid(6))
    for tick in range(1, 21):
        w.step({"p": THRUST if tick % 2 else COAST})
        t = taxi_state(w)
        assert -2 <= t["vy"] <= 2 and t["state"] == "flying"
    assert abs(taxi_state(w)["fy"] - mid(6)) <= 2 * 10


def test_ay_down_does_nothing():
    rng = random.Random(3)
    moves = [rng.choice((-1, 0, 1)) for _ in range(8)]
    down, coast = world("falling"), world("falling")
    for ax in moves:
        down.step({"p": {"ax": ax, "ay": 1}})
        coast.step({"p": {"ax": ax, "ay": 0}})
        assert taxi_state(down) == taxi_state(coast)
    assert parse_move({"ax": 0, "ay": 1}) == (0, 1)  # a valid answer, all the same


def test_main_engine_against_gravity():
    w = world("falling")
    place(w, mid(3), mid(8))
    w.step({"p": THRUST})
    assert taxi_state(w)["vy"] == RULES.gravity - RULES.main_thrust == -2
    w.step({"p": THRUST})
    assert taxi_state(w)["vy"] == -4


def test_side_thrust_grows_vx_and_is_clamped():
    w = world("falling")
    place(w, mid(1), mid(2), vy=-RULES.gravity)
    vxs = []
    for _ in range(3):
        w.step({"p": {"ax": 1, "ay": 0}})
        vxs.append(taxi_state(w)["vx"])
    assert vxs == [2, 4, 6]

    for vx, ax in ((RULES.max_speed - 1, 1), (-RULES.max_speed + 1, -1)):
        w = world("falling")
        place(w, mid(3), mid(2), vx=vx, vy=-RULES.gravity)
        w.step({"p": {"ax": ax, "ay": 0}})
        assert taxi_state(w)["vx"] == ax * RULES.max_speed

    w = world("falling")
    place(w, mid(3), mid(1), vy=RULES.max_speed)
    w.step({})
    assert taxi_state(w)["vy"] == RULES.max_speed
    w = world("falling")
    place(w, mid(3), mid(10), vy=-RULES.max_speed)
    w.step({"p": THRUST})
    assert taxi_state(w)["vy"] == -RULES.max_speed


def test_speed_never_exceeds_max_under_random_moves():
    w = world("classic", players=[f"p{i}" for i in range(6)], seed=7)
    rng = random.Random(1)
    for _ in range(RULES.round_ticks):
        w.step({t.id: {"ax": rng.choice((-1, 0, 1)), "ay": rng.choice((-1, 0, 1))} for t in w.taxis})
        for t in w.state()["taxis"]:
            assert -RULES.max_speed <= t["vx"] <= RULES.max_speed
            assert -RULES.max_speed <= t["vy"] <= RULES.max_speed
            assert (t["x"], t["y"]) == (t["fx"] // U, t["fy"] // U)


def test_path_is_bresenham_x_first_on_ties():
    assert list(path_steps(1, 1)) == [(1, 0), (0, 1)]
    assert list(path_steps(3, 1)) == [(1, 0), (1, 0), (0, 1), (1, 0)]
    assert list(path_steps(-1, -3)) == [(0, -1), (-1, 0), (0, -1), (0, -1)]
    assert list(path_steps(0, 0)) == []


def test_no_tunnelling_horizontal_at_full_speed():
    # the wall cell (4, 3) spans units 128..159; the box reaches taxi_half_width right of fx
    w = world()
    place(w, 100, mid(3), vx=RULES.max_speed, vy=-RULES.gravity)
    assert w.step({}) == [{"type": "crash", "taxi": "p", "cause": "wall"}]
    t = taxi_state(w)
    assert (t["fx"], t["fy"], t["state"]) == (128 - 1 - RULES.taxi_half_width, mid(3), "crashed")
    assert (t["vx"], t["vy"]) == (0, 0)


def test_no_tunnelling_down_at_full_speed():
    # falling onto the 1-cell wall (4, 3) from above: stops with its box just over it
    w = world()
    place(w, mid(4), 70, vy=RULES.max_speed - RULES.gravity)
    assert w.step({}) == [{"type": "crash", "taxi": "p", "cause": "wall"}]
    t = taxi_state(w)
    assert (t["fx"], t["fy"]) == (mid(4), 3 * U - 1 - RULES.taxi_half_height)


def test_no_tunnelling_diagonal_at_full_speed():
    w = world()
    place(w, 90, mid(2), vx=RULES.max_speed, vy=RULES.max_speed - RULES.gravity)
    assert w.step({}) == [{"type": "crash", "taxi": "p", "cause": "wall"}]
    t = taxi_state(w)
    # it never got inside the wall's box: no unit of it overlaps cell (4, 3)
    assert not (t["fx"] + RULES.taxi_half_width >= 128 and t["fy"] + RULES.taxi_half_height >= 96)


def test_touchdown_safe_at_safe_landing_vy():
    w = world()
    place(w, mid(3), resting_fy(5), vx=RULES.safe_landing_vx,
          vy=RULES.safe_landing_vy - RULES.gravity)
    assert w.step({}) == [{"type": "landed", "taxi": "p", "pad": "A"}]
    t = taxi_state(w)
    assert (t["fx"], t["fy"], t["x"], t["y"], t["vx"], t["vy"]) == (
        mid(3), resting_fy(5), 3, 4, 0, 0)
    assert (t["state"], t["pad"], t["money"]) == ("landed", "A", 0)


def test_touchdown_too_fast_is_a_hard_landing():
    w = world()
    place(w, mid(3), resting_fy(5), vy=RULES.safe_landing_vy + 1 - RULES.gravity)
    assert w.step({}) == [{"type": "crash", "taxi": "p", "cause": "hard_landing"}]
    t = taxi_state(w)
    assert (t["fy"], t["state"], t["money"]) == (resting_fy(5), "crashed", -RULES.crash_cost)


def test_touchdown_with_sideways_speed_is_a_hard_landing():
    for vx in (RULES.safe_landing_vx + 1, -RULES.safe_landing_vx - 1):
        w = world()
        place(w, mid(3), resting_fy(5), vx=vx, vy=RULES.safe_landing_vy - RULES.gravity)
        assert w.step({}) == [{"type": "crash", "taxi": "p", "cause": "hard_landing"}]


def test_touchdown_with_the_centre_over_air_lands_on_the_pad_touched():
    w = world()
    place(w, 165, resting_fy(5))  # box 150..180: over pad A's last tile and the gap (5, 5)
    assert w.step({}) == [{"type": "landed", "taxi": "p", "pad": "A"}]
    w = world()
    place(w, 190, resting_fy(5))  # box 175..205: centre over the gap, then pad B
    assert w.step({}) == [{"type": "landed", "taxi": "p", "pad": "B"}]


def test_touching_a_pad_from_the_side_is_a_wall_crash():
    # in the gap (5, 5) between the pads, sliding left into pad A's side
    w = world()
    place(w, mid(5), mid(5), vx=-4, vy=-RULES.gravity)
    assert w.step({}) == [{"type": "crash", "taxi": "p", "cause": "wall"}]
    assert taxi_state(w)["fx"] == 5 * U + RULES.taxi_half_width


def landed_world():
    w = world()
    place(w, mid(3), resting_fy(5))
    w.step({})
    assert taxi_state(w)["state"] == "landed"
    return w


def test_takeoff():
    w = landed_world()
    assert w.step({"p": {"ax": 1, "ay": -1}}) == [{"type": "takeoff", "taxi": "p", "pad": "A"}]
    t = taxi_state(w)
    vy = RULES.gravity - RULES.main_thrust
    assert (t["fx"], t["fy"], t["vx"], t["vy"]) == (mid(3), resting_fy(5) + vy, 0, vy)
    assert (t["state"], t["pad"]) == ("flying", None)


def test_other_moves_stay_landed():
    w = landed_world()
    for move in (COAST, {"ax": 1, "ay": 0}, {"ax": -1, "ay": 1}, {"ax": 0, "ay": 1}, None,
                 {"ax": 0, "ay": True}, {"ax": 0, "ay": -2}, "junk"):
        assert w.step({"p": move}) == []
        t = taxi_state(w)
        assert (t["fx"], t["fy"], t["vx"], t["vy"], t["state"], t["pad"]) == (
            mid(3), resting_fy(5), 0, 0, "landed", "A")


def test_parse_move_rejects_invalid():
    assert parse_move({"ax": -1, "ay": 1}) == (-1, 1)
    for bad in (None, {}, {"ax": 1}, {"ax": True, "ay": 0}, {"ax": 1.0, "ay": 0}, {"ax": 2, "ay": 0}):
        assert parse_move(bad) == (0, 0)


def test_crash_and_respawn_at_the_centre_of_a_spawn():
    w = world()
    place(w, 100, mid(3), vx=RULES.max_speed, vy=-RULES.gravity)
    w.step({})
    t = taxi_state(w)
    assert (t["state"], t["money"], t["respawn_in"]) == (
        "crashed", -RULES.crash_cost, RULES.crash_penalty_ticks)
    crashed_at = (t["fx"], t["fy"])
    for n in range(RULES.crash_penalty_ticks - 1):
        assert w.step({"p": THRUST}) == []
        t = taxi_state(w)
        assert t["state"] == "crashed" and t["respawn_in"] == RULES.crash_penalty_ticks - 1 - n
        assert (t["fx"], t["fy"]) == crashed_at
    events = w.step({})
    assert events == [{"type": "respawn", "taxi": "p", "x": 2, "y": 1}]
    t = taxi_state(w)
    assert (t["x"], t["y"], t["fx"], t["fy"], t["vx"], t["vy"]) == (2, 1, mid(2), mid(1), 0, 0)
    assert (t["state"], t["respawn_in"], t["money"]) == ("flying", 0, -RULES.crash_cost)
    assert w.state()["events"] == events


def test_ghosts_do_not_collide():
    moves = [{"ax": 1, "ay": 0}, COAST, {"ax": -1, "ay": -1}, COAST, THRUST, COAST]
    solo = world("classic")
    pair = world("classic", players=("a", "b"))
    place(solo, mid(10), mid(5))
    place(pair, mid(10), mid(5), i=0)
    place(pair, mid(10), mid(5), i=1)
    for m in moves:
        solo.step({"p": m})
        pair.step({"a": m, "b": m})
        s = {k: v for k, v in taxi_state(solo).items() if k != "id"}
        for i in (0, 1):
            assert {k: v for k, v in taxi_state(pair, i).items() if k != "id"} == s

    # head-on through each other: both pass unharmed
    w = world("classic", players=("a", "b"))
    place(w, mid(10), mid(5), vx=24, vy=-RULES.gravity, i=0)
    place(w, mid(10) + 40, mid(5), vx=-24, vy=-RULES.gravity, i=1)
    w.step({})
    a, b = taxi_state(w, 0), taxi_state(w, 1)
    assert (a["fx"], a["state"]) == (mid(10) + 24, "flying")
    assert (b["fx"], b["state"]) == (mid(10) + 16, "flying")


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
