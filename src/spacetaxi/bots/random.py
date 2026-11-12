"""Thrusts uniformly at random. Uses the global `random` module, which the runner seeds."""

import random


def move(state: dict) -> dict:
    return {"ax": random.randint(-1, 1), "ay": random.randint(-1, 1)}
