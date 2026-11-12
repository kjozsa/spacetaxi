"""A scripted bot for the viewer's debug overlay: never thrusts, its `debug` is plain text."""


def move(state: dict) -> dict:
    return {"ax": 0, "ay": 0, "debug": "just text"}
