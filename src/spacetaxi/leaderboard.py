"""The arena's leaderboard: totals per name, from the `round_end` records of the server's log."""

import json


class Leaderboard:
    """Totals per name, from `round_end` log records."""

    def __init__(self, house=(), banned=None):
        self.rows: dict[str, dict] = {}
        self.house = {n.lower() for n in house}  # names marked as house bots, ignoring case
        self.banned = banned if banned is not None else set()  # lowercased, left out of table

    def add(self, record: dict) -> None:
        for r in record["results"]:
            row = self.rows.setdefault(r["id"], {"rounds": 0, "money": 0, "deliveries": 0,
                                                 "crashes": 0, "ticks": 0, "late": 0})
            row["rounds"] += 1
            if r.get("human"):
                row["human"] = True
            for key in ("money", "deliveries", "crashes", "ticks", "late"):
                row[key] += r.get(key, 0)

    def table(self) -> list[dict]:
        """One row per name, best average money first."""
        out = []
        for name, row in self.rows.items():
            if name.lower() in self.banned:
                continue
            out.append({
                "name": name,
                "rounds": row["rounds"],
                "total_money": row["money"],
                "avg_money": round(row["money"] / row["rounds"], 2),
                "deliveries": row["deliveries"],
                "crashes": row["crashes"],
                "late_pct": round(100 * row["late"] / row["ticks"], 1) if row["ticks"] else 0.0,
                "human": row.get("human", False),
                "house": name.lower() in self.house,
            })
        out.sort(key=lambda r: (-r["avg_money"], -r["total_money"], r["name"]))
        return out

    def load(self, path) -> tuple[int, int]:
        """Add every `round_end` record of the log at `path` (a JSON line each); returns the
        highest round and wave numbers in it, 0 when it has none."""
        rounds = wave = 0
        with open(path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                record = json.loads(line)
                self.add(record)
                rounds = max(rounds, record.get("round", 0))
                wave = max(wave, record.get("wave", 0))
        return rounds, wave
