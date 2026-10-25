"""The KSP grader against scripted plugin states. Run: uv run --project toolserver pytest graders/ksp/tests"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_spec = importlib.util.spec_from_file_location("ksp_grader", Path(__file__).resolve().parents[1] / "grader.py")
g = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(g)

T0 = 1_800_000_000.0


def vessel(vid="v1", name="Rocket", type_="Ship", situation="PRELAUNCH", body="Kerbin", alt=70.0, pe=-600000.0,
           ap=100.0, speed=175.0, energy=-5.6e6, accel=0.0, crew=("Jebediah Kerman",)):
    return {"id": vid, "name": name, "type": type_, "situation": situation, "body": body, "alt": alt, "pe": pe,
            "ap": ap, "speed": speed, "energy": energy, "max_accel": accel, "crew": list(crew)}


class Feed:
    def __init__(self) -> None:
        self.seq, self.state = 0, None

    def __call__(self):
        return self.state

    def set(self, vessels, ut, boot="b1", actions=None, activity=None, cheats=None, error=""):
        self.seq += 1
        self.state = {"boot": boot, "seq": self.seq, "real_time": T0, "scene": "FLIGHT", "error": error, "ut": ut,
                      "cheats": cheats or {}, "actions": actions or {}, "activity": activity or {},
                      "vessels": vessels, "roster": []}


def records(run: Path, type_=None):
    rows = [json.loads(l) for l in (run / "grader.jsonl").read_text().splitlines()]
    return [r for r in rows if type_ is None or r["type"] == type_]


def test_the_ladder_to_the_mun_and_back(tmp_path):
    feed = Feed()
    gr = g.Grader(tmp_path, read=feed)
    steps = [
        [vessel()],
        [vessel(situation="FLYING", alt=5_000, speed=300, energy=-5.5e6, accel=20)],
        [vessel(situation="SUB_ORBITAL", alt=80_000, speed=2000, energy=-3.2e6, accel=20)],
        [vessel(situation="ORBITING", alt=80_000, pe=75_000, speed=2280, energy=-2.6e6)],
        [vessel(situation="ORBITING", body="Mun", alt=500_000, pe=20_000, speed=800, energy=-8e4)],
        [vessel(situation="LANDED", body="Mun", alt=1_000, speed=9, energy=-3.2e5)],
        [vessel(situation="SPLASHED", alt=0, speed=170, energy=-5.6e6)],
    ]
    for i, vs in enumerate(steps):
        feed.set(vs, ut=100.0 * (i + 1))
        gr.poll(now=T0)
    got = [r["id"] for r in records(tmp_path, "milestone")]
    assert got == ["launch", "space", "orbit", "mun_soi", "mun_orbit", "mun_landing", "crewed_mun_landing", "return"]
    back = [r for r in records(tmp_path, "milestone") if r["id"] == "return"][0]
    assert back["evidence"]["kerbals"] == ["Jebediah Kerman"]
    # A restarted grader remembers what was reached, and who landed where.
    again = g.Grader(tmp_path, read=feed)
    assert again.reached == set(gr.reached) and ("Jebediah Kerman", "Mun") in again.landed


def test_debris_and_flags_reach_nothing_and_a_probe_landing_isnt_crewed(tmp_path):
    feed = Feed()
    gr = g.Grader(tmp_path, read=feed)
    feed.set([vessel(type_="Debris", situation="ORBITING", pe=80_000, alt=90_000),
              vessel(vid="p", type_="Probe", situation="LANDED", body="Mun", crew=())], ut=10)
    gr.poll(now=T0)
    assert [r["id"] for r in records(tmp_path, "milestone")] == ["launch", "space", "mun_soi", "mun_landing"]


def test_an_orbit_set_from_nothing_is_an_energy_jump(tmp_path):
    feed = Feed()
    gr = g.Grader(tmp_path, read=feed)
    feed.set([vessel(situation="SUB_ORBITAL", alt=75_000, speed=1500, energy=-4.2e6, accel=0)], ut=50)
    gr.poll(now=T0)
    feed.set([vessel(situation="SUB_ORBITAL", alt=75_000, speed=1500, energy=-4.199e6, accel=0)], ut=51)
    gr.poll(now=T0)                                       # drift within the slack: fine
    assert not [r for r in records(tmp_path, "integrity") if r["signal"] == "energy_jump"]
    feed.set([vessel(situation="ORBITING", alt=75_000, pe=72_000, speed=2290, energy=-2.6e6, accel=0)], ut=52)
    gr.poll(now=T0)
    jumps = [r for r in records(tmp_path, "integrity") if r["signal"] == "energy_jump"]
    assert jumps and jumps[-1]["active"] and jumps[-1]["evidence"]["count"] == 1
    # The same rise under thrust the engines can give isn't a jump.
    feed2 = Feed()
    gr2 = g.Grader(tmp_path / "b", read=feed2) if (tmp_path / "b").mkdir() is None else None
    feed2.set([vessel(situation="SUB_ORBITAL", alt=75_000, speed=1500, energy=-4.2e6, accel=30)], ut=50)
    gr2.poll(now=T0)
    feed2.set([vessel(situation="SUB_ORBITAL", alt=75_000, speed=1530, energy=-4.155e6, accel=30)], ut=51)
    gr2.poll(now=T0)
    assert not [r for r in records(tmp_path / "b", "integrity") if r["signal"] == "energy_jump"]


def test_a_vessel_appearing_in_orbit_alone_is_flagged_but_staging_isnt(tmp_path):
    feed = Feed()
    gr = g.Grader(tmp_path, read=feed)
    feed.set([vessel(situation="SUB_ORBITAL", alt=60_000, speed=1500, energy=-4.3e6, accel=20)], ut=10)
    gr.poll(now=T0)
    stage = vessel(vid="v2", name="Rocket Debris", type_="Debris", situation="SUB_ORBITAL", alt=60_100)
    upper = vessel(vid="v3", name="Upper", situation="SUB_ORBITAL", alt=60_050, speed=1500, energy=-4.3e6, accel=20)
    feed.set([vessel(situation="SUB_ORBITAL", alt=60_200, speed=1500, energy=-4.3e6, accel=20), stage, upper], ut=11)
    gr.poll(now=T0)
    assert not [r for r in records(tmp_path, "integrity") if r["signal"] == "vessel_from_nothing"]
    alone = vessel(vid="v9", name="Station", situation="ORBITING", alt=400_000, pe=390_000, energy=-1.8e6)
    feed.set([vessel(situation="SUB_ORBITAL", alt=60_300, speed=1500, energy=-4.3e6, accel=20), alone], ut=12)
    gr.poll(now=T0)
    flagged = [r for r in records(tmp_path, "integrity") if r["signal"] == "vessel_from_nothing"]
    assert flagged and flagged[-1]["evidence"]["recent"][0]["vessel"]["name"] == "Station"


def test_actions_count_across_game_restarts_and_cheats_are_flagged(tmp_path):
    feed = Feed()
    gr = g.Grader(tmp_path, read=feed)
    feed.set([vessel()], ut=1, actions={"part_attach": 12}, activity={"controls": 3})
    gr.poll(now=T0)
    feed.set([vessel()], ut=2, actions={"part_attach": 15, "launch": 1})
    gr.poll(now=T0)
    feed.set([vessel()], ut=3, boot="b2", actions={"stage": 2}, cheats={"InfinitePropellant": True})
    gr.poll(now=T0)
    acts = records(tmp_path, "program_actions")
    assert [a["count"] for a in acts] == [12, 4, 2]
    assert records(tmp_path, "program_activity")[0]["kinds"] == {"controls": 3}
    cheat = [r for r in records(tmp_path, "integrity") if r["signal"] == "cheat_options"]
    assert cheat[-1]["active"] and cheat[-1]["evidence"]["cheats"] == ["InfinitePropellant"]


def test_stale_or_missing_state_is_a_grader_status(tmp_path):
    feed = Feed()
    gr = g.Grader(tmp_path, read=feed)
    assert gr.poll(now=T0) is None
    feed.set([vessel()], ut=1)
    assert gr.poll(now=T0 + 60) is None
    assert gr.poll(now=T0) is not None
    assert [r["ok"] for r in records(tmp_path, "grader_status")] == [False, True]


def test_a_duna_ladder_needs_an_escape_and_its_own_crewed_landing(tmp_path):
    feed = Feed()
    gr = g.Grader(tmp_path, read=feed)                    # a Mun run first: Jeb lands on the Mun
    feed.set([vessel(situation="LANDED", body="Mun", alt=1_000, speed=9, energy=-3.2e5)], ut=10)
    gr.poll(now=T0)
    gr = g.Grader(tmp_path, read=feed, target="Duna")     # then the goal moves to Duna
    ids = [m["id"] for m in records(tmp_path, "milestones_defined")[-1]["milestones"]]
    assert ids == ["launch", "space", "orbit", "escape", "duna_soi", "duna_orbit", "duna_landing",
                   "crewed_duna_landing", "duna_return"]
    feed.set([vessel(situation="SPLASHED", alt=0, speed=170, energy=-5.6e6)], ut=20)
    gr.poll(now=T0)                                       # back from the Mun is not back from Duna
    assert "duna_return" not in gr.reached and "escape" not in gr.reached
    steps = [[vessel(situation="ESCAPING", body="Sun", alt=1.3e10, speed=9000, energy=-4e8)],
             [vessel(situation="ORBITING", body="Duna", alt=300_000, pe=60_000, speed=900, energy=-1e5)],
             [vessel(situation="LANDED", body="Duna", alt=2_000, speed=0, energy=-3e5)],
             [vessel(situation="LANDED", alt=70, speed=175, energy=-5.6e6)]]
    for i, vs in enumerate(steps):
        feed.set(vs, ut=1e6 + 1e4 * i)
        gr.poll(now=T0)
    got = [r["id"] for r in records(tmp_path, "milestone")]
    assert got[-6:] == ["escape", "duna_soi", "duna_orbit", "duna_landing", "crewed_duna_landing", "duna_return"]
