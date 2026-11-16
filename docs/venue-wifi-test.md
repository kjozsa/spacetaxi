# Venue WiFi test

Live rounds run 4 taxis at one `tick` every `tick_ms` (100 ms) with a `deadline_ms` (75 ms)
answer deadline. A late answer makes the taxi coast, and under gravity a coasting taxi falls.
This test tells whether the venue WiFi carries that for a full room, before the workshop.

## What you need

- The **server laptop**, with this repo and `uv`, on the venue WiFi. No internet needed, but
  stay on the same network the participants will use (not a guest network the server is not on).
- **A few phones and laptops**: they join with just a browser.
- **One load laptop**, with this repo, to run 12 headless bots (the workshop has at most 12
  participants).
- A projector or a second screen for the dashboard, if there is one.

## Procedure

1. On the server laptop, start the server for the first tick rate, with a report file:

   ```sh
   uv run spacetaxi wifitest --rule tick_ms=100 --report wifi-100.json
   ```

   It prints the join, dashboard and bot URLs on every network address. Use the address on the
   venue WiFi (the first one listed is the default route's; skip `172.x` Docker or VPN ones).
   If the OS firewall asks, allow incoming connections on port 8000.

2. Open `http://ADDRESS:8000/stats` on the projector.
3. On each phone and laptop, open `http://ADDRESS:8000/`, type a name and press **Join**. Keep
   the screen on and the page in front: a locked phone drops the connection.
4. On the load laptop, add the room's load, for a little longer than the 5-minute run:

   ```sh
   uv run spacetaxi wifitest --connect ws://ADDRESS:8000/bot --clients 12 --seconds 330 --name lab
   ```

5. Let it run **5 minutes**, people moving around and using the WiFi as they would on the day.
6. Write down the numbers (below), then stop the server with **Ctrl-C**: it writes the report.
   The load laptop prints its own numbers when its time is up.
7. Repeat steps 1-6 with `--rule tick_ms=150 --report wifi-150.json` and with
   `--rule tick_ms=200 --report wifi-200.json`. The deadline stays 75 ms unless you also give
   `--rule deadline_ms=...` (write that down too).

## Reading the dashboard

One row per client, plus a totals row; refreshed twice a second.

- **ticks**: ticks sent to the client. **answers**: moves received back.
- **late %**: ticks with no answer within `deadline_ms`, missing ones included. In the game
  each of these makes the taxi coast.
- **missing %**: ticks with no answer at all before the next tick.
- **p50 / p95 / p99 / max ms**: round trip, timed on the server, from sending a `tick` to
  receiving its `move`.
- **longest late run**: the most late-or-missing ticks in a row. A run of 5 at 100 ms is half a
  second of coasting: enough to crash.

## What to write down

For each run: time of day, `tick_ms`, `deadline_ms`, the number of clients (phones, laptops,
headless), where in the room the phones were, and from the dashboard the totals row plus the
worst client's late %, p99, max and longest late run. Keep the `wifi-*.json` reports.

## Go / no-go

**Go** for a tick rate when **every client's late % is under 1 %** in its 5-minute run with at
least the 12 headless bots on: that tick rate is safe for the workshop (the dashboard says GO or
NO-GO below the table). Pick the fastest safe one of 100, 150 and 200 ms. If none is safe, look
at the longest late runs and the clients that fail (one bad corner of the room, or everyone),
and plan a wired server or a dedicated access point.

After a run the clients disappear from the server only when it stops; restart the server for
each tick rate so each report covers one run.
