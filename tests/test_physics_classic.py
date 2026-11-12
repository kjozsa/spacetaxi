"""End-to-end smoke check of the physics on the bundled classic map."""

import json
import random

from spacetaxi.maps import PAD_IDS, WALL, load_map
from spacetaxi.sim import Rules, World


def run(seed):
    world = World(load_map("classic"), seed=seed, players=["a", "b", "c"])
    rng = random.Random(1)
    states = []
    for _ in range(Rules().round_ticks):
        world.step({p: {"ax": rng.choice([-1, 0, 1]), "ay": rng.choice([-1, 0, 1])} for p in "abc"})
        states.append(json.dumps(world.state(), sort_keys=True))
    return world, states


def test_full_round_on_classic_map_is_valid_and_deterministic():
    world, first = run(5)
    _, second = run(5)
    assert first == second
    r = Rules()
    for state in first:
        for t in json.loads(state)["taxis"]:
            assert abs(t["vx"]) <= r.max_speed and abs(t["vy"]) <= r.max_speed
            # the taxi's box never overlaps a solid cell
            for row in world._box_cells(t["fx"], t["fy"]):
                assert all(c != WALL and c not in PAD_IDS for c, _, _ in row), t
