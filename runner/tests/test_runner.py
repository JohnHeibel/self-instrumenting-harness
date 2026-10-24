"""Tier-0 runner tests against the fake app-server: no model, no game, no Docker."""

from __future__ import annotations

import json
import sys
import threading
import time

from runner.run import CONTINUE_PROMPT


def prompts(h) -> list[str]:
    return [r["params"]["input"][0]["text"] for r in h.requests("turn/start")]


def test_goal_then_continue_and_event_mapping(h):
    script = {"processes": [{"turns": [
        [{"reasoning": ["**Planning**\n\nLook at the map first."]}, {"tool": "list_hooks"},
         {"message": "I have no hooks yet."}, {"usage": 1000}],
        [{"usage": 2500}],
    ]}]}
    assert h.runner(script, budget_tokens=2000).run() == "budget_tokens"

    p = prompts(h)
    assert len(p) == 2 and p[0].startswith("Reach the goal.") and p[1] == CONTINUE_PROMPT
    assert len(h.requests("thread/start")) == 1 and not h.requests("thread/resume")

    recs = h.records()
    assert [r["seq"] for r in recs] == list(range(1, len(recs) + 1))
    assert all(r["writer"] == "runner" and r["v"] == 1 for r in recs)
    assert h.records("run_started")[0]["model"] == "fake-model"
    assert {"title": "Planning", "text": "Look at the map first."}.items() <= h.records("reasoning")[0].items()
    assert {"state": "acting", "text": "harness.list_hooks"}.items() <= h.records("agent_state")[1].items()
    assert h.records("agent_message")[0]["text"] == "I have no hooks yet."
    u1, u2 = h.records("usage")
    # One usage record per request, with that request's tokens and the thread totals.
    assert (u1["turn"], u1["total_tokens"], u1["input_tokens"], u1["uncached_tokens"]) == (1, 1000, 1000, 1000)
    assert (u2["turn"], u2["total_tokens"], u2["input_tokens"]) == (2, 2500, 1500)
    done = h.records("turn_completed")
    assert [r["status"] for r in done] == ["completed", "completed"]
    assert [r["turn_total_tokens"] for r in done] == [1000, 1500]
    cps = h.records("checkpoint")
    assert [c["label"] for c in cps if "artifact" not in c] == ["turn 1 completed", "turn 2 completed"]
    assert [c["label"] for c in cps if "artifact" in c] == ["workspace snapshot (end)"]
    assert h.records("run_ended")[0]["reason"] == "budget_tokens"
    assert h.records("agent_state")[-1]["state"] == "ended"
    st = h.state()
    assert st["thread_id"] == "thr_fake" and st["status"] == "ended" and st["turns"] == 2


def test_token_budget_interrupts_mid_turn(h):
    script = {"processes": [{"turns": [[{"usage": 500}, {"sleep": 0.1}, {"usage": 5000}, {"hang": True}]]}]}
    assert h.runner(script, budget_tokens=1000).run() == "budget_tokens"
    assert len(h.requests("turn/interrupt")) == 1
    assert h.records("turn_completed")[0]["status"] == "interrupted"
    assert [u["total_tokens"] for u in h.records("usage")] == [500, 5000]
    assert h.records("turn_completed")[0]["turn_total_tokens"] == 5000


def test_time_budget_interrupts_mid_turn(h):
    script = {"processes": [{"turns": [[{"hang": True}]]}]}
    t0 = time.monotonic()
    assert h.runner(script, budget_seconds=0.5).run() == "budget_time"
    assert time.monotonic() - t0 < 5
    assert len(h.requests("turn/interrupt")) == 1


def test_stop_file_mid_turn_ends_cleanly(h):
    script = {"processes": [{"turns": [[{"hang": True}]]}]}
    r = h.runner(script)
    threading.Timer(0.5, lambda: (h.run_dir / "STOP").write_text("")).start()
    assert r.run() == "stop"
    assert len(h.requests("turn/interrupt")) == 1
    assert h.records("run_ended")[0]["reason"] == "stop"
    # An ended run stays ended.
    assert h.runner(script).run() == "stop"
    assert len(h.requests("turn/start")) == 1


