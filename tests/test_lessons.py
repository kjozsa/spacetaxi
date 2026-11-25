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
    assert all(l["after"] != "start" for l in LESSONS)  # a lesson follows its experience


def test_lessons_open_at_liftoff_touchdown_fare_clean_then_by_reveal():
    assert [l["after"] for l in LESSONS] == ["liftoff", "touchdown", "fare", "clean", "reveal", "reveal"]


def test_the_page_holds_no_lesson_text():
    page = server().http("/lessons")[2].decode("utf-8")
    for l in LESSONS:
        for k in TEXT_KEYS:
            assert l[k] not in page, (l["n"], k)


def test_without_a_bot_none_is_open_and_a_locked_one_carries_no_text():
    data = get(server(), "/lessons.json")
    assert opened(data) == []
    for l in data["lessons"]:
        if not l["open"]:
            assert set(l) == {"n", "after", "open"}
    assert all(l[k] not in json.dumps(data) for l in LESSONS for k in TEXT_KEYS)


def test_a_bot_opens_the_lessons_of_the_milestones_it_reached_ignoring_case():
    s = server()
    award(s, "Anna", "hello", "liftoff", "touchdown")
    want = [1, 2]
    assert opened(get(s, "/lessons/anna.json")) == want
    assert opened(get(s, "/lessons/ANNA.json")) == want
    assert opened(get(s, "/lessons/bob.json")) == []


def test_reveal_lessons_stay_locked_until_the_operator_reveals_them():
    s = server()
    award(s, "anna", *MILESTONES)
    assert opened(get(s, "/lessons/anna.json")) == [l["n"] for l in LESSONS if l["after"] != "reveal"]


def test_lesson_n_opens_the_first_n_for_everyone_and_is_kept_in_the_standings_file(tmp_path):
    path = tmp_path / "standings.json"
    s = server(path)
    assert (s.command("lesson") or "").startswith("lessons revealed to everyone: 0 of 6; all of them")
    assert s.command("lesson 5") == "lessons revealed to everyone: 5 of 6"
    data = get(s, "/lessons.json")
    assert opened(data) == [1, 2, 3, 4, 5] and data["revealed"] == 5
    assert LESSONS[5]["lesson"] not in json.dumps(data)
    assert opened(get(server(path), "/lessons/nobody.json")) == [1, 2, 3, 4, 5]
    assert (s.command("lesson 7") or "").startswith("lesson: a number from 0 to 6")
    assert (s.command("lesson x") or "").startswith("lesson: a number")
    assert s.command("lesson 0") == "lessons revealed to everyone: 0 of 6"
    assert opened(get(s, "/lessons.json")) == []


def test_a_path_that_is_not_a_bot_name_is_not_found():
    s = server()
    for path in ("/lessons/.json", "/lessons/a b.json", "/lessons/abcdefghijklm.json",
                 "/lessons/../x.json", "/lessons/anna"):
        assert s.http(path) is None, path


def test_the_key_shows_every_lesson_whatever_was_revealed_and_a_wrong_key_nothing():
    s = server()
    s.address = "10.0.0.7:8000"
    url = s.lessons_url()
    assert url == f"http://10.0.0.7:8000/lessons?key={s.lessons_key}" and len(s.lessons_key) == 16
    assert url in (s.command("lesson") or "")
    data = get(s, f"/lessons-all/{s.lessons_key}.json")
    assert opened(data) == [1, 2, 3, 4, 5, 6] and data["all"]
    assert [l["lesson"] for l in data["lessons"]] == [l["lesson"] for l in LESSONS]
    assert s.http("/lessons-all/0000000000000000.json") is None
    assert s.lessons_key != server().lessons_key
    assert s.lessons_key not in s.http("/lessons")[2].decode("utf-8")


def test_pit_stops_come_from_the_lessons_doc_at_touchdown_then_paid():
    assert [(l["n"], l["pitstop"]) for l in LESSONS if l["pitstop"]] == [(3, "touchdown"), (4, "paid")]


def test_the_next_pit_stop_is_due_at_half_the_participants_and_over_when_its_lesson_opens():
    s = server()
    s.house = {"ace"}
    assert s.pitstop() == {"number": 1, "milestone": "touchdown", "lesson": 3, "participants": 0,
                           "reached": 0, "need": 1, "due": False}
    for name in ("anna", "bob", "cy", "dee", "ace"):
        award(s, name, "hello")
    award(s, "anna", "hello", "liftoff", "touchdown")
    stop = s.pitstop()
    assert (stop["participants"], stop["reached"], stop["need"], stop["due"]) == (4, 1, 2, False)
    award(s, "ace", "hello", "touchdown")  # a house bot doesn't count
    award(s, "bob", "hello", "touchdown")
    assert s.pitstop()["due"]
    lb = json.loads(s.http("/leaderboard.json")[2])
    assert lb["pitstop"]["number"] == 1 and lb["pitstop"]["due"]
    s.command("lesson 3")
    stop = s.pitstop()
    assert (stop["number"], stop["milestone"], stop["reached"], stop["due"]) == (2, "paid", 0, False)
    s.command("lesson 4")
    assert s.pitstop() is None


def test_serve_says_once_that_a_pit_stop_is_due():
    s = server()
    said: list[str] = []
    s.say = said.append
    s.award("hello", [("anna", 1), ("bob", 1)])
    s.award("touchdown", [("anna", 1)])
    s.award("touchdown", [("bob", 1)])
    due = [line for line in said if line.startswith("pit stop")]
    assert due == ["pit stop 1 is due: 1 of 2 participants have touchdown; after it, open its "
                   "lesson for everyone: lesson 3"]
