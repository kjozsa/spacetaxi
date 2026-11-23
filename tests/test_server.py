"""The arena server (`spacetaxi serve`): queue, parallel arenas, timing, errors, leaderboard."""

import asyncio
import json
import re
import os
import socket
import subprocess
import sys
import time

from spacetaxi import ws
from spacetaxi.maps import load_map
from spacetaxi.server import ArenaServer, Leaderboard, group_sizes
from spacetaxi.show import load_replay
from spacetaxi.sim import Rules
from spacetaxi.viewer import build_html

RULES = Rules(tick_ms=50, deadline_ms=30, round_ticks=20)


def make_server(fill_seconds=0.3, **kw) -> ArenaServer:
    return ArenaServer({"cavern": load_map("cavern")}, RULES, fill_seconds, seed=1, **kw)


async def start(server: ArenaServer) -> tuple[asyncio.Server, int]:
    srv = await server.start("127.0.0.1", 0)
    return srv, srv.sockets[0].getsockname()[1]


async def join(port: int, name: str, protocol=1) -> ws.WebSocket:
    sock = await ws.connect(f"ws://127.0.0.1:{port}/bot")
    await sock.send(json.dumps({"type": "hello", "name": name, "protocol": protocol}))
    return sock


async def recv(sock: ws.WebSocket, timeout: float = 5):
    async with asyncio.timeout(timeout):
        text = await sock.recv()
    return None if text is None else json.loads(text)


async def play_round(sock: ws.WebSocket, answer=lambda tick: {"ax": 0, "ay": -1, "tick": tick},
                     quit_after: int | None = None) -> dict:
    """Read until `round_end` and the `queued` after it, answering each tick with `answer`
    (None: stay silent). Returns what it saw, with receive times."""
    seen = {"welcome": None, "queued": [], "start": None, "ticks": 0, "end": None,
            "requeued": None, "start_at": None, "end_at": None}
    while True:
        msg = await recv(sock)
        assert msg is not None, "the server closed the socket"
        kind = msg["type"]
        if kind == "welcome":
            seen["welcome"] = msg
        elif kind == "queued":
            if seen["end"]:
                seen["requeued"] = msg
                return seen
            seen["queued"].append(msg)
        elif kind == "round_start":
            seen["start"], seen["start_at"] = msg, time.monotonic()
        elif kind == "tick":
            seen["ticks"] += 1
            if quit_after is not None and seen["ticks"] > quit_after:
                await sock.close()
                return seen
            move = answer(msg["tick"])
            if move is not None:
                await sock.send(json.dumps({"type": "move", **move}))
        elif kind == "round_end":
            seen["end"], seen["end_at"] = msg, time.monotonic()


def test_six_bots_play_one_wave_of_two_arenas_that_start_together_and_requeue():
    async def main():
        server = make_server(fill_seconds=0.3)
        srv, port = await start(server)
        joined = time.monotonic()
        socks = [await join(port, f"bot{i}") for i in range(6)]
        seen = await asyncio.gather(*(play_round(s) for s in socks))
        for s in socks:
            await s.close()
        srv.close()
        return joined, seen, json.loads(server.http("/leaderboard.json")[2])

    joined, seen, board = asyncio.run(main())
    for i, s in enumerate(seen):
        assert s["welcome"]["id"] == f"bot{i}" and s["welcome"]["rules"] == RULES.to_dict()
        assert s["queued"][0]["position"] >= 1 and type(s["queued"][0]["starts_in"]) is int
        assert s["ticks"] == RULES.round_ticks
        assert s["end"]["round"] == s["start"]["round"]
        assert s["requeued"]["type"] == "queued" and s["requeued"]["starts_in"] >= 0
    rounds = {}
    for s in seen:
        rounds.setdefault(s["start"]["round"], s)
    assert sorted(len(s["start"]["players"]) for s in rounds.values()) == [3, 3]
    starts = sorted(s["start_at"] for s in seen)
    # the wave waited the fill time from the first join, then both arenas started together
    assert starts[0] - joined >= 0.25
    assert starts[-1] - starts[0] < 0.05
    assert board["wave"] == 1 and board["rounds"] == 2


