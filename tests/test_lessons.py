"""The /lessons page: docs/lessons.md, a lesson opened per bot by its milestone or for everyone by
the operator's `lesson N`, and no locked lesson's text anywhere a browser (or an agent) can read."""

import json
from pathlib import Path

from spacetaxi.maps import load_map
from spacetaxi.milestones import MILESTONES
from spacetaxi.server import ArenaServer
from spacetaxi.sim import Rules
from spacetaxi.viewer import parse_lessons

DOCS = Path(__file__).resolve().parent.parent / "docs"
LESSONS = parse_lessons((DOCS / "lessons.md").read_text(encoding="utf-8"))
TEXT_KEYS = ("lesson", "why", "try")


def server(standings=None) -> ArenaServer:
    return ArenaServer({"cavern": load_map("cavern")}, Rules(), 60, seed=1, standings=standings)


def get(s: ArenaServer, path: str) -> dict:
    status, kind, body = s.http(path)
    assert status == 200 and kind == "application/json"
    return json.loads(body)


def opened(data: dict) -> list[int]:
    return [l["n"] for l in data["lessons"] if l["open"]]


def award(s: ArenaServer, name: str, *milestones: str) -> None:
    s.standings.awards[name] = {m: {"place": 1, "points": 25} for m in milestones}


def test_the_lessons_doc_has_six_numbered_lessons_each_opened_by_start_a_milestone_or_reveal():
    assert [l["n"] for l in LESSONS] == [1, 2, 3, 4, 5, 6]
    for l in LESSONS:
        assert all(l[k] for k in TEXT_KEYS), l["n"]
        assert l["after"] in ("start", "reveal", *MILESTONES), l["n"]
    assert LESSONS[0]["after"] == "start"


def test_the_page_holds_no_lesson_text():
    page = server().http("/lessons")[2].decode("utf-8")
    for l in LESSONS:
        for k in TEXT_KEYS:
            assert l[k] not in page, (l["n"], k)


def test_without_a_bot_only_the_start_lessons_open_and_a_locked_one_carries_no_text():
    data = get(server(), "/lessons.json")
    assert opened(data) == [l["n"] for l in LESSONS if l["after"] == "start"]
    for l in data["lessons"]:
        if not l["open"]:
            assert set(l) == {"n", "after", "open"}
    assert all(l[k] not in json.dumps(data) for l in LESSONS[1:] for k in TEXT_KEYS)


def test_a_bot_opens_the_lessons_of_the_milestones_it_reached_ignoring_case():
    s = server()
    award(s, "Anna", "hello", "liftoff", "touchdown")
    want = [l["n"] for l in LESSONS if l["after"] in ("start", "hello", "liftoff", "touchdown")]
    assert opened(get(s, "/lessons/anna.json")) == want
    assert opened(get(s, "/lessons/ANNA.json")) == want
    assert opened(get(s, "/lessons/bob.json")) == [1]


def test_reveal_lessons_stay_locked_until_the_operator_reveals_them():
    s = server()
    award(s, "anna", *MILESTONES)
    assert opened(get(s, "/lessons/anna.json")) == [l["n"] for l in LESSONS if l["after"] != "reveal"]


def test_lesson_n_opens_the_first_n_for_everyone_and_is_kept_in_the_standings_file(tmp_path):
    path = tmp_path / "standings.json"
    s = server(path)
    assert s.command("lesson") == "lessons revealed to everyone: 0 of 6"
    assert s.command("lesson 5") == "lessons revealed to everyone: 5 of 6"
    data = get(s, "/lessons.json")
    assert opened(data) == [1, 2, 3, 4, 5] and data["revealed"] == 5
    assert LESSONS[5]["lesson"] not in json.dumps(data)
    assert opened(get(server(path), "/lessons/nobody.json")) == [1, 2, 3, 4, 5]
    assert (s.command("lesson 7") or "").startswith("lesson: a number from 0 to 6")
    assert (s.command("lesson x") or "").startswith("lesson: a number")
    assert s.command("lesson 0") == "lessons revealed to everyone: 0 of 6"
    assert opened(get(s, "/lessons.json")) == [1]


def test_a_path_that_is_not_a_bot_name_is_not_found():
    s = server()
    for path in ("/lessons/.json", "/lessons/a b.json", "/lessons/abcdefghijklm.json",
                 "/lessons/../x.json", "/lessons/anna"):
        assert s.http(path) is None, path
