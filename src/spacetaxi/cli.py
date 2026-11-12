"""Command-line entry point. Subcommands: map (run and show are added by later tasks)."""

import argparse
import sys

from spacetaxi import __version__
from spacetaxi.maps import MapError, load_map


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spacetaxi",
        description="spacetaxi: a bot-arena game where taxis fly on a grid and carry passengers.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="<command>")

    map_cmd = commands.add_parser("map", help="load a map, check it and print it")
    map_cmd.add_argument("map", help="a bundled map name (e.g. classic) or a map file path")
    map_cmd.set_defaults(func=cmd_map)
    return parser


def cmd_map(args: argparse.Namespace) -> int:
    try:
        m = load_map(args.map)
    except MapError as exc:
        print(f"spacetaxi map: error: {exc}", file=sys.stderr)
        return 2
    for row in m.rows:
        print(row)
    for pad in m.pads:
        print(f"{pad.id} {pad.x} {pad.y} {pad.len}")
    print(f"spawns: {len(m.spawns)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    return args.func(args)
