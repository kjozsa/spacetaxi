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
- Positions `(x, y)` and velocities `(vx, vy)` are integers, in cells and cells per tick.
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

- Every player has one taxi. A taxi occupies one cell.
- Taxis are ghosts: they never collide with each other, and any number may share a cell.
- A taxi is in one of three states: `flying`, `landed`, `crashed`.
- Each taxi has `money` (starts at 0, may go negative), and carries at most one passenger.
- At round start every taxi is `flying` with velocity `(0, 0)` at a random `S` cell (seeded RNG).

## Moves

Every tick each bot answers with a move:

```json
{"ax": -1, "ay": 0}
```

`ax` and `ay` must each be one of the integers `-1`, `0`, `1`. A missing answer, an answer that
arrives after `deadline_ms`, or an invalid answer (bad JSON, missing key, any other value or
type, booleans included) counts as `{"ax": 0, "ay": 0}`: the taxi coasts.

## Tick order

Ticks are numbered from `1` to `round_ticks`. Every tick runs these four steps in order:

### 1. Apply moves

- A **flying** taxi:
  1. `vx += ax`, `vy += ay`;
  2. then `vy += gravity` if `tick % gravity_every == 0`;
  3. then both `vx` and `vy` are clamped to `[-max_speed, max_speed]`.
- A **landed** taxi takes off if `ay == -1`: it becomes `flying` with `vx = 0`, `vy = -1`
  (gravity and `ax` are not applied on the takeoff tick) and emits `takeoff`. Otherwise it stays
  where it is, landed, with velocity `(0, 0)`.
- A **crashed** taxi ignores its move.

### 2. Move

Each flying taxi (including one that just took off) moves along a 4-connected Bresenham path
from `(x, y)` to `(x + vx, y + vy)`, one cell per step, `|vx| + |vy|` steps in total.

The path, with `dx = |vx|`, `dy = |vy|`, after `i` x-steps and `j` y-steps: the next step is an
x-step (by `sign(vx)`) if `(2i + 1) * dy <= (2j + 1) * dx`, otherwise a y-step (by `sign(vy)`).
So on ties the x-step comes first.

For each step, look at the cell being entered:

- **air / spawn:** the taxi moves there and continues.
- **wall:** the taxi **crashes** with cause `wall`. It stays at the last free cell.
- **pad tile, entered by a y-step downward:** a **touchdown**. The taxi stays on the cell above
  the pad tile (its current cell).
  - If `vx == 0` and `vy <= safe_landing_vy`: the taxi **lands**. Its state becomes `landed`,
    its velocity `(0, 0)`, and it emits `landed`.
  - Otherwise it **crashes** with cause `hard_landing`.
- **pad tile, entered any other way** (x-step, or y-step upward): a crash with cause `wall`.

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
   `respawn_in`. When it reaches `0` the taxi respawns: `flying`, velocity `(0, 0)`, at a random
   `S` cell (seeded RNG), and emits `respawn`. A taxi that crashed this tick is not counted
   down, so it sits out exactly `crash_penalty_ticks` full ticks.
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
- after `crash_penalty_ticks` ticks it respawns at a random `S` cell (seeded RNG) with
  velocity `(0, 0)` (see step 4).

## Randomness

All randomness (start and respawn cells, passenger pads, spots and destinations, pickup
tie-breaks) comes from one RNG seeded at round start, so a round with the same seed, map and
moves replays identically.

## Constants

| Name                    | Default | Meaning                                                          |
|-------------------------|--------:|------------------------------------------------------------------|
| `tick_ms`               |   `200` | wall-clock length of one tick, in milliseconds                   |
| `deadline_ms`           |   `150` | time a bot has to answer a tick, in milliseconds                 |
| `round_ticks`           |   `375` | ticks per round                                                  |
| `gravity`               |     `1` | added to `vy` on gravity ticks                                   |
| `gravity_every`         |     `2` | gravity applies on ticks where `tick % gravity_every == 0`       |
| `max_speed`             |     `3` | `vx` and `vy` are clamped to `±max_speed`                        |
| `safe_landing_vy`       |     `1` | highest `vy` that lands instead of crashing (and `vx` must be 0) |
| `crash_penalty_ticks`   |    `15` | ticks a crashed taxi waits before respawning                     |
| `crash_cost`            |    `25` | money a taxi pays per crash                                      |
| `passenger_spawn_every` |    `20` | a passenger may spawn on ticks where `tick % this == 0`          |
| `passenger_expires`     |   `150` | ticks a waiting passenger waits before disappearing              |
| `fare_base`             |    `50` | fixed part of every fare                                         |
| `fare_per_cell`         |     `2` | fare per cell of Manhattan distance between pad centres          |

## Messages

Every message is one JSON object with a `type`. Server to bot: `welcome`, `round_start`,
`tick`, `round_end`. Bot to server: `move`.

### `welcome`

Sent once when a bot joins. Carries the bot's taxi id and the rules (every constant above).

```json
{
  "type": "welcome",
  "id": "alice",
  "rules": {
    "tick_ms": 200, "deadline_ms": 150, "round_ticks": 375,
    "gravity": 1, "gravity_every": 2, "max_speed": 3, "safe_landing_vy": 1,
    "crash_penalty_ticks": 15, "crash_cost": 25,
    "passenger_spawn_every": 20, "passenger_expires": 150,
    "fare_base": 50, "fare_per_cell": 2
  }
}
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
    "tick_ms": 200, "deadline_ms": 150, "round_ticks": 375,
    "gravity": 1, "gravity_every": 2, "max_speed": 3, "safe_landing_vy": 1,
    "crash_penalty_ticks": 15, "crash_cost": 25,
    "passenger_spawn_every": 20, "passenger_expires": 150,
    "fare_base": 50, "fare_per_cell": 2
  }
}
```

### `tick`

Sent at the start of every tick with the state after the previous tick, and the events that
happened in it. The bot answers with a `move` for tick `tick`.

- `taxis`: one per player, with `id`, `x`, `y`, `vx`, `vy`, `state`, `pad` (the pad id the taxi
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
    {"id": "alice", "x": 4, "y": 6, "vx": 0, "vy": 0, "state": "landed", "pad": "A",
     "passenger": {"id": 3, "to": "D", "fare": 78}, "money": 101, "respawn_in": 0},
    {"id": "bob", "x": 9, "y": 3, "vx": 0, "vy": 0, "state": "crashed", "pad": null,
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

The bot's answer to a `tick`. `debug` is optional, for viewers and logs; the simulation
ignores it. It is either free text, or an object with an optional `say` (free text) and an
optional `path` (a list of `[x, y]` integer cells, e.g. the planned route). Other keys of the
object, a `say` that is not text and a `path` that is not such a list are dropped; any other
`debug` is dropped whole.

```json
{"type": "move", "ax": 0, "ay": -1, "debug": "climbing to cruise height"}
{"type": "move", "ax": 1, "ay": 0, "debug": {"say": "to pad B", "path": [[5, 5], [6, 5]]}}
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
