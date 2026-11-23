# Workshop agenda

2.5 hours, at most 12 participants. The facilitator's plan: what happens when, and what each pit
stop teaches. Participants never see this page; they follow `/mission`.

## Timeline

- **0:00–0:10 Intro.** The slides: `docs/slides/intro.html`.
- **0:10–0:20 Fly by hand** on `/fly` to feel the physics. Meanwhile, in two terminals:
  `scripts/arena.fish up --fresh -m cavern -m classic`, then `scripts/arena.fish house` and
  `scripts/arena.fish open --card`.
- **0:20–0:55 Chapter 1, Get flying:** hello, liftoff, touchdown.
  The first prompt sets the contract: start from `uv run spacetaxi new-bot bot.py` and fly it
  with `spacetaxi connect`. Without it the offline eval can't load the bot.
- **Pit stop 1** at touchdown.
- **~1:05 Break**, 10 minutes.
- **1:15–1:55 Chapter 2, Earn:** fare, paid, clean.
- **Pit stop 2** at paid.
- **1:55–2:20 Chapter 3, Compete:** hat_trick, beat_greedy, beat_pilot.
- **~2:05 The map twist:** `scripts/arena.fish map classic`.
- **Pit stop 3**, room-wide, at the twist.
- **2:20–2:30 Debrief:**
  - the podium;
  - the author vs fresh review tally;
  - an exam objective named for each failure seen;
  - a 60-second "defend your design" from a few volunteers.

The fast ones keep going through the chapter talks; nothing is gated by the clock.

## Pit stops

The bot keeps racing during a pit stop, so nothing is lost by stopping.

### 1. Evidence before guessing (~10 min, at touchdown)

- Have the agent write an offline eval:
  `uv run spacetaxi run --bots ./bot.py,pilot --rounds 100 --json`, reporting money, crashes and
  missed deadlines.
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
- Look at whose bot held up and whose was tuned to cavern.
- Teaches test-set coverage (4.1).

## Optional lane: the tuner

For the fast ones from milestone 7. A separate process runs `claude -p` between rounds, reads
the last rounds' log, proposes new values for the bot's `params.json`, and writes them only if
the offline eval says they win. The bot itself stays deterministic: a move must arrive within
75 ms, too fast for a model call per tick.

## Open

- The pit stops are not in `/mission` yet. Planned: a pit stop step that must be ticked before
  the next goal shows, so the fastest players don't skip them.
