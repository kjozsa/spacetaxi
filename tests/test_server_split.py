"""The arena server's modules: `spacetaxi.plan` stays free of asyncio and sockets, and the names
other code imports from `spacetaxi.server` still resolve."""

import ast
import random
from pathlib import Path

from spacetaxi import limits, plan, server

PLAN_NAMES = ("plan_order", "replan", "plan_join", "plan_leave", "rebalance", "deal")


def test_plan_module_has_no_asyncio_or_socket():
    tree = ast.parse(Path(plan.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        assert not isinstance(node, (ast.AsyncFunctionDef, ast.Await))
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add("." * node.level + (node.module or ""))
    assert imported == {"itertools"}


def test_arena_server_uses_the_plan_module():
    for name in PLAN_NAMES:
        assert getattr(server.ArenaServer, name) is getattr(plan.Plan, name)
    assert server.group_sizes is plan.group_sizes


def test_plan_works_without_a_server():
    said = []
    p = plan.Plan()
    p.rng, p.max_arenas, p.say = random.Random(1), 1, said.append
    p.wave, p.next_map = 0, "cavern"
    p.init_plan()
    for name in "abcde":
        p.plan_join(name)
    assert p.plan_arenas == [["a", "b", "c", "d"]]
    assert p.plan_waitlist == ["e"]
    assert p.places()["a"] == (1, 0)
    assert p.places()["e"] == (5, 1)
    p.plan_leave("b")
    assert sorted(p.plan_order()) == ["a", "c", "d", "e"]
    assert p.plan_waitlist == []
    assert said[-1] == "next wave 1 on cavern: a, c, d, e"


def test_server_still_exports_its_names():
    for name in ("Arena", "ArenaServer", "Bot", "Leaderboard", "parse", "group_sizes", "NAME",
                 "PROTOCOL", "ARENA_SIZE", "MAX_ARENAS", "HELLO_TIMEOUT", "BOT_MAX_MESSAGE",
                 "SPECTATE_MAX_MESSAGE", "FLOOD_MESSAGES", "FLOOD_BYTES", "MAX_BOTS_PER_IP",
                 "MAX_BOTS", "OUTBOX_TICKS"):
        assert hasattr(server, name), name
    assert server.parse is limits.parse
