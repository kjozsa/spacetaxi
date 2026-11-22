# space taxi: missions

The milestones of `docs/rules.md` as missions, one at a time, for the `/mission` page: one
section per milestone in the rules' order, each with a one-line goal and a hint, and a ready
prompt for the first one only: after that, the participant writes the prompts. In a prompt, `NAME` becomes the participant's name and `HOST:PORT`
the server's address.

## hello
Goal: Get your bot connected under your first name.
Hint: Ship the smallest bot that connects and keeps answering, then improve it while it runs.
Prompt: Read brief.md (download it from http://HOST:PORT/brief.md). Write the smallest Python bot that connects to ws://HOST:PORT/bot as NAME and answers every tick with no thrust. Run it and leave it running.

## liftoff
Goal: Keep your taxi in the air for 50 ticks without crashing.
Hint: What is the least your bot has to do to stay up? It doesn't have to go anywhere yet.

## touchdown
Goal: Land on a pad.
Hint: How slow is slow enough? The brief doesn't say. How will you find out?

## fare
Goal: Pick up a passenger.
Hint: Where do waiting passengers show up in the messages your bot already gets?

## paid
Goal: Deliver a passenger.
Hint: Once a passenger is aboard, how does your bot know where to take them?

## clean
Goal: A whole round with at least one delivery and no crash.
Hint: Where exactly does your taxi crash? Watch it on /watch before you ask for a fix. Counts only in rounds with at least 2 taxis.

## hat_trick
Goal: Deliver 3 passengers in one round.
Hint: What does your taxi do between two passengers, and how long does it take? Counts only in rounds with at least 2 taxis.

## beat_greedy
Goal: Earn more money in one round than the greedy house bot's benchmark.
Hint: Where does your money go: crashes, or time? Measure before you tune. Counts only in rounds with at least 2 taxis.

## beat_pilot
Goal: Earn more money in one round than the pilot house bot's benchmark.
Hint: What stands between your taxi and the pad it wants? Counts only in rounds with at least 2 taxis.
