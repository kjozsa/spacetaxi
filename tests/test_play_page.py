import json
import re

from spacetaxi.cli import main
from spacetaxi.maps import load_map
from spacetaxi.play import build_html, fixed_spawn, simulate
from spacetaxi.sim import CRASHED, Rules


def embedded(html: str) -> dict:
    return json.loads(re.search(r"const DATA = (\{.*?\});\n", html).group(1))


def test_out_file_is_self_contained(tmp_path, capsys):
    out = tmp_path / "p.html"
    assert main(["play", "--map", "cavern", "--out", str(out)]) == 0
    html = out.read_text(encoding="utf-8")
    assert not re.search(r"https?://", html)
    assert "/*FONT*/" not in html and "/*DATA*/" not in html
    data = embedded(html)
    assert data["map"] == list(load_map("cavern").rows)
    assert data["rules"] == Rules().to_dict()


def test_rules_are_embedded(tmp_path):
    out = tmp_path / "p.html"
    assert main(["play", "--map", "classic", "--rule", "gravity=3", "--out", str(out)]) == 0
    data = embedded(out.read_text(encoding="utf-8"))
    assert data["rules"]["gravity"] == 3
    assert data["map"] == list(load_map("classic").rows)


def test_bad_rule_or_map_is_an_error(tmp_path, capsys):
    assert main(["play", "--rule", "gravity=x", "--out", str(tmp_path / "p.html")]) == 2
    assert main(["play", "--map", "nope", "--out", str(tmp_path / "p.html")]) == 2
    assert not (tmp_path / "p.html").exists()
    assert "spacetaxi play: error" in capsys.readouterr().err


def test_fixed_spawn_respawns_there():
    m = load_map("cavern")
    assert fixed_spawn(m).spawns == (m.spawns[0],)
    assert fixed_spawn(m, (19, 2)).spawns == ((19, 2),)
    # fall straight down at full throttle sideways into the wall, then wait out the penalty
    states = simulate(m, Rules(), [{"ax": -1, "ay": 0}] * 80)
    assert CRASHED in [s["state"] for s in states]
    assert states[-1]["state"] == "flying"
    start = simulate(m, Rules(), [{}])[0]
    respawned = next(s for i, s in enumerate(states)
                     if i and states[i - 1]["state"] == CRASHED and s["state"] == "flying")
    assert (respawned["fx"], respawned["fy"]) == (3 * 32 + 16, 2 * 32 + 16)
    assert start["fy"] == 2 * 32 + 16 + 2  # one tick of gravity from the spawn centre


def test_build_html_escapes_the_data():
    html = build_html(load_map("cavern"), Rules())
    assert "</script>" in html and html.count("</script>") == 1
