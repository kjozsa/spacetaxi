"""The venue WiFi test (`spacetaxi wifitest`): server, headless clients, report."""

import asyncio
import json

import pytest

from spacetaxi import ws
from spacetaxi.cli import main
from spacetaxi.sim import Rules
from spacetaxi.wifitest import (Client, WifiTest, client_names, lan_addresses, percentile,
                                record_ticks, run_clients)

FIELDS = {"name", "ticks", "answers", "late_pct", "missing_pct", "rtt_p50_ms", "rtt_p95_ms",
          "rtt_p99_ms", "rtt_max_ms", "longest_bad_run"}


@pytest.fixture(scope="module")
def ticks():
    return record_ticks()


def test_recorded_ticks_are_a_4_pilot_cavern_round(ticks):
    assert len(ticks) == Rules().round_ticks
    first = json.loads(ticks[0])
    assert first["type"] == "tick" and len(first["taxis"]) == 4
    assert "moves" not in first
    assert all(500 < len(t) < 2000 for t in ticks)


def test_percentile_and_names():
    assert percentile([], 50) == 0
    assert percentile([1, 2, 3, 4], 50) == 2
    assert percentile(list(range(1, 101)), 99) == 99
    assert percentile([5], 99) == 5
    assert client_names("lab", 12) == [f"lab-{i}" for i in range(1, 13)]
    assert all(len(n) <= 12 for n in client_names("abcdefghijkl", 12))
    assert client_names("solo", 1) == ["solo"]


def test_client_judges_late_missing_and_runs():
    ms = 1_000_000
    c = Client("a", "?", deadline_ns=75 * ms)
    c.on_sent(0, 0)
    c.on_move(0, 10 * ms)             # on time
    c.on_sent(1, 100 * ms)
    c.on_move(1, 190 * ms)            # late, before the next tick
    c.on_sent(2, 200 * ms)            # tick 2: no answer before tick 3
    c.on_sent(3, 300 * ms)
    c.on_move(2, 320 * ms)            # tick 2's answer after the next tick: counted, still missing
    c.on_move(3, 330 * ms)            # on time
    c.on_leave(400 * ms, 100 * ms)
    row = c.row()
    assert (row["ticks"], row["answers"]) == (4, 4)
    assert (row["late_pct"], row["missing_pct"]) == (50.0, 25.0)
    assert row["longest_bad_run"] == 2
    assert row["rtt_max_ms"] == 120.0


def test_server_and_five_clients(tmp_path, ticks):
    async def run():
        test = WifiTest(Rules(tick_ms=100), ticks)
        server = await test.start("127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        results = await run_clients(f"ws://127.0.0.1:{port}/bot", 5, 3, "lab")
        report = test.report()
        server.close()
        await server.wait_closed()
        return results, report

    results, report = asyncio.run(run())
    assert [r["error"] for r in results] == [None] * 5
    assert all(r["received"] >= 25 for r in results)
    assert len(report["clients"]) == 5
    assert {r["name"] for r in report["clients"]} == {f"lab-{i}" for i in range(1, 6)}
    for row in report["clients"]:
        assert FIELDS <= set(row)
        assert row["ticks"] >= 25
        assert row["answers"] == row["ticks"]
        assert row["missing_pct"] == 0.0
    assert FIELDS <= set(report["total"])
    assert report["total"]["ticks"] == sum(r["ticks"] for r in report["clients"])
    assert report["tick_ms"] == 100 and report["deadline_ms"] == 75
    path = tmp_path / "r.json"
    path.write_text(json.dumps(report))
    assert json.loads(path.read_text())["clients"][0]["name"].startswith("lab-")


def test_hello_errors(ticks):
    async def run():
        test = WifiTest(Rules(tick_ms=100), ticks)
        server = await test.start("127.0.0.1", 0)
        url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/bot"
        out = []
        for hello in ({"type": "hello", "name": "bad name!", "protocol": 1},
                      {"type": "hello", "name": "x", "protocol": 9},
                      {"type": "move"}):
            sock = await ws.connect(url)
            await sock.send(json.dumps(hello))
            out.append(json.loads(await sock.recv())["code"])
            assert await sock.recv() is None
        first = await ws.connect(url)
        await first.send(json.dumps({"type": "hello", "name": "x", "protocol": 1}))
        assert json.loads(await first.recv())["type"] == "welcome"
        second = await ws.connect(url)
        await second.send(json.dumps({"type": "hello", "name": "x", "protocol": 1}))
        out.append(json.loads(await second.recv())["code"])
        await first.close()
        server.close()
        await server.wait_closed()
        return out

    assert asyncio.run(run()) == ["bad_name", "bad_protocol", "bad_message", "name_taken"]


def test_pages_and_stats_json(ticks):
    test = WifiTest(Rules(), ticks)
    assert b"WebSocket" in test.http("/")[2]
    assert b"/stats.json" in test.http("/stats")[2]
    stats = json.loads(test.http("/stats.json")[2])
    assert stats["clients"] == [] and FIELDS <= set(stats["total"])
    assert test.http("/nope") is None


def test_lan_addresses_are_not_loopback():
    assert all(not a.startswith("127.") for a in lan_addresses())


def test_cli_rejects_bad_rule_and_bad_name(capsys):
    assert main(["wifitest", "--rule", "tick_ms=x"]) == 2
    assert main(["wifitest", "--connect", "ws://127.0.0.1:1/bot", "--name", "bad name"]) == 2


def test_cli_connect_reports_unreachable_server(capsys):
    assert main(["wifitest", "--connect", "ws://127.0.0.1:1/bot", "--clients", "2",
                 "--seconds", "1"]) == 1
    out = capsys.readouterr().out
    assert "lab-1" in out and "cannot connect" in out
