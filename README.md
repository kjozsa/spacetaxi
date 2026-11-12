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

`spacetaxi` is the command-line entry point. Its subcommands (`map`, `run`, `show`, `new-bot`)
are added as the game grows; `spacetaxi <command> --help` describes each one.

## Write a bot

```sh
uv run spacetaxi new-bot mybot.py
uv run spacetaxi run --bots ./mybot.py,lander --rounds 100 --json
```

`new-bot` writes a copy of the `greedy` house bot with a header that explains the bot interface,
how to evaluate it and how to watch a replay.
