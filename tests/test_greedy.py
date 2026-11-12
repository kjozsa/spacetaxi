import json

import pytest

from spacetaxi.cli import main
from spacetaxi.runner import BOTS_DIR


def run_json(capsys, *args) -> list[dict]:
    assert main(["run", "--json", *args]) == 0
    return json.loads(capsys.readouterr().out)


def strip_names(results: list[dict]) -> list[dict]:
    return [{k: v for k, v in r.items() if k != "name"} for r in results]


@pytest.mark.skip(reason="bots are rewritten for lander physics")
def test_greedy_delivers_and_earns_alone_on_classic(capsys):
    greedy = run_json(capsys, "--map", "classic", "--bots", "greedy,idle,idle,idle",
                      "--rounds", "100", "--seed", "1")[0]
    assert greedy["avg_deliveries"] >= 3
    assert greedy["avg_money"] > 0
    assert greedy["errors"] == 0 and greedy["timeouts"] == 0


def test_pilot_beats_every_greedy(capsys):
    results = run_json(capsys, "--map", "classic", "--bots", "pilot,greedy,greedy,greedy",
                       "--rounds", "100", "--seed", "1")
    pilot, greedies = results[0], results[1:]
    assert all(pilot["avg_money"] > g["avg_money"] for g in greedies)


def test_greedy_stays_small():
    assert len((BOTS_DIR / "greedy.py").read_text().splitlines()) <= 130


def test_new_bot_copy_plays_like_greedy(tmp_path, capsys):
    path = tmp_path / "x" / "mybot.py"
    assert main(["new-bot", str(path)]) == 0
    capsys.readouterr()
    text = path.read_text()
    assert text.endswith((BOTS_DIR / "greedy.py").read_text())
    for needle in ("docs/rules.md", "spacetaxi run --bots ./mybot.py,pilot --rounds 100 --json",
                   "--replay", "spacetaxi show"):
        assert needle in text

    common = ["--rounds", "5", "--seed", "1"]
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
