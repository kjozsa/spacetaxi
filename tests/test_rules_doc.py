import re
from pathlib import Path

RULES = Path(__file__).resolve().parent.parent / "docs" / "rules.md"

DEFAULTS = {
    "tick_ms": 200,
    "deadline_ms": 150,
    "round_ticks": 375,
    "gravity": 1,
    "gravity_every": 2,
    "max_speed": 3,
    "safe_landing_vy": 1,
    "crash_penalty_ticks": 15,
    "crash_cost": 25,
    "passenger_spawn_every": 20,
    "passenger_expires": 150,
    "fare_base": 50,
    "fare_per_cell": 2,
}


def test_constants_table_has_all_defaults():
    rows = re.findall(r"^\|\s*`(\w+)`\s*\|\s*`(-?\d+)`\s*\|", RULES.read_text(), re.M)
    assert {k: int(v) for k, v in rows} == DEFAULTS


def test_message_types_documented():
    text = RULES.read_text()
    for t in ("welcome", "round_start", "tick", "move", "round_end"):
        assert f'"type": "{t}"' in text
