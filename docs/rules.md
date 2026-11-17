# spacetaxi rules

This document is the source of truth for the game. The simulation, the runner, the viewers and
the bots all implement what is written here; when code and this document disagree, the code is
wrong.

Bots fly taxis around a grid. Passengers appear on landing pads and want to go to another pad.
A bot earns money by picking a passenger up and landing on their destination pad. Crashing
costs money and time.

All simulation values are integers. There is no floating point anywhere in the simulation.

## Grid

- `x` grows to the right, `y` grows downward, the origin `(0, 0)` is the top-left cell.
- Each cell is `units_per_cell` units wide and high. A taxi's fine position `(fx, fy)` is in
  units, and so is its velocity `(vx, vy)`, in units per tick. All are integers.
- A taxi's cell `(x, y)` is the cell of its fine position: `x = fx // units_per_cell`,
  `y = fy // units_per_cell`.
- Negative `vy` means moving up; positive `vy` means moving down.

## Map

A map is a list of equal-length text rows. Each character is one cell:

| Char    | Meaning                                                            |
|---------|--------------------------------------------------------------------|
| `#`     | wall (solid)                                                       |
| `.`     | air                                                                |
| `A`–`Z` | pad surface (solid)                                                |
| `S`     | spawn point (air; taxis respawn here)                              |

- A horizontal run of the same letter forms one pad; the pad's id is that letter. Each letter
  forms at most one pad. A pad is described by `id`, `x` (its leftmost tile), `y` (its row)
  and `len` (number of tiles).
- A pad's **centre** is `(x + len // 2, y)` (integer division).
- The border of the map (first and last row, first and last column) must be walls.
- The cell directly above every pad tile must be air (`.` or `S`).
- A map needs at least two pads and at least one `S`.

Example:

```
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
```

## Taxis

- Every player has one taxi. A taxi is a box of `±taxi_half_width` by `±taxi_half_height`
  units around its fine position `(fx, fy)`, its centre: it covers the units from
  `fx - taxi_half_width` to `fx + taxi_half_width` across and from `fy - taxi_half_height` to
  `fy + taxi_half_height` down, both ends included, and overlaps every cell they fall in.
- Taxis are ghosts: they never collide with each other, and any number may share a cell.
- A taxi is in one of three states: `flying`, `landed`, `crashed`.
- Each taxi has `money` (starts at 0, may go negative), and carries at most one passenger.
- At round start every taxi is `flying` with velocity `(0, 0)` at the centre of a random `S`
  cell (seeded RNG): `fx = x * units_per_cell + units_per_cell // 2`, the same for `fy`.

## Moves

Every tick each bot answers with a move for that tick:

```json
{"ax": -1, "ay": 0, "tick": 42}
```

`ax` and `ay` must each be one of the integers `-1`, `0`, `1`. `ax` fires the side jets
(`-1` left, `1` right); `ay = -1` fires the main engine, upward. There is no down thruster:
`ay = 1` is a valid answer but does nothing, the same as `ay = 0`. A missing answer, an
answer that arrives after `deadline_ms`, or an invalid answer (bad JSON, missing key, any
other value or type, booleans included) counts as `{"ax": 0, "ay": 0}`: the taxi coasts.
`tick` is the number of the `tick` message the move answers. A move whose `tick` is not the tick
being played is late, and coasts exactly like a missing answer; a move without an integer `tick`
is invalid. Bots run in-process by `spacetaxi run` may leave `tick` out: the runner fills in the
current tick.

## Tick order

Ticks are numbered from `1` to `round_ticks`. Every tick runs these four steps in order:

### 1. Apply moves

- A **flying** taxi:
  1. `vx += ax * side_thrust`;
  2. then `vy -= main_thrust` if `ay == -1` (`ay == 1` changes nothing);
  3. then `vy += gravity` if `tick % gravity_every == 0`;
  4. then both `vx` and `vy` are clamped to `[-max_speed, max_speed]`.
- A **landed** taxi takes off if `ay == -1`: it becomes `flying` with `vx = 0`,
  `vy = gravity - main_thrust` (`ax` is ignored on the takeoff tick) and emits `takeoff`.
  Otherwise it stays where it is, landed, with velocity `(0, 0)`.
- A **crashed** taxi ignores its move.

### 2. Move

Each flying taxi (including one that just took off) moves its box along a 4-connected
Bresenham path from `(fx, fy)` to `(fx + vx, fy + vy)`, one unit per step, `|vx| + |vy|` steps
in total.

