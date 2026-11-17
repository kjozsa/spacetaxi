# space taxi: the milestone race

## The server

- Your bot connects to `ws://HOST:PORT/bot`. Connect under **your first name**.
- The brief for your agent: `http://HOST:PORT/brief` (download it as `http://HOST:PORT/brief.md`).
- The live view: `http://HOST:PORT/watch`.
- The standings: `http://HOST:PORT/standings`.

## The milestones

1. `hello`: your bot connects and says hello.
2. `liftoff`: your taxi flies 50 ticks in a round without crashing.
3. `touchdown`: your taxi lands on a pad.
4. `fare`: your taxi picks up a passenger.
5. `paid`: your taxi delivers a passenger.
6. `clean`: a round with at least 1 delivery and no crash.
7. `hat_trick`: 3 deliveries in one round.
8. `beat_greedy`: more money in a round than the greedy house bot.
9. `beat_pilot`: more money in a round than the pilot house bot.

Milestones 6 to 9 count only in rounds with at least 2 taxis.

The first ten to reach each milestone score **25, 18, 15, 12, 10, 8, 6, 4, 2, 1** points.

## Rules of thumb

- Points go to whoever gets there first, not to the best bot.
- Your bot keeps playing while your agent works, so leave it connected.
- A broken bot scores nothing, so ship small and often.

## Your first prompt

```
Read brief.md. Write the smallest Python bot that connects as Anna and answers every tick with no thrust. Run it and leave it running.
```
