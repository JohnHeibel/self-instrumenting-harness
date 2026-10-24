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
