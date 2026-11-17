"""The milestone race (docs/rules.md, Milestones): detection, F1 points, ties, benchmarks,
standings on disk, and the server's awards to spectators."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from spacetaxi import ws
from spacetaxi.cli import main
from spacetaxi.maps import load_map
from spacetaxi.milestones import (LIFTOFF_TICKS, MILESTONES, POINTS, Standings, Tracker,
                                  benchmarks)
from spacetaxi.server import Arena, ArenaServer
from spacetaxi.sim import Rules

RULES = Rules(tick_ms=50, deadline_ms=30, round_ticks=20)
GIVEN = {"greedy": 300, "pilot": 450}


# --- detection


def flying(*names, state="flying"):
    return {n: state for n in names}


def test_event_milestones_from_a_tick():
    t = Tracker(["anna", "bob"])
    events = [{"type": "landed", "taxi": "anna", "pad": "A"},
              {"type": "pickup", "taxi": "anna", "passenger": 3},
              {"type": "deliver", "taxi": "bob", "passenger": 1, "fare": 70},
              {"type": "landed", "taxi": "bob", "pad": "B"},
              {"type": "spawn", "passenger": 4, "at": "B", "to": "C"}]
    found = t.tick({"anna": "landed", "bob": "landed"}, events)
    assert found == [("touchdown", "anna"), ("fare", "anna"), ("paid", "bob"),
                     ("touchdown", "bob")]


def test_liftoff_after_50_ticks_flying():
    t = Tracker(["anna", "bob"])
    for k in range(1, LIFTOFF_TICKS):
        assert t.tick(flying("anna", "bob"), []) == []
    assert t.tick(flying("anna", "bob"), []) == [("liftoff", "anna"), ("liftoff", "bob")]
    assert t.tick(flying("anna", "bob"), []) == []  # once per streak


def test_liftoff_streak_reset_by_a_crash():
    t = Tracker(["anna"])
    for _ in range(LIFTOFF_TICKS - 1):
        t.tick(flying("anna"), [])
    # the crash tick: the taxi ends crashed
    assert t.tick(flying("anna", state="crashed"),
                  [{"type": "crash", "taxi": "anna", "cause": "wall"}]) == []
    for _ in range(LIFTOFF_TICKS - 1):
        assert t.tick(flying("anna"), []) == []
    assert t.tick(flying("anna"), []) == [("liftoff", "anna")]


def test_liftoff_streak_reset_by_a_crash_even_when_flying_again():
    t = Tracker(["anna"])
    for _ in range(LIFTOFF_TICKS - 1):
        t.tick(flying("anna"), [])
    assert t.tick(flying("anna"), [{"type": "crash", "taxi": "anna", "cause": "wall"}]) == []
    assert t.tick(flying("anna"), []) == []


def test_liftoff_streak_reset_by_a_landing():
    t = Tracker(["anna"])
    for _ in range(LIFTOFF_TICKS - 1):
        t.tick(flying("anna"), [])
    t.tick(flying("anna", state="landed"), [{"type": "landed", "taxi": "anna", "pad": "A"}])
    assert t.tick(flying("anna"), []) == []


def test_humans_are_not_tracked():
    t = Tracker(["anna", "kris"], humans=["kris"])
    found = t.tick({"anna": "landed", "kris": "landed"},
                   [{"type": "landed", "taxi": "kris", "pad": "A"},
                    {"type": "landed", "taxi": "anna", "pad": "B"}])
    assert found == [("touchdown", "anna")]
    results = [{"id": "kris", "money": 900, "deliveries": 5, "crashes": 0},
               {"id": "anna", "money": 0, "deliveries": 0, "crashes": 0}]
    assert t.round_end(results, GIVEN) == []


def result(name, money=0, deliveries=0, crashes=0):
    return {"id": name, "money": money, "deliveries": deliveries, "crashes": crashes}


def test_round_end_milestones():
    t = Tracker(["anna", "bob", "cid"])
    results = [result("anna", 460, 3, 0), result("bob", 310, 1, 1), result("cid", 120, 1, 0)]
    found = t.round_end(results, GIVEN)
    assert found == [("clean", "anna", 460), ("hat_trick", "anna", 460),
                     ("beat_greedy", "anna", 460), ("beat_pilot", "anna", 460),
                     ("beat_greedy", "bob", 310), ("clean", "cid", 120)]


def test_clean_needs_a_delivery_and_beating_means_above():
    t = Tracker(["anna", "bob"])
    found = t.round_end([result("anna", 300, 0, 0), result("bob", 450, 2, 1)], GIVEN)
    assert found == [("beat_greedy", "bob", 450)]


def test_round_end_milestones_need_two_taxis():
    t = Tracker(["anna"])
    assert t.round_end([result("anna", 900, 5, 0)], GIVEN) == []
    # a human counts as a taxi of the round
    t = Tracker(["anna", "kris"], humans=["kris"])
    assert ("hat_trick", "anna", 900) in t.round_end(
        [result("anna", 900, 5, 0), result("kris")], GIVEN)


def test_benchmarks_unknown_are_not_awarded():
    t = Tracker(["anna", "bob"])
    found = t.round_end([result("anna", 900, 5, 0), result("bob")],
                        {"greedy": None, "pilot": None})
    assert [m for m, *_ in found] == ["clean", "hat_trick"]


# --- scoring


def test_places_and_points_1_to_11():
    s = Standings()
    names = [f"n{i}" for i in range(11)]
    awards = [s.award("paid", [(n, 0)]) for n in names]
    assert [a[0]["place"] for a in awards] == list(range(1, 12))
    assert [a[0]["points"] for a in awards] == [25, 18, 15, 12, 10, 8, 6, 4, 2, 1, 0]
    assert s.has("n10", "paid")  # no points, but the badge
    assert s.award("paid", [("n0", 0)]) == []  # once per name


def test_same_tick_tie_shares_the_place_and_skips_the_next():
    s = Standings()
    s.award("fare", [("a", 0)])
    tie = s.award("fare", [("b", 0), ("c", 0)])
    assert [(a["name"], a["place"], a["points"]) for a in tie] == [("b", 2, 18), ("c", 2, 18)]
    [d] = s.award("fare", [("d", 0)])
    assert (d["place"], d["points"]) == (4, 12)


def test_round_end_tie_broken_by_money():
    s = Standings()
    out = s.award("clean", [("low", 100), ("high", 400), ("mid", 250), ("mid2", 250)])
    assert [(a["name"], a["place"], a["points"]) for a in out] == [
        ("high", 1, 25), ("mid", 2, 18), ("mid2", 2, 18), ("low", 4, 12)]


def test_table_totals_and_ranks():
    s = Standings()
    s.award("hello", [("a", 0)])
    s.award("hello", [("b", 0)])
    s.award("touchdown", [("b", 0)])
    rows = s.table()
    assert [(r["rank"], r["name"], r["points"]) for r in rows] == [(1, "b", 43), (2, "a", 25)]
    assert rows[0]["milestones"] == {"hello": {"place": 2, "points": 18},
                                     "touchdown": {"place": 1, "points": 25}}


def test_standings_survive_on_disk(tmp_path):
    path = tmp_path / "standings.json"
    s = Standings(path)
    s.award("hello", [("anna", 0)])
    s.save()
    again = Standings(path)
    assert again.table() == s.table()
    [bob] = again.award("hello", [("bob", 0)])
    assert bob["place"] == 2


# --- benchmarks


def test_automatic_benchmark_is_a_positive_integer():
    got = benchmarks(load_map("cavern"), Rules(round_ticks=200), rounds=2)
    assert set(got) == {"greedy", "pilot"}
    assert all(type(v) is int and v > 0 for v in got.values())


def test_benchmark_override_is_used_and_shown():
    lines = []

    async def main_():
        server = ArenaServer(load_map("cavern"), RULES, 0.3, seed=1, say=lines.append,
                             benchmarks=GIVEN)
        srv, _ = await start(server)
        await asyncio.sleep(0.05)
        srv.close()
        return server

    server = asyncio.run(main_())
    assert server.benchmarks == GIVEN
    assert "benchmarks (given): greedy=300 pilot=450" in lines
    assert not any("simulating" in line for line in lines)
    data = json.loads(server.http("/standings.json")[2])
    assert data["benchmarks"] == GIVEN


def test_automatic_benchmarks_are_simulated_in_the_background():
    lines = []

    async def main_():
        server = ArenaServer(load_map("cavern"), RULES, 0.3, seed=1, say=lines.append,
                             benchmarks={"pilot": 450})
        srv, _ = await start(server)
        assert server.benchmarks["greedy"] is None  # the server is up before it is known
        for _ in range(200):
            if server.benchmarks["greedy"] is not None:
                break
            await asyncio.sleep(0.05)
        srv.close()
        return server

    server = asyncio.run(main_())
    assert type(server.benchmarks["greedy"]) is int and server.benchmarks["greedy"] > 0
    assert server.benchmarks["pilot"] == 450
    assert any(line.startswith("benchmarks (simulated): greedy=") for line in lines)


def test_beat_milestones_wait_for_the_round_after_the_benchmark():
    async def main_():
        server = ArenaServer(load_map("cavern"), RULES, 0.3, seed=1)
        arena = Arena(server, 1, [], seed=1)  # started before the benchmarks are known
        server.benchmarks.update(GIVEN)
        later = Arena(server, 2, [], seed=1)
        return arena.benchmarks, later.benchmarks

    before, after = asyncio.run(main_())
    assert before == {"greedy": None, "pilot": None} and after == GIVEN


def test_serve_rejects_a_bad_benchmark(capsys):
    assert main(["serve", "--benchmark", "random=3"]) == 2
    assert main(["serve", "--benchmark", "greedy=lots"]) == 2
    assert "--benchmark" in capsys.readouterr().err


# --- the server


def test_same_tick_in_two_arenas_of_a_wave_ties():
    server = ArenaServer(load_map("cavern"), RULES, 0.3, seed=1, benchmarks=GIVEN)
    one, two = SimpleNamespace(wave=1, stepped=0), SimpleNamespace(wave=1, stepped=0)
    server.arenas = [one, two]
    server.pending.append((1, 7, "touchdown", "anna", 0))
    one.stepped = 7
    server.flush(1)  # arena two has not played tick 7 yet
    assert not server.standings.awards
    server.pending.append((1, 7, "touchdown", "bob", 0))
    server.pending.append((1, 8, "touchdown", "cid", 0))
    two.stepped = 7
    server.flush(1)
    assert {n: a["touchdown"]["place"] for n, a in server.standings.awards.items()} == {
        "anna": 1, "bob": 1}
    server.arenas = set()
    server.flush(1)
    assert server.standings.awards["cid"]["touchdown"] == {"place": 3, "points": 15}


async def start(server):
    srv = await server.start("127.0.0.1", 0)
    return srv, srv.sockets[0].getsockname()[1]


async def join(port, name, human=False):
    sock = await ws.connect(f"ws://127.0.0.1:{port}/bot")
    hello = {"type": "hello", "name": name, "protocol": 1}
    if human:
        hello["client"] = "human"
    await sock.send(json.dumps(hello))
    return sock


async def recv(sock, timeout=5.0):
    async with asyncio.timeout(timeout):
        text = await sock.recv()
    return None if text is None else json.loads(text)


async def play(sock, rounds=1):
    """Coast every tick until `rounds` round_ends; returns the types seen and the last
    round_end."""
    seen, ends, end = [], 0, None
    while ends < rounds:
        msg = await recv(sock)
        assert msg is not None
        seen.append(msg["type"])
        if msg["type"] == "tick":
            await sock.send(json.dumps({"type": "move", "ax": 0, "ay": 0, "tick": msg["tick"]}))
        elif msg["type"] == "round_end":
            ends, end = ends + 1, msg
    return seen, end


def test_server_awards_hello_to_bots_not_humans_and_tells_spectators(tmp_path):
    path = tmp_path / "standings.json"
    lines = []

    async def main_():
        server = ArenaServer(load_map("cavern"), RULES, 0.2, seed=1, say=lines.append,
                             standings=path, benchmarks=GIVEN)
        srv, port = await start(server)
        spec = await ws.connect(f"ws://127.0.0.1:{port}/spectate")
        assert (await recv(spec))["type"] == "spectate"
        anna = await join(port, "anna")
        first = await recv(spec)
        kris = await join(port, "kris", human=True)
        bob = await join(port, "bob")
        second = await recv(spec)
        seen = await asyncio.gather(play(anna), play(kris), play(bob))
        await anna.close()
        # anna reconnects: same name, same points, no second hello award
        anna = await join(port, "anna")
        assert (await recv(anna))["type"] == "welcome"
        for s in (anna, kris, bob, spec):
            await s.close()
        srv.close()
        return first, second, seen, server

    first, second, seen, server = asyncio.run(main_())
    assert first == {"type": "milestone", "name": "anna", "milestone": "hello", "place": 1,
                     "points": 25}
    assert second == {"type": "milestone", "name": "bob", "milestone": "hello", "place": 2,
                      "points": 18}
    assert all("milestone" not in types for types, _ in seen)  # bots never get it
    data = json.loads(server.http("/standings.json")[2])
    names = {r["name"] for r in data["standings"]}
    assert "kris" not in names and {"anna", "bob"} <= names
    assert data["milestones"] == list(MILESTONES) and data["points"] == list(POINTS)
    assert data["reached"]["hello"] == 2
    assert "* milestone hello: anna P1 +25" in lines
    assert sum("milestone hello: anna" in line for line in lines) == 1
    # a restart with the same file keeps the race
    again = ArenaServer(load_map("cavern"), RULES, 0.2, seed=1, standings=path,
                        benchmarks=GIVEN)
    assert again.standings.table() == server.standings.table()
    assert json.loads(path.read_text())["awards"]["anna"]["hello"] == {"place": 1, "points": 25}


def test_round_end_milestones_not_awarded_in_a_one_taxi_round():
    async def main_():
        server = ArenaServer(load_map("cavern"), RULES, 0.1, seed=1, benchmarks={"greedy": -10**6, "pilot": -10**6})
        srv, port = await start(server)
        solo = await join(port, "solo")
        await play(solo)
        await solo.close()
        srv.close()
        return server

    server = asyncio.run(main_())
    assert set(server.standings.awards["solo"]) <= {"hello", "liftoff", "touchdown", "fare",
                                                    "paid"}


def test_round_end_milestones_awarded_with_two_taxis():
    async def main_():
        server = ArenaServer(load_map("cavern"), RULES, 0.2, seed=1,
                             benchmarks={"greedy": -10**6, "pilot": -10**6})
        srv, port = await start(server)
        a, b = await join(port, "a"), await join(port, "b")
        [(_, end), _] = await asyncio.gather(play(a), play(b))
        await asyncio.sleep(0.05)
        for s in (a, b):
            await s.close()
        srv.close()
        return server, end

    server, end = asyncio.run(main_())
    money = {r["id"]: r["money"] for r in end["results"]}
    # both beat the (very low) benchmarks at the same round end: placed by money
    for m in ("beat_greedy", "beat_pilot"):
        place = {n: got[m]["place"] for n, got in server.standings.awards.items()}
        if money["a"] == money["b"]:
            assert place == {"a": 1, "b": 1}
        else:
            rich = max(money, key=money.get)
            assert place[rich] == 1 and sorted(place.values()) == [1, 2]


def test_standings_page_renders_and_leaderboard_links_it():
    server = ArenaServer(load_map("cavern"), RULES, 0.2, seed=1, benchmarks=GIVEN)
    status, kind, body = server.http("/standings")
    assert status == 200 and kind.startswith("text/html")
    assert b"/standings.json" in body and b"/*FONT*/" not in body
    assert b'href="/standings"' in server.http("/")[2]


@pytest.mark.parametrize("bad", ["{", '{"awards": 3}'])
def test_serve_refuses_a_broken_standings_file(tmp_path, bad, capsys):
    path = tmp_path / "s.json"
    path.write_text(bad)
    assert main(["serve", "--standings", str(path), "--benchmark", "greedy=1",
                 "--benchmark", "pilot=1"]) == 2
    assert "spacetaxi serve: error" in capsys.readouterr().err