def test_six_bots_six_waves_every_pair_meets_and_no_group_repeats():
    rules = Rules(tick_ms=20, deadline_ms=15, round_ticks=3)
    lines = []

    async def bot(port, name, waves):
        sock = await join(port, name)
        ends = 0
        while ends < waves:
            msg = await recv(sock)
            if msg["type"] == "tick":
                await sock.send(json.dumps({"type": "move", "ax": 0, "ay": 0, "tick": msg["tick"]}))
            elif msg["type"] == "round_end":
                ends += 1
        await sock.close()

    async def main():
        server = ArenaServer({"cavern": load_map("cavern")}, rules, 0.05, seed=1, say=lines.append)
        srv, port = await start(server)
        names = ["pia", "gus", "rnd", "gina", "greg", "ace"]
        await asyncio.gather(*(bot(port, n, 6) for n in names))
        srv.close()
        return names

    names = asyncio.run(main())
    waves = [line.split(": ", 1)[1] for line in lines if line.startswith("wave ")][:6]
    assert len(waves) == 6
    groups = [[frozenset(g.split(", ")) for g in w.split(" | ")] for w in waves]
    met = set()
    for wave in groups:
        assert sorted(len(g) for g in wave) == [3, 3]
        for g in wave:
            met |= {frozenset((a, b)) for a in g for b in g if a != b}
    assert met == {frozenset((a, b)) for a in names for b in names if a != b}
    for before, after in zip(groups, groups[1:]):
        assert not set(before) & set(after)


def test_group_sizes():
    assert group_sizes(0) == []
    assert group_sizes(1) == [1]
    assert group_sizes(5) == [3, 2]
    assert group_sizes(6) == [3, 3]
    assert group_sizes(7) == [4, 3]
    assert group_sizes(12) == [4, 4, 4]
    assert group_sizes(13) == [4, 4, 4]
    server = make_server()
    for n in (1, 5, 6, 7, 12, 13):
        groups = server.deal([f"b{i}" for i in range(n)])
        assert [len(g) for g in groups] == group_sizes(n)
        assert len({name for g in groups for name in g}) == min(n, 12)


def test_a_bot_joining_mid_wave_plays_from_the_next_wave():
    async def main():
        server = make_server(fill_seconds=0.1)
        srv, port = await start(server)
        early = await join(port, "early")
        first = asyncio.ensure_future(play_round(early))
        while not any(a.playing > 2 for a in server.arenas):
            await asyncio.sleep(0.01)
        late = await join(port, "late")
        welcome, queued = await recv(late), await recv(late)
        await first
        joined_wave = server.wave
        second = await asyncio.gather(play_round(early), play_round(late))
        for s in (early, late):
            await s.close()
        srv.close()
        return welcome, queued, joined_wave, second

    welcome, queued, joined_wave, (early, late) = asyncio.run(main())
    assert welcome["type"] == "welcome"
    # round 1 has ~17 ticks of 50 ms left, then the 0.1 s pause: it starts within 2 s
    assert {k: v for k, v in queued.items() if k != "starts_in"} == {
        "type": "queued", "position": 1, "waiting": 1}
    assert 1 <= queued["starts_in"] <= 2
    assert joined_wave == 1
    assert late["start"]["round"] == 2 and sorted(late["start"]["players"]) == ["early", "late"]
    assert early["start"]["round"] == 2


