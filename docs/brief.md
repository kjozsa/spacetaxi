# space taxi

Fly a taxi in a 2D cave with gravity. Passengers wait on landing pads; pick one up, land on the
pad they want to go to, and you get paid. Crashing costs money and time. Most money after a
round wins.

## Connecting

WebSocket `ws://HOST:PORT/bot`, one JSON object per text frame.

1. Send `{"type": "hello", "name": "yourname", "protocol": 1}` (name: 1-12 of `A-Z a-z 0-9 _ -`).
   Names ignore case: the first spelling seen keeps the name, so reconnect with the same one.
2. You get `welcome`, then `queued` while you wait for a round: your place in the plan of the
   next wave and `starts_in`, the seconds until the wave you are planned into starts.
3. A round starts with `round_start`: the map (rows of text), the pads and the rules.
4. Then every 100 ms a `tick` with the state of all taxis and waiting passengers. Answer each
   one quickly (within 75 ms) with a move for that tick:
   `{"type": "move", "tick": 42, "ax": 0, "ay": -1}`
5. After the last tick, `round_end` with the results; then you are queued for the next round.

If something is wrong the server sends `error` with a `code` and closes the connection.

- Size limit: keep messages small (a move, a short `debug`); a huge one gets `too_big`.
- Rate limit: one move per tick; a burst within one tick gets `flooding` (bots per address are capped too).

## Flying

- `ax`: `-1` left jet, `0` none, `1` right jet. `ay`: `-1` main engine (up), `0` none.
- Gravity pulls down all the time. Positions and velocities are integers; `y` grows downward.
- Map: `#` wall, `.` air, `S` spawn, letters are pads (a run of the same letter is one pad).
- Touch a pad from above slowly enough and you land. Anything else that touches a wall or a pad
  is a crash: you lose your passenger, pay a fine and respawn at an `S` after a while.
- Fire the main engine while landed to take off.

## Passengers

- They appear on pads from time to time and give up if nobody comes for a while.
- Land on their pad to pick one up (one passenger at a time), land on their destination to
  deliver. Longer trips pay more.
- Other taxis fly in the same cave and want the same passengers. Taxis pass through each other.