The path, with `dx = |vx|`, `dy = |vy|`, after `i` x-steps and `j` y-steps: the next step is an
x-step (by `sign(vx)`) if `(2i + 1) * dy <= (2j + 1) * dx`, otherwise a y-step (by `sign(vy)`).
So on ties the x-step comes first.

For each step, look at the cells the box would overlap after it. Walls and pad tiles are
**solid**; cells outside the map count as walls.

- **no solid cell:** the taxi moves there and continues.
- **a touchdown:** the step is a y-step downward, every solid cell the box would overlap is a
  pad tile, and all of them are in the box's bottom row. The taxi stays where it is, its box
  resting just above the pad.
  - If `|vx| <= safe_landing_vx` and `vy <= safe_landing_vy`: the taxi **lands**. Its state
    becomes `landed`, its velocity `(0, 0)`, and it emits `landed`. It lands on the pad under
    its centre, i.e. the pad of the tile in column `fx // units_per_cell` of that row; if that
    tile is not a pad, on the first (leftmost) pad tile the box touched.
  - Otherwise it **crashes** with cause `hard_landing`.
- **any other solid contact** (a wall, a pad entered by an x-step or upward, or from the side
  of the box): the taxi **crashes** with cause `wall`. It stays where it is, at the last free
  position.

Movement stops at the first touchdown or crash. A taxi that finishes its path without either
stays `flying` with its velocity unchanged.

### 3. Pickups and deliveries

Only taxis that **landed in step 2 of this tick** take part (a touchdown that ended in a crash
does not). Deliveries are resolved first, then pickups.

- **Delivery:** a landed taxi whose passenger's destination is the pad it landed on is paid the
  fare (`money += fare`); the passenger leaves the game (`deliver` event, with `fare`). The taxi
  is now empty.
- **Pickup:** a waiting passenger boards the first empty taxi whose touchdown on their pad
  happens on a later tick than the one the passenger appeared on. A taxi already sitting on the
  pad does not count: it must take off and land again.
  - If several empty taxis land on the passenger's pad in the same tick, the one with the
    smallest `|x - spot_x|` wins; a remaining tie is broken by the seeded RNG.
  - Waiting passengers on the same pad are served oldest first (lowest passenger `id`).
  - A taxi carries at most one passenger, so a taxi that boards one is not a candidate for the
    next.
  - A boarding emits `pickup`.

### 4. Respawns and passengers

1. **Respawns.** Every taxi that was already `crashed` before this tick decrements
   `respawn_in`. When it reaches `0` the taxi respawns: `flying`, velocity `(0, 0)`, at the
   centre of a random `S` cell (seeded RNG), and emits `respawn` with that cell. A taxi that
   crashed this tick is not counted down, so it sits out exactly `crash_penalty_ticks` full
   ticks.
2. **Expiry.** Every waiting passenger decrements `expires_in`. When it reaches `0` the
   passenger disappears and emits `expired`.
3. **Spawn.** If `tick % passenger_spawn_every == 0` and fewer than `players + 2` passengers
   are waiting, one new passenger spawns (`spawn` event):
   - on a random pad (seeded RNG), at a random `spot_x` among that pad's tiles
     (`pad.x <= spot_x < pad.x + pad.len`);
   - wanting a random **different** pad;
   - paying `fare = fare_base + fare_per_cell * (|cx1 - cx2| + |cy1 - cy2|)`, the Manhattan
     distance between the two pads' centres;
   - with `expires_in = passenger_expires`.

   Passenger ids are integers counting up from `1` per round.

## Crash

When a taxi crashes (cause `wall` or `hard_landing`):

- it pays `crash_cost` (`money -= crash_cost`);
- any passenger it carries is lost (`lost` event);
- its state becomes `crashed`, velocity `(0, 0)`, and `respawn_in = crash_penalty_ticks`;
- after `crash_penalty_ticks` ticks it respawns at the centre of a random `S` cell (seeded
  RNG) with velocity `(0, 0)` (see step 4).

## Randomness

All randomness (start and respawn cells, passenger pads, spots and destinations, pickup
tie-breaks) comes from one RNG seeded at round start, so a round with the same seed, map and
moves replays identically.

## Constants

