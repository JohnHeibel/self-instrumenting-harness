"""The grader against a fake RCON. Run: uv run --project toolserver pytest graders/factorio/tests"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import grader as g  # noqa: E402
from rcon import RconError  # noqa: E402


class FakeRcon:
    """Answers /harness-grader with the next scripted reply (a dict, a raw string, or an error)."""

    def __init__(self, replies: list) -> None:
        self.replies, self.sent = replies, []

    def __call__(self):
        return self

    def __enter__(self):
        if self.replies and isinstance(self.replies[0], Exception):
            raise self.replies.pop(0)
        return self

    def __exit__(self, *exc):
        pass

    def command(self, cmd: str) -> str:
        self.sent.append(cmd)
        r = self.replies.pop(0)
        return r if isinstance(r, str) else json.dumps(r)


def state(tick=100, items=None, fluids=None, recipes=None, commands=(), console=False, players=None, mods=None):
    return {"tick": tick, "items": items or [], "fluids": fluids or [], "recipes": recipes or [],
            "console_command_used": console, "commands": list(commands), "mods": mods or {"base": "2.1.23"},
            "players": players or [{"name": "p", "connected": True, "cheat_mode": False, "controller": "character"}]}


def records(run: Path, type_: str | None = None) -> list[dict]:
    recs = [json.loads(l) for l in (run / "grader.jsonl").read_text(encoding="utf-8").splitlines()]
    return [r for r in recs if type_ is None or r["type"] == type_]


def test_milestones_from_production_and_assemblers(tmp_path):
    fake = FakeRcon([
        state(),
        state(items={"assembling-machine-1": 2}, recipes={"automation-science-pack": {"machines": 2, "finished": 9}}),
        state(tick=900, items={"assembling-machine-1": 2, "automation-science-pack": 500},
              recipes={"automation-science-pack": {"machines": 2, "finished": 10}}, fluids={"petroleum-gas": 30.5}),
    ])
    gr = g.Grader(tmp_path, connect=fake)
    for _ in range(3):
        gr.poll()
    defined = records(tmp_path, "milestones_defined")
    assert len(defined) == 1 and [m["id"] for m in defined[0]["milestones"]] == [
        "first_assembler", "red_auto", "green_auto", "oil", "plastic", "adv_circuit", "engine", "blue",
        "purple", "yellow", "silo", "rocket_part", "launch"]
    ms = records(tmp_path, "milestone")
    assert [m["id"] for m in ms] == ["first_assembler", "red_auto", "oil"]
    assert ms[0]["evidence"]["counts"]["assembling-machine-1"] == 2 and ms[0]["evidence"]["tick"] == 100
    # Hand-crafted red science (production stats) doesn't count; assembler output does.
    assert ms[1]["evidence"]["source"] == "assembling machines" and ms[1]["evidence"]["finished"] == 10
    assert ms[1]["evidence"]["integrity"] == {"console_command_used": False, "cheat_mode": False, "editor_mode": False}
    assert all(r["writer"] == "grader" for r in records(tmp_path))
    req = json.loads(fake.sent[0].split(" ", 1)[1])
    assert "automation-science-pack" in req["recipes"] and "petroleum-gas" in req["fluids"]


def test_integrity_signals_turn_on_and_off_once_each(tmp_path):
    cheat = [{"name": "p", "connected": True, "cheat_mode": True, "controller": "editor"}]
    fake = FakeRcon([state(), state(console=True, players=cheat), state(console=True, players=cheat), state(console=True)])
    gr = g.Grader(tmp_path, connect=fake)
    for _ in range(4):
        gr.poll()
    sig = [(r["signal"], r["active"]) for r in records(tmp_path, "integrity")]
    assert sig == [("console_command_used", True), ("cheat_mode", True), ("editor_mode", True),
                   ("cheat_mode", False), ("editor_mode", False)]
    assert "cheat mode on for p" in records(tmp_path, "integrity")[1]["detail"]


def test_host_commands_are_logged_with_their_channel(tmp_path):
    c1 = {"id": 1, "tick": 5, "command": "probe-state", "parameters": "", "by": "rcon", "custom": True}
    c2 = {"id": 2, "tick": 6, "command": "c", "parameters": "game.player.insert{...}", "by": "player p", "custom": False}
    fake = FakeRcon([state(commands=[c1]), state(commands=[c2]), state()])
    gr = g.Grader(tmp_path, connect=fake)
    for _ in range(3):
        gr.poll()
    cmds = records(tmp_path, "host_command")
    assert [(c["command"], c["by"], c["custom"]) for c in cmds] == [("probe-state", "rcon", True), ("c", "player p", False)]
    assert json.loads(fake.sent[2].split(" ", 1)[1])["since"] == 2      # asks only for newer ones


def test_unreachable_game_and_missing_mod(tmp_path):
    fake = FakeRcon([ConnectionRefusedError("refused"), "Unknown command \"harness-grader\".", state(),
                     RconError("RCON connection closed")])
    gr = g.Grader(tmp_path, connect=fake)
    for _ in range(4):
        gr.poll()
    st = [(r["ok"]) for r in records(tmp_path, "grader_status")]
    assert st == [False, True, False]
    sig = [(r["signal"], r["active"]) for r in records(tmp_path, "integrity")]
    assert sig == [("grader_unavailable", True), ("grader_unavailable", False)]
    assert "Unknown command" in records(tmp_path, "integrity")[0]["evidence"]["reply"]


def test_restart_resumes_without_repeating_records(tmp_path):
    (tmp_path / "runner-state.json").write_text(json.dumps({"run_id": "run-42"}))
    c1 = {"id": 7, "tick": 5, "command": "probe-state", "by": "rcon", "custom": True}
    g.Grader(tmp_path, connect=FakeRcon([state(items={"assembling-machine-2": 1}, console=True, commands=[c1])])).poll()
    fake = FakeRcon([state(items={"assembling-machine-2": 1}, console=True)])
    gr = g.Grader(tmp_path, connect=fake)
    gr.poll()
    assert len(records(tmp_path, "milestones_defined")) == 1
    assert len(records(tmp_path, "milestone")) == 1 and len(records(tmp_path, "integrity")) == 1
    assert json.loads(fake.sent[0].split(" ", 1)[1])["since"] == 7
    seqs = [r["seq"] for r in records(tmp_path)]
    assert seqs == list(range(1, len(seqs) + 1)) and {r["run_id"] for r in records(tmp_path)} == {"run-42"}


def test_judge_ignores_empty_tables_written_as_lists():
    assert g.judge(g.MILESTONES[0], {"items": []}) is None
    assert g.judge(g.MILESTONES[1], {"recipes": []}) is None


@pytest.mark.parametrize("m", g.MILESTONES, ids=lambda m: m["id"])
def test_every_milestone_can_be_reached(m):
    s = {"items": {}, "fluids": {}, "recipes": {}}
    if "stat" in m:
        s[m["stat"]] = m["min"]
    elif "recipe" in m:
        s["recipes"][m["recipe"]] = {"machines": 1, "finished": m["min"]}
    else:
        kind = "items" if "items" in m else "fluids"
        s[kind][m[kind][0]] = m["min"]
    assert g.judge(m, s)


def test_rocket_launch_is_judged_from_the_force_counter():
    m = next(m for m in g.MILESTONES if m["id"] == "launch")
    assert g.judge(m, {"rockets_launched": 0}) is None
    assert g.judge(m, {"rockets_launched": 1})["value"] == 1


def test_a_longer_milestone_list_is_announced_again_on_resume(tmp_path, monkeypatch):
    run = tmp_path / "run"
    run.mkdir()
    short = g.MILESTONES[:2]
    monkeypatch.setattr(g, "MILESTONES", short)
    g.Grader(run, connect=lambda: None)
    g.Grader(run, connect=lambda: None)   # same list: not written again
    monkeypatch.setattr(g, "MILESTONES", short + [{"id": "x", "name": "X", "items": ["x"], "min": 1}])
    g.Grader(run, connect=lambda: None)
    lines = [json.loads(l) for l in (run / "grader.jsonl").read_text().splitlines()]
    defined = [r["milestones"] for r in lines if r["type"] == "milestones_defined"]
    assert [len(d) for d in defined] == [2, 3]


def fairplay(jumps=(), items=(), crafts=(), jump_count=None, item_count=None, craft_count=None):
    """A mod 0.2 `fairplay` field; empty lists are written as {} by Factorio, like here."""
    return {"position_jump": {"count": len(jumps) if jump_count is None else jump_count, "recent": list(jumps) or {},
                              "sample_ticks": 10, "tracked": 1},
            "conservation": {"count": len(items) if item_count is None else item_count, "items": list(items) or {},
                             "checked_tick": 50, "baseline_tick": 1, "tolerance": 2},
            "instant_crafting": {"count": len(crafts) if craft_count is None else craft_count,
                                 "recent": list(crafts) or {}}}


JUMP = {"who": "player p", "mode": "on foot", "surface": 1, "from": {"x": 0, "y": 0}, "to": {"x": 50, "y": 0},
        "from_tick": 90, "to_tick": 100, "ticks": 10, "distance": 50, "limit": 4.38}
INSERT = {"item": "iron-plate", "first_tick": 100, "tick": 100, "stock": 108, "produced": 0, "given": 8,
          "excess": 100, "max_excess": 100}
CRAFT = {"who": "player p", "recipe": "iron-gear-wheel", "tick": 100, "stretch_start": 95, "crafts": 10,
         "elapsed": 5, "required": 300, "speed": 1}


def test_clean_fairplay_writes_nothing_and_shows_in_milestone_integrity(tmp_path):
    fake = FakeRcon([dict(state(items={"assembling-machine-1": 1}), fairplay=fairplay())])
    g.Grader(tmp_path, connect=fake).poll()
    assert records(tmp_path, "integrity") == []
    assert records(tmp_path, "milestone")[0]["evidence"]["integrity"] == {
        "console_command_used": False, "cheat_mode": False, "editor_mode": False,
        "position_jump": False, "conservation": False, "instant_crafting": False}


def test_each_fairplay_check_turns_on_with_its_evidence(tmp_path):
    fake = FakeRcon([dict(state(), fairplay=fairplay()),
                     dict(state(tick=200), fairplay=fairplay(jumps=[JUMP], items=[INSERT], crafts=[CRAFT]))])
    gr = g.Grader(tmp_path, connect=fake)
    gr.poll()
    gr.poll()
    recs = {r["signal"]: r for r in records(tmp_path, "integrity")}
    assert set(recs) == {"position_jump", "conservation", "instant_crafting"}
    assert all(r["active"] for r in recs.values())
    j = recs["position_jump"]
    assert j["evidence"]["recent"][0]["to"] == {"x": 50, "y": 0} and j["evidence"]["count"] == 1
    assert "moved 50 tiles in 10 ticks" in j["detail"] and "(0, 0) to (50, 0)" in j["detail"]
    c = recs["conservation"]
    assert c["evidence"]["items"][0]["item"] == "iron-plate" and "iron-plate +100" in c["detail"]
    assert c["evidence"]["checked_tick"] == 50
    k = recs["instant_crafting"]
    assert "10 craft(s)" in k["detail"] and "needing 300" in k["detail"]


def test_fairplay_writes_again_only_when_more_instances_appear(tmp_path):
    two = dict(JUMP, to_tick=300)
    fake = FakeRcon([dict(state(), fairplay=fairplay(jumps=[JUMP])),
                     dict(state(), fairplay=fairplay(jumps=[JUMP])),
                     dict(state(), fairplay=fairplay(jumps=[JUMP, two]))])
    gr = g.Grader(tmp_path, connect=fake)
    for _ in range(3):
        gr.poll()
    jumps = [r for r in records(tmp_path, "integrity") if r["signal"] == "position_jump"]
    assert [r["evidence"]["count"] for r in jumps] == [1, 2] and all(r["active"] for r in jumps)
    # A restarted grader remembers the count and doesn't repeat it.
    fake = FakeRcon([dict(state(), fairplay=fairplay(jumps=[JUMP, two]))])
    g.Grader(tmp_path, connect=fake).poll()
    assert len([r for r in records(tmp_path, "integrity") if r["signal"] == "position_jump"]) == 2


def test_an_old_mod_without_fairplay_reports_no_fairplay_signals(tmp_path):
    fake = FakeRcon([state(), state()])
    gr = g.Grader(tmp_path, connect=fake)
    gr.poll()
    gr.poll()
    assert records(tmp_path, "integrity") == []
    assert set(g.fairplay_signals(state())) == set()


def test_fairplay_count_beyond_recent_list_still_counts():
    s = dict(state(), fairplay=fairplay(jumps=[JUMP], jump_count=37))
    on, detail, evidence, n = g.fairplay_signals(s)["position_jump"]
    assert on and n == 37 and detail.startswith("37 position jump(s)")


def test_recipe_milestone_counts_only_machines_with_automated_supply():
    m = next(m for m in g.MILESTONES if m["id"] == "green_auto")
    hand_fed = {"position": {"x": 3.5, "y": 4.5}, "finished": 40,
                "reason": "iron-chest at (1.5, 4.5) has no inserter or drill feeding it (hand-fed)"}
    # The mod reports machines/finished for qualifying machines only, all_* for every machine.
    s = {"recipes": {"logistic-science-pack": {"machines": 0, "finished": 0, "all_machines": 1,
                                               "all_finished": 40, "excluded": [hand_fed]}}}
    assert g.judge(m, s) is None
    s["recipes"]["logistic-science-pack"].update(machines=1, finished=12, all_machines=2, all_finished=52)
    ev = g.judge(m, s)
    assert ev["finished"] == 12 and ev["supply_chain"] and ev["all_finished"] == 52
    assert "hand-fed" in ev["excluded"][0]["reason"]


def test_recipe_evidence_from_an_old_mod_has_no_supply_chain_fields():
    m = next(m for m in g.MILESTONES if m["id"] == "red_auto")
    ev = g.judge(m, {"recipes": {"automation-science-pack": {"machines": 1, "finished": 10}}})
    assert ev and "supply_chain" not in ev


def test_program_actions_are_written_as_deltas_and_survive_a_restart(tmp_path):
    with_actions = lambda tick, **a: {**state(tick=tick), "actions": a}
    fake = FakeRcon([
        state(),                                          # mod older than 0.3.0: nothing
        with_actions(200, build=3, mine=1, craft=0, rotate=0),
        with_actions(300, build=3, mine=1, craft=0, rotate=0),   # nothing new: no record
        with_actions(400, build=5, mine=1, craft=2, rotate=0),
    ])
    gr = g.Grader(tmp_path, connect=fake)
    for _ in range(4):
        gr.poll()
    recs = records(tmp_path, "program_actions")
    assert [(r["count"], r["kinds"]) for r in recs] == [(4, {"build": 3, "mine": 1}), (4, {"build": 2, "craft": 2})]
    assert recs[-1]["totals"] == {"build": 5, "mine": 1, "craft": 2, "rotate": 0} and recs[-1]["tick"] == 400

    # A restarted grader continues from the last totals; a lower count (an older save) restarts from it.
    fake2 = FakeRcon([with_actions(500, build=6, mine=1, craft=2, rotate=0),
                      with_actions(600, build=2, mine=1, craft=2, rotate=0)])
    gr2 = g.Grader(tmp_path, connect=fake2)
    gr2.poll(); gr2.poll()
    recs = records(tmp_path, "program_actions")[2:]
    assert [(r["count"], r["kinds"]) for r in recs] == [(1, {"build": 1}), (2, {"build": 2})]
