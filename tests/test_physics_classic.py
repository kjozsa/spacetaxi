"""End-to-end smoke check of the physics on the bundled classic map."""

import json
import random

from spacetaxi.maps import load_map
from spacetaxi.sim import World


def run(seed):
    world = World(load_map("classic"), seed=seed, players=["a", "b", "c"])
    rng = random.Random(1)
    states = []
    for _ in range(375):
        world.step({p: {"ax": rng.choice([-1, 0, 1]), "ay": rng.choice([-1, 0, 1])} for p in "abc"})
        states.append(json.dumps(world.state(), sort_keys=True))
    return world, states


def test_full_round_on_classic_map_is_valid_and_deterministic():
    world, first = run(5)
    _, second = run(5)
    assert first == second
    for t in world.state()["taxis"]:
        assert abs(t["vx"]) <= 3 and abs(t["vy"]) <= 3
        assert world.map.rows[t["y"]][t["x"]] not in "#ABCDEFGHIJKLMNOPQRSTUVWXYZ"