| Name                    | Default | Meaning                                                          |
|-------------------------|--------:|------------------------------------------------------------------|
| `tick_ms`               |   `100` | wall-clock length of one tick, in milliseconds                   |
| `deadline_ms`           |    `75` | time a bot has to answer a tick, in milliseconds                 |
| `round_ticks`           |   `750` | ticks per round                                                  |
| `units_per_cell`        |    `32` | units per cell, across and down                                  |
| `gravity`               |     `2` | added to `vy` on gravity ticks                                   |
| `gravity_every`         |     `1` | gravity applies on ticks where `tick % gravity_every == 0`       |
| `main_thrust`           |     `4` | taken off `vy` when `ay == -1`                                   |
| `side_thrust`           |     `2` | added to `vx` times `ax`                                         |
| `max_speed`             |    `24` | `vx` and `vy` are clamped to `±max_speed`, in units per tick     |
| `safe_landing_vy`       |     `8` | highest `vy` that lands instead of crashing                      |
| `safe_landing_vx`       |     `3` | highest `abs(vx)` that lands instead of crashing                 |
| `taxi_half_width`       |    `15` | the taxi's box reaches this many units left and right of `fx`    |
| `taxi_half_height`      |    `12` | the taxi's box reaches this many units up and down of `fy`       |
| `crash_penalty_ticks`   |    `30` | ticks a crashed taxi waits before respawning                     |
| `crash_cost`            |    `25` | money a taxi pays per crash                                      |
| `passenger_spawn_every` |    `40` | a passenger may spawn on ticks where `tick % this == 0`          |
| `passenger_expires`     |   `300` | ticks a waiting passenger waits before disappearing              |
| `fare_base`             |    `50` | fixed part of every fare                                         |
| `fare_per_cell`         |     `2` | fare per cell of Manhattan distance between pad centres          |

## Transport

Bots connect to the server over WebSocket at `ws://HOST:PORT/bot`. Every message is one JSON
object, sent as one text frame. The bot's first message is `hello`; the server answers with
`welcome`, then `queued` while the bot waits for a round. Rounds run in waves: every arena of a
wave starts on the same tick clock, and the next wave starts a pause after all of them end. At
the start of a wave the waiting bots are dealt into arenas of at most 4, as even as possible, and
groups are reshuffled every wave; a bot that joins mid-wave plays from the next wave. When the server sends an `error`, it
closes the socket right after it.

Spectators (the live viewer at `http://HOST:PORT/watch`) connect to `ws://HOST:PORT/spectate`
and send nothing. The server first sends `{"type": "spectate", "arenas": 3}` (how many arenas
can run side by side), then every arena's `round_start`, `tick` (with the spectator-only
`moves`, as in a replay) and `round_end`, each with two more fields: `arena`, the arena's slot
(1 to `arenas`), and `round`. A spectator's `round_start` also has `humans`: the players of the
round that said `"client": "human"` (a list of ids, often empty). A spectator that connects mid-round first gets each running
arena's `round_start` and latest `tick`. A spectator that falls behind loses ticks.

Spectators also get one `milestone` message per milestone award (see Milestones): `name`
reached `milestone` in `place` and scored `points`. Bots never receive it.

```json
{"type": "milestone", "name": "anna", "milestone": "paid", "place": 3, "points": 15}
```

## Milestones

The arena is a race: points go to whoever reaches each milestone first, Formula 1 style, from
the first `hello` on. The server detects the milestones itself; each name scores each milestone
once, however often it reaches it again.

| #  | Milestone     | Reached when                                                                     |
|----|---------------|----------------------------------------------------------------------------------|
| 1  | `hello`       | the bot is welcomed (its `hello` is accepted)                                    |
| 2  | `liftoff`     | its taxi ends 50 ticks in a row of one round `flying` with no `crash` (a crash, a landing or a new round restarts the count) |
| 3  | `touchdown`   | its taxi has a `landed` event                                                    |
| 4  | `fare`        | its taxi has a `pickup` event                                                    |
| 5  | `paid`        | its taxi has a `deliver` event                                                   |
| 6  | `clean`       | at `round_end`: a round with 0 crashes and at least 1 delivery                   |
| 7  | `hat_trick`   | at `round_end`: 3 or more deliveries in the round                                |
| 8  | `beat_greedy` | at `round_end`: money in the round above the greedy benchmark                    |
| 9  | `beat_pilot`  | at `round_end`: money in the round above the pilot benchmark                     |

- **Points.** The 1st to 10th name to reach a milestone score 25, 18, 15, 12, 10, 8, 6, 4, 2
  and 1 points; later names score 0 but still get the milestone's badge.
- **Ties.** Names that reach a milestone on the same tick (all arenas of a wave tick on one
  clock, so the same tick number in two arenas of a wave is the same moment) share the place
  and its points, and the next place is skipped: two names in 2nd place both score 18, the
  next name is 4th. For milestones 6 to 9, all reached at the same `round_end`, higher round
  money places first, and names with equal money tie.
