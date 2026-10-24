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
