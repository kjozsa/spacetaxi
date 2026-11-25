"""Who the arena server lets in: the checks of a bot's `hello` and the hostile-client limits.

Names ignore case: the first spelling seen (connected, or in the standings or the leaderboard)
keeps the name, and a `hello` with another spelling of it gets `name_taken` naming that
spelling; the server never renames a bot. A banned name gets `banned`.

Hostile clients (docs/rules.md, Limits): a bot's message over `BOT_MAX_MESSAGE` bytes gets
`too_big`, more than `FLOOD_MESSAGES` or `FLOOD_BYTES` in one tick `flooding` (`Flood`), and
the connection is closed; `max_bots_per_ip` and `max_bots` (None: no cap) cap the bots
connected at once (`too_many`, `full`), house bots aside.
"""

import json
import re

NAME = re.compile(r"[A-Za-z0-9_-]{1,12}")
PROTOCOL = 1
HELLO_TIMEOUT = 10  # seconds a new connection has to say hello
BOT_MAX_MESSAGE = 64 * 1024  # bytes: a bigger message from a bot gets `too_big` and is closed
SPECTATE_MAX_MESSAGE = 4 * 1024  # spectators send nothing
FLOOD_MESSAGES = 20  # more messages than this from one bot within one tick: `flooding`
FLOOD_BYTES = 128 * 1024  # more bytes than this from one bot within one tick: `flooding` too
MAX_BOTS_PER_IP = 4  # `serve`'s default for bots connected at once from one IP, house bots aside
MAX_BOTS = 40  # `serve`'s default for bots connected at once, house bots aside


def parse(text) -> dict | None:
    """A message as a dict; None when it is not a JSON object."""
    if not isinstance(text, str):
        return None
    try:
        msg = json.loads(text)
    except (ValueError, RecursionError):  # deeply nested JSON is no message either
        return None
    return msg if isinstance(msg, dict) else None


class Flood:
    """One bot's messages in the current tick-long window, against the flooding limits."""

    def __init__(self, tick_ns: int):
        self.tick_ns = tick_ns
        self.window, self.count, self.size = 0, 0, 0  # the window's start, messages, size

    def add(self, text, now: int) -> bool:
        """Count message `text` arriving at `now` (perf_counter_ns); True when it floods."""
        if now - self.window >= self.tick_ns:
            self.window, self.count, self.size = now, 0, 0
        self.count += 1
        self.size += (len(text.encode("utf-8")) if isinstance(text, str) and not text.isascii()
                      else len(text))  # bytes for a binary message
        return self.count > FLOOD_MESSAGES or self.size > FLOOD_BYTES


class Admission:
    """The hello checks of `ArenaServer`, mixed into it. The class mixing it in sets `bots`,
    `standings`, `leaderboard`, `house`, `max_bots` and `max_bots_per_ip`."""

    def spelling(self, name: str) -> str | None:
        """The spelling the server knows `name` by, ignoring case: a connected bot's, a
        standings row's or a leaderboard row's; None for a new name."""
        key = name.lower()
        for known in (*self.bots, *self.standings.awards, *self.leaderboard.rows):
            if known.lower() == key:
                return known
        return None

    def refuse(self, sock, hello: dict | None) -> tuple[str, str] | None:
        """Why `hello`, the first message on `sock`, is refused, as `(code, message)`; None to
        welcome it."""
        if hello is None or hello.get("type") != "hello":
            return "bad_message", "the first message must be hello"
        name = hello.get("name")
        if not isinstance(name, str) or not NAME.fullmatch(name):
            return "bad_name", "name: 1 to 12 characters of A-Z a-z 0-9 _ -"
        if type(hello.get("protocol")) is not int or hello["protocol"] != PROTOCOL:
            return "bad_protocol", f"this server speaks protocol {PROTOCOL}"
        if name.lower() in self.standings.banned:
            return "banned", f"name {name!r} is banned"
        if name in self.bots:
            return "name_taken", f"name {name!r} is already connected"
        spelling = self.spelling(name)
        if spelling is not None and spelling != name:
            return "name_taken", f"name {name!r} is taken as {spelling!r}; connect as {spelling!r}"
        if name.lower() in self.house:
            return None
        guests = [b for n, b in self.bots.items() if n.lower() not in self.house]
        if self.max_bots is not None and len(guests) >= self.max_bots:
            return "full", f"the arena is full ({self.max_bots} bots)"
        if self.max_bots_per_ip is not None and sum(
                b.sock.ip == sock.ip for b in guests) >= self.max_bots_per_ip:
            return "too_many", f"at most {self.max_bots_per_ip} bots from one address at once"
        return None