def test_silent_and_vanishing_bots_coast_and_the_round_ends_on_time():
    async def main():
        server = make_server(fill_seconds=0.2)
        srv, port = await start(server)
        socks = [await join(port, n) for n in ("fast1", "fast2", "silent", "quitter")]
        tasks = [play_round(socks[0]), play_round(socks[1]),
                 play_round(socks[2], answer=lambda tick: None),
                 play_round(socks[3], quit_after=5)]
        seen = await asyncio.gather(*tasks)
        # the quitter's name is free again once it is gone
        again = await join(port, "quitter")
        welcome = await recv(again)
        await again.close()
        for s in socks:
            await s.close()
        srv.close()
        return seen, welcome, server.leaderboard.table()

    seen, welcome, table = asyncio.run(main())
    assert welcome["type"] == "welcome"
    fast = seen[0]
    duration = fast["end_at"] - fast["start_at"]
    expected = (RULES.round_ticks + 1) * RULES.tick_ms / 1000
    assert expected - 0.02 <= duration <= expected + 0.25
    assert seen[2]["end"] is not None and seen[2]["ticks"] == RULES.round_ticks
    late = {r["name"]: r["late_pct"] for r in table}
    assert late["silent"] == 100.0
    assert late["quitter"] >= 70.0
    assert late["fast1"] <= 10.0 and late["fast2"] <= 10.0
    results = {r["id"]: r for r in fast["end"]["results"]}
    assert set(results) == {"fast1", "fast2", "silent", "quitter"}
    assert set(results["fast1"]) == {"id", "money", "deliveries", "crashes"}


def test_wrong_tick_move_coasts_and_replay_opens_in_viewer(tmp_path):
    async def main():
        server = make_server(fill_seconds=0, replays=tmp_path / "replays")
        srv, port = await start(server)
        good, wrong = await join(port, "good"), None
        seen_good = asyncio.create_task(
            play_round(good, answer=lambda t: {"ax": 0, "ay": -1, "tick": t}))
        # joins mid-wave, so it plays the next wave, answering with the previous tick's number
        wrong = await join(port, "wrong")
        seen_wrong = await play_round(wrong, answer=lambda t: {"ax": 1, "ay": -1, "tick": t - 1})
        await seen_good
        await good.close()
        await wrong.close()
        srv.close()
        return seen_wrong

    seen = asyncio.run(main())
    path = tmp_path / "replays" / f"round-{seen['start']['round']:05d}.jsonl"
    rnd = load_replay(str(path))[seen["start"]["round"]]
    moves = [rnd["ticks"][t]["moves"]["wrong"] for t in sorted(rnd["ticks"])]
    assert len(moves) == RULES.round_ticks
    assert all(m == {"ax": 0, "ay": 0, "coast": True} for m in moves)
    # with no fill time the first bot's wave starts as it joins, and it flies alone
    good = load_replay(str(tmp_path / "replays" / "round-00001.jsonl"))
    assert next(iter(good.values()))["start"]["players"] == ["good"]
    good_moves = [t["moves"]["good"] for t in next(iter(good.values()))["ticks"].values()]
    assert sum(not m["coast"] for m in good_moves) >= RULES.round_ticks - 2
    assert "<html" in build_html(str(path)).lower()


def test_join_errors_close_the_socket():
    async def main():
        server = make_server(fill_seconds=60)
        srv, port = await start(server)
        alice = await join(port, "alice")
        assert (await recv(alice))["type"] == "welcome"
        out = {}
        for key, name, protocol in [("name_taken", "alice", 1), ("bad_name", "no spaces", 1),
                                    ("bad_name", "x" * 13, 1), ("bad_protocol", "bob", 2)]:
            sock = await join(port, name, protocol)
            msg = await recv(sock)
            assert await recv(sock) is None
            out.setdefault(key, []).append(msg)
        await alice.close()
        srv.close()
        return out

    out = asyncio.run(main())
    for code, msgs in out.items():
        for msg in msgs:
            assert msg["type"] == "error" and msg["code"] == code and msg["message"]


def test_log_rebuilds_the_leaderboard_on_restart(tmp_path):
    log = tmp_path / "results.jsonl"

    async def main():
        server = make_server(fill_seconds=0.1, log=log)
        srv, port = await start(server)
        socks = [await join(port, n) for n in ("a", "b", "c")]
        await asyncio.gather(play_round(socks[0]), play_round(socks[1]),
                             play_round(socks[2], answer=lambda t: None))
        for s in socks:
            await s.close()
        srv.close()
        return json.loads(server.http("/leaderboard.json")[2])

    before = asyncio.run(main())
    lines = log.read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["type"] == "round_end"
    restarted = make_server(log=log)
    after = json.loads(restarted.http("/leaderboard.json")[2])
    assert after["leaderboard"] == before["leaderboard"]
    assert after["rounds"] == before["rounds"] == 1
    assert after["wave"] == before["wave"] == 1
    assert {r["name"] for r in after["leaderboard"]} == {"a", "b", "c"}
    row = next(r for r in after["leaderboard"] if r["name"] == "c")
    assert set(row) == {"name", "rounds", "total_money", "avg_money", "deliveries", "crashes",
                        "late_pct", "human", "house"}
    assert row["rounds"] == 1 and row["late_pct"] == 100.0
    status, ctype, body = restarted.http("/")
    assert status == 200 and b"leaderboard.json" in body