def test_stop_file_before_start_runs_no_turn(h):
    h.run_dir.mkdir(parents=True)
    (h.run_dir / "STOP").write_text("")
    assert h.runner({"processes": [{"turns": [[]]}]}).run() == "stop"
    assert not h.requests("turn/start")


def test_crash_in_first_turn_resumes_same_thread(h):
    script = {"thread_id": "thr_A", "processes": [
        {"turns": [[{"usage": 300}, {"exit": 1}]]},
        {"resume_total": 300, "turns": [[{"usage": 800}, {"message": "back"}]]},
    ]}
    assert h.runner(script, budget_tokens=700).run() == "budget_tokens"
    first, second = [r for r in h.requests() if r["launch"] == 0], [r for r in h.requests() if r["launch"] == 1]
    assert [r["method"] for r in first if r.get("method") == "thread/start"] == ["thread/start"]
    assert [r["params"]["threadId"] for r in second if r.get("method") == "thread/resume"] == ["thr_A"]
    assert not any(r.get("method") == "thread/start" for r in second)
    assert prompts(h)[1] == CONTINUE_PROMPT  # the goal already reached Codex
    assert [r["status"] for r in h.records("turn_completed")] == ["failed", "completed"]
    assert h.records("usage")[-1]["total_tokens"] == 800


def test_runner_restart_resumes_from_state_file(h):
    h.run_dir.mkdir(parents=True)
    (h.run_dir / "runner-state.json").write_text(json.dumps({
        "run_id": "r1", "started_at": time.time(), "thread_id": "thr_B", "goal_sent": True,
        "run_started_logged": True, "turns": 3, "active_turn_id": "turn_x",
        "extra": {"goal": "Reach blue science.", "host": "factorio",
                  "turn_base": {"totalTokens": 400}, "usage_total": {"totalTokens": 900}}}))
    script = {"thread_id": "thr_B", "processes": [{"resume_total": 900, "turns": [[{"usage": 1500}]]}]}
    assert h.runner(script, budget_tokens=1400).run() == "budget_tokens"
    assert [r["params"]["threadId"] for r in h.requests("thread/resume")] == ["thr_B"]
    assert prompts(h) == [CONTINUE_PROMPT]
    marker = h.records("marker")[0]
    assert marker["kind"] == "runner_restart" and "cut off" in marker["label"]
    assert not h.records("run_started")
    # The cut-off turn is closed with the tokens it used before the crash.
    cut, nxt = h.records("turn_completed")
    assert (cut["turn"], cut["status"], nxt["turn"]) == (3, "cut_off", 4)
    assert [t["turn_total_tokens"] for t in h.records("turn_completed")] == [500, 600]


def test_reopen_continues_an_ended_run_on_its_thread(h):
    h.run_dir.mkdir(parents=True)
    (h.run_dir / "runner-state.json").write_text(json.dumps({
        "run_id": "r1", "started_at": time.time(), "thread_id": "thr_C", "goal_sent": True,
        "run_started_logged": True, "turns": 2, "status": "ended", "ended_reason": "stop",
        "extra": {"goal": "g", "host": "factorio", "usage_total": {"totalTokens": 900}}}))
    script = {"thread_id": "thr_C", "processes": [{"resume_total": 900, "turns": [[{"usage": 1500}]]}]}
    assert h.runner(script).run() == "stop"                     # ended runs stay ended
    assert h.runner(script, budget_tokens=1400, reopen=True).run() == "budget_tokens"
    assert [r["params"]["threadId"] for r in h.requests("thread/resume")] == ["thr_C"]
    assert any(m["kind"] == "reopen" for m in h.records("marker"))


