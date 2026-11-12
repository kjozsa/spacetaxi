import json
import subprocess
import sys


def _run(*args):
    result = subprocess.run(
        [sys.executable, "-m", "spacetaxi", "run", "--map", "classic", "--json", *args],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_lander_beats_random_bots():
    rows = _run("--bots", "lander,random,random,random", "--rounds", "100", "--seed", "1")
    lander = rows[0]
    assert lander["name"].startswith("lander")
    assert lander["avg_deliveries"] >= 3
    assert sum(lander["crashes"].values()) / lander["rounds"] <= 0.5
    for other in rows[1:]:
        assert lander["avg_money"] > other["avg_money"]


def test_four_landers_no_errors_or_timeouts():
    rows = _run("--bots", "lander,lander,lander,lander", "--rounds", "20")
    assert len(rows) == 4
    for row in rows:
        assert row["errors"] == 0
        assert row["timeouts"] == 0
