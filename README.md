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
uv run spacetaxi serve --map cavern --map canyon --map classic             # maps to switch between
```

`--map` is repeatable: the first is the map the arena starts on, the others are the ones it can
switch to. While `serve` runs, type `map canyon` and Enter in its terminal to set the map of the
next wave (the wave in progress finishes on its map; `serve` answers `next wave: canyon`), and
`maps` to list them with the current and the next one. All arenas of a wave play one map; bots
get it, and its name as `map_name`, in every `round_start`, and the log and the replays record
it. Without a terminal (e.g. run in the background) the map stays the first one.

Bots connect to `ws://HOST:8000/bot` (protocol in [docs/rules.md](docs/rules.md)). Rounds run in
waves: a wave starts `--fill-seconds` (default 10) after the first bot joins an idle server, and
again `--fill-seconds` after every arena of the previous wave has ended (the pause for the podium
and the leaderboard); all arenas of a wave start together. The server keeps a plan of the next
wave, so everyone can see early who plays where: when a wave starts, every connected bot is dealt
into the next wave's up to 3 arenas of at most 4, as even as possible (7 bots play 4 + 3, 6 play
3 + 3), the bots that sit the current wave out seated first, and the bots beyond 12 go on the
waitlist. The deal is a shuffle (`--seed`, default random) that prefers bots who have met least
so far, so groups mix and averages compare. On an idle server the plan starts with the first
join. After that the plan is stable: a joiner takes a free seat or the end of the waitlist, a
leaver's seat goes to the first on the waitlist, and a bot moves to another arena only when the
arena sizes must change (4 bots and a 5th join: a second arena opens and one bot moves to it). The
next wave plays exactly as planned. `queued` tells each bot its place in the plan and `starts_in`
for the wave it is planned into; `serve` prints the plan whenever it is made or changes
(`next wave 6 on cavern: a, b, c | d, e | f, g, h  waiting: x, y`) and each wave's groups as it
starts; `/leaderboard.json` has it as `next_wave` (`wave`, `map`, `starts_in`, `arenas`,
`waitlist`). A bot that disconnects mid-round coasts until
the round ends. `/` is the leaderboard (`/leaderboard.json` its data); with `--log` each round's
results are appended as a JSON line and the leaderboard is rebuilt from it on restart; with
`--replays` each round is written as a replay for `spacetaxi view`.

