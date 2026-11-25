"""The plan of the next wave: its arenas and its waitlist, every connected bot.

The next wave is planned early: when a wave starts, every connected bot is dealt into up to
`max_arenas` arenas of at most 4, as even as possible (7 bots play 4 and 3, 6 play 3 and 3),
the ones sitting the current wave out first; bots beyond that wait on the plan's waitlist. The
deal is a seeded shuffle that prefers bots who have met least so far and avoids repeating a
group of the previous wave, so groups mix and the leaderboard compares like with like. On an
idle server the plan starts with the first join. Joins and leaves change the plan as little as
they can (`rebalance`), and the next wave plays exactly as planned.

No asyncio and no sockets here: `Plan` is the arena server's plan bookkeeping, mixed into
`ArenaServer`, on names only.
"""

import itertools

ARENA_SIZE = 4
MAX_ARENAS = 3
DEAL_TRIES = 200  # shuffles tried per deal; the one whose groups have met least wins


def group_sizes(waiting: int, max_arenas: int = MAX_ARENAS) -> list[int]:
    """Arena sizes for a wave with `waiting` bots: at most 4 each, as even as possible, largest
    first; bots beyond `max_arenas` full arenas wait for the next wave."""
    n = min(waiting, ARENA_SIZE * max_arenas)
    if n <= 0:
        return []
    k = -(-n // ARENA_SIZE)
    return [n // k + (1 if i < n % k else 0) for i in range(k)]


class Plan:
    """The next wave's arenas and waitlist, made from every connected bot when a wave starts
    (and from the first join on an idle server), then changed only by joins and leaves.

    The class mixing it in sets `rng`, `max_arenas`, `say`, `wave` (the last wave started) and
    `next_map` (the next wave's map), and calls `init_plan` first."""

    def init_plan(self) -> None:
        self.plan_arenas: list[list[str]] = []
        self.plan_waitlist: list[str] = []
        self.said_plan = ""  # the plan as last printed
        self.met: dict[frozenset, int] = {}  # rounds each pair of names has shared
        self.last_groups: set[frozenset] = set()  # the previous wave's groups

    def plan_order(self) -> list[str]:
        """The plan's names in order: the next wave's arenas first, then the waitlist."""
        return [n for g in self.plan_arenas for n in g] + self.plan_waitlist

    def places(self) -> dict[str, tuple[int, int]]:
        """Each planned name's place in `plan_order` (from 1) and how many waves after the
        next one it plays in, if the plan keeps its seats."""
        seats = sum(len(g) for g in self.plan_arenas)
        return {name: (i, 0 if i <= seats else 1 + (i - seats - 1) // seats)
                for i, name in enumerate(self.plan_order(), 1)}

    def replan(self, names: list[str]) -> None:
        """A new plan from `names`, the first ones seated first: dealt as a wave (`deal`), the
        names beyond its seats wait in order."""
        self.plan_arenas = self.deal(names)
        seated = {n for g in self.plan_arenas for n in g}
        self.plan_waitlist = [n for n in names if n not in seated]
        self.plan_changed()

    def plan_join(self, name: str) -> None:
        """A joiner takes a free seat, or the end of the waitlist."""
        self.plan_waitlist.append(name)
        self.rebalance()

    def plan_leave(self, name: str) -> None:
        """A leaver's seat goes to the first on the waitlist."""
        for g in self.plan_arenas:
            if name in g:
                g.remove(name)
        if name in self.plan_waitlist:
            self.plan_waitlist.remove(name)
        self.rebalance()

    def rebalance(self) -> None:
        """Fit the plan to `group_sizes` of its names, moving as few as possible: the arenas
        get the sizes largest to largest, an arena over its size gives up its last names, and
        free seats are filled by those first, then from the head of the waitlist."""
        arenas = [list(g) for g in self.plan_arenas]
        sizes = group_sizes(len(self.plan_order()), self.max_arenas)
        target = [0] * len(arenas)
        for rank, i in enumerate(sorted(range(len(arenas)), key=lambda i: -len(arenas[i]))):
            target[i] = sizes[rank] if rank < len(sizes) else 0
        for size in sizes[len(arenas):]:
            arenas.append([])
            target.append(size)
        pool = []
        for g, size in zip(arenas, target):
            while len(g) > size:
                pool.append(g.pop())
        pool += self.plan_waitlist
        for g, size in zip(arenas, target):
            while len(g) < size:
                g.append(pool.pop(0))
        self.plan_arenas = [g for g in arenas if g]
        self.plan_waitlist = pool
        self.plan_changed()

    def plan_changed(self) -> None:
        """Print the plan when it differs from the one printed last."""
        if not self.plan_arenas:
            return
        text = (f"next wave {self.wave + 1} on {self.next_map}: "
                + " | ".join(", ".join(g) for g in self.plan_arenas))
        if self.plan_waitlist:
            text += "  waiting: " + ", ".join(self.plan_waitlist)
        if text != self.said_plan:
            self.said_plan = text
            self.say(text)

    def deal(self, names: list[str]) -> list[list[str]]:
        """Split `names` into a wave's groups (`group_sizes`; names beyond them are left out,
        they wait): of `DEAL_TRIES` seeded shuffles, the one whose groups repeat fewest of the
        previous wave's groups, then hold the most pairs who never met, then the fewest past
        meetings."""
        sizes = group_sizes(len(names), self.max_arenas)
        names = names[:sum(sizes)]  # the first ones play
        best, best_cost = None, None
        for _ in range(DEAL_TRIES):
            order = sorted(names)  # the shuffle depends on the seed, not on the queue order
            self.rng.shuffle(order)
            groups, at = [], 0
            for size in sizes:
                groups.append(order[at:at + size])
                at += size
            repeats = sum(frozenset(g) in self.last_groups for g in groups)
            met = [self.met.get(frozenset(pair), 0)
                   for g in groups for pair in itertools.combinations(g, 2)]
            cost = (repeats, -met.count(0), sum(met))
            if best_cost is None or cost < best_cost:
                best, best_cost = groups, cost
        return best

    def played(self, groups: list[list[str]]) -> None:
        """`groups` start as a wave: each pair in them has met once more, and they are the
        previous wave's groups from now on."""
        for g in groups:
            for pair in itertools.combinations(g, 2):
                key = frozenset(pair)
                self.met[key] = self.met.get(key, 0) + 1
        self.last_groups = {frozenset(g) for g in groups}
