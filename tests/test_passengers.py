import json
import re
from pathlib import Path

from spacetaxi.maps import load_map
from spacetaxi.sim import CRASHED, FLYING, LANDED, Passenger, Rules, World

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "maps"
RULES_DOC = Path(__file__).resolve().parent.parent / "docs" / "rules.md"
TAKEOFF = {"ax": 0, "ay": -1}

# passengers.txt pads: A x=1..3 y=4 (centre 2), B x=7..9 y=4 (centre 8), C x=3..4 y=6 (centre 4)


def world(name="passengers", players=("p",), seed=0, **rules):
    return World(load_map(FIXTURES / f"{name}.txt"), Rules(**rules), seed, list(players))


def pad(w, pid):
    return next(p for p in w.map.pads if p.id == pid)


def park(w, i, pid, x=None):
    """Put taxi `i` landed on pad `pid` (it landed some earlier tick)."""
    p = pad(w, pid)
    t = w.taxis[i]
    t.x, t.y = (p.x if x is None else x), p.y - 1
    t.vx = t.vy = 0
    t.state, t.pad = LANDED, pid
    return t


def hover(w, i, pid, x=None):
    """Put taxi `i` right above pad `pid` so that coasting this tick lands it softly."""
    p = pad(w, pid)
    t = w.taxis[i]
    t.x, t.y = (p.x if x is None else x), p.y - 1
    t.vx = 0
    t.vy = 0 if w.tick % w.rules.gravity_every == 0 else 1
    t.state, t.pad = FLYING, None
    return t


def add_passenger(w, at, to, spot_x=None, fare=70):
    """A waiting passenger, as if it spawned on an earlier tick."""
    p = Passenger(
        id=w.next_passenger_id,
        at=at,
        spot_x=pad(w, at).x if spot_x is None else spot_x,
        to=to,
        fare=fare,
        expires_in=w.rules.passenger_expires,
    )
    w.next_passenger_id += 1
    w.passengers.append(p)
    return p


def of_type(events, kind):
    return [e for e in events if e["type"] == kind]


# fare_base + fare_per_cell * Manhattan distance of the centres A (2,4), B (8,4), C (4,6)
FARES = {
    ("A", "B"): 50 + 2 * 6, ("B", "A"): 50 + 2 * 6,
    ("A", "C"): 50 + 2 * (2 + 2), ("C", "A"): 50 + 2 * (2 + 2),
    ("B", "C"): 50 + 2 * (4 + 2), ("C", "B"): 50 + 2 * (4 + 2),
}


# spawning


def test_spawn_every_n_ticks_up_to_players_plus_two():
    for players in (("p",), ("p", "q")):
        w = world(players=players, passenger_spawn_every=3, passenger_expires=1000)
        for i in range(len(players)):
            park(w, i, "A")  # campers never pick anybody up
        spawn_ticks = []
        for _ in range(30):
            tick = w.tick
            events = w.step({})
            if of_type(events, "spawn"):
                spawn_ticks.append(tick)
            assert len(w.passengers) <= len(players) + 2
        assert spawn_ticks == [3 * k for k in range(1, len(players) + 3)]
        assert [p["id"] for p in w.state()["passengers"]] == list(range(1, len(players) + 3))


def test_spawn_resumes_when_a_passenger_leaves():
    w = world(passenger_spawn_every=2, passenger_expires=1000)
    park(w, 0, "A")
    for _ in range(10):
        w.step({})
    assert len(w.passengers) == 3
    w.passengers.pop()
    w.step({})  # tick 11: not a spawn tick
    assert len(w.passengers) == 2
    events = w.step({})  # tick 12
    assert of_type(events, "spawn") and len(w.passengers) == 3


def test_spawned_passengers_have_valid_pads_spots_fares_and_expiry():
    seen_pairs = set()
    for seed in range(40):
        w = world(seed=seed, passenger_spawn_every=1, passenger_expires=1000)
        park(w, 0, "A")
        for _ in range(3):
            events = w.step({})
            (spawn,) = of_type(events, "spawn")
            p = w.state()["passengers"][-1]
            assert spawn == {"type": "spawn", "passenger": p["id"], "at": p["at"], "to": p["to"]}
            assert p["at"] != p["to"]
            at = pad(w, p["at"])
            assert at.x <= p["spot_x"] < at.x + at.len
            assert p["fare"] == FARES[(p["at"], p["to"])]
            assert p["expires_in"] == 1000
            seen_pairs.add((p["at"], p["to"]))
    assert len(seen_pairs) == 6  # every ordered pair of the 3 pads shows up


