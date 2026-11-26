# Workshop agenda

2.5 hours, at most 12 participants. The facilitator's plan: what happens when, and what each pit
stop teaches. Participants never see this page; they follow `/mission`.

Two terminals on the server laptop, both in the repo: **T1** runs the arena and shows its
answers; **T2** is where you type every other command. Every `scripts/arena.fish` command below
goes in T2; its answer (to `lesson`, `map`, `kick`, ...) appears in T1. You can also type the
same commands without `scripts/arena.fish` straight into T1 (`lesson 3` + Enter).

## Run sheet

### The day before

- [ ] `git pull && uv sync` in the repo.
- [ ] Rehearse once at home: the "30 minutes before" block, a bot connected, `down`.
- [ ] Tell the participants to come with Claude Code installed and logged in, and `uv`
  installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`): setup is where chapter 1
  loses its time. And: "we will use Haiku (`/model haiku` in Claude Code)": it is fast,
  cheap, and what the rehearsal ran on.

### 30 minutes before

1. Turn the VPN off (it routes all of `10.0.0.0/8`). Join the venue WiFi.
2. T2, stop anything left over from a rehearsal (house bots included):

   ```fish
   scripts/arena.fish down
   ```

3. T1, start a fresh race on classic, with cavern ready for the twist:

   ```fish
   scripts/arena.fish up --fresh -m classic -m cavern
   ```

   Check its first lines: the `mission http://ADDRESS:8000/mission` address must be the
   venue WiFi one (not `10.47.x`, `100.x` or `172.x`). If it is wrong, Ctrl-C, fix the network,
   start again. Copy the `all lessons ... /lessons?key=...` link into a private browser window
   on your own screen, never on the projector.
4. T2, the house bots (one pilot `ace`, one greedy `gus`) and the projector pages:

   ```fish
   scripts/arena.fish house
   scripts/arena.fish open
   scripts/arena.fish status
   ```

   `status` should say `house: ace gus`. Within a minute `/watch` shows them flying.
5. Once everyone is seated, count heads and tell the arena (here 11), so a pit stop waits for
   half the room, not half of the bots connected so far:

   ```fish
   scripts/arena.fish room 11
   ```

   Someone arrives late: raise it (`room 13`). The standings count instead whenever they hold
   more participants than you said.
6. Projector: `http://localhost:8000/slides` (needs internet for reveal.js). Keep the `/watch`
   and `/standings` tabs `open` made for later.
7. From a phone on the venue WiFi, open `http://ADDRESS:8000/mission`: it must load.

### 0:00–0:10 Intro

- Slides, arrows to page, `S` for speaker notes.
- On the last slide ("Go"), say:
  - "Open `http://ADDRESS:8000/mission`, type your bot's name (your first name, no accents),
    copy the prompt into your agent."
  - **"First, switch Claude Code to Haiku: type `/model haiku`."** Walk the room once to
    check: the model shows under the prompt box. Bigger models are slower per step and burn
    the budget; the rehearsal timings are Haiku's.
  - "Don't start the agent yet: first, fly by hand."

### 0:10–0:20 Fly by hand

- Say: "Open `http://ADDRESS:8000/fly`, use your name plus `-h` (`anna-h`), arrow keys:
  up is the main engine, left and right the side jets. Land softly on a pad."
- Projector: `/watch`.
- At 0:20 say: "Press LEAVE on `/fly` and close the tab: hand-flown taxis take seats from the
  bots." Anyone still flying a few minutes later:

  ```fish
  scripts/arena.fish kick anna-h
  ```

### 0:20–0:45 Chapter 1, Get flying: hello, liftoff, touchdown

In the 6-bot rehearsal (`out/ws6/run.md`), 5 of 6 landed within 9 minutes of the hello prompt
and the slowest at 16; half the room had touchdown at 6.5 minutes. Expect 15-25 minutes here:
the first 5-10 go on setup. Pit stop 1 will likely be due before 0:45.


- Say: "Paste the prompt from `/mission` into your agent. From liftoff on, you write the
  prompts: `/mission` gives the goal and a hint."
- Projector: `/standings`, and now and then `/watch`.
- Every ~10 minutes, how many are in:

  ```fish
  scripts/arena.fish status
  ```

- Once 10 or more participants have bots connected, free the seats (3 arenas x 4 = 12):

  ```fish
  scripts/arena.fish unhouse
  ```

  With fewer than 8, keep them: milestones 6 to 9 need a round of at least 2 taxis.
- Lessons 1 (at liftoff) and 2 (at touchdown) open by themselves on each `/lessons` page.

