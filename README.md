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
`play`, `new-bot`, `wifitest`, `serve`)
are added as the game grows; `spacetaxi <command> --help` describes each one.

## Watch a replay in the browser

```sh
uv run spacetaxi run --map canyon --bots pilot,greedy,pilot,random --rounds 2 --replay r.jsonl
uv run spacetaxi view r.jsonl --out replay.html   # one self-contained file, works via file://
uv run spacetaxi view r.jsonl --port 8000         # or serve it at http://localhost:8000/
```

The page opens on an INSERT COIN splash: browsers allow sound only after a click, so the round
starts on the click. Space plays and pauses, `1`-`4` set 0.5x/1x/2x/4x, `C` turns the CRT scanlines
off and on, `M` or the bar's `SOUND ON`/`SOUND OFF` button mutes and unmutes; the bar also has
a scrub bar and a round selector. Every page load starts with sound on. All sounds (thrust hum,
crash, pickup, delivery, round jingles) are synthesised with Web Audio, no audio files.
The page makes no external requests. Its browser tests (`tests/e2e/`) need Chromium once:
`uv run playwright install chromium`; they skip without it.

## Fly a taxi by hand

```sh
uv run spacetaxi play --out play.html                       # the cavern map, one self-contained file
uv run spacetaxi play --map canyon --rule gravity=3 --port 8000   # or serve it
```

The flight lab: one taxi on the real map and rules, flown with the arrow keys (`R` restarts) or
the touch buttons on a phone, to feel what a bot is up against. The dials change the physics
live and list the `--rule` flags for what you set. Its physics is a port of the simulation;
`tests/e2e/test_play.py` checks it ticks exactly like `World`.

## Test the venue WiFi

```sh
uv run spacetaxi wifitest --rule tick_ms=100 --report wifi-100.json    # the server, Ctrl-C stops
uv run spacetaxi wifitest --connect ws://HOST:8000/bot --clients 12 --seconds 300 --name lab
```

The server sends real-size `tick` messages at the game's rhythm to every client on `/bot` and
times each answer; phones and laptops join on `/`, `/stats` is the dashboard for the projector.
The procedure and the go/no-go reading are in [docs/venue-wifi-test.md](docs/venue-wifi-test.md).

## Run the arena

```sh
uv run spacetaxi serve --map cavern --port 8000 --log results.jsonl --replays replays/
uv run spacetaxi serve --rule round_ticks=300 --fill-seconds 5 --seed 1   # shorter rounds and pauses
```

Bots connect to `ws://HOST:8000/bot` (protocol in [docs/rules.md](docs/rules.md)) and wait in one
queue. Rounds run in waves: a wave starts `--fill-seconds` (default 10) after the first bot
joins an idle server, and again `--fill-seconds` after every arena of the previous wave has ended
(the pause for the podium and the leaderboard). At wave start the waiting bots are dealt into up
to 3 arenas of at most 4, as even as possible (7 bots play 4 + 3, 6 play 3 + 3), and all arenas
start together; bots beyond 12 wait for the next wave. The deal is a shuffle (`--seed`, default
random) that prefers bots who have met least so far, so groups mix and averages compare. A bot
that joins mid-wave gets `queued` with `starts_in` and plays from the next wave; after its round
every bot goes back to the queue, and the server prints each wave's groups. A bot that disconnects mid-round coasts until
the round ends. `/` is the leaderboard (`/leaderboard.json` its data); with `--log` each round's
results are appended as a JSON line and the leaderboard is rebuilt from it on restart; with
`--replays` each round is written as a replay for `spacetaxi view`.

`/watch` is the neon viewer, live: put it on the projector. It follows the arena whose round
started last, or the one picked at the bottom right, with the leaderboard beside it; between
rounds it shows the INSERT COIN splash with the queue length; the leaderboard shows the wave. Click once to start the sound.

`/fly` lets a person fly in the arena by hand, against the bots: type a name, wait in the queue,
then fly your taxi (the one with the dashed ring and YOU) with `↑` (main engine) and `←` `→`
(side jets), or the touch buttons on a phone; the VY and VX gauges show whether a landing is
soft. After each round the page shows the results and joins the queue again; LEAVE disconnects.
Humans get a `human` tag on the leaderboard (`"human": true` in `/leaderboard.json`) and in
`/watch`.

The arena is a **milestone race** (rules in [docs/rules.md](docs/rules.md#milestones)): from
the first `hello` on, the first bots to say hello, fly 50 ticks, land, pick up, deliver, fly a
clean round, deliver 3 in a round, or beat the greedy and pilot house bots' money score F1
points (25, 18, 15, ...). `/standings` is the race on the projector (`/standings.json` its
data), and `/watch` pops up every award. Humans do not score.

```sh
uv run spacetaxi serve --standings standings.json                      # keep the race across restarts
uv run spacetaxi serve --benchmark greedy=300 --benchmark pilot=450    # skip the benchmark simulation
```

## Shortcuts (fish)

```fish
scripts/play.fish                              # serve the flight lab on cavern and open it
scripts/play.fish -m canyon -r gravity=3       # another map, a rule override
scripts/sim.fish                               # pilot vs greedy, 3 rounds on cavern, open the replay
scripts/sim.fish -m canyon -b pilot,./mybot.py -n 10 -s 7 -p 8001   # serve the replay instead
```

`-h` lists every option. Replays and pages go to `out/` (git-ignored).

## Write a bot

```sh
uv run spacetaxi new-bot mybot.py
uv run spacetaxi run --bots ./mybot.py,pilot --rounds 100 --json
```

`new-bot` writes a copy of the `greedy` house bot with a header that explains the bot interface,
how to evaluate it, how to watch a replay and how to connect it.

## Fly your bot in the arena

```sh
uv run spacetaxi connect --bot mybot.py --name alice --server ws://HOST:8000/bot
export SPACETAXI_SERVER=ws://HOST:8000/bot      # fish: set -x SPACETAXI_SERVER ws://HOST:8000/bot
uv run spacetaxi connect --bot mybot.py --name alice
```

`connect` loads the bot just as `run` does (a bundled name or a `.py` path) and calls the same
`welcome`, `start` and `move`: no network code to write. `--server` defaults to
`$SPACETAXI_SERVER`, then `ws://localhost:8000/bot`. It prints your place in the queue, each round
start with your opponents, each round's results, and server errors in plain words. When `move`
raises, the traceback is printed once per round and the taxi coasts that tick; a lost connection
is retried every 2 s until Ctrl-C.