def test_spawning_is_seeded():
    def run(seed):
        w = world(seed=seed, passenger_spawn_every=1, passenger_expires=1000)
        park(w, 0, "A")
        for _ in range(3):
            w.step({})
        return w.state()["passengers"]

    assert run(7) == run(7)
    assert len({json.dumps(run(s)) for s in range(10)}) > 1


# pickup


def test_empty_taxi_landing_on_the_pad_picks_up():
    w = world()
    p = add_passenger(w, "B", "C", spot_x=8, fare=62)
    hover(w, 0, "B", x=9)
    events = w.step({})
    assert of_type(events, "landed") == [{"type": "landed", "taxi": "p", "pad": "B"}]
    assert of_type(events, "pickup") == [{"type": "pickup", "taxi": "p", "passenger": p.id}]
    assert events.index(of_type(events, "landed")[0]) < events.index(of_type(events, "pickup")[0])
    s = w.state()
    assert s["passengers"] == []
    assert s["taxis"][0]["passenger"] == {"id": p.id, "to": "C", "fare": 62}


def test_landing_on_another_pad_does_not_pick_up():
    w = world()
    add_passenger(w, "B", "C")
    hover(w, 0, "A")
    events = w.step({})
    assert of_type(events, "landed") and not of_type(events, "pickup")
    assert w.taxis[0].passenger is None and len(w.passengers) == 1


def test_pickup_of_a_really_spawned_passenger():
    w = world(passenger_spawn_every=4, passenger_expires=1000)
    park(w, 0, "A")
    for _ in range(4):
        w.step({})
    (p,) = w.passengers
    hover(w, 0, p.at)
    events = w.step({})
    assert of_type(events, "pickup") == [{"type": "pickup", "taxi": "p", "passenger": p.id}]


def test_passengers_on_one_pad_are_served_oldest_first_one_per_taxi():
    w = world(players=("p", "q"))
    old = add_passenger(w, "A", "B", spot_x=1)
    new = add_passenger(w, "A", "C", spot_x=3)
    hover(w, 0, "A", x=3)
    events = w.step({})
    assert of_type(events, "pickup") == [{"type": "pickup", "taxi": "p", "passenger": old.id}]
    assert w.passengers == [new]
    hover(w, 1, "A", x=1)
    events = w.step({})
    assert of_type(events, "pickup") == [{"type": "pickup", "taxi": "q", "passenger": new.id}]


# camping


def test_taxi_parked_when_the_passenger_appears_must_take_off_and_land_again():
    w = world()
    park(w, 0, "A")
    w.step({})
    p = add_passenger(w, "A", "B")  # appears while the taxi sits on A
    for _ in range(5):
        assert not of_type(w.step({}), "pickup")
    assert w.taxis[0].state == LANDED and w.passengers == [p]

    events = w.step({"p": TAKEOFF})
    assert of_type(events, "takeoff") and not of_type(events, "pickup")
    for _ in range(10):
        events = w.step({})
        if of_type(events, "landed"):
            break
    assert of_type(events, "landed") == [{"type": "landed", "taxi": "p", "pad": "A"}]
    assert of_type(events, "pickup") == [{"type": "pickup", "taxi": "p", "passenger": p.id}]


def test_taxi_landing_on_the_tick_the_passenger_appears_is_camping():
    checked = 0
    for seed in range(20):
        w = world(seed=seed, passenger_spawn_every=1, passenger_expires=1000)
        hover(w, 0, "A")
        events = w.step({})  # tick 1: the taxi lands, then a passenger spawns
        assert of_type(events, "landed") and of_type(events, "spawn")
        (p,) = w.passengers
        if p.at != "A":
            continue
        checked += 1
        for _ in range(5):
            assert not of_type(w.step({}), "pickup")
        assert w.taxis[0].state == LANDED and p in w.passengers
    assert checked > 0