def test_leaderboard_totals_and_order():
    board = Leaderboard()
    board.add({"results": [{"id": "a", "money": 100, "deliveries": 2, "crashes": 1,
                            "ticks": 10, "late": 1},
                           {"id": "b", "money": 300, "deliveries": 4, "crashes": 0,
                            "ticks": 10, "late": 0}]})
    board.add({"results": [{"id": "a", "money": 50, "deliveries": 1, "crashes": 0,
                            "ticks": 10, "late": 3}]})
    assert board.table() == [
        {"name": "b", "rounds": 1, "total_money": 300, "avg_money": 300.0, "deliveries": 4,
         "crashes": 0, "late_pct": 0.0, "human": False, "house": False},
        {"name": "a", "rounds": 2, "total_money": 150, "avg_money": 75.0, "deliveries": 3,
         "crashes": 1, "late_pct": 20.0, "human": False, "house": False},
    ]


def test_cli_serve_prints_lan_urls():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen([sys.executable, "-m", "spacetaxi", "serve", "--port", str(port),
                             "--fill-seconds", "2", "--house", "ace", "--house", "gus",
                             "--benchmark", "greedy=1", "--benchmark", "pilot=1"],
                            stdout=subprocess.PIPE, text=True, env=env)
    try:
        lines = []
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            lines.append(line)
            if "Ctrl-C" in line:
                break
        text = "".join(lines)
        assert f"ws://" in text and f":{port}/bot" in text and f":{port}/" in text
        assert f":{port}/card" in text and f":{port}/mission" in text
        assert "house bots (never score): ace, gus" in text
    finally:
        proc.terminate()
        proc.wait(5)


def test_cli_serve_bad_arguments():
    from spacetaxi.cli import main
    assert main(["serve", "--map", "nope"]) == 2
    assert main(["serve", "--rule", "tick_ms=0"]) == 2
    assert main(["serve", "--fill-seconds", "-1"]) == 2


def test_spectator_gets_every_arena_with_moves_and_slots():
    async def main():
        server = make_server(fill_seconds=0.1)
        srv, port = await start(server)
        spec = await ws.connect(f"ws://127.0.0.1:{port}/spectate")
        first = await recv(spec)
        socks = [await join(port, f"bot{i}") for i in range(5)]
        say = lambda tick: {"ax": 0, "ay": -1, "tick": tick, "debug": {"say": "up", "path": [[1, 1]]}}
        rounds = asyncio.gather(*(play_round(s, say) for s in socks))
        seen = []
        while sum(m["type"] == "round_end" for m in seen) < 2:
            seen.append(await recv(spec))
        late = await ws.connect(f"ws://127.0.0.1:{port}/spectate")
        catch_up = [await recv(late) for _ in range(1)]
        await rounds
        for s in socks + [spec, late]:
            await s.close()
        srv.close()
        return first, seen, catch_up

    first, seen, catch_up = asyncio.run(main())
    assert first == {"type": "spectate", "arenas": 3}
    starts = [m for m in seen if m["type"] == "round_start"]
    slots = sorted((m["arena"], m["round"], len(m["players"])) for m in starts)
    # wave 1: rounds 1 and 2 side by side, 5 bots dealt 3 and 2; wave 2 reuses the slots
    assert [x for x in slots if x[1] in (1, 2)] == [(1, 1, 3), (2, 2, 2)]
    assert all((arena, rnd) in ((1, 3), (2, 4)) for arena, rnd, _ in slots if rnd > 2)
    starts = [m for m in starts if m["round"] in (1, 2)]
    ticks = [m for m in seen if m["type"] == "tick" and m["round"] in (1, 2)]
    assert {(m["arena"], m["round"]) for m in ticks} == {(1, 1), (2, 2)}
    assert all(set(m["moves"]) == set(s["players"]) for m in ticks
               for s in starts if s["round"] == m["round"])
    debug = [mv.get("debug") for m in ticks for mv in m["moves"].values() if not mv["coast"]]
    assert debug and all(d == {"say": "up", "path": [[1, 1]]} for d in debug)
    for rnd in (1, 2):
        nums = [m["tick"] for m in ticks if m["round"] == rnd]
        assert nums == list(range(1, RULES.round_ticks + 1))
    assert catch_up[0]["type"] == "spectate"


