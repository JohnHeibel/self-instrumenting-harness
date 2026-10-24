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


def test_report_shows_milestone_costs(tmp_path):
    _write(tmp_path / "runner.jsonl", [
        {"ts": "2026-01-01T10:00:00Z", "type": "usage", "total_tokens": 0, "uncached_tokens": 0},
        {"ts": "2026-01-01T10:10:00Z", "type": "usage", "total_tokens": 9, "uncached_tokens": 50_000},
        {"ts": "2026-01-01T10:20:00Z", "type": "usage", "total_tokens": 19, "uncached_tokens": 60_000},
    ])
    _write(tmp_path / "grader.jsonl", [
        {"ts": "2026-01-01T10:10:30Z", "type": "milestone", "id": "a"},
        {"ts": "2026-01-01T10:10:30Z", "type": "milestone", "id": "b"},
        {"ts": "2026-01-01T10:20:30Z", "type": "milestone", "id": "c"},
    ])
    text = report(tmp_path, 60, now=datetime(2026, 1, 1, 10, 30, tzinfo=timezone.utc))
    assert "a, b: 10 min, 1 requests, 50,000 uncached tokens" in text
    assert "c: 10 min, 1 requests, 10,000 uncached tokens" in text
