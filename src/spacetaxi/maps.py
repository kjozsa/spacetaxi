"""Map format and loader, as described in the Map section of docs/rules.md."""

from dataclasses import dataclass
from pathlib import Path

MAPS_DIR = Path(__file__).resolve().parent / "maps"

WALL = "#"
AIR = "."
SPAWN = "S"
PAD_IDS = "ABCDEFGHIJKLMNOPQRTUVWXYZ"  # A–Z except S, which is a spawn cell
CELLS = WALL + AIR + SPAWN + PAD_IDS


class MapError(ValueError):
    """A map that breaks the rules. `line` is 1-based, or None when no single line is at fault."""

    def __init__(self, message: str, line: int | None = None, source: str = "<map>"):
        self.message = message
        self.line = line
        self.source = source
        where = f"{source}:{line}" if line is not None else source
        super().__init__(f"{where}: {message}")


@dataclass(frozen=True)
class Pad:
    id: str
    x: int
    y: int
    len: int

    def to_dict(self) -> dict:
        return {"id": self.id, "x": self.x, "y": self.y, "len": self.len}


@dataclass(frozen=True)
class Map:
    rows: tuple[str, ...]
    pads: tuple[Pad, ...]
    spawns: tuple[tuple[int, int], ...]

    @property
    def width(self) -> int:
        return len(self.rows[0])

    @property
    def height(self) -> int:
        return len(self.rows)


def parse_map(text: str, source: str = "<map>") -> Map:
    """Parse and validate map text. Raises MapError naming the problem and its line."""
    rows = [row.rstrip("\r") for row in text.split("\n")]
    while rows and rows[-1] == "":
        rows.pop()
    if not rows:
        raise MapError("map is empty", None, source)

    width = len(rows[0])
    height = len(rows)
    for y, row in enumerate(rows):
        if len(row) != width:
            raise MapError(
                f"row has length {len(row)}, expected {width} like the first row", y + 1, source
            )
        for x, ch in enumerate(row):
            if ch not in CELLS:
                raise MapError(f"unknown cell {ch!r} at column {x}", y + 1, source)
    if width < 3 or height < 3:
        raise MapError(f"map is {width}x{height}, needs at least 3x3", None, source)

    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            on_border = y in (0, height - 1) or x in (0, width - 1)
            if on_border and ch != WALL:
                raise MapError(
                    f"gap in the border wall at column {x}: {ch!r} instead of '#'", y + 1, source
                )

    pads: dict[str, Pad] = {}
    spawns: list[tuple[int, int]] = []
    for y, row in enumerate(rows):
        x = 0
        while x < width:
            ch = row[x]
            if ch == SPAWN:
                spawns.append((x, y))
            if ch in PAD_IDS:
                start = x
                while x + 1 < width and row[x + 1] == ch:
                    x += 1
                if ch in pads:
                    raise MapError(
                        f"pad {ch} appears more than once (first at line {pads[ch].y + 1})",
                        y + 1,
                        source,
                    )
                pads[ch] = Pad(ch, start, y, x - start + 1)
                for px in range(start, x + 1):
                    above = rows[y - 1][px]
                    if above not in (AIR, SPAWN):
                        raise MapError(
                            f"pad {ch} tile at column {px} has {above!r} above it, needs air",
                            y + 1,
                            source,
                        )
            x += 1

    if not spawns:
        raise MapError(f"no spawn cell 'S' on any of lines 1-{height}", None, source)
    if len(pads) < 2:
        raise MapError(
            f"map has {len(pads)} pad(s) on lines 1-{height}, needs at least 2", None, source
        )

    return Map(
        rows=tuple(rows),
        pads=tuple(sorted(pads.values(), key=lambda p: p.id)),
        spawns=tuple(spawns),
    )


def load_map(name_or_path: str | Path) -> Map:
    """Load a map from a file path, or a bundled map by name (e.g. `classic`)."""
    path = Path(name_or_path)
    if not path.is_file():
        bundled = MAPS_DIR / f"{name_or_path}.txt"
        if isinstance(name_or_path, str) and "/" not in name_or_path and bundled.is_file():
            path = bundled
        else:
            raise MapError(f"no such map file or bundled map: {name_or_path}", None, str(name_or_path))
    return parse_map(path.read_text(encoding="utf-8"), str(path))


def bundled_maps() -> list[str]:
    return sorted(p.stem for p in MAPS_DIR.glob("*.txt"))
