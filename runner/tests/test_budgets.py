"""Active-time and uncached-token budgets, rate-limit backoff, workspace snapshots, and the
host check from the host config. Time is a fake clock that only moves when the runner waits."""

from __future__ import annotations

import sys
import tarfile

import pytest

from runner import hostcheck
from toolserver.extensions import HostConfig

T0 = 1_000_000.0


class Clock:
    def __init__(self) -> None:
        self.t = T0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


def flaky_host(down_calls: int):
    calls = {"n": 0}

    def check():
        calls["n"] += 1
        return (calls["n"] > down_calls, "test host")
    return check


def test_active_time_skips_host_down_and_counts_backoff(h):
    clock = Clock()
    script = {"processes": [{"turns": [[{"fail": "boom"}], [{"usage": 100}]]}]}
    r = h.runner(script, clock=clock, sleep=clock.sleep, host_check=flaky_host(2), host_poll=100,
                 backoff_base=10, backoff_cap=10, budget_tokens=50)
    assert r.run() == "budget_tokens"
    assert clock.t - T0 == pytest.approx(210)        # 2 x 100 s host down + 10 s backoff
    assert h.records("run_ended")[0]["active_s"] == 10
    assert [s["state"] for s in h.records("agent_state")].count("host_down") == 1


def test_time_budget_counts_only_active_time(h):
    clock = Clock()
    script = {"processes": [{"turns": [[{"fail": "boom"}]]}]}
    r = h.runner(script, clock=clock, sleep=clock.sleep, host_check=flaky_host(5), host_poll=100,
                 backoff_base=30, backoff_cap=30, budget_seconds=100)
    assert r.run() == "budget_time"
    # 500 s of host downtime first, then failures every 30 s until 100 active seconds.
    assert clock.t - T0 == pytest.approx(500 + 4 * 30)
    assert h.records("run_ended")[0]["active_s"] == 120


def test_token_budget_counts_uncached_input_and_output(h):
    script = {"processes": [{"turns": [
        [{"usage": 10_000, "cached": 9_500, "output": 100}],   # uncached 9900-9500+100 = 500
        [{"usage": 20_000, "cached": 18_000, "output": 300}],  # 19700-18000+300 = 2000
    ]}]}
    assert h.runner(script, budget_tokens=1000).run() == "budget_tokens"
    assert len(h.requests("turn/start")) == 2
    u1, u2 = h.records("usage")
    assert (u1["uncached_tokens"], u2["uncached_tokens"]) == (500, 2000)
    assert (u2["input_tokens"], u2["cached_input_tokens"], u2["output_tokens"]) == (9_800, 8_500, 200)
    end = h.records("run_ended")[0]
    assert (end["uncached_tokens"], end["total_tokens"]) == (2000, 20_000)


def test_rate_limit_backoff_waits_until_reset_plus_margin(h):
    clock = Clock()
    limits = {"rateLimits": {"primary": {"usedPercent": 100, "windowDurationMins": 300, "resetsAt": T0 + 500},
                             "secondary": {"usedPercent": 40, "windowDurationMins": 10080, "resetsAt": T0 + 90_000},
                             "rateLimitReachedType": "primary"}}
    script = {"processes": [{"turns": [
        [{"notify": "account/rateLimits/updated", "params": limits}, {"fail": "usage limit reached"}],
        [{"usage": 100}],
    ]}]}
    r = h.runner(script, clock=clock, sleep=clock.sleep, backoff_base=30, backoff_cap=3600, budget_tokens=50)
    assert r.run() == "budget_tokens"
    backoff = next(s for s in h.records("agent_state") if s["state"] == "backing_off")
    assert backoff["delay_s"] == 560 and backoff["cause"] == "rate_limit"
    assert backoff["until"] == "1970-01-12T13:55:00Z"  # T0 + 500
    assert clock.t - T0 == pytest.approx(560)
    assert h.records("run_ended")[0]["active_s"] == 560  # backoff counts as active time


def test_rate_limit_waits_do_not_count_as_failures(h):
    clock = Clock()
    def hit(reset):
        limits = {"rateLimits": {"primary": {"usedPercent": 100, "resetsAt": T0 + reset}}}
        return [{"notify": "account/rateLimits/updated", "params": limits}, {"fail": "usage limit reached"}]
    script = {"processes": [{"turns": [hit(100), hit(1000), hit(5000), [{"usage": 100}]]}]}
    r = h.runner(script, clock=clock, sleep=clock.sleep, max_failures=2, budget_tokens=50)
    assert r.run() == "budget_tokens"   # three rate-limited turns, yet not too_many_failures
    waits = [s for s in h.records("agent_state") if s["state"] == "backing_off"]
    assert [w["failures"] for w in waits] == [0, 0, 0] and waits[0]["text"].startswith("rate limited")


def test_rate_limit_reached_without_a_full_window_waits_for_the_soonest_reset(h):
    clock = Clock()
    limits = {"rateLimits": {"primary": {"usedPercent": 97, "resetsAt": T0 + 900},
                             "secondary": {"usedPercent": 60, "resetsAt": T0 + 90_000},
                             "rateLimitReachedType": "unknown"}}
    script = {"processes": [{"turns": [
        [{"notify": "account/rateLimits/updated", "params": limits}, {"fail": "limit"}], [{"usage": 100}]]}]}
    r = h.runner(script, clock=clock, sleep=clock.sleep, backoff_base=30, backoff_cap=3600, budget_tokens=50)
    assert r.run() == "budget_tokens"
    backoff = next(s for s in h.records("agent_state") if s["state"] == "backing_off")
    assert backoff["delay_s"] == 960


