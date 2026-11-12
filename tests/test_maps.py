import subprocess
import sys
from pathlib import Path

import pytest

from spacetaxi.bots.pilot import Pilot
from spacetaxi.cli import main
from spacetaxi.maps import Map, MapError, Pad, load_map, parse_map
from spacetaxi.sim import Rules

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


def _pocket_pads(m: Map) -> list[str]:
    """Pads with a wall over every tile within 4 rows above, so the only way in is sideways."""
    return [
        p.id
        for p in m.pads
        if all(
            any(m.rows[y][x] == "#" for y in range(max(0, p.y - 4), p.y))
            for x in range(p.x, p.x + p.len)
        )
    ]


def test_canyon_map_loads():
    m = load_map("canyon")
    assert (m.width, m.height) == (40, 24)
    assert 6 <= len(m.pads) <= 8
    assert all(2 <= p.len <= 3 for p in m.pads)
    assert len(m.spawns) >= 4
    assert len(_pocket_pads(m)) >= 3
    # a taxi landed on any tile can take off: the cell it rises into is air
    for p in m.pads:
        for x in range(p.x, p.x + p.len):
            assert m.rows[p.y - 2][x] in ".S", (p.id, x)


def test_cavern_map_loads():
    m = load_map("cavern")
    assert (m.width, m.height) == (40, 24)
    assert m.pads == (
        Pad("A", 5, 6, 4),
        Pad("B", 30, 6, 4),
        Pad("C", 10, 13, 4),
        Pad("D", 26, 13, 4),
        Pad("E", 2, 21, 4),
        Pad("F", 16, 21, 4),
        Pad("G", 35, 21, 4),
    )
    assert len(m.spawns) == 3


def test_cavern_cli_prints_pads_and_spawns(capsys):
    assert main(["map", "cavern"]) == 0
    out = capsys.readouterr().out
    assert "spawns: 3" in out
    for pad_id in "ABCDEFG":
        assert f"\n{pad_id} " in out


@pytest.mark.parametrize("name", ["canyon", "cavern"])
def test_every_pad_reachable_from_every_spawn_and_pad(name):
    m = load_map(name)
    pilot = Pilot(m.rows, [p.to_dict() for p in m.pads], Rules().to_dict())
    starts = list(m.spawns) + [(x, p.y - 1) for p in m.pads for x in range(p.x, p.x + p.len)]
    for cell in starts:
        for pad in m.pads:
            assert pilot.ticks(pad.id, cell) > 0, (cell, pad.id)
