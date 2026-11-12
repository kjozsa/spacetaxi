import subprocess
import sys
from pathlib import Path

import pytest

from spacetaxi.cli import main
from spacetaxi.maps import Map, MapError, Pad, load_map, parse_map

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "maps"

RULES_EXAMPLE = """\
####################
#..................#
#..S...........S...#
#..................#
#....###......###..#
#..................#
#..................#
#.AAAA......BBBB...#
#.......####.......#
#..CCC.........DDD.#
####################
"""


def test_rules_example_matches_round_start():
    m = parse_map(RULES_EXAMPLE)
    assert (m.width, m.height) == (20, 11)
    assert [p.to_dict() for p in m.pads] == [
        {"id": "A", "x": 2, "y": 7, "len": 4},
        {"id": "B", "x": 12, "y": 7, "len": 4},
        {"id": "C", "x": 3, "y": 9, "len": 3},
        {"id": "D", "x": 15, "y": 9, "len": 3},
    ]
    assert m.spawns == ((3, 2), (15, 2))


def test_classic_map_loads():
    m = load_map("classic")
    assert isinstance(m, Map)
    assert (m.width, m.height) == (40, 24)
    assert len(m.pads) >= 5
    assert all(3 <= p.len <= 5 for p in m.pads)
    assert len({p.y for p in m.pads}) > 1
    assert len(m.spawns) >= 4
    interior = [row[1:-1] for row in m.rows[1:-1]]
    assert any("#" in row for row in interior), "classic needs obstacles"


def test_load_by_path():
    m = load_map(FIXTURES / "good.txt")
    assert m.pads == (Pad("A", 2, 3, 2), Pad("B", 6, 3, 2))
    assert m.spawns == ((3, 1),)


@pytest.mark.parametrize(
    "fixture, line, words",
    [
        ("uneven_rows.txt", 3, "length"),
        ("border_gap.txt", 4, "border"),
        ("pad_no_air.txt", 4, "above"),
        ("no_spawn.txt", None, "spawn"),
    ],
)
def test_bad_maps_raise(fixture, line, words):
    with pytest.raises(MapError) as exc:
        load_map(FIXTURES / fixture)
    assert exc.value.line == line
    assert words in exc.value.message


@pytest.mark.parametrize(
    "text, words",
    [
        ("#####\n#S..#\n#..A#\n#####\n", "at least 2"),
        ("#######\n#S....#\n#A...A#\n#..B..#\n#######\n", "more than once"),
        ("#####\n#S?.#\n#####\n", "unknown cell"),
        ("", "empty"),
    ],
)
def test_other_rule_violations(text, words):
    with pytest.raises(MapError, match=words):
        parse_map(text)


def test_unknown_map_name():
    with pytest.raises(MapError, match="no such map"):
        load_map("does-not-exist")


def test_cli_map_classic(capsys):
    assert main(["map", "classic"]) == 0
    out = capsys.readouterr().out.splitlines()
    m = load_map("classic")
    assert out[: m.height] == list(m.rows)
    assert out[m.height : m.height + len(m.pads)] == [
        f"{p.id} {p.x} {p.y} {p.len}" for p in m.pads
    ]
    assert out[-1] == f"spawns: {len(m.spawns)}"


@pytest.mark.parametrize(
    "fixture, where",
    [
        ("uneven_rows.txt", "uneven_rows.txt:3:"),
        ("border_gap.txt", "border_gap.txt:4:"),
        ("pad_no_air.txt", "pad_no_air.txt:4:"),
        ("no_spawn.txt", "lines 1-5"),
    ],
)
def test_cli_map_bad_exits_2(fixture, where):
    result = subprocess.run(
        [sys.executable, "-m", "spacetaxi", "map", str(FIXTURES / fixture)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "error" in result.stderr
    assert where in result.stderr