def test_ordinary_failure_backs_off_without_rate_limit(h):
    clock = Clock()
    script = {"processes": [{"turns": [[{"fail": "boom"}], [{"usage": 100}]]}]}
    r = h.runner(script, clock=clock, sleep=clock.sleep, backoff_base=30, backoff_cap=3600, budget_tokens=50)
    assert r.run() == "budget_tokens"
    backoff = next(s for s in h.records("agent_state") if s["state"] == "backing_off")
    assert backoff["delay_s"] == 30 and "cause" not in backoff


def test_workspace_snapshots_periodically_and_at_end(h):
    clock = Clock()
    script = {"processes": [{"turns": [[{"usage": 100}]]}]}
    r = h.runner(script, clock=clock, sleep=clock.sleep, host_check=flaky_host(1), host_poll=100,
                 checkpoint_every=50, budget_tokens=50)
    ws = h.run_dir / "workspace"
    (ws / "notes").mkdir()
    (ws / "notes" / "NOTES.md").write_text("plan")
    assert r.run() == "budget_tokens"
    snaps = [c for c in h.records("checkpoint") if "artifact" in c]
    assert [s["label"] for s in snaps] == ["workspace snapshot (periodic)", "workspace snapshot (end)"]
    for s in snaps:
        path = h.run_dir / s["artifact"]
        assert path.parent == h.run_dir / "checkpoints"          # outside the mounted workspace
        with tarfile.open(path) as tar:
            assert tar.getnames() == ["notes/NOTES.md"]
            assert tar.extractfile("notes/NOTES.md").read() == b"plan"
        assert s["files"] == 1 and s["bytes"] == path.stat().st_size
    assert snaps[0]["ref"] == "/"                                 # before the thread existed
    assert snaps[1]["ref"].startswith("thr_fake/turn_")


# Host check from the host config -------------------------------------------------------

def test_up_check_always_and_command(tmp_path):
    assert hostcheck.from_config(HostConfig(up_check="always"))() == (True, "always up")
    (tmp_path / "flag").write_text("")
    exists = [sys.executable, "-c", "import os, sys; sys.exit(0 if os.path.exists('flag') else 1)"]
    up, detail = hostcheck.from_config(HostConfig(up_check=exists), tmp_path)()
    assert up and "exited 0" in detail                            # run from the config's directory
    (tmp_path / "flag").unlink()
    assert hostcheck.from_config(HostConfig(up_check=exists), tmp_path)()[0] is False


def test_up_check_window_needs_a_window_to_look_for():
    with pytest.raises(ValueError, match="window_title or process"):
        hostcheck.from_config(HostConfig(up_check="window"))
    assert callable(hostcheck.from_config(HostConfig(window_title="Untitled", up_check="window")))
    with pytest.raises(ValueError, match="up_check must be"):
        hostcheck.from_config(HostConfig(up_check="sometimes"))


def test_host_config_is_required_on_the_command_line(capsys):
    from runner.run import main
    with pytest.raises(SystemExit):
        main(["--goal", "x", "--agent-cmd", "[]"])
    assert "--host-config" in capsys.readouterr().err


# Starting and stopping the host ---------------------------------------------------------

HOST_CTL = ('[host]\nname = "test-host"\n'
            'up_check = ["{python}", "-c", "import os, sys; sys.exit(0 if os.path.exists(r\'{run_dir}/up\') else 1)"]\n'
            'start = ["{python}", "-c", "import sys; open(r\'{run_dir}/up\', \'w\'); open(r\'{run_dir}/starts\', \'a\').write(\'x\'); print(\'started\')"]\n'
            'stop = ["{python}", "-c", "import os; os.remove(r\'{run_dir}/up\')"]\n'
            'notes = "Read {run_dir}/up for nothing in particular."\n')


def test_runner_starts_a_down_host_and_stops_it_at_the_end(h):
    script = {"processes": [{"turns": [[{"usage": 100}]]}]}
    r = h.runner(script, host_toml=HOST_CTL, host_check=None, budget_tokens=50)
    assert r.run() == "budget_tokens"
    assert (h.run_dir / "starts").read_text() == "x"             # started once, then up
    assert not (h.run_dir / "up").exists()                        # stopped at the end
    kinds = [(m["kind"], m["ok"]) for m in h.records("marker") if m["kind"].startswith("host_")]
    assert kinds == [("host_start", True), ("host_stop", True)]
    assert "started" in (h.run_dir / "raw" / "host.log").read_text()
    goal = h.requests("turn/start")[0]["params"]["input"][0]["text"]
    assert f"About this host: Read {h.run_dir.resolve()}/up" in goal


def test_failed_host_start_waits_before_retrying(h):
    clock = Clock()
    toml = HOST_CTL.replace("open(r'{run_dir}/up', 'w'); ", "").replace("print('started')", "sys.exit(3)")
    checks = {"n": 0}

    def down():   # always down; after 10 polls (1000 s), stop the run
        checks["n"] += 1
        if checks["n"] > 10:
            (h.run_dir / "STOP").write_text("")
        return False, "down"
    r = h.runner({"processes": [{"turns": []}]}, host_toml=toml, host_check=down, clock=clock,
                 sleep=clock.sleep, host_poll=100, start_retry=250)
    r.run()
    starts = [m for m in h.records("marker") if m["kind"] == "host_start"]
    assert [m["ok"] for m in starts] == [False] * 4                 # t = 0, 300, 600, 900
    assert "exited 3" in starts[0]["label"]
    assert h.records("run_ended")[0]["active_s"] == 0
