"""The costs tool's report, from a run directory's logs."""

import json
from datetime import datetime, timezone
from pathlib import Path

from toolserver.costs import report

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _write(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def test_report_counts_calls_tokens_and_largest_results(tmp_path):
    call = lambda ts, tool, ok=True, size=100, kind="text", ms=500: {
        "ts": ts, "type": "tool_call", "tool": tool, "ok": ok, "result_bytes": size, "result_kind": kind,
        "duration_ms": ms, "args": {"op": tool}}
    _write(tmp_path / "events.jsonl", [
        call("2026-01-01T10:00:00Z", "old"),                       # outside the window
        call("2026-01-01T11:30:00Z", "status", size=20000),
        call("2026-01-01T11:30:10Z", "status", ok=False),
        call("2026-01-01T11:30:40Z", "screenshot", size=10**6, kind="image"),
        call("2026-01-01T11:31:00Z", "costs"),                      # its own calls don't count
        {**call("2026-01-01T11:30:50Z", "status"), "via": "script"},  # inside a script: not a step
    ])
    _write(tmp_path / "runner.jsonl", [
        {"ts": "2026-01-01T11:29:00Z", "type": "usage", "total_tokens": 1000, "uncached_tokens": 100, "input_tokens": 50},
        {"ts": "2026-01-01T11:59:00Z", "type": "usage", "total_tokens": 31000, "uncached_tokens": 400, "input_tokens": 900},
    ])
    text = report(tmp_path, 60, now=NOW)
    assert text.startswith("Last 60 min: 3 tool calls, 1 failed; 1 more calls ran inside scripts.")
    assert "Model: 1 steps, 30,000 tokens (300 not cached), about 30,000 per step. Context now: 900" in text
    assert "status: 3, 1 failed" in text and "screenshot: 1, 0 failed, 0.5 s, 1 images" in text
    assert "old" not in text and "Largest text results: status" in text
    assert "median 30 s, longest 30 s" in text


def test_report_without_logs(tmp_path):
    assert report(tmp_path, 30, now=NOW) == "Last 30 min: 0 tool calls, 0 failed."


def test_report_shows_program_actions_per_step_from_any_log(tmp_path):
    _write(tmp_path / "runner.jsonl", [
        {"ts": "2026-01-01T11:10:00Z", "type": "usage", "total_tokens": 0, "uncached_tokens": 0, "input_tokens": 10},
        {"ts": "2026-01-01T11:20:00Z", "type": "usage", "total_tokens": 40000, "uncached_tokens": 0, "input_tokens": 10},
        {"ts": "2026-01-01T11:30:00Z", "type": "usage", "total_tokens": 80000, "uncached_tokens": 0, "input_tokens": 10},
    ])
    _write(tmp_path / "grader.jsonl", [
        {"ts": "2026-01-01T09:00:00Z", "type": "program_actions", "count": 99, "kinds": {"build": 99}},  # too old
        {"ts": "2026-01-01T11:15:00Z", "type": "program_actions", "count": 30, "kinds": {"build": 20, "mine": 10}},
        {"ts": "2026-01-01T11:25:00Z", "type": "program_actions", "count": 10, "kinds": {"craft": 10}},
        {"ts": "2026-01-01T11:26:00Z", "type": "host_command", "parameters": "program_actions"},
    ])
    text = report(tmp_path, 60, now=NOW)
    assert "Done in the program: 40 actions (build 20, mine 10, craft 10), 20.0 per step, about 2,000 tokens per action." in text


def test_report_without_program_counts_says_nothing_about_them(tmp_path):
    _write(tmp_path / "grader.jsonl", [{"ts": "2026-01-01T11:26:00Z", "type": "milestone", "id": "x"}])
    assert "Done in the program" not in report(tmp_path, 60, now=NOW)


def test_time_use_splits_hands_tools_and_idle(tmp_path):
    from toolserver.costs import time_use
    _write(tmp_path / "events.jsonl", [
        {"ts": "2026-01-01T11:00:00Z", "type": "task_started", "task": "t1", "hands": True},
        {"ts": "2026-01-01T11:10:00Z", "type": "task_ended", "task": "t1"},          # 10 min hands
        {"ts": "2026-01-01T11:20:00Z", "type": "tool_call", "tool": "read_docs", "channel": "docs",
         "duration_ms": 300_000},                                                     # 5 min tools only
        {"ts": "2026-01-01T11:30:00Z", "type": "tool_call", "tool": "probe", "channel": "hook",
         "duration_ms": 60_000, "hook": {"acts": False}},                             # 1 min reading
        {"ts": "2026-01-01T11:40:00Z", "type": "tool_call", "tool": "probe", "channel": "hook",
         "duration_ms": 60_000, "via": "task:t9"},                                    # inside a task: skipped
    ])
    _write(tmp_path / "runner.jsonl", [
        {"ts": "2026-01-01T11:00:00Z", "type": "agent_state", "state": "thinking"},
        {"ts": "2026-01-01T11:50:00Z", "type": "agent_state", "state": "paused"},     # last 10 min off
    ])
    a, b = NOW.timestamp() - 3600, NOW.timestamp()
    u = time_use(tmp_path, a, b)
    assert [round(u[k] / 60) for k in ("hands", "tools", "idle", "off")] == [10, 6, 34, 10], u
    assert "hands busy 20%" in report(tmp_path, 60, now=NOW)