### ~0:45 Pit stop 1: evidence before guessing (~10 min)

- When: T1 prints `pit stop 1 is due: ...` and `/watch` shows PIT STOP 1 DUE in pink, or at
  0:45, whichever is first.
- Run it: [Pit stop 1](#1-evidence-before-guessing-10-min-at-touchdown) below.
- End it, which opens lesson 3 for everyone and clears it from `/watch`:

  ```fish
  scripts/arena.fish lesson 3
  ```

### ~0:55 Break, 10 minutes

- Bots keep racing. Say: "Leave your bot running."

### 1:05–1:50 Chapter 2, Earn: fare, paid, clean

The long tail starts here: in the rehearsal one participant spent 18 minutes on fare, another
reached paid only after 70.


- Projector: `/standings`.
- `scripts/arena.fish status` now and then.

### ~1:35 Pit stop 2: fresh-context review (~15 min)

- When: T1 prints `pit stop 2 is due: ...`, or at 1:35.
- Run it: [Pit stop 2](#2-fresh-context-review-15-min-at-paid) below. Ask for the tally now
  (show of hands: "whose fresh session found a real bug the author missed?") and write it down
  for the debrief.
- End it:

  ```fish
  scripts/arena.fish lesson 4
  ```

### 1:50–2:20 Chapter 3, Compete: hat_trick, beat_greedy, beat_pilot

In the rehearsal 2 of 6 beat greedy in 88 minutes of racing, and nobody beat pilot.


- `/standings` shows the two benchmarks to beat for the current map.

### ~2:00 The map twist and pit stop 3 (~5 min)

Classic is open space with a few loose wall blocks; cavern has a shelf under every pad and
long walls across. A bot that flies straight lines got away with it on classic and hits walls
on cavern; one that plans a path from the map keeps earning (pilot loses ~18%, greedy crashes
2.5 times as often).

1. Switch the next wave to cavern (T1 answers `next wave: cavern`):

   ```fish
   scripts/arena.fish map cavern
   ```

2. Projector: `/watch`. Its INSERT COIN splash names CAVERN; the wave after the current one
   plays it (at most ~85 s).
3. Once cavern is flying, every bot has had it in a `round_start`: run
   [Pit stop 3](#3-is-it-a-general-bot-5-min-room-wide-at-the-map-twist) below.
4. End it:

   ```fish
   scripts/arena.fish lesson 5
   ```

### 2:20–2:30 Debrief

1. Projector: `/standings`, the podium.
2. The author vs fresh review tally from pit stop 2.
3. An exam objective named for each failure seen.
4. A 60-second "defend your design" from two or three volunteers.
5. The canyon demo, a third map nobody raced on: closed chambers, corridors, overhangs. One
   round of the pilot house bot against three greedy ones, as a replay page (no internet
   needed; click to start, `4` plays at 4x):

   ```fish
   scripts/sim.fish -m canyon -b pilot,greedy,greedy,greedy -n 1
   ```

   Pilot plans paths and delivers 4 passengers ($370); the three greedy bots, flying straight
   lines, earn $0.
   Ask: "would yours survive this?" Not raced on purpose: most bots would sit at $0, and its
   benchmarks would hand out `beat_greedy` for one delivery.
6. Open the last lesson, then put all six on the projector:

   ```fish
   scripts/arena.fish lesson 6
   ```

   Projector: the `/lessons?key=...` link from step 3 of the setup (`scripts/arena.fish lesson`
   prints it again in T1).

### After

```fish
scripts/arena.fish down
```

The race (standings, log, replays) stays in `out/arena/8000/`; the next `up --fresh` archives it.

### When something goes wrong

| What | Do |
| --- | --- |
| A bot floods, spams names or misbehaves | `scripts/arena.fish kick NAME`; if it comes back, `scripts/arena.fish ban NAME` (`unban NAME` lifts it) |
| A participant gets `name_taken` | an old copy of their bot is still running: have them stop it (or `kick NAME`), then start one |
| A participant gets `bad_name` | a name with an accent or a space: 1-12 of `A-Z a-z 0-9 _ -` |
| Someone can't connect | same WiFi as the server? the URL from `/mission`, not `localhost` |
| The arena died | T1: `scripts/arena.fish up -m classic -m cavern -b classic:greedy=428 -b classic:pilot=710 -b cavern:greedy=536 -b cavern:pilot=662` (the `-b` values are the benchmarks every start simulates anyway, so it skips that; no `--fresh`: the race and the revealed lessons are kept; the lessons key is new, `scripts/arena.fish lesson` prints it); the house bots reconnect by themselves |
| `/watch` keeps showing a pit stop you already held | open its lesson: `scripts/arena.fish lesson 3` (or `4`) |
| Lost track of which lessons are open | `scripts/arena.fish lesson` |

The fast ones keep going through the chapter talks; nothing is gated by the clock.

## Pit stops

The bot keeps racing during a pit stop, so nothing is lost by stopping. Pit stops 1 and 2 are
for the whole room, called when half the participants have reached their milestone. The
participants are your head count (`scripts/arena.fish room N`, setup step 5) or the bots in the
standings, whichever is more; house bots and banned names never count:

- `/watch` shows the next one under the leaderboard and on the splash ("PIT STOP 1 AT
  TOUCHDOWN, 2 OF 6 NEEDED"), and it turns pink, "DUE", at half the room. `serve` says it too.
- Call it then, or at the clock time (0:45, 1:35) if the room is slow.
- After it, open its lesson for everyone (`scripts/arena.fish lesson 3`, then `lesson 4`): that
  also ends the pit stop on `/watch`, which moves on to the next.
- Pit stop 3 has no milestone: you call it at the map twist.

### 1. Evidence before guessing (~10 min, at touchdown)

- Participants have only their bot and the brief, no spacetaxi. Say: "Have your agent build a
  local copy of the game from what your bot has seen (the rules in `welcome`, the map in
  `round_start`, the ticks it logged), and an eval that plays 100 rounds in seconds, reporting
  money, crashes and missed deadlines."
- From here on a build goes into the arena only if it beats the last one.
- Rehearsal case: Feri compared builds on single live rounds and chased noise.
- Exam objectives 4.1, 4.3, 4.6.

### 2. Fresh-context review (~15 min, at paid)

- Open a second terminal and a brand-new `claude` in the same folder. Give it only `bot.py`,
  the brief and the logs. Prompt:
  "Review bot.py against brief.md. List the bugs that lose money or cause crashes. For each,
  give the line and a concrete situation where it goes wrong. Don't edit anything."
- Don't tell it what you think is wrong.
- Also ask the author session the same question, for the A vs B tally.
- Paste the fresh findings into the author session; have it verify each against the logs or an
  offline run before fixing.
- Rehearsal result (6 Haiku bots, blind verifier): fresh findings were real 65% of the time,
  the author's 43%; the costliest defect was found only by a fresh reviewer. Authors reviewed
  their memory of the bot, citing bugs they had already fixed.
- The line to say: "the author's context holds history, not the current state".
- Exam objectives 4.4, 1.4, 5.3.

### 3. Is it a general bot? (~5 min, room-wide, at the map twist)

- Add the new map to the eval and run it before changing any code.
- Look at whose bot held up and whose was tuned to classic.
- Teaches test-set coverage (4.1).

## Lessons

The take-home of the workshop: six lessons on `/lessons` (text in `docs/lessons.md`). Each opens
after the experience that teaches it, never up front: it names what the participant just went
through, and nobody can one-shot the event from them.

- **1. Say what done looks like, not how:** opens at liftoff, after the first prompts of their own.
- **2. Let the agent check its own work:** opens at touchdown, after the landing struggle.
- **3. Make the loop fast, and measure:** opens at fare, after pit stop 1's offline eval.
- **4. Get fresh eyes:** opens at clean, after pit stop 2's fresh review.
- **5. Don't overfit to your test:** reveal it after the map twist and pit stop 3:
  `scripts/arena.fish lesson 5`.
- **6. Put the model where there is time for it:** reveal it in the debrief:
  `scripts/arena.fish lesson 6`, then close on all six.

To review them, and for the projector at the end, open `/lessons?key=KEY`: every lesson,
whatever was revealed. `serve` prints the link at the start, and `scripts/arena.fish lesson`
sends it to `up`'s output. Keep the key off the projector until the end.

A lesson opens per bot name, from that bot's milestones, or for everyone with `lesson N`
(lessons 1 to N, kept in the standings file). The server sends a locked lesson's text to no one,
so an agent reading the page can't get ahead.

## Optional lane: the tuner

For the fast ones from milestone 7. A separate process runs `claude -p` between rounds, reads
the last rounds' log, proposes new values for the bot's `params.json`, and writes them only if
the offline eval says they win. The bot itself stays deterministic: a move must arrive within
75 ms, too fast for a model call per tick.

## Open

- The pit stops show on `/watch`, not in `/mission` yet. Planned: a pit stop step that must be ticked before
  the next goal shows, so the fastest players don't skip them.