def test_usage_total_is_saved_on_every_update(h):
    script = {"processes": [{"turns": [[{"usage": 700}, {"hang": True}]]}]}
    r = h.runner(script)
    seen = []
    orig = r._save_usage
    r._save_usage = lambda t: (orig(t), seen.append(h.state()["extra"]["usage_total"]["totalTokens"]))
    threading.Timer(0.5, lambda: (h.run_dir / "STOP").write_text("")).start()
    r.run()
    assert 700 in seen


def test_backoff_grows_to_cap_and_gives_up_after_12(h):
    slept: list[float] = []
    script = {"processes": [{"turns": [[{"fail": "rate limited"}]]}]}
    r = h.runner(script, backoff_base=30, backoff_cap=3600, sleep=slept.append)
    assert r.run() == "too_many_failures"
    delays = [s["delay_s"] for s in h.records("agent_state") if s["state"] == "backing_off"]
    assert delays == [30, 60, 120, 240, 480, 960, 1920, 3600, 3600, 3600, 3600]
    assert sum(slept) == sum(delays)
    assert len(h.requests("turn/start")) == 12
    assert "rate limited" in h.records("turn_completed")[0]["error"]


def test_long_turn_resets_failure_count(h):
    script = {"processes": [{"turns": [[{"fail": "x"}], [{"fail": "x"}],
                                       [{"duration_ms": 700_000}, {"fail": "x"}], [{"fail": "x"}]]}]}
    assert h.runner(script, max_failures=3, sleep=lambda s: None).run() == "too_many_failures"
    counts = [s["failures"] for s in h.records("agent_state") if s["state"] == "backing_off"]
    assert counts == [1, 2, 1, 2]
    assert len(h.requests("turn/start")) == 5


def test_no_turn_while_host_down(h):
    calls = {"n": 0}

    def check():
        calls["n"] += 1
        return (calls["n"] > 3, "factorio window")

    script = {"processes": [{"turns": [[{"usage": 100}]]}]}
    assert h.runner(script, host_check=check, budget_tokens=50).run() == "budget_tokens"
    states = [s["state"] for s in h.records("agent_state")]
    assert states[0] == "host_down"
    first_turn = next(i for i, r in enumerate(h.records()) if r["type"] == "turn_started")
    assert all(r["type"] != "turn_started" for r in h.records()[:first_turn])
    assert len(h.requests("turn/start")) == 1


def test_host_down_mid_turn_interrupts_without_counting_failure(h):
    calls = {"n": 0}

    def check():
        calls["n"] += 1
        return (calls["n"] not in (2, 3), "factorio window")

    script = {"processes": [{"turns": [[{"hang": True}], [{"usage": 100}]]}]}
    assert h.runner(script, host_check=check, budget_tokens=50).run() == "budget_tokens"
    host_interrupts = [m for m in h.records("marker") if m["kind"] == "interrupt" and "host_down" in m["label"]]
    assert len(host_interrupts) == 1
    assert [r["status"] for r in h.records("turn_completed")][0] == "interrupted"
    assert len(h.requests("turn/start")) == 2  # resumed after the host came back
    assert not [s for s in h.records("agent_state") if s["state"] == "backing_off"]


def test_pause_file_holds_the_run(h):
    script = {"processes": [{"turns": [[{"usage": 100}]]}]}
    h.run_dir.mkdir(parents=True)
    (h.run_dir / "PAUSE").write_text("")
    threading.Timer(0.4, lambda: (h.run_dir / "PAUSE").unlink()).start()
    assert h.runner(script, budget_tokens=50).run() == "budget_tokens"
    assert h.records("agent_state")[0]["state"] == "paused"


def test_input_blocked_holds_the_run_without_prompting(h):
    script = {"processes": [{"turns": [[{"usage": 100}]]}]}
    h.run_dir.mkdir(parents=True)
    (h.run_dir / "INPUT_BLOCKED").write_text('{"detail": "Windows kept \'Claude\' in front"}')
    threading.Timer(0.4, lambda: (h.run_dir / "INPUT_BLOCKED").unlink()).start()
    assert h.runner(script, budget_tokens=50).run() == "budget_tokens"
    first = h.records("agent_state")[0]
    assert first["state"] == "input_blocked" and "Claude" in first["text"]
    assert len(h.requests("turn/start")) == 1   # no turn while blocked


