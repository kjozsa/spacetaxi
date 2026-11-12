import json
import re
import socket
import threading
import urllib.request
from pathlib import Path

from spacetaxi.cli import main
from spacetaxi.viewer import build_html, make_server, replay_data

FIXTURES = Path(__file__).parent / "fixtures"
STACKED = FIXTURES / "show" / "stacked.jsonl"


def test_view_out_writes_self_contained_page(tmp_path, capsys):
    out = tmp_path / "v.html"
    assert main(["view", str(STACKED), "--out", str(out)]) == 0
    html = out.read_text(encoding="utf-8")
    assert not re.search(r"https?://", html)
    assert "data:font/ttf;base64," in html
    assert "/*FONT*/" not in html and "/*SCRIPT*/" not in html and "/*DATA*/" not in html
    assert f"wrote {out}" in capsys.readouterr().out


def test_view_bad_replay(tmp_path, capsys):
    assert main(["view", str(tmp_path / "missing.jsonl"), "--out", str(tmp_path / "v.html")]) == 2
    assert "spacetaxi view: error" in capsys.readouterr().err


def test_replay_data_compacts_ticks():
    [rnd] = replay_data(str(STACKED))
    assert rnd["players"] == ["alice", "bob", "carol"]
    assert rnd["rules"]["tick_ms"] == 200
    tick = rnd["ticks"][0]
    assert tick["t"] == 1
    # [x, y, vx, state, carrying, money]
    assert tick["x"][0] == [4, 2, 0, 1, 0, 10]
    assert tick["x"][2][4] == 1


def test_replay_data_has_each_players_move(tmp_path):
    assert all(m == [0, 0] for m in replay_data(str(STACKED))[0]["ticks"][0]["m"])  # no moves
    replay = tmp_path / "r.jsonl"
    assert main(["run", "--map", "canyon", "--bots", "lander,random", "--rounds", "1",
                 "--seed", "1", "--replay", str(replay)]) == 0
    lines = [json.loads(line) for line in replay.read_text().splitlines()]
    raw = [m for m in lines if m["type"] == "tick"]
    [rnd] = replay_data(str(replay))
    for msg, tick in zip(raw, rnd["ticks"], strict=True):
        assert tick["m"] == [[msg["moves"][p]["ax"], msg["moves"][p]["ay"]] for p in rnd["players"]]


def test_embedded_data_cannot_close_the_script_tag(tmp_path):
    replay = tmp_path / "r.jsonl"
    replay.write_text(STACKED.read_text().replace('"alice"', '"</script><b>"'))
    html = build_html(str(replay))
    data = html.split('<script id="replay" type="application/json">', 1)[1].split("</script>", 1)[0]
    assert json.loads(data)[0]["players"][0] == "</script><b>"


def test_server_serves_the_page():
    html = build_html(str(STACKED))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = make_server(html, port, host="127.0.0.1")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as resp:
            assert resp.headers["Content-Type"].startswith("text/html")
            assert resp.read().decode("utf-8") == html
    finally:
        server.shutdown()
        server.server_close()


def test_replay_data_has_each_players_debug(tmp_path):
    assert replay_data(str(STACKED))[0]["ticks"][0]["d"] == [None, None, None]  # no moves
    bots = FIXTURES / "bots"
    replay = tmp_path / "r.jsonl"
    assert main(["run", "--bots", f"{bots / 'planner.py'},{bots / 'talker.py'},idle",
                 "--rounds", "1", "--rule", "round_ticks=3", "--replay", str(replay)]) == 0
    [rnd] = replay_data(str(replay))
    for tick in rnd["ticks"]:
        assert tick["d"] == [{"say": "hi", "path": [[5, 5], [6, 5]]}, "just text", None]
