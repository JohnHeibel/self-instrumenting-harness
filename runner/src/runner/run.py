"""The runner: drives one long Codex session on ONE thread, through `codex app-server`.

It owns the run directory and the tool server's lifecycle, writes runner.jsonl
(docs/interfaces/event-log.md), and keeps the run alive:

- The thread id goes to runner-state.json the moment Codex reports it, so a crash at any
  point (even in the first turn) resumes the same thread instead of starting over.
- Time and token budgets are checked mid-turn; hitting one interrupts the turn. Time is
  active time: it excludes runner downtime, PAUSE and the host being down, but counts
  backoff. Tokens are uncached input plus output (thread totals from Codex).
- The agent's workspace is snapshotted to checkpoints/ (not mounted into the container)
  every 30 minutes and at the end of the run.
- A STOP file in the run dir ends the run cleanly. A PAUSE file holds it (interrupting
  the current turn) until removed.
- Failures back off 30 s, 60 s, ... up to 1 h, and the run gives up after 12 in a row. When
  Codex reports a rate limit reached with a reset time, the backoff waits until then instead.
  A completed turn, or any turn longer than 10 minutes, resets the count.
- No turn starts while the host is down; a host that goes down mid-turn interrupts it.
- Every turn after the first gets the same fixed CONTINUE_PROMPT, except a wake-up: when the tool
  server writes WAKE (an urgent notice from one of the agent's own rules or tasks), the runner
  interrupts the agent's thinking and starts a turn that tells it what happened. While the agent is
  in a tool call, the notice reaches it on that call's result, so the runner waits (wake_grace)
  before interrupting; wake-ups are at least wake_cooldown apart.

Run commands: runner/README.md.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tarfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from toolserver.events import EventLog
from toolserver.extensions import HostConfig

from . import agent, hostcheck
from .appserver import AppServer, AppServerError
from .mapping import Mapper
from .state import RunLock, RunState, kill_tree, pid_alive
from .toolserver_proc import ToolServer, ToolServerError

NOTES_HINT = ("\n\nKeep notes on your progress in /home/agent/work/NOTES.md (/home/agent/work survives "
              "restarts). Your session may be interrupted and resumed at any time.")

CONTINUE_PROMPT = (
    "Your previous turn has ended: it finished, was interrupted, or the harness restarted. "
    "Keep working toward the run goal. Your context may have been compacted or reset, so first "
    "rebuild your picture of where things stand: re-read the brief, goal and host notes in "
    "/home/agent/work/run/ (BRIEF.md, GOAL.md, HOST.md) and your notes in /home/agent/work/NOTES.md, "
    "call status (your goal, running tasks and rules, notices), check which hooks and tools you have "
    "(list_hooks), and take a fresh observation of the host before "
    "you act. Restate your current goal with set_goal. Then continue from where your notes say "
    "you are, and keep the notes current.")
WAKE_PROMPT = (
    "You were interrupted because something you asked to be woken for happened:\n{notices}\n"
    "Your context is intact. Deal with it if it needs you, then carry on with what you were doing.")
WAKE = "WAKE"   # written by the tool server while urgent notices wait for the agent
DEFAULT_BRIEF = agent.REPO / "prompts" / "brief.md"
RUN_FILES = "run"   # workspace/run/: BRIEF.md, GOAL.md, HOST.md, rewritten on every runner start
RATE_LIMIT_MARGIN_S = 60
INPUT_BLOCKED = "INPUT_BLOCKED"   # written by the tool server while input can't reach the host


class Stalled(Exception):
    pass


@dataclass
class Config:
    run_dir: Path
    host_config: Path                         # hosts/<host>.toml: which program, how to check it
    goal: str | None = None
    brief: str | None = None                  # how the agent works here; comes before the goal
    run_id: str | None = None
    agent_cmd: list[str] | None = None        # None: the agent container (docker)
    auth_json: Path = field(default_factory=lambda: Path.home() / ".codex" / "auth.json")
    image: str = agent.DEFAULT_IMAGE
    model: str | None = None
    reasoning_summary: str = "detailed"
    compact_tokens: int | None = 200_000      # Codex compacts the thread past this many context tokens
    port: int = 8765
    toolserver: bool = True
    host_check: hostcheck.HostCheck | None = None  # None: the host config's up_check
    cwd: str = "/home/agent/work"
    budget_seconds: float | None = None
    budget_tokens: int | None = None          # uncached input + output
    until: float | None = None                # wall-clock deadline (epoch seconds): end then
    update_note: str | None = None            # told to the agent with the next prompt (why things changed)
    update_instructions: bool = False         # on resume, replace the run's brief/goal with these
    checkpoint_every: float = 1800
    backoff_base: float = 30
    backoff_cap: float = 3600
    max_failures: int = 12
    long_turn_seconds: float = 600
    poll: float = 1.0                         # how often mid-turn checks run
    host_poll: float = 30                     # how often the host is checked
    start_retry: float = 600                  # after a failed [host] start, wait this long to retry
    stall_timeout: float = 1800               # no message at all for this long: kill and retry
    interrupt_grace: float = 60               # turn/interrupt must end the turn within this
    wake_grace: float = 60                    # a WAKE during a tool call waits this long for its result
    wake_cooldown: float = 60                 # least time between wake-up interrupts
    request_timeout: float = 120
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.time


@dataclass
class Outcome:
    status: str                    # completed | interrupted | failed
    duration_s: float | None = None
    error: str | None = None
    interrupt: str | None = None   # why we interrupted it, if we did


class Runner:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.run_dir = cfg.run_dir = cfg.run_dir.resolve()  # docker mounts need absolute paths
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "raw").mkdir(exist_ok=True)
        (self.run_dir / "workspace").mkdir(exist_ok=True)
        self.state_path = self.run_dir / "runner-state.json"
        self.lock = RunLock(self.run_dir / "runner.lock")
        cfg.host_config = cfg.host_config.resolve()
        self.host = HostConfig.load(cfg.host_config, self.run_dir)
        self.host_check = cfg.host_check or hostcheck.from_config(self.host, cfg.host_config.parent)
        self.srv: AppServer | None = None
        self.toolserver: ToolServer | None = None
        self.state: RunState
        self.log: EventLog
        self.mapper: Mapper
        self._last_tick: float | None = None   # active-time clock; None after a (re)start
        self._last_wake = 0.0
        self._counting = True

    # Top level ------------------------------------------------------------------

    def run(self) -> str:
        self.lock.acquire()
        try:
            return self._run()
        except KeyboardInterrupt:
            self.log.write("marker", label="runner stopped by signal; run is resumable",
                           kind="runner_signal")
            raise
        finally:
            self._close_session()
            if self.toolserver:
                self.toolserver.stop()
            self.lock.release()

    def _run(self) -> str:
        existing = RunState.load(self.state_path)
        run_id = (existing.run_id if existing else None) or self.cfg.run_id or self.run_dir.name
        self.log = EventLog(self.run_dir / "runner.jsonl", run_id, writer="runner")
        self.mapper = Mapper(self.log)
        self.mapper.on_usage = self._save_usage
        if existing:
            self.state = existing
            if self.state.status == "ended":
                print(f"run already ended: {self.state.ended_reason}")
                return self.state.ended_reason or "ended"
            self.mapper.total = dict(self.state.extra.get("usage_total") or {})
            lost = f"; turn {self.state.turns} was cut off" if self.state.active_turn_id else ""
            self.log.write("marker", label=f"runner restarted, resuming thread {self.state.thread_id}{lost}",
                           kind="runner_restart")
            if self.state.active_turn_id:  # close it, with the tokens it used before the crash
                self.mapper.restore_turn(self.state.turns, self.state.active_turn_id,
                                         self.state.extra.get("turn_base") or {})
                self.mapper.end_turn("cut_off", None, "the runner stopped during this turn")
            self.state.active_turn_id = None
            self._apply_updates()
            self._save()
        else:
            if not self.cfg.goal:
                raise SystemExit("a new run needs --goal or --goal-file")
            self.state = RunState(run_id=run_id, started_at=self.cfg.clock(),
                                  extra={"goal": self.cfg.goal, "brief": self._brief(), "host": self.host.name})
            self._save()
        self._write_run_files()

        if self.cfg.toolserver:
            old = self.state.extra.get("toolserver_pid")
            if old and pid_alive(old):  # orphaned by a runner that crashed
                kill_tree(old)
                self.log.write("marker", label=f"killed tool server {old} left by a crashed runner",
                               kind="toolserver_restart")
            self.toolserver = ToolServer(self.run_dir, run_id, self.cfg.port, self.cfg.host_config)
            self._start_toolserver()

        self.state.extra.setdefault("last_snapshot_at", self.cfg.clock())
        while True:
            self._set_counting(True)
            reason = self._end_reason()
            if reason:
                return self._end(reason)
            self._maybe_snapshot()
            if (self.run_dir / "PAUSE").exists():
                self._set_counting(False)
                self.mapper.state("paused", "PAUSE file present")
                self._wait(self.cfg.host_poll)
                continue
            blocked = self._input_blocked()
            if blocked is not None:
                # The tool server can't deliver input (it retries and removes the file). Prompting
                # the agent would only burn turns, so wait like a down host; the time doesn't count.
                self._set_counting(False)
                self.mapper.state("input_blocked", blocked)
                self._wait(self.cfg.host_poll)
                continue
            up, detail = self.host_check()
            if not up and self._start_host(detail):
                up, detail = self.host_check()
            if not up:
                self._set_counting(False)
                self.mapper.state("host_down", detail)
                self._wait(self.cfg.host_poll)
                continue
            if self.toolserver and not self.toolserver.alive():
                self.log.write("marker", label="tool server died; restarting", kind="toolserver_restart")
                self._start_toolserver()

            try:
                self._ensure_session()
                outcome = self._run_turn()
            except (AppServerError, ToolServerError, Stalled) as e:
                outcome = Outcome("failed", error=f"{type(e).__name__}: {e}")
                if self.mapper.turn is not None:  # the turn died with the process
                    self.mapper.end_turn("failed", None, outcome.error)
                    self.state.active_turn_id = None
                self._close_session()

            if outcome.interrupt in ("host_down", "input_blocked", "paused", "stop", "budget_time", "budget_tokens",
                                     "wake"):
                continue  # not a failure; the loop top decides what happens next
            if outcome.duration_s is not None and outcome.duration_s > self.cfg.long_turn_seconds:
                self.state.failures = 0
            if outcome.status == "completed":
                self.state.failures = 0
                self._save()
                continue
            reset = self._rate_limit_reset()
            extra: dict = {}
            if reset is not None:
                # A rate limit is not the run failing, so it doesn't count toward max_failures.
                delay = reset - self.cfg.clock() + RATE_LIMIT_MARGIN_S
                extra = {"until": _iso(reset), "cause": "rate_limit"}
                why = f"rate limit reached; waiting until it resets at {_iso(reset)}"
            else:
                self.state.failures += 1
                self._save()
                if self.state.failures >= self.cfg.max_failures:
                    return self._end("too_many_failures")
                delay = min(self.cfg.backoff_base * 2 ** (self.state.failures - 1), self.cfg.backoff_cap)
                why = f"retrying in {delay:.0f} s"
            what = "rate limited" if reset is not None else f"failure {self.state.failures}"
            self.mapper.state("backing_off", f"{what}: {outcome.error or outcome.status}; {why}",
                              delay_s=round(delay, 3), failures=self.state.failures, **extra)
            self._wait(delay)

    # One turn -------------------------------------------------------------------

    def _run_turn(self) -> Outcome:
        assert self.srv
        wake = self.state.extra.pop("pending_wake", None)
        if self.state.goal_sent and wake:
            prompt, kind = WAKE_PROMPT.format(notices=wake), "wake"
        elif self.state.goal_sent:
            prompt, kind = CONTINUE_PROMPT, "continue"
            update = self.state.extra.pop("pending_update", None)
            if update:
                prompt, kind = update + "\n\n" + prompt, "update"
        else:
            prompt, kind = self.first_prompt(), "goal"
        res = self.srv.request("turn/start", {"threadId": self.state.thread_id,
                                              "input": [{"type": "text", "text": prompt}]},
                               timeout=self.cfg.request_timeout)
        turn_id = res["turn"]["id"]
        self.state.turns += 1
        self.state.goal_sent = True
        self.state.active_turn_id = turn_id
        self.mapper.begin_turn(self.state.turns, turn_id)
        self.state.extra["turn_base"] = dict(self.mapper.turn_base)
        self._save()
        self.log.write("marker", label=f"prompt: {kind}", kind=f"prompt_{kind}")

        t0 = last = self.cfg.clock()
        next_host = t0 + self.cfg.host_poll
        interrupt: str | None = None
        interrupt_deadline = 0.0
        while True:
            msg = self.srv.next_message(self.cfg.poll)
            now = self.cfg.clock()
            if msg:
                last = now
                self.mapper.handle(msg)
                if msg.get("method") == "turn/completed":
                    turn = (msg.get("params") or {}).get("turn") or {}
                    if turn.get("id") == turn_id:
                        break
            elif now - last > self.cfg.stall_timeout:
                raise Stalled(f"no message from the app-server for {self.cfg.stall_timeout:.0f} s")
            self._maybe_snapshot()
            if interrupt is None:
                check_host = now >= next_host
                if check_host:
                    next_host = now + self.cfg.host_poll
                interrupt = self._interrupt_reason(check_host)
                if interrupt == "wake":
                    self.state.extra["pending_wake"] = self._take_wake()
                    self._last_wake = now
                    self._save()
                if interrupt:
                    self.srv.send_request("turn/interrupt", {"threadId": self.state.thread_id,
                                                             "turnId": turn_id})
                    self.log.write("marker", label=f"interrupting turn {self.state.turns}: {interrupt}",
                                   kind="interrupt")
                    interrupt_deadline = now + self.cfg.interrupt_grace
            elif now > interrupt_deadline:
                raise AppServerError(f"turn did not end {self.cfg.interrupt_grace:.0f} s after turn/interrupt")

        status = turn.get("status") or "failed"
        duration_ms = turn.get("durationMs")
        if duration_ms is None:
            duration_ms = round((self.cfg.clock() - t0) * 1000)
        error = ((turn.get("error") or {}).get("message")) or (self.mapper.last_error if status == "failed" else None)
        self.mapper.end_turn(status, duration_ms, error)
        if status == "completed":
            self.state.extra["last_turn_id"] = turn_id
            self.log.write("checkpoint", label=f"turn {self.state.turns} completed",
                           ref=f"{self.state.thread_id}/{turn_id}")
        self.state.active_turn_id = None
        self.state.extra["usage_total"] = self.mapper.total
        self._save()
        return Outcome(status, duration_ms / 1000, error, interrupt)

    def _interrupt_reason(self, check_host: bool) -> str | None:
        reason = self._end_reason()
        if reason:
            return reason
        if (self.run_dir / "PAUSE").exists():
            return "paused"
        if self._input_blocked() is not None:
            return "input_blocked"
        if self._wake_due():
            return "wake"
        if check_host:
            up, detail = self.host_check()
            if not up:
                self.mapper.state("host_down", detail)
                return "host_down"
        return None

    def _wake_due(self) -> bool:
        """An urgent notice is waiting: wake the agent now if it is thinking, or once a tool call
        has had wake_grace to deliver it on its result."""
        p = self.run_dir / WAKE
        try:
            age = self.cfg.clock() - p.stat().st_mtime
        except OSError:
            return False
        if self.cfg.clock() - self._last_wake < self.cfg.wake_cooldown:
            return False
        return self.mapper.current != "acting" or age >= self.cfg.wake_grace

    def _take_wake(self) -> str:
        p = self.run_dir / WAKE
        try:
            items = json.loads(p.read_text(encoding="utf-8"))
            p.unlink(missing_ok=True)
        except (OSError, ValueError):
            return "- (the notice could not be read; call status)"
        return "\n".join(f"- {i.get('source', '?')}: {i.get('text', '')}" for i in items)[:2000]

    def _input_blocked(self) -> str | None:
        """The tool server's INPUT_BLOCKED detail while input can't reach the host, else None."""
        p = self.run_dir / INPUT_BLOCKED
        try:
            return json.loads(p.read_text(encoding="utf-8")).get("detail") or "input blocked"
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return "input blocked"

    def _apply_updates(self) -> None:
        """On resume: a changed --brief-file or --goal-file replaces the run's copy, and the next
        prompt tells the agent what changed (plus --update-note), so instructions can be tuned
        during a long run without restarting it."""
        changed = []
        update = self.cfg.update_instructions
        if update and self.cfg.brief is not None and self.cfg.brief != self.state.extra.get("brief"):
            self.state.extra["brief"] = self.cfg.brief
            changed.append("BRIEF.md")
        if update and self.cfg.goal and self.cfg.goal != self.state.extra.get("goal"):
            self.state.extra["goal"] = self.cfg.goal
            changed.append("GOAL.md")
        if not changed and not self.cfg.update_note:
            return
        parts = []
        if changed:
            parts.append(f"The run's instructions were updated: {' and '.join(changed)} in /home/agent/work/run/ "
                         "changed. Re-read them now; where they differ from what you remember, the new "
                         "version wins.")
        if self.cfg.update_note:
            parts.append("Note from the people running this harness: " + self.cfg.update_note.strip())
        self.state.extra["pending_update"] = " ".join(parts)
        self.log.write("marker", label="instructions updated: " + (", ".join(changed) or "note only")
                       + (f"; note: {self.cfg.update_note.strip()}" if self.cfg.update_note else ""),
                       kind="instructions_updated")

    def _end_reason(self) -> str | None:
        if (self.run_dir / "STOP").exists():
            return "stop"
        if self.cfg.until is not None and self.cfg.clock() >= self.cfg.until:
            return "deadline"
        self._tick()
        if self.cfg.budget_seconds is not None and self.active_seconds() >= self.cfg.budget_seconds:
            return "budget_time"
        if self.cfg.budget_tokens is not None and self.mapper.uncached_tokens() >= self.cfg.budget_tokens:
            return "budget_tokens"
        return None

    # Active time ----------------------------------------------------------------

    def active_seconds(self) -> float:
        return float(self.state.extra.get("active_s", 0.0))

    def _tick(self) -> None:
        """Add the time since the last tick if it counts. The first tick after a (re)start
        adds nothing, so runner downtime never counts."""
        now = self.cfg.clock()
        if self._last_tick is not None and self._counting:
            self.state.extra["active_s"] = self.active_seconds() + max(0.0, now - self._last_tick)
        self._last_tick = now

    def _set_counting(self, on: bool) -> None:
        self._tick()
        self._counting = on

    # Rate limits ----------------------------------------------------------------

    def _rate_limit_reset(self) -> float | None:
        """When a rate limit that Codex reported as reached resets (unix s), if still ahead.
        The full windows (usedPercent >= 100) decide it, latest reset first. When Codex says a
        limit was reached but no window reads full, wait for the soonest reset, not the weekly."""
        rl = self.mapper.rate_limits or {}
        reached = bool(rl.get("rateLimitReachedType")) or self.mapper.last_error_info == "usageLimitExceeded"
        now = self.cfg.clock()
        windows = [w for w in (rl.get("primary"), rl.get("secondary"))
                   if isinstance(w, dict) and w.get("resetsAt") and float(w["resetsAt"]) > now]
        full = [float(w["resetsAt"]) for w in windows if (w.get("usedPercent") or 0) >= 100]
        if full:
            return max(full)
        if reached and windows:
            return min(float(w["resetsAt"]) for w in windows)
        return None

    # Checkpoints ----------------------------------------------------------------

    def _maybe_snapshot(self, force: str | None = None) -> None:
        now = self.cfg.clock()
        last = self.state.extra.get("last_snapshot_at", now)
        if force is None and now - last < self.cfg.checkpoint_every:
            return
        n = int(self.state.extra.get("snapshots", 0)) + 1
        self.state.extra.update(last_snapshot_at=now, snapshots=n)
        self._save()
        label = force or "periodic"
        rel = f"checkpoints/{n:04d}-{label}.tar.gz"
        ref = f"{self.state.thread_id or ''}/{self.state.extra.get('last_turn_id') or ''}"
        try:
            files, size = _snapshot(self.run_dir / "workspace", self.run_dir / rel)
        except OSError as e:
            self.log.write("checkpoint", label=f"workspace snapshot failed: {e}", ref=ref, artifact=None)
            return
        self.log.write("checkpoint", label=f"workspace snapshot ({label})", ref=ref, artifact=rel,
                       files=files, bytes=size)

    # Session --------------------------------------------------------------------

    def _ensure_session(self) -> None:
        if self.srv and self.srv.alive():
            return
        self._close_session()
        if self.cfg.agent_cmd:
            cmd = self.cfg.agent_cmd
        else:
            agent.remove_container(self.state.run_id)
            cmd = agent.docker_cmd(self.run_dir, self.state.run_id, self.cfg.auth_json, self.cfg.port,
                                   self.cfg.image, self.cfg.model, self.cfg.reasoning_summary,
                                   self.cfg.compact_tokens)
        self.srv = AppServer(cmd, raw_log=self.run_dir / "raw" / "appserver.jsonl",
                             stderr_path=self.run_dir / "raw" / "appserver.stderr.log")
        self.srv.initialize()
        if self.state.thread_id:
            res = self.srv.request("thread/resume", {"threadId": self.state.thread_id, "excludeTurns": True},
                                   timeout=self.cfg.request_timeout)
        else:
            res = self.srv.request("thread/start", {"cwd": self.cfg.cwd}, timeout=self.cfg.request_timeout)
            self.state.thread_id = res["thread"]["id"]
            self._save()  # before anything else can go wrong
        self.state.model = res.get("model") or self.state.model
        self._save()
        if not self.state.run_started_logged:
            self.log.write("run_started", agent="codex", model=self.state.model, host=self.state.extra.get("host"),
                           goal=self.state.extra.get("goal"), thread_id=self.state.thread_id)
            self.state.run_started_logged = True
            self._save()

    def _close_session(self) -> None:
        if self.srv:
            self.srv.close(grace=10)
            self.srv = None
        if not self.cfg.agent_cmd and hasattr(self, "state"):
            agent.remove_container(self.state.run_id)

    # Helpers --------------------------------------------------------------------

    def _save_usage(self, total: dict) -> None:
        self.state.extra["usage_total"] = dict(total)
        self._save()

    def _start_toolserver(self) -> None:
        assert self.toolserver
        self.toolserver.start()
        self.state.extra["toolserver_pid"] = self.toolserver.proc.pid
        self._save()

    def _end(self, reason: str) -> str:
        self._maybe_snapshot(force="end")
        if self.host.stop:
            ok, what = self._host_cmd(self.host.stop, "stop", 120)
            self.log.write("marker", label=f"stopped the host: {what}", kind="host_stop", ok=ok)
        self.mapper.state("ended", reason)
        self.log.write("run_ended", reason=reason, turns=self.state.turns,
                       total_tokens=self.mapper.total_tokens(), uncached_tokens=self.mapper.uncached_tokens(),
                       active_s=round(self.active_seconds()))
        self.state.status, self.state.ended_reason = "ended", reason
        self._save()
        return reason

    # The host ----------------------------------------------------------------------

    def _brief(self) -> str:
        if self.cfg.brief is not None:
            return self.cfg.brief
        return DEFAULT_BRIEF.read_text(encoding="utf-8") if DEFAULT_BRIEF.exists() else ""

    def first_prompt(self) -> str:
        """Brief, then goal, then host notes, then the notes hint."""
        brief = self.state.extra.get("brief")
        parts = [(brief if brief is not None else self._brief()).strip(), self.state.extra["goal"].strip()]
        if self.host.notes:
            parts.append("## About this host\n\n" + self.host.notes)
        return "\n\n".join(p for p in parts if p) + NOTES_HINT

    def _write_run_files(self) -> None:
        """workspace/run/: the brief, goal and host notes, restored on every runner start so the
        agent can re-read them after a reset (its own edits there don't stick)."""
        d = self.run_dir / "workspace" / RUN_FILES
        d.mkdir(parents=True, exist_ok=True)
        brief = self.state.extra.get("brief")
        files = {"BRIEF.md": brief if brief is not None else self._brief(),
                 "GOAL.md": self.state.extra.get("goal") or "",
                 "HOST.md": self.host.notes or "(no notes for this host)"}
        for name, text in files.items():
            (d / name).write_text(text.strip() + "\n", encoding="utf-8")

    def _start_host(self, why: str) -> bool:
        """Run the host config's start command; True if it succeeded. Time spent starting isn't
        active time. After a failure it waits start_retry before trying again."""
        last = self.state.extra.get("host_start_failed_at")
        if not self.host.start or (last is not None and self.cfg.clock() - last < self.cfg.start_retry):
            return False
        self._set_counting(False)
        self.mapper.state("host_starting", why)
        ok, what = self._host_cmd(self.host.start, "start", self.host.start_timeout_s)
        self.log.write("marker", label=f"started the host: {what}" if ok else f"could not start the host: {what}",
                       kind="host_start", ok=ok)
        self.state.extra["host_start_failed_at"] = None if ok else self.cfg.clock()
        self._save()
        self._set_counting(True)  # the start itself didn't count
        return ok

    def _host_cmd(self, argv: list[str], what: str, timeout: float) -> tuple[bool, str]:
        """Output goes to raw/host.log."""
        shown = " ".join(argv)
        t0 = time.monotonic()
        with (self.run_dir / "raw" / "host.log").open("a", encoding="utf-8") as out:
            out.write(f"--- {what} {_iso(self.cfg.clock())}: {shown}\n")
            out.flush()
            try:
                rc = subprocess.run(argv, cwd=self.host.config_dir, stdout=out, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, timeout=timeout).returncode
            except subprocess.TimeoutExpired:
                return False, f"`{shown}` timed out after {timeout:.0f} s"
            except OSError as e:
                return False, f"`{shown}` could not run: {e}"
        return rc == 0, f"`{shown}` exited {rc} after {time.monotonic() - t0:.0f} s (output in raw/host.log)"

    def _wait(self, seconds: float) -> None:
        """Sleep, waking early if STOP appears."""
        left = seconds
        while left > 0:
            if (self.run_dir / "STOP").exists():
                return
            step = min(left, 1.0)
            self.cfg.sleep(step)
            left -= step
            self._tick()

    def _save(self) -> None:
        self.state.save(self.state_path)


def _snapshot(src: Path, dest: Path) -> tuple[int, int]:
    """tar.gz of the workspace, written beside the run's logs, where the agent can't reach it."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    files = 0
    with tarfile.open(tmp, "w:gz") as tar:
        for path in sorted(src.rglob("*")):
            if path.is_file() and not path.is_symlink():
                tar.add(path, arcname=path.relative_to(src).as_posix())
                files += 1
    tmp.replace(dest)
    return files, dest.stat().st_size


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# CLI ----------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Drive one long Codex session. See runner/README.md.")
    ap.add_argument("--run-dir", type=Path, help="default: runs/<UTC stamp>; pass an existing one to resume")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--goal", help="the run's objective (first prompt)")
    g.add_argument("--goal-file", type=Path)
    ap.add_argument("--brief-file", type=Path, default=DEFAULT_BRIEF,
                    help="how the agent works here; the first prompt is brief, goal, host notes")
    ap.add_argument("--host-config", type=Path, required=True,
                    help="hosts/<host>.toml: the program the agent works in, how to see it and check it is up")
    ap.add_argument("--host-check", help="override the host config's up_check: "
                    "window | always | cmd:<command> | py:<module>:<fn>")
    ap.add_argument("--model")
    ap.add_argument("--reasoning-summary", default="detailed", choices=["auto", "concise", "detailed", "none"])
    ap.add_argument("--compact-tokens", type=int, default=200_000,
                    help="compact the agent's thread once its context passes this many tokens (0: model default)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--image", default=agent.DEFAULT_IMAGE)
    ap.add_argument("--auth-json", type=Path, default=Path.home() / ".codex" / "auth.json")
    ap.add_argument("--budget-hours", type=float)
    ap.add_argument("--budget-tokens", type=int, help="uncached input + output tokens (Codex thread totals)")
    ap.add_argument("--until", help="wall-clock deadline, ISO 8601 (e.g. 2026-10-24T15:00:00Z): the run ends then")
    ap.add_argument("--update-instructions", action="store_true",
                    help="on resume: replace the run's brief and goal with --brief-file/--goal-file and tell the agent")
    ap.add_argument("--update-note", help="on resume: a note told to the agent with the next prompt, e.g. why "
                    "the brief changed")
    ap.add_argument("--no-toolserver", action="store_true")
    ap.add_argument("--agent-cmd", help="JSON list; replaces the agent container (tests, fakes)")
    ap.add_argument("--stall-timeout", type=float, default=1800)
    ap.add_argument("--build", action="store_true", help="docker build the agent image first")
    args = ap.parse_args(argv)

    run_dir = args.run_dir or agent.REPO / "runs" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    goal = args.goal_file.read_text(encoding="utf-8") if args.goal_file else args.goal
    if args.build and not args.agent_cmd:
        if subprocess.run(["docker", "build", "-t", args.image, "--build-arg",
                           f"CODEX_VERSION={agent.CODEX_VERSION}", str(agent.REPO / "agent")]).returncode:
            print("docker build failed", file=sys.stderr)
            return 3
    if not args.agent_cmd and not args.auth_json.exists():
        print(f"{args.auth_json} not found; run `codex login` first", file=sys.stderr)
        return 3
    hc = HostConfig.load(args.host_config, run_dir)
    cfg = Config(run_dir=run_dir, host_config=args.host_config, goal=goal, model=args.model,
                 brief=args.brief_file.read_text(encoding="utf-8"),
                 reasoning_summary=args.reasoning_summary, compact_tokens=args.compact_tokens or None, port=args.port, image=args.image,
                 auth_json=args.auth_json, toolserver=not args.no_toolserver,
                 agent_cmd=json.loads(args.agent_cmd) if args.agent_cmd else None,
                 host_check=hostcheck.resolve(args.host_check, hc) if args.host_check else None, stall_timeout=args.stall_timeout,
                 budget_seconds=args.budget_hours * 3600 if args.budget_hours else None,
                 budget_tokens=args.budget_tokens, update_note=args.update_note,
                 update_instructions=args.update_instructions,
                 until=datetime.fromisoformat(args.until.replace("Z", "+00:00")).timestamp() if args.until else None)
    print(f"run dir: {run_dir}")
    reason = Runner(cfg).run()
    print(f"run ended: {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
