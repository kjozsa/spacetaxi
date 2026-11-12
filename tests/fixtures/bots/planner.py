"""A scripted bot for the viewer's debug overlay: never thrusts, always says `hi` and shows a
two-cell planned path."""


def move(state: dict) -> dict:
    return {"ax": 0, "ay": 0, "debug": {"say": "hi", "path": [[5, 5], [6, 5]]}}
