"""The event mapping parses real `codex app-server` sessions (recorded with
runner/scripts/record_fixture.py, Codex 0.164.0, scrubbed of account data)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runner.mapping import Mapper
from toolserver.events import EventLog

FIXTURES = Path(__file__).parent / "fixtures"


def replay(name: str, tmp_path: Path) -> list[dict]:
    log = EventLog(tmp_path / "runner.jsonl", "fixture", writer="runner")
    m = Mapper(log)
    turn_no = 0
    for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        msg = rec["msg"]
        if rec["dir"] != "in" or not isinstance(msg, dict) or "method" not in msg:
            continue
        p = msg.get("params") or {}
        if msg["method"] == "turn/started":
            turn_no += 1
            m.begin_turn(turn_no, p["turn"]["id"])
        m.handle(msg)
        if msg["method"] == "turn/completed":
            t = p["turn"]
            m.end_turn(t["status"], t.get("durationMs"), (t.get("error") or {}).get("message"))
    return [json.loads(l) for l in (tmp_path / "runner.jsonl").read_text(encoding="utf-8").splitlines()]


def last_total(name: str) -> int:
    total = 0
    for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines():
        msg = json.loads(line)["msg"]
        if isinstance(msg, dict) and msg.get("method") == "thread/tokenUsage/updated":
            total = msg["params"]["tokenUsage"]["total"]["totalTokens"]
    return total


def test_tool_call_session(tmp_path):
    recs = replay("real_session.jsonl", tmp_path)
    types = [r["type"] for r in recs]
    assert types[0] == "turn_started" and types[-2:] == ["usage", "turn_completed"]
    acting = [r for r in recs if r["type"] == "agent_state" and r["state"] == "acting"]
    assert [a["text"] for a in acting] == ["harness.list_hooks"]
    msgs = [r for r in recs if r["type"] == "agent_message"]
    assert [m["phase"] for m in msgs] == ["commentary", "final_answer"]
    assert "17" in msgs[-1]["text"]
    usage = next(r for r in recs if r["type"] == "usage")
    assert usage["total_tokens"] == usage["turn_total_tokens"] == last_total("real_session.jsonl") > 0
    assert usage["input_tokens"] > usage["cached_input_tokens"] > 0
    assert usage["context_window"]
    assert recs[-1]["status"] == "completed" and recs[-1]["duration_ms"] > 0


def test_reasoning_summary_session(tmp_path):
    recs = replay("real_session_reasoning.jsonl", tmp_path)
    reasoning = [r for r in recs if r["type"] == "reasoning"]
    assert reasoning and reasoning[0]["title"] == "Calculating products"
    assert reasoning[0]["text"] and not reasoning[0]["text"].startswith("**")


@pytest.mark.parametrize("name", ["real_session.jsonl", "real_session_reasoning.jsonl"])
def test_fixtures_hold_no_account_data(name):
    text = (FIXTURES / name).read_text(encoding="utf-8")
    for needle in ("accessToken", "refresh_token", "planType", "installationId", "balance", "@"):
        assert needle not in text
