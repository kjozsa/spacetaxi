# space taxi: lessons

The six lessons of the workshop for the `/lessons` page, one section per lesson in order. A
lesson shows on a participant's page once their bot reaches its `After:` milestone, or for the
whole room once the facilitator reveals it (`lesson N` in `serve`'s terminal,
`scripts/arena.fish lesson N`), which also shows every lesson before it. `After: start` shows
from the beginning; `After: reveal` waits for the facilitator.

`Lesson:` is the rule, written about agents in general: the line to take home. `Why:` is the
reason, with evidence from the rehearsals. `Try:` is what to do with it now, in this workshop.

## 1
After: start
Lesson: Say what done looks like, not how to get there.
Why: You know the goal; the agent knows a thousand ways to write the code. Steps you dictate are steps it can't improve on, and a goal without a finish line lets it wander.
Try: Give each prompt the result, the hard limits (the brief, the 75 ms deadline) and how you will check it. Leave out the algorithm.

## 2
After: liftoff
Lesson: Let the agent check its own work.
Why: An agent that can see the result fixes its own mistakes. One that can't asks you to look, and you become its test suite.
Try: Have the bot log what it does each round, and have the agent read those logs to prove the bot did what it claims before it tells you it's done.

## 3
After: touchdown
Lesson: Make the loop fast, and measure, don't eyeball.
Why: A live round takes 75 seconds, and one round is noise. In rehearsal, a participant who compared builds on single live rounds chased noise for half an hour.
Try: Have the agent build a local copy of the game from what the bot has seen, and an eval that plays 100 rounds in seconds. A build goes live only if it beats the last one.

## 4
After: paid
Lesson: Get fresh eyes on it.
Why: The session that wrote the code remembers its history, not the code as it is now. In rehearsal, a fresh session's bug reports were real 65% of the time, the author's 43%, and only a fresh reviewer found the costliest bug.
Try: Start a new agent session in the same folder. Give it bot.py, the brief and the logs, and ask for the bugs that lose money or crash, each with its line and a situation where it goes wrong. Don't say what you suspect. Then have the author check each finding before fixing it.

## 5
After: reveal
Lesson: Don't overfit to your test.
Why: A bot that wins your eval may only have learned your eval. A new map shows which bots learned to fly and which learned the cave.
Try: Add the new map to your eval and run it before you change any code.

## 6
After: reveal
Lesson: Put the model where there is time for it.
Why: A move is due every 100 ms; a model call takes seconds. Fast, fixed code flies the taxi. The model works where time allows: writing the code, reviewing it, tuning it between rounds.
Try: Back at work: where does a model sit in a loop that needs an answer faster than a model can give one?
