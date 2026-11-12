"""Command-line entry point. Subcommands: map, run, show."""

import argparse
import json
import sys
import time

from spacetaxi import __version__
from spacetaxi.maps import MapError, load_map
from spacetaxi.runner import BotError, format_table, run
from spacetaxi.sim import Rules
from spacetaxi.show import ReplayError, frame, get_round, get_tick, load_replay, render


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
        help="comma-separated bots: bundled names (idle, random, lander) or .py file paths",
    )
    run_cmd.add_argument("--rounds", type=int, default=10, help="rounds to play (default 10)")
    run_cmd.add_argument("--seed", type=int, default=0, help="seed for every round (default 0)")
    run_cmd.add_argument("--json", action="store_true", help="print results as JSON")
    run_cmd.add_argument("--replay", metavar="FILE", help="write every message as JSON lines")
    run_cmd.add_argument(
        "--rule", action="append", default=[], metavar="KEY=VALUE",
        help="override one rule constant with an integer, e.g. max_speed=4 (repeatable)",
    )
    run_cmd.set_defaults(func=cmd_run)

    show_cmd = commands.add_parser("show", help="print one tick of a replay as text")
    show_cmd.add_argument("replay", help="a replay file written by run --replay")
    show_cmd.add_argument("--round", type=int, default=1, help="round to show (default 1)")
    show_cmd.add_argument("--tick", type=int, help="tick to show (required unless --watch)")
    show_cmd.add_argument("--watch", action="store_true",
                          help="animate the round from its first tick to its last")
    show_cmd.add_argument("--fps", type=float, default=10, help="frames per second (default 10)")
    show_cmd.set_defaults(func=cmd_show)
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


class RuleError(ValueError):
    """A `--rule` override with an unknown key or a non-integer value."""


def parse_rules(overrides: list[str]) -> Rules:
    """`Rules` with each `key=value` override applied; values must be integers."""
    valid = list(Rules().to_dict())
    values = {}
    for item in overrides:
        key, sep, value = item.partition("=")
        key = key.strip()
        if not sep or key not in valid:
            raise RuleError(f"unknown rule {key!r} in --rule {item!r}; "
                            f"valid keys: {', '.join(valid)}")
        try:
            values[key] = int(value.strip())
        except ValueError:
            raise RuleError(f"rule {key!r} needs an integer, got {value!r}; "
                            f"valid keys: {', '.join(valid)}") from None
    return Rules(**values)


def cmd_run(args: argparse.Namespace) -> int:
    bots = [b.strip() for b in args.bots.split(",") if b.strip()]
    if not bots:
        print("spacetaxi run: error: no bots given", file=sys.stderr)
        return 2
    if args.rounds < 1:
        print("spacetaxi run: error: --rounds must be at least 1", file=sys.stderr)
        return 2
    try:
        rules = parse_rules(args.rule)
    except RuleError as exc:
        print(f"spacetaxi run: error: {exc}", file=sys.stderr)
        return 2
    try:
        game_map = load_map(args.map)
        if args.replay:
            with open(args.replay, "w", encoding="utf-8") as replay:
                results = run(game_map, bots, args.rounds, args.seed, rules=rules,
                              replay=replay)
        else:
            results = run(game_map, bots, args.rounds, args.seed, rules=rules)
    except (MapError, BotError, OSError) as exc:
        print(f"spacetaxi run: error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(format_table(results))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    try:
        if not args.watch and args.tick is None:
            raise ReplayError("give --tick N or --watch")
        if args.fps <= 0:
            raise ReplayError("--fps must be positive")
        rnd = get_round(load_replay(args.replay), args.round)
        if not args.watch:
            print(render(rnd["start"], get_tick(rnd, args.tick)))
            return 0
        if not rnd["ticks"]:
            raise ReplayError(f"round {args.round} has no ticks")
    except ReplayError as exc:
        print(f"spacetaxi show: error: {exc}", file=sys.stderr)
        return 2
    delay = 1 / args.fps
    try:
        for tick_no in sorted(rnd["ticks"]):
            sys.stdout.write("\x1b[H\x1b[2J" + frame(rnd["start"], rnd["ticks"][tick_no]) + "\n")
            sys.stdout.flush()
            time.sleep(delay)
    except KeyboardInterrupt:
        print()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    return args.func(args)
