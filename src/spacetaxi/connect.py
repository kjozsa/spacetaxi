"""`spacetaxi connect`: carry a local bot to the arena server, so participants never write
network code.

The bot is the same module `spacetaxi run` loads (`welcome`, `start`, `move`). The client says
`hello`, calls `welcome(info)` on `welcome`, `start(info)` on `round_start` and `move(state)` on
every `tick`, and sends the answer as a `move` for that tick. A hook that raises prints its
traceback (a raising `move` once per round) and the tick is answered with no thrust. A lost
connection is retried every `retry` seconds until Ctrl-C; an `error` the bot cannot fix by
retrying (a bad or taken name, another protocol) ends the run.
"""

import asyncio
import json
import os
import traceback
from types import ModuleType

from . import ws
from .runner import COAST, is_valid_move

PROTOCOL = 1
DEFAULT_SERVER = "ws://localhost:8000/bot"
RETRY_SECONDS = 2.0

FATAL = {"name_taken", "bad_name", "bad_protocol"}


def server_url(given: str | None) -> str:
    """The server to connect to: `given`, else `$SPACETAXI_SERVER`, else the local default."""
    return given or os.environ.get("SPACETAXI_SERVER") or DEFAULT_SERVER


class Fatal(Exception):
    """The server refused the bot for a reason retrying does not fix."""


def error_text(name: str, error: dict) -> str:
    """A server `error` in plain words."""
    code = error.get("code")
    if code == "name_taken":
        return f"the name {name} is taken: another bot connected with it, pick another --name"
    if code == "bad_name":
        return f"the name {name!r} is not allowed: use 1 to 12 characters of A-Z a-z 0-9 _ -"
    if code == "bad_protocol":
        return f"the server speaks another protocol ({error.get('message', '')}); " \
               f"update spacetaxi"
    return f"the server refused a message: {error.get('message', code)}"


def describe_results(results: list[dict], me: str) -> str:
    ranked = sorted(results, key=lambda r: -r["money"])
    parts = []
    for place, r in enumerate(ranked, 1):
        who = f"{r['id']} (you)" if r["id"] == me else r["id"]
        parts.append(f"{place}. {who} {r['money']} money, {r['deliveries']} deliveries, "
                     f"{r['crashes']} crashes")
    return "; ".join(parts)


class Client:
    """One bot's link to the arena; `run()` keeps it connected until cancelled."""

    def __init__(self, url: str, name: str, bot: ModuleType, say=print,
                 retry: float = RETRY_SECONDS):
        self.url = url
        self.name = name
        self.bot = bot
        self.say = say
        self.retry = retry
        self.round = None
        self.connected = False  # the current session got past the handshake
        self.move_failed = False  # a move raised (or was invalid) in this round already

    def call(self, hook: str, info: dict) -> None:
        fn = getattr(self.bot, hook, None)
        if callable(fn):
            try:
                fn(info)
            except Exception:
                self.say(f"your bot's {hook}() raised:\n{traceback.format_exc().rstrip()}")

    def answer(self, state: dict) -> dict:
        """The `move` message for a `tick`: the bot's move, or no thrust if it fails."""
        reply = {"type": "move", "tick": state["tick"]}
        try:
            move = self.bot.move(state)
        except Exception:
            if not self.move_failed:
                self.move_failed = True
                self.say(f"your bot's move() raised at tick {state['tick']} (coasting; shown "
                         f"once per round):\n{traceback.format_exc().rstrip()}")
            return {**reply, **COAST}
        if not is_valid_move(move):
            if not self.move_failed:
                self.move_failed = True
                self.say(f"your bot's move() returned {move!r} at tick {state['tick']}, not "
                         f'{{"ax": -1|0|1, "ay": -1|0|1}} (coasting; shown once per round)')
            return {**reply, **COAST}
        reply.update(ax=move["ax"], ay=move["ay"])
        if "debug" in move:
            reply["debug"] = move["debug"]
        return reply

    async def session(self) -> None:
        """One connection, until the server closes it. Raises `Fatal` on a refusal."""
        sock = await ws.connect(self.url)
        self.connected = True
        try:
            self.say(f"connected to {self.url} as {self.name}")
            await sock.send(json.dumps({"type": "hello", "name": self.name,
                                        "protocol": PROTOCOL}))
            while (text := await sock.recv()) is not None:
                try:
                    msg = json.loads(text)
                except (ValueError, TypeError):
                    continue
                if not isinstance(msg, dict):
                    continue
                await self.handle(sock, msg)
        finally:
            await sock.close()

    async def handle(self, sock: ws.WebSocket, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "tick":
            reply = self.answer(msg)
            try:
                await sock.send(json.dumps(reply, separators=(",", ":")))
            except (TypeError, ValueError):  # a debug that is not JSON: send the move without it
                reply.pop("debug", None)
                await sock.send(json.dumps(reply, separators=(",", ":")))
        elif kind == "welcome":
            self.call("welcome", msg)
        elif kind == "queued":
            self.say(f"queued: position {msg.get('position')} of {msg.get('waiting')} waiting")
        elif kind == "round_start":
            self.round = msg.get("round")
            self.move_failed = False
            others = [p for p in msg.get("players", []) if p != self.name]
            self.say(f"round {self.round} starts: you vs "
                     + (", ".join(others) if others else "nobody (flying alone)"))
            self.call("start", msg)
        elif kind == "round_end":
            self.say(f"round {msg.get('round')} over: "
                     + describe_results(msg.get("results", []), self.name))
        elif kind == "error":
            text = error_text(self.name, msg)
            if msg.get("code") in FATAL:
                raise Fatal(text)
            self.say(f"server error: {text}")

    async def run(self) -> None:
        """Stay connected: reconnect every `retry` seconds after a lost connection, until
        cancelled. Raises `Fatal` when the server refuses the bot for good."""
        retrying = f"retrying every {self.retry:g} s (Ctrl-C stops)"
        down = False  # the outage has been reported, so failed retries stay quiet
        while True:
            self.connected = False
            try:
                await self.session()
            except (OSError, ConnectionError, TimeoutError) as exc:
                if not self.connected:
                    if not down:
                        reason = str(exc) or type(exc).__name__
                        self.say(f"cannot reach {self.url} ({reason}); {retrying}")
                    down = True
                    await asyncio.sleep(self.retry)
                    continue
            self.say(f"lost the connection to {self.url}; {retrying}")
            down = True
            await asyncio.sleep(self.retry)
