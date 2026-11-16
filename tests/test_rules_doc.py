import json
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
    for t in MESSAGE_TYPES:
        assert f'"type": "{t}"' in text


MESSAGE_TYPES = ("hello", "welcome", "queued", "round_start", "tick", "move", "round_end",
                 "error")


def json_examples(text: str) -> list:
    """Every JSON value in the doc's ```json blocks: a whole block, or one per line."""
    values = []
    for block in re.findall(r"^```json\n(.*?)^```", text, re.M | re.S):
        try:
            values.append(json.loads(block))
        except json.JSONDecodeError:
            for line in block.splitlines():
                try:
                    values.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return values


def test_every_message_type_has_a_parsing_json_example():
    text = RULES.read_text()
    examples = [v for v in json_examples(text) if isinstance(v, dict)]
    found = {v.get("type") for v in examples}
    for t in MESSAGE_TYPES:
        assert f"### `{t}`" in text
        assert t in found, f"no JSON example of {t!r} parses"


def test_message_examples_shapes():
    examples = {v["type"]: v for v in json_examples(RULES.read_text())
                if isinstance(v, dict) and "type" in v}
    assert examples["hello"] == {"type": "hello", "name": "alice", "protocol": 1}
    assert examples["queued"] == {"type": "queued", "position": 3, "waiting": 5, "starts_in": 8}
    assert set(examples["error"]) == {"type", "code", "message"}
    assert type(examples["move"]["tick"]) is int
    text = RULES.read_text()
    for code in ("name_taken", "bad_name", "bad_protocol", "bad_message"):
        assert f"`{code}`" in text
    assert "ws://HOST:PORT/bot" in text