def test_camping_with_a_really_spawned_passenger():
    w = world(players=("p", "q", "r"), passenger_spawn_every=4, passenger_expires=1000)
    for i, pid in enumerate("ABC"):
        park(w, i, pid)
    for _ in range(4):
        w.step({})
    (p,) = w.passengers
    for _ in range(10):
        assert not of_type(w.step({}), "pickup")
    assert p in w.passengers and len(w.passengers) == 3


# tie-break


def test_two_touchdowns_closer_to_spot_x_wins():
    for closer in (0, 1):
        w = world(players=("p", "q"))
        p = add_passenger(w, "B", "A", spot_x=8)
        hover(w, closer, "B", x=8)
        hover(w, 1 - closer, "B", x=9)
        events = w.step({})
        assert len(of_type(events, "landed")) == 2
        winner = w.taxis[closer].id
        assert of_type(events, "pickup") == [{"type": "pickup", "taxi": winner, "passenger": p.id}]
        assert w.taxis[1 - closer].passenger is None


def test_equal_distance_tie_is_seeded():
    def winner(seed):
        w = world(players=("p", "q"), seed=seed)
        add_passenger(w, "B", "A", spot_x=8)
        hover(w, 0, "B", x=7)
        hover(w, 1, "B", x=9)
        (pickup,) = of_type(w.step({}), "pickup")
        return pickup["taxi"]

    for seed in range(20):
        assert winner(seed) == winner(seed)
    assert {winner(seed) for seed in range(20)} == {"p", "q"}


# occupied taxis


def test_occupied_taxi_never_picks_up():
    w = world(players=("p", "q"))
    p = add_passenger(w, "A", "B", spot_x=1)
    hover(w, 0, "A", x=1).passenger = {"id": 99, "to": "C", "fare": 58}
    events = w.step({})
    assert not of_type(events, "pickup")
    assert w.taxis[0].passenger["id"] == 99 and w.passengers == [p]

    hover(w, 0, "A", x=1)
    hover(w, 1, "A", x=3)  # farther, but the only empty one
    events = w.step({})
    assert of_type(events, "pickup") == [{"type": "pickup", "taxi": "q", "passenger": p.id}]
    assert w.taxis[0].passenger["id"] == 99


# delivery


def test_touchdown_on_destination_pays_the_fare():
    w = world()
    w.taxis[0].money = 10
    hover(w, 0, "C").passenger = {"id": 5, "to": "C", "fare": 58}
    events = w.step({})
    assert of_type(events, "deliver") == [
        {"type": "deliver", "taxi": "p", "passenger": 5, "fare": 58}
    ]
    t = w.state()["taxis"][0]
    assert t["money"] == 68 and t["passenger"] is None
    assert w.taxis[0].deliveries == 1


def test_touchdown_elsewhere_keeps_the_passenger():
    w = world()
    hover(w, 0, "A").passenger = {"id": 5, "to": "C", "fare": 58}
    events = w.step({})
    assert of_type(events, "landed") and not of_type(events, "deliver")
    assert w.taxis[0].passenger["id"] == 5 and w.taxis[0].money == 0


def test_deliver_then_pick_up_on_the_same_touchdown():
    w = world()
    waiting = add_passenger(w, "C", "A")
    hover(w, 0, "C").passenger = {"id": 5, "to": "C", "fare": 58}
    events = w.step({})
    kinds = [e["type"] for e in events]
    assert kinds == ["landed", "deliver", "pickup"]
    assert w.taxis[0].passenger["id"] == waiting.id and w.taxis[0].money == 58


def test_full_round_trip_with_spawned_passenger():
    w = world(passenger_spawn_every=4, passenger_expires=1000)
    park(w, 0, "A")
    for _ in range(4):
        w.step({})
    (p,) = w.passengers
    hover(w, 0, p.at)
    assert of_type(w.step({}), "pickup")
    hover(w, 0, p.to)
    (deliver,) = of_type(w.step({}), "deliver")
    assert deliver == {"type": "deliver", "taxi": "p", "passenger": p.id, "fare": p.fare}
    assert w.taxis[0].money == FARES[(p.at, p.to)]


