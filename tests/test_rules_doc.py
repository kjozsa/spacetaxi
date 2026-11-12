import re
from pathlib import Path

RULES = Path(__file__).resolve().parent.parent / "docs" / "rules.md"

DEFAULTS = {
    "tick_ms": 100,
    "deadline_ms": 75,
    "round_ticks": 750,
    "units_per_cell": 32,
    "gravity": 2,
    "gravity_every": 1,
    "main_thrust": 4,
    "side_thrust": 2,
    "max_speed": 24,
    "safe_landing_vy": 8,
    "safe_landing_vx": 3,
    "taxi_half_width": 15,
    "taxi_half_height": 12,
    "crash_penalty_ticks": 30,
    "crash_cost": 25,
    "passenger_spawn_every": 40,
    "passenger_expires": 300,
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
