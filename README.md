# spacetaxi

A multiplayer bot-arena game for a Claude Code workshop. Participants write bots that fly taxis
around a grid, pick up passengers on landing pads and earn money by delivering them. The
prototype is a headless simulation with a runner, a text view of the board and house bots.

The game rules, constants and message formats are in [docs/rules.md](docs/rules.md).

## Install

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
```

## Run the tests

```sh
uv run pytest
```

## CLI

```sh
uv run spacetaxi --help
```

`spacetaxi` is the command-line entry point. Its subcommands (`map`, `run`, `show`, `view`,
`new-bot`)
are added as the game grows; `spacetaxi <command> --help` describes each one.

## Watch a replay in the browser

```sh
uv run spacetaxi run --map canyon --bots lander,greedy,lander,random --rounds 2 --replay r.jsonl
uv run spacetaxi view r.jsonl --out replay.html   # one self-contained file, works via file://
uv run spacetaxi view r.jsonl --port 8000         # or serve it at http://localhost:8000/
```

Space plays and pauses, `1`-`4` set 0.5x/1x/2x/4x, `C` turns the CRT scanlines off and on; the bar
has a scrub bar and a round selector.
The page makes no external requests. Its browser tests (`tests/e2e/`) need Chromium once:
`uv run playwright install chromium`; they skip without it.

## Write a bot

```sh
uv run spacetaxi new-bot mybot.py
uv run spacetaxi run --bots ./mybot.py,lander --rounds 100 --json
```

`new-bot` writes a copy of the `greedy` house bot with a header that explains the bot interface,
how to evaluate it and how to watch a replay.