Limits keep a buggy or hostile bot from spoiling the race (details in
[docs/rules.md](docs/rules.md#transport)): a bot's message is at most 64 KB (`too_big`), a
spectator's 4 KB; more than 20 messages or 128 KB from one bot within one tick close it
(`flooding`); a move's `debug` text is cut to 200 characters and its `path` to 200 cells. At most
`--max-bots-per-ip` bots (default 4) connect at once from one IP address (`too_many`) and
`--max-bots` (default 40) in all (`full`); the `--house` bots do not count. Deeply nested JSON is
a `bad_message`, and an exception in one connection is logged as one line (`! NAME: ...`).

When a bot misbehaves, or someone connects dozens of names, type in `serve`'s terminal (or write
into the `--control` pipe, or use `scripts/arena.fish kick NAME`):

- `kick NAME`: disconnect the bot; it may connect again.
- `ban NAME`: disconnect it, refuse the name from then on (ignoring case) and leave it out of the
  leaderboard and the next-wave plan. Its milestone places are dropped and everyone behind it
  moves up (with the points of the new place), so spam names that took the first `hello`
  places give them back. Bans are saved in the `--standings` file.
- `unban NAME`: accept the name again (its leaderboard row comes back; its milestones start
  over).

`/watch` is the neon viewer, live: put it on the projector. It follows the arena whose round
started last, or the one picked at the bottom right, with the leaderboard beside it; between
rounds it shows the INSERT COIN splash with the queue length and the plan of the next wave (its
number, map and countdown, a column per arena, the waitlist); during rounds a compact NEXT WAVE
box sits under the leaderboard, which shows every name (the rows shrink to fit) and the wave.
`/mission` shows under the goal where the followed bot plays: `NOW: arena 2, round ends in 41 s`,
`NEXT: wave 6, arena 1 with anna, bob, gus — in ~55 s`, `WAITLIST #2: plays in wave 7 at the
latest` or `not connected`. Click once to start the sound.

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
uv run spacetaxi serve --map cavern --map canyon --benchmark canyon:greedy=250   # another map's
```

Every map has its own benchmarks (simulated at start, or given per map as `MAP:BOT=MONEY`), and
a round's `beat_greedy` / `beat_pilot` compare against its map's; `/standings` and `/mission`
show those of the current wave's map.

### Running the workshop

The facilitator's timeline and pit stops are in [docs/agenda.md](docs/agenda.md).

Open the intro slides at `http://HOST:8000/slides` (reveal.js from [docs/slides/intro.html](docs/slides/intro.html),
the server's address filled in; arrows or Space page through, `S` opens the speaker notes).

Participants follow `http://HOST:8000/mission`: their next milestone, and the first prompt with
the server's address and their name filled in (`serve` prints the URL). Their agents read the
brief, `http://HOST:8000/brief` (raw Markdown at `/brief.md`, from [docs/brief.md](docs/brief.md));
the milestones are for the people, who slice the work one small prompt per milestone. During the race keep `/standings` on the projector.
For a mid-race twist that shows whose agent built a general bot rather than one tuned to one map,
start `serve` with every map you may use (`--map cavern --map canyon`) and type `map canyon` in
its terminal (or, with `serve --control PATH`, write it into that named pipe:
`echo 'map canyon' > PATH`): the next wave plays canyon, bots stay connected, and the INSERT
COIN splash on `/watch` names the next map.
Each participant keeps `http://HOST:8000/mission` open next to their agent: it shows only their
next milestone (goal, a hint phrased as a question, a ready prompt for the first one only, from
[docs/missions.md](docs/missions.md)) and celebrates each award. The name typed there only
picks the bot to follow, ignoring case; bot names ignore case too, the first spelling seen keeps
the name.

A participant flying alone never reaches milestones 6 to 9 (they need a round of at least 2
taxis), so start house bots to fill the rounds, and name them to `serve` so they never take a
place or points from the participants:

```sh
uv run spacetaxi serve --house ace --house gus
uv run spacetaxi connect --bot pilot --name ace --server ws://HOST:8000/bot
uv run spacetaxi connect --bot greedy --name gus --server ws://HOST:8000/bot
```

## Shortcuts (fish)

```fish
scripts/play.fish                              # serve the flight lab on cavern and open it
scripts/play.fish -m canyon -r gravity=3       # another map, a rule override
scripts/sim.fish                               # pilot vs greedy, 3 rounds on cavern, open the replay
scripts/sim.fish -m canyon -b pilot,./mybot.py -n 10 -s 7 -p 8001   # serve the replay instead
```

`-h` lists every option. Replays and pages go to `out/` (git-ignored).

`scripts/arena.fish` runs the workshop arena; every command takes `-p PORT` (default 8000) and
keeps its state (standings, log, replays, pid files, bot logs, the control pipe) in
`out/arena/PORT/`:

| command | what it does |
| --- | --- |
| `up [-m MAP]... [--fresh] [-r KEY=VALUE]... [-f SECONDS]` | serve the arena in the foreground, house bot names reserved; `--fresh` archives the race so far to `archive-<timestamp>/` |
| `house [pilot\|greedy] [-n N]` | start house bots in the background: `ace`, `ace2`, `ace3` fly pilot, `gus`, `gus2`, `gus3` greedy |
| `unhouse [NAME\|all]` | stop house bots started by `house` |
| `map NAME`, `maps` | switch the next wave's map, list the maps (answered in `up`'s output) |
| `kick NAME`, `ban NAME`, `unban NAME` | disconnect a bot, ban its name, lift a ban (answered in `up`'s output) |
| `status` | up or not, current and next map, waiting and playing, house bots, top 5 |
| `open` | open `/watch` and `/standings` in the browser |
| `down` | stop the house bots and the arena |

A workshop run, `up` in one terminal and the rest in another:

```fish
scripts/arena.fish up --fresh -m cavern -m classic   # a new race; Ctrl-C or down stops it
scripts/arena.fish house                             # one pilot and one greedy fill the rounds
scripts/arena.fish open                              # watch and standings
scripts/arena.fish map classic                       # the mid-race twist
scripts/arena.fish down                              # the end
```

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
