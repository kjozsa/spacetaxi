"""Command-line entry point. Subcommands: map, run (show is added by a later task)."""

import argparse
import json
import sys

from spacetaxi import __version__
from spacetaxi.maps import MapError, load_map
from spacetaxi.runner import BotError, format_table, run


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

    run_cmd = commands.add_parser("run", help="play rounds headless with Python bots")
    run_cmd.add_argument("--map", default="classic", help="bundled map name or map file path")
    run_cmd.add_argument(
        "--bots", required=True,
        help="comma-separated bots: bundled names (idle, random) or .py file paths",
    )
    run_cmd.add_argument("--rounds", type=int, default=10, help="rounds to play (default 10)")
    run_cmd.add_argument("--seed", type=int, default=0, help="seed for every round (default 0)")
    run_cmd.add_argument("--json", action="store_true", help="print results as JSON")
    run_cmd.add_argument("--replay", metavar="FILE", help="write every message as JSON lines")
    run_cmd.set_defaults(func=cmd_run)
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


def cmd_run(args: argparse.Namespace) -> int:
    bots = [b.strip() for b in args.bots.split(",") if b.strip()]
    if not bots:
        print("spacetaxi run: error: no bots given", file=sys.stderr)
        return 2
    if args.rounds < 1:
        print("spacetaxi run: error: --rounds must be at least 1", file=sys.stderr)
        return 2
    try:
        game_map = load_map(args.map)
        if args.replay:
            with open(args.replay, "w", encoding="utf-8") as replay:
                results = run(game_map, bots, args.rounds, args.seed, replay=replay)
        else:
            results = run(game_map, bots, args.rounds, args.seed)
    except (MapError, BotError, OSError) as exc:
        print(f"spacetaxi run: error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(format_table(results))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    return args.func(args)