# crash


def test_crash_while_carrying_loses_the_passenger():
    w = world()
    t = hover(w, 0, "A")
    t.passenger = {"id": 5, "to": "C", "fare": 58}
    t.vy = 3  # hard landing
    events = w.step({})
    assert events == [
        {"type": "crash", "taxi": "p", "cause": "hard_landing"},
        {"type": "lost", "taxi": "p", "passenger": 5},
    ]
    s = w.state()
    assert s["taxis"][0]["passenger"] is None and s["taxis"][0]["state"] == CRASHED
    assert s["passengers"] == []


def test_hard_landing_on_a_passengers_pad_does_not_pick_up():
    w = world()
    p = add_passenger(w, "A", "B")
    hover(w, 0, "A").vy = 3
    events = w.step({})
    assert of_type(events, "crash") and not of_type(events, "pickup")
    assert w.passengers == [p]


# expiry


def test_waiting_passenger_expires():
    w = world(passenger_expires=3)
    park(w, 0, "B")
    p = add_passenger(w, "A", "B")
    assert w.state()["passengers"][0]["expires_in"] == 3
    assert not of_type(w.step({}), "expired")
    assert w.state()["passengers"][0]["expires_in"] == 2
    assert not of_type(w.step({}), "expired")
    events = w.step({})
    assert of_type(events, "expired") == [{"type": "expired", "passenger": p.id}]
    assert w.state()["passengers"] == []


def test_spawned_passenger_expires_after_passenger_expires_ticks():
    w = world(passenger_spawn_every=5, passenger_expires=7)
    park(w, 0, "A")
    expired_at = None
    for _ in range(14):
        tick = w.tick
        if of_type(w.step({}), "expired"):
            expired_at = tick
            break
    assert expired_at == 5 + 7


def test_carried_passenger_does_not_expire():
    w = world(passenger_expires=2)
    park(w, 0, "A").passenger = {"id": 5, "to": "C", "fare": 58}
    for _ in range(5):
        assert not of_type(w.step({}), "expired")
    assert w.taxis[0].passenger["id"] == 5


# state shape


def doc_json_blocks():
    return re.findall(r"```json\n(.*?)```", RULES_DOC.read_text(), re.S)


def doc_tick_message():
    ticks = [json.loads(b) for b in doc_json_blocks() if '"type": "tick"' in b]
    assert len(ticks) == 1
    return ticks[0]


def doc_event_shapes():
    (shapes,) = [b for b in doc_json_blocks() if b.startswith('{"type": "landed"')]
    return {(e := json.loads(line))["type"]: set(e) for line in shapes.splitlines()}


def test_state_has_exactly_the_keys_of_the_tick_message():
    doc = doc_tick_message()
    w = world(players=("p", "q"))
    park(w, 0, "A").passenger = {"id": 9, "to": "B", "fare": 62}
    add_passenger(w, "B", "C")
    s = w.state()
    assert set(s) == set(doc)
    assert set(s["taxis"][0]) == set(doc["taxis"][0])
    assert set(s["taxis"][0]["passenger"]) == set(doc["taxis"][0]["passenger"])
    assert set(s["passengers"][0]) == set(doc["passengers"][0])
    w.step({})
    assert set(w.state()) == set(doc)


def test_events_have_the_documented_shapes():
    shapes = doc_event_shapes()
    w = world(players=("p", "q"), passenger_spawn_every=1, passenger_expires=2,
              crash_penalty_ticks=1)
    seen = set()
    add_passenger(w, "A", "B")
    hover(w, 0, "A")
    t = hover(w, 1, "C")
    t.passenger, t.vy = {"id": 99, "to": "B", "fare": 62}, 3
    for tick in range(12):
        moves = {"p": TAKEOFF} if w.taxis[0].state == LANDED else {}
        if w.taxis[0].passenger and w.taxis[0].state == FLYING:
            hover(w, 0, w.taxis[0].passenger["to"])
        for e in w.step(moves):
            assert set(e) == shapes[e["type"]], e
            seen.add(e["type"])
    assert seen == set(shapes)