def test_spectator_joining_mid_round_gets_its_start_and_latest_tick():
    async def main():
        server = make_server(fill_seconds=0)
        srv, port = await start(server)
        sock = await join(port, "solo")
        rnd = asyncio.ensure_future(play_round(sock))
        while not any(a.playing > 3 for a in server.arenas):
            await asyncio.sleep(0.01)
        spec = await ws.connect(f"ws://127.0.0.1:{port}/spectate")
        got = [await recv(spec) for _ in range(4)]
        await rnd
        await sock.close()
        await spec.close()
        srv.close()
        return got

    got = asyncio.run(main())
    assert [m["type"] for m in got] == ["spectate", "round_start", "tick", "tick"]
    assert got[1]["arena"] == 1 and got[1]["players"] == ["solo"]
    assert got[2]["tick"] >= 3 and got[3]["tick"] == got[2]["tick"] + 1
    assert "moves" in got[2]


def test_watch_page_is_the_live_viewer():
    status, kind, body = make_server().http("/watch")
    html = body.decode("utf-8")
    assert status == 200 and kind.startswith("text/html")
    assert 'data-mode="live"' in html and "/spectate" in html and "/*" + "MODE*/" not in html
    assert not re.search(r"https?://", html)
    data = json.loads(re.search(r'<script id="replay" type="application/json">(.*?)</script>',
                                html, re.S).group(1))
    assert data[0]["round"] == 0 and data[0]["ticks"] == [] and data[0]["map"]


def test_a_human_client_is_marked_on_the_leaderboard_in_the_log_and_to_spectators(tmp_path):
    log = tmp_path / "results.jsonl"

    async def main():
        server = make_server(fill_seconds=0.1, log=log)
        srv, port = await start(server)
        spec = await ws.connect(f"ws://127.0.0.1:{port}/spectate")
        await recv(spec)
        human = await ws.connect(f"ws://127.0.0.1:{port}/bot")
        await human.send(json.dumps({"type": "hello", "name": "me", "protocol": 1,
                                     "client": "human"}))
        bot = await join(port, "bot")
        seen = await asyncio.gather(play_round(human), play_round(bot))
        start_msg = None
        while start_msg is None:
            msg = await recv(spec)
            if msg["type"] == "round_start":
                start_msg = msg
        for s in (human, bot, spec):
            await s.close()
        srv.close()
        return seen, start_msg, json.loads(server.http("/leaderboard.json")[2])

    seen, start_msg, board = asyncio.run(main())
    # the human plays as any bot: same messages, no humans field on its round_start
    assert seen[0]["start"]["players"] == seen[1]["start"]["players"]
    assert "humans" not in seen[0]["start"]
    assert start_msg["humans"] == ["me"]
    rows = {r["name"]: r for r in board["leaderboard"]}
    assert rows["me"]["human"] is True and rows["bot"]["human"] is False
    [record] = [json.loads(line) for line in log.read_text().splitlines()]
    flags = {r["id"]: r.get("human") for r in record["results"]}
    assert flags == {"me": True, "bot": None}
    restarted = make_server(log=log)
    rows = {r["name"]: r for r in json.loads(restarted.http("/leaderboard.json")[2])["leaderboard"]}
    assert rows["me"]["human"] is True and rows["bot"]["human"] is False


