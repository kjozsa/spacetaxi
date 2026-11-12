"""Command-line entry point. Subcommands: map, run, show, view, play, new-bot."""

import argparse
import json
import sys
import time
from pathlib import Path

from spacetaxi import __version__
from spacetaxi.maps import MapError, load_map
from spacetaxi.play import build_html as build_play_html
from spacetaxi.runner import BOTS_DIR, BotError, format_table, run
from spacetaxi.sim import Rules
from spacetaxi.show import ReplayError, frame, get_round, get_tick, load_replay, render
from spacetaxi.viewer import build_html, make_server


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
        help="comma-separated bots: bundled names (idle, random, greedy, pilot) or .py file paths",
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

    view_cmd = commands.add_parser(
        "view", help="watch a replay in the browser (writes an HTML file or serves it)")
    view_cmd.add_argument("replay", help="a replay file written by run --replay")
    view_cmd.add_argument("--out", metavar="FILE",
                          help="write one self-contained HTML file instead of serving it")
    view_cmd.add_argument("--port", type=int, default=8000,
                          help="port to serve the page on (default 8000)")
    view_cmd.set_defaults(func=cmd_view)

    play_cmd = commands.add_parser(
        "play", help="fly a taxi by hand in the browser (writes an HTML file or serves it)")
    play_cmd.add_argument("--map", default="cavern", help="bundled map name or map file path")
    play_cmd.add_argument(
        "--rule", action="append", default=[], metavar="KEY=VALUE",
        help="override one rule constant with an integer, e.g. gravity=3 (repeatable)",
    )
    play_cmd.add_argument("--out", metavar="FILE",
                          help="write one self-contained HTML file instead of serving it")
    play_cmd.add_argument("--port", type=int, default=8000,
                          help="port to serve the page on (default 8000)")
    play_cmd.set_defaults(func=cmd_play)

    new_bot_cmd = commands.add_parser("new-bot", help="write a starter bot (a copy of greedy)")
    new_bot_cmd.add_argument("path", help="the .py file to create; never overwritten")
    new_bot_cmd.set_defaults(func=cmd_new_bot)
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


def cmd_view(args: argparse.Namespace) -> int:
    try:
        html = build_html(args.replay)
    except ReplayError as exc:
        print(f"spacetaxi view: error: {exc}", file=sys.stderr)
        return 2
    return write_or_serve("view", "the viewer", html, args.out, args.port)


def cmd_play(args: argparse.Namespace) -> int:
    try:
        html = build_play_html(load_map(args.map), parse_rules(args.rule))
    except (MapError, RuleError) as exc:
        print(f"spacetaxi play: error: {exc}", file=sys.stderr)
        return 2
    return write_or_serve("play", "the flight lab", html, args.out, args.port)


def write_or_serve(command: str, what: str, html: str, out: str | None, port: int) -> int:
    """Write `html` to `out`, or serve it on `port` until Ctrl-C."""
    if out:
        try:
            Path(out).write_text(html, encoding="utf-8")
        except OSError as exc:
            print(f"spacetaxi {command}: error: {exc}", file=sys.stderr)
            return 2
        print(f"wrote {out}")
        return 0
    try:
        server = make_server(html, port)
    except OSError as exc:
        print(f"spacetaxi {command}: error: cannot serve on port {port}: {exc}", file=sys.stderr)
        return 2
    print(f"serving {what} at http://localhost:{port}/ (Ctrl-C stops)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
    return 0


NEW_BOT_HEADER = """\
# A spacetaxi bot, started as a copy of the bundled `greedy` house bot. Make it better!
#
# The bot interface: a bot is a Python module with
#   move(state) -> {"ax": -1|0|1, "ay": -1|0|1}   called every tick, `state` is the `tick`
#                                                  message: taxis, waiting passengers, events
# and two optional hooks:
#   welcome(info)  once, with the `welcome` message: your taxi id and the rules
#   start(info)    every round, with the `round_start` message: map rows, pads, spawns, rules
# The shape of every message, the physics and the scoring are in docs/rules.md.
#
# Evaluate it against the house bots (100 rounds, results as JSON):
#   spacetaxi run --bots ./mybot.py,pilot --rounds 100 --json
# Add --map canyon for the harder map, --seed N for other rounds.
#
# Watch a round: record a replay, then play it back in the terminal (or show one tick):
#   spacetaxi run --bots ./mybot.py,pilot --rounds 1 --replay replay.jsonl
#   spacetaxi show replay.jsonl --watch
#   spacetaxi show replay.jsonl --tick 100
# The `debug` text a move returns is free text for you; the simulation ignores it.

"""


def cmd_new_bot(args: argparse.Namespace) -> int:
    path = Path(args.path)
    source = (BOTS_DIR / "greedy.py").read_text(encoding="utf-8")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "x", encoding="utf-8") as out:
            out.write(NEW_BOT_HEADER + source)
    except FileExistsError:
        print(f"spacetaxi new-bot: error: {path} already exists, not overwriting it",
              file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"spacetaxi new-bot: error: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    return args.func(args)