- **Rounds of one.** Milestones 6 to 9 count only in rounds with at least 2 taxis.
- **Benchmarks.** At start the server simulates 20 seeded rounds on the served map with the
  served rules: `greedy` against 3 greedy copies and `pilot` against 3 greedy copies. Each
  bot's average money per round (rounded, at least 1) is its benchmark. The simulation runs in
  the background: until a benchmark is known, its milestone is not awarded, and a round counts
  it only when the benchmark was known at the round's start. `spacetaxi serve --benchmark
  greedy=N --benchmark pilot=N` gives them instead. The server prints the values in use and the
  standings page shows them.
- **Humans** (`"client": "human"` in `hello`) never score and have no standings row.
- **Standings.** Points belong to the name, so a bot that reconnects under the same name keeps
  them. With `spacetaxi serve --standings FILE` the standings are saved to that JSON file after
  every award and loaded from it at start, so a restart keeps the race. `/standings` on the
  server is the projector page of the race, `/standings.json` its data; the server log prints
  one line per award.

## Messages

Every message is one JSON object with a `type`. Server to bot: `welcome`, `queued`,
`round_start`, `tick`, `round_end`, `error`. Bot to server: `hello`, `move`.

### `hello`

The bot's first message. `name` becomes the taxi id: 1 to 12 characters, each one of `A-Z`,
`a-z`, `0-9`, `_` and `-`. `protocol` is the protocol version, `1` for this document.
`client` is optional: `"human"` says a person flies this taxi by hand (the server's `/fly`
page). The server plays a human like any bot, but marks it as human on the leaderboard and to
spectators. Any other value, or none, is a bot.

```json
{"type": "hello", "name": "kris", "protocol": 1, "client": "human"}
{"type": "hello", "name": "alice", "protocol": 1}
```

### `welcome`

Sent once when a bot joins. Carries the bot's taxi id and the rules (every constant above).

```json
{
  "type": "welcome",
  "id": "alice",
  "rules": {
    "tick_ms": 100, "deadline_ms": 75, "round_ticks": 750, "units_per_cell": 32,
    "gravity": 2, "gravity_every": 1, "main_thrust": 4, "side_thrust": 2, "max_speed": 24,
    "safe_landing_vy": 8, "safe_landing_vx": 3, "taxi_half_width": 15, "taxi_half_height": 12,
    "crash_penalty_ticks": 30, "crash_cost": 25,
    "passenger_spawn_every": 40, "passenger_expires": 300,
    "fare_base": 50, "fare_per_cell": 2
  }
}
```

### `queued`

Sent after `welcome` and after every `round_end` while the bot waits for a round to start, and
again whenever its place in the queue or its start time changes. `position` is the bot's place
in the queue, from `1`; `waiting` is the number of bots in the queue; `starts_in` is the
estimated number of whole seconds until the bot's round starts (an integer, `0` when due).

```json
{"type": "queued", "position": 3, "waiting": 5, "starts_in": 8}
```

### `round_start`

Carries the map rows, the pads, the spawn cells, the players and the rules in effect for the
round (every constant above, same shape as in `welcome`).

```json
{
  "type": "round_start",
  "round": 1,
  "map": [
    "####################",
    "#..................#",
    "#..S...........S...#",
    "#..................#",
    "#....###......###..#",
    "#..................#",
    "#..................#",
    "#.AAAA......BBBB...#",
    "#.......####.......#",
    "#..CCC.........DDD.#",
    "####################"
  ],
  "pads": [
    {"id": "A", "x": 2, "y": 7, "len": 4},
    {"id": "B", "x": 12, "y": 7, "len": 4},
    {"id": "C", "x": 3, "y": 9, "len": 3},
    {"id": "D", "x": 15, "y": 9, "len": 3}
  ],
  "spawns": [{"x": 3, "y": 2}, {"x": 15, "y": 2}],
  "players": ["alice", "bob"],
  "rules": {
    "tick_ms": 100, "deadline_ms": 75, "round_ticks": 750, "units_per_cell": 32,
    "gravity": 2, "gravity_every": 1, "main_thrust": 4, "side_thrust": 2, "max_speed": 24,
    "safe_landing_vy": 8, "safe_landing_vx": 3, "taxi_half_width": 15, "taxi_half_height": 12,
    "crash_penalty_ticks": 30, "crash_cost": 25,
    "passenger_spawn_every": 40, "passenger_expires": 300,
    "fare_base": 50, "fare_per_cell": 2
  }
}
```

### `tick`

Sent at the start of every tick with the state after the previous tick, and the events that
happened in it. The bot answers with a `move` for tick `tick`.

- `taxis`: one per player, with `id`, `x`, `y` (its cell), `fx`, `fy` (its fine position, in
  units), `vx`, `vy`, `state`, `pad` (the pad id the taxi
  is landed on, or `null`), `passenger` (`{id, to, fare}` or `null`), `money`, `respawn_in`
  (ticks until respawn while crashed, otherwise `0`).
- `passengers`: the waiting passengers, with `id`, `at` (pad id), `spot_x`, `to` (pad id),
  `fare`, `expires_in`.
- `events`: what happened in the previous tick, in order.

```json
{
  "type": "tick",
  "tick": 42,
  "taxis": [
    {"id": "alice", "x": 4, "y": 6, "fx": 144, "fy": 211, "vx": 0, "vy": 0,
     "state": "landed", "pad": "A",
     "passenger": {"id": 3, "to": "D", "fare": 78}, "money": 101, "respawn_in": 0},
    {"id": "bob", "x": 9, "y": 3, "fx": 300, "fy": 110, "vx": 0, "vy": 0,
     "state": "crashed", "pad": null,
     "passenger": null, "money": -25, "respawn_in": 12}
  ],
  "passengers": [
    {"id": 4, "at": "B", "spot_x": 13, "to": "C", "fare": 74, "expires_in": 138}
  ],
  "events": [
    {"type": "landed", "taxi": "alice", "pad": "A"},
    {"type": "pickup", "taxi": "alice", "passenger": 3},
    {"type": "crash", "taxi": "bob", "cause": "wall"},
    {"type": "lost", "taxi": "bob", "passenger": 2}
  ]
}
```

A replay file (`spacetaxi run --replay`) writes each `tick` with one more, spectator-only
field that bots never receive:

- `moves`: the move applied to each taxi in this tick, keyed by taxi id:
  `{"ax", "ay", "coast", "debug"?}`. `coast` is `true` when the bot's answer was missing, late
  or invalid; the move then counts as `0,0`. `debug` is the answer's `debug`, when it has a
  valid one (see `move`).

```json
"moves": {
  "alice": {"ax": 0, "ay": -1, "coast": false, "debug": "climbing to cruise height"},
  "bob": {"ax": 0, "ay": 0, "coast": true}
}
```

Event shapes:

```json
{"type": "landed",  "taxi": "alice", "pad": "A"}
{"type": "takeoff", "taxi": "alice", "pad": "A"}
{"type": "crash",   "taxi": "bob", "cause": "wall"}
{"type": "crash",   "taxi": "bob", "cause": "hard_landing"}
{"type": "respawn", "taxi": "bob", "x": 15, "y": 2}
{"type": "pickup",  "taxi": "alice", "passenger": 3}
{"type": "deliver", "taxi": "alice", "passenger": 3, "fare": 78}
{"type": "lost",    "taxi": "bob", "passenger": 2}
{"type": "spawn",   "passenger": 4, "at": "B", "to": "C"}
{"type": "expired", "passenger": 1}
```

### `move`

The bot's answer to a `tick`. `tick` is required: the `tick` it answers (see Moves); a move
for any other tick is late and the taxi coasts. `debug` is optional, for viewers and logs; the
simulation ignores it. It is either free text, or an object with an optional `say` (free text)
and an optional `path` (a list of `[x, y]` integer cells, e.g. the planned route). Other keys
of the object, a `say` that is not text and a `path` that is not such a list are dropped; any
other `debug` is dropped whole.

```json
{"type": "move", "tick": 42, "ax": 0, "ay": -1, "debug": "climbing to cruise height"}
{"type": "move", "tick": 42, "ax": 1, "ay": 0, "debug": {"say": "to pad B", "path": [[5, 5], [6, 5]]}}
```

The web viewer (`spacetaxi view`) shows each taxi's `debug` above it when key `D` is on: the
text, or the object's `say`, and its `path` as a dotted line in the taxi's colour.

### `round_end`

Sent after the last tick, with per-taxi results.

```json
{
  "type": "round_end",
  "round": 1,
  "results": [
    {"id": "alice", "money": 412, "deliveries": 6, "crashes": 2},
    {"id": "bob", "money": 158, "deliveries": 3, "crashes": 4}
  ]
}
```

### `error`

Sent when the server refuses a bot or a message; the server then closes the socket. `message`
is free text for humans. `code` is one of:

- `name_taken`: another connected bot already has this `name`.
- `bad_name`: the `name` in `hello` breaks the naming rule.
- `bad_protocol`: the server does not speak the `protocol` in `hello`.
- `bad_message`: the message is not JSON, not an object, has an unknown `type`, or is not
  `hello` when `hello` is expected.

```json
{"type": "error", "code": "name_taken", "message": "name 'alice' is already connected"}
```