def test_fly_page_is_the_viewer_in_fly_mode_without_external_requests():
    status, kind, body = make_server().http("/fly")
    html = body.decode("utf-8")
    assert status == 200 and kind.startswith("text/html")
    assert 'data-mode="fly"' in html and "/bot" in html and '"human"' in html
    assert 'id="join"' in html and 'id="leave"' in html and 'id="touch"' in html
    assert not re.search(r"https?://", html)
    assert "url(data:font/ttf;base64," in html


# --- names ignore case


async def refused(port: int, name: str) -> dict:
    sock = await join(port, name)
    msg = await recv(sock)
    assert await recv(sock) is None
    return msg


def test_names_ignore_case_and_the_first_spelling_keeps_the_name():
    async def main():
        server = make_server(fill_seconds=60)
        srv, port = await start(server)
        haiku = await join(port, "Haiku")
        assert (await recv(haiku))["type"] == "welcome"
        while_connected = await refused(port, "haiku")
        same_spelling = await refused(port, "Haiku")
        human = await ws.connect(f"ws://127.0.0.1:{port}/bot")  # /fly: the same rule
        await human.send(json.dumps({"type": "hello", "name": "HaiKu", "protocol": 1,
                                     "client": "human"}))
        human_msg = await recv(human)
        assert await recv(human) is None
        await haiku.close()
        await asyncio.sleep(0.1)
        assert "Haiku" not in server.bots
        after_leaving = await refused(port, "HAIKU")  # in the standings with its hello
        again = await join(port, "Haiku")
        welcome = await recv(again)
        # two other names from the same client address both connect
        a, b = await join(port, "a1"), await join(port, "b1")
        others = [(await recv(a))["type"], (await recv(b))["type"]]
        for s in (again, a, b):
            await s.close()
        srv.close()
        return server, while_connected, same_spelling, human_msg, after_leaving, welcome, others

    (server, while_connected, same_spelling, human_msg, after_leaving, welcome,
     others) = asyncio.run(main())
    assert human_msg["code"] == "name_taken" and "connect as 'Haiku'" in human_msg["message"]
    assert while_connected["code"] == "name_taken"
    assert while_connected["message"] == "name 'haiku' is taken as 'Haiku'; connect as 'Haiku'"
    assert same_spelling["code"] == "name_taken" and "already connected" in same_spelling["message"]
    assert after_leaving["code"] == "name_taken" and "connect as 'Haiku'" in after_leaving["message"]
    assert welcome["type"] == "welcome" and welcome["id"] == "Haiku"
    assert others == ["welcome", "welcome"]
    assert set(server.standings.awards) == {"Haiku", "a1", "b1"}
    assert server.standings.awards["Haiku"]["hello"] == {"place": 1, "points": 25}


def test_names_in_the_rebuilt_leaderboard_and_standings_ignore_case(tmp_path):
    log = tmp_path / "results.jsonl"
    log.write_text(json.dumps({"type": "round_end", "round": 1, "wave": 1, "results": [
        {"id": "Gus", "money": 10, "deliveries": 0, "crashes": 0, "ticks": 20, "late": 0}]})
        + "\n")
    standings = tmp_path / "standings.json"
    standings.write_text(json.dumps({"awards": {"Anna": {"hello": {"place": 1, "points": 25}}}}))

    async def main():
        server = make_server(fill_seconds=60, log=log, standings=standings)
        srv, port = await start(server)
        out = [await refused(port, "gus"), await refused(port, "ANNA")]
        srv.close()
        return out

    gus, anna = asyncio.run(main())
    assert gus["code"] == anna["code"] == "name_taken"
    assert "connect as 'Gus'" in gus["message"] and "connect as 'Anna'" in anna["message"]


def test_house_names_ignore_case():
    async def main():
        server = make_server(fill_seconds=60, house=["Ace"])
        srv, port = await start(server)
        ace = await join(port, "ace")
        assert (await recv(ace))["type"] == "welcome"
        await asyncio.sleep(0.05)
        await ace.close()
        srv.close()
        return server

    server = asyncio.run(main())
    assert "ace" not in server.standings.awards
    assert Leaderboard(["ACE"]).house == {"ace"}