def test_input_blocked_mid_turn_interrupts_without_counting_failure(h):
    script = {"processes": [{"turns": [[{"hang": True}], [{"usage": 100}]]}]}
    threading.Timer(0.3, lambda: (h.run_dir / "INPUT_BLOCKED").write_text("{}")).start()
    threading.Timer(0.8, lambda: (h.run_dir / "INPUT_BLOCKED").unlink()).start()
    assert h.runner(script, budget_tokens=50).run() == "budget_tokens"
    assert any(m["kind"] == "interrupt" and "input_blocked" in m["label"] for m in h.records("marker"))
    assert not [s for s in h.records("agent_state") if s["state"] == "backing_off"]


def test_second_runner_on_same_run_dir_is_refused(h):
    import os
    import pytest
    from runner.state import RunLock
    h.run_dir.mkdir(parents=True)
    (h.run_dir / "runner.lock").write_text(str(os.getppid()))  # a live process that isn't us
    with pytest.raises(SystemExit):
        RunLock(h.run_dir / "runner.lock").acquire()


def test_wake_file_interrupts_thinking_and_prompts_with_the_notices(h):
    script = {"processes": [{"turns": [[{"hang": True}], [{"usage": 100}]]}]}
    wake = '[{"source": "rule:low_iron", "text": "iron below 50"}]'
    threading.Timer(0.3, lambda: (h.run_dir / "WAKE").write_text(wake)).start()
    assert h.runner(script, budget_tokens=50).run() == "budget_tokens"
    assert any(m["kind"] == "interrupt" and "wake" in m["label"] for m in h.records("marker"))
    assert not [s for s in h.records("agent_state") if s["state"] == "backing_off"]
    second = h.requests("turn/start")[1]["params"]["input"][0]["text"]
    assert "rule:low_iron: iron below 50" in second and "context is intact" in second
    assert not (h.run_dir / "WAKE").exists()


def test_deadline_mid_turn_ends_without_backing_off(h):
    script = {"processes": [{"turns": [[{"hang": True}]]}]}
    t0 = time.monotonic()
    assert h.runner(script, until=time.time() + 0.5).run() == "deadline"
    assert time.monotonic() - t0 < 10
    assert not [r for r in h.records("agent_state") if r["state"] == "backing_off"]


def test_host_down_mid_turn_shows_the_agent_what_the_host_wrote(h):
    logs = h.run_dir / "host" / "server"
    logs.mkdir(parents=True)
    (logs / "prog.log").write_text("\n".join(f"line {i}" for i in range(100))
                                   + "\nError: mod.lua:9: attempt to index nil\nstack traceback:\n  mod.lua:9\n"
                                   + "\n".join(f"after {i}" for i in range(30)) + "\n")
    (logs / "save.bin").write_bytes(b"\x00\x01binary")
    calls = {"n": 0}

    def check():
        calls["n"] += 1
        return (calls["n"] not in (2, 3), "prog")

    toml = f'[host]\nname = "test-host"\nup_check = "always"\nstart = [{json.dumps(sys.executable)}, "-c", "pass"]\n'
    script = {"processes": [{"turns": [[{"hang": True}], [{"usage": 100}]]}]}
    assert h.runner(script, host_check=check, budget_tokens=50, host_toml=toml).run() == "budget_tokens"
    second = prompts(h)[1]
    assert second.startswith("The program went down") and "mod.lua:9: attempt to index nil" in second, second
    assert "after 29" in second and "line 3\n" not in second and "binary" not in second
    assert [m["files"] for m in h.records("marker") if m["kind"] == "host_log"] == [["server/prog.log"]]
