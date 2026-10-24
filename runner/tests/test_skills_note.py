import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import claude_runner as cr  # noqa: E402


def test_no_index_means_no_note(tmp_path):
    assert cr.skills_note(tmp_path) == ""


def test_index_is_injected_and_capped(tmp_path):
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "INDEX.md").write_text("- chop_logs.py: gather N logs\n")
    note = cr.skills_note(tmp_path)
    assert "chop_logs.py" in note and "skills/INDEX.md" in note
    (tmp_path / "skills" / "INDEX.md").write_text("x" * 10000)
    assert len(cr.skills_note(tmp_path)) < cr.SKILLS_CAP + 400


def test_update_note_is_delivered_once(tmp_path):
    assert cr.take_update_note(tmp_path) == ""
    (tmp_path / "UPDATE_NOTE.md").write_text("wait in long steps\n")
    assert "wait in long steps" in cr.take_update_note(tmp_path)
    assert cr.take_update_note(tmp_path) == ""
    assert (tmp_path / "UPDATE_NOTE.md.sent").exists()


def test_idle_watch(tmp_path):
    now = [0.0]
    w = cr.IdleWatch(tmp_path, 60, clock=lambda: now[0])
    (tmp_path / "live").mkdir()
    (tmp_path / "live" / "latest.jpg").write_bytes(b"a")
    w.reset()
    now[0] = 59
    assert not w.idle()
    now[0] = 61
    assert w.idle()                                   # nothing changed for 61 s
    (tmp_path / "live" / "latest.jpg").write_bytes(b"b")
    assert not w.idle()                               # the picture changed: activity
    now[0] = 130
    (tmp_path / "events.jsonl").write_text("x")       # a tool call was logged: activity
    assert not w.idle()
    assert not cr.IdleWatch(tmp_path, 0).idle()       # off


def test_handoff_state_machine():
    e = {}
    assert not cr.handoff_trigger(e, 100, 0)            # off
    assert not cr.handoff_trigger(e, 100, 150)          # under the limit
    assert cr.handoff_trigger(e, 200, 150) and e["handoff"] == "wanted"
    assert not cr.handoff_trigger(e, 300, 150)          # already in progress
    assert not cr.handoff_end_turn(e, False)            # the interrupted turn is not the asking turn
    assert "NOTES.md" in cr.handoff_begin_turn(e) and e["handoff"] == "asked"
    assert not cr.handoff_end_turn(e, True)             # asking turn failed: ask again next turn
    assert "NOTES.md" in cr.handoff_begin_turn(e)
    assert cr.handoff_end_turn(e, False) and e["fresh_session"] and not e["handoff"]
    assert cr.handoff_begin_turn(e) == ""


def test_new_host_events(tmp_path):
    import json
    p = tmp_path / "grader.jsonl"
    seen = set()
    assert cr.new_host_events(p, seen) == []
    p.write_text(json.dumps({"type": "marker", "kind": "agent_died", "key": "k1", "cause": "drowned"}) + "\n"
                 + json.dumps({"type": "milestone", "id": "x"}) + "\n")
    assert [e["cause"] for e in cr.new_host_events(p, seen)] == ["drowned"]
    assert cr.new_host_events(p, seen) == []


def test_recent_deaths_counts_a_loop(tmp_path):
    import json
    p = tmp_path / "grader.jsonl"
    rows = [("2026-10-24T10:00:00.000Z"), ("2026-10-24T12:44:01.000Z"), ("2026-10-24T12:44:41.000Z"), ("2026-10-24T12:45:01.000Z")]
    p.write_text("".join(json.dumps({"type": "marker", "kind": "agent_died", "ts": t}) + "\n" for t in rows))
    assert cr.recent_deaths(p) == 3
    assert cr.recent_deaths(tmp_path / "none") == 0
