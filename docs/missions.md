# space taxi: missions

The milestones of `docs/rules.md` as missions, one at a time, for the `/mission` page: one
section per milestone in the rules' order, each with a one-line goal and a hint, and a ready
prompt for the first three. In a prompt, `NAME` becomes the participant's name and `HOST:PORT`
the server's address.

## hello
Goal: Get your bot connected under your first name.
Hint: The smallest bot that connects and answers every tick with no thrust is enough. Ship it, then improve it.
Prompt: Read brief.md (download it from http://HOST:PORT/brief.md). Write the smallest Python bot that connects to ws://HOST:PORT/bot as NAME and answers every tick with no thrust. Run it and leave it running.

## liftoff
Goal: Keep your taxi in the air for 50 ticks without crashing.
Hint: Don't go anywhere yet. Hovering is enough: fire the main engine whenever the taxi is falling.
Prompt: Change the bot so it hovers in place: fire the main engine when the taxi is falling, otherwise let gravity pull. Restart it and keep it running.

## touchdown
Goal: Land on a pad.
Hint: Pick the nearest pad, get above it, then sink slowly. A landing that is too fast is a crash.
Prompt: Make the bot fly to the nearest pad and land on it slowly. Keep hovering as the fallback when it is unsure. Restart it and keep it running.

## fare
Goal: Pick up a passenger.
Hint: Passengers wait on pads. Every tick tells you which pad they are on: land there.

## paid
Goal: Deliver a passenger.
Hint: Once aboard, a passenger wants a particular pad. Fly there and land.

## clean
Goal: A whole round with at least one delivery and no crash.
Hint: Open /watch and see where your taxi crashes. Slower near walls is often faster overall. Counts only in rounds with at least 2 taxis.

## hat_trick
Goal: Deliver 3 passengers in one round.
Hint: No idling: after a delivery, go straight for the next passenger. Counts only in rounds with at least 2 taxis.

## beat_greedy
Goal: Earn more money in one round than the greedy house bot's benchmark.
Hint: Crashes cost money. A safe route that always lands beats a fast one that sometimes crashes. Counts only in rounds with at least 2 taxis.

## beat_pilot
Goal: Earn more money in one round than the pilot house bot's benchmark.
Hint: The pilot plans its route around the walls. Yours will have to as well. Counts only in rounds with at least 2 taxis.
