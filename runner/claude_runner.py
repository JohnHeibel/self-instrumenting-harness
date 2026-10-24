"""Runner for Claude Code: one long session, driven as a series of `claude -p` turns in the agent container.

Counterpart of runner/src/runner/run.py (Codex app-server), host-generic, same run directory layout and
event log v1 (docs/interfaces/event-log.md). Run it with the runner project's environment:

    uv run --project runner python runner/claude_runner.py --host-config hosts/<host>.toml \
        --run-dir runs/<run> --goal-file goals/<goal>.md --budget-hours 8 --budget-tokens 50000000

Resume after a crash or Ctrl-C: the same command with the same --run-dir (the session id is saved).

How it works
- One Claude Code *session* for the whole run: turn 1 is `claude -p <brief+goal>`, every later turn is
  `claude -p --resume <session_id> <continue prompt>`. The session lives in <run>/claude-home (mounted at
  /home/agent/.claude), so it survives container, runner and machine restarts. Claude Code compacts the
  context itself (`compact_boundary` events are logged). One container per turn: the tool server (a child of
  this runner) outlives them, and MCP reconnects each turn.
- Credentials: `docker run --env-file ~/.sisyphus/claude.env` (CLAUDE_CODE_OAUTH_TOKEN=...). This runner never
  reads the file; it only checks that it exists.
- Budgets (--budget-hours: active time; --budget-tokens: uncached input + output) are checked while a turn
  streams; hitting one kills the container and ends the run. --until is a wall-clock deadline.
- <run>/STOP ends the run; <run>/PAUSE holds it between/within turns until removed.
- A turn that fails or stalls (no output for --stall-timeout) is retried after a backoff (30 s doubling to
  1 h; 12 in a row ends the run). A runner crash mid-turn is closed with `turn_completed status=cut_off`.
- Writes runner.jsonl (writer "runner"); raw stream-json goes to raw/claude-stream.jsonl.
"""

from __future__ import annotations

import argparse
import json
import queue
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from runner import agent, hostcheck
from runner.state import RunLock, RunState
from runner.toolserver_proc import ToolServer, ToolServerError
from toolserver.events import EventLog
from toolserver.extensions import HostConfig

REPO = agent.REPO
CLAUDE_DIR = REPO / "agent" / "claude"
CLAUDE_VERSION = __import__("re").search(r"ARG CLAUDE_CODE_VERSION=(\S+)",
                                         (CLAUDE_DIR / "Dockerfile").read_text(encoding="utf-8")).group(1)
DEFAULT_IMAGE = f"harness-agent:claude-{CLAUDE_VERSION}"
DEFAULT_ENV_FILE = Path.home() / ".sisyphus" / "claude.env"
DEFAULT_BRIEF = REPO / "prompts" / "brief.md"
NOTES_HINT = ("\n\nKeep notes on your progress in /home/agent/work/NOTES.md (/home/agent/work survives "
              "restarts). Your session may be interrupted and resumed at any time.")
CONTINUE_PROMPT = (
    "Your previous turn has ended: it finished, was interrupted, or the harness restarted. "
    "Keep working toward the run goal. Your context may have been compacted or reset, so first "
    "rebuild your picture of where things stand: re-read the brief, goal and host notes in "
    "/home/agent/work/run/ (BRIEF.md, GOAL.md, HOST.md) and your notes in /home/agent/work/NOTES.md, "
    "check which hooks and tools you have (list_hooks), and take a fresh observation of the host before "
    "you act. Restate your current goal with set_goal. Then continue from where your notes say "
    "you are, and keep the notes current. Do not stop to ask questions: nobody is watching.")
SKILLS_INDEX = "skills/INDEX.md"
SKILLS_CAP = 3000


def skills_note(workspace: Path) -> str:
    """Text for the continue prompt that puts the agent's skill index back in front of it after a
    restart or compaction. Empty when the agent has not written one."""
    p = workspace / SKILLS_INDEX
    try:
        text = p.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    if not text:
        return ""
    cut = len(text) > SKILLS_CAP
    return ("Your skill library index (/home/agent/work/" + SKILLS_INDEX + "). Use these saved scripts and "
            "tools before doing a chore by hand, and add to it when you save a new one:\n\n"
            + text[:SKILLS_CAP] + ("\n... (index cut; read the file for the rest)" if cut else ""))


IDLE_NOTE = ("Idle check: for {m:.0f} minutes nothing on the host screen changed and you called no harness tool. "
             "If you are waiting on a background job, assume it is stuck until it proves otherwise: your shell "
             "jobs were stopped by this restart, so look at the host now (a screenshot or a state read) and see "
             "what is really happening. Run actions in the foreground with progress printed every few seconds, "
             "give any background job a progress line you check within 60 seconds, and kill a job that shows no "
             "progress in 2 minutes. Never wait more than about 2 minutes without observing the host.")


HANDOFF_ASK = ("Context handoff: your context is large, which makes every step slower and dearer, so this session "
               "will be replaced by a fresh one. Do only this now, in a few steps: bring /home/agent/work/NOTES.md "
               "fully up to date (current goal and subgoal, what is done, exactly what you are in the middle of, "
               "what comes next, lessons and known problems) and /home/agent/work/skills/INDEX.md (one line per "
               "skill: what it does, how to call it, known failures). Then end your turn with the single line "
               "'handoff ready'. Do not start new work.")
HANDOFF_TIMEOUT_S = 300


def handoff_trigger(extra: dict, context: int, limit: int) -> bool:
    """True when a handoff should start: the feature is on, the context is over the limit, none in progress."""
    if limit <= 0 or context < limit or extra.get("handoff"):
        return False
    extra["handoff"] = "wanted"
    return True


def handoff_begin_turn(extra: dict) -> str:
    """At the start of a turn: the ask to append to the prompt, if a handoff was wanted."""
    if extra.get("handoff") == "wanted":
        extra["handoff"] = "asked"
        return HANDOFF_ASK
    return ""


def handoff_end_turn(extra: dict, failed: bool) -> bool:
    """After a turn: when the asking turn is over, the next turn starts a fresh session (returns True)."""
    if extra.get("handoff") == "asked":
        if failed:
            extra["handoff"] = "wanted"   # ask again on the next turn
            return False
        extra["handoff"] = ""
        extra["fresh_session"] = True
        return True
    return False


class IdleWatch:
    """Notices a stall that the agent's own waiting hides: the host's picture (live/latest.jpg) is unchanged
    and the tool server logged no call for `idle_s` seconds. Host-generic; off when idle_s <= 0."""

    def __init__(self, run_dir: Path, idle_s: float, clock=time.monotonic) -> None:
        self.run_dir, self.idle_s, self.clock = run_dir, idle_s, clock
        self.sig = None
        self.since = clock()

    def _signature(self):
        try:
            ev = (self.run_dir / "events.jsonl").stat().st_size
        except OSError:
            ev = 0
        try:
            pic = hash((self.run_dir / "live" / "latest.jpg").read_bytes())
        except OSError:
            pic = None
        return ev, pic

    def reset(self) -> None:
        self.sig, self.since = self._signature(), self.clock()

    def idle(self) -> bool:
        if self.idle_s <= 0:
            return False
        sig = self._signature()
        if sig != self.sig:
            self.sig, self.since = sig, self.clock()
            return False
        return self.clock() - self.since >= self.idle_s


def take_update_note(run_dir: Path) -> str:
    """Advice for the agent written by a human or critic into <run>/UPDATE_NOTE.md. Delivered once, with the
    next continue prompt, then renamed to UPDATE_NOTE.md.sent so it is kept for the record."""
    p = run_dir / "UPDATE_NOTE.md"
    try:
        text = p.read_text(encoding="utf-8").strip()
        p.rename(p.with_name(p.name + ".sent"))
    except OSError:
        return ""
    return ("Update from the people running this harness (guidance changed since your brief was written; it "
            "applies from now on):\n" + text) if text else ""


SETTINGS_IN_IMAGE = "/opt/harness/claude-settings.json"
MCP_IN_CONTAINER = "/opt/harness/mcp.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def container_name(run_id: str) -> str:
    return agent.container_name(run_id)


class Turn:
    """One `claude -p` process in a container, streamed line by line on a reader thread."""

    def __init__(self, cmd: list[str], prompt: str, raw_path: Path, stderr_path: Path) -> None:
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=stderr_path.open("a", encoding="utf-8"), text=True, bufsize=1)
        self.q: queue.Queue = queue.Queue()
        self.raw = raw_path.open("a", encoding="utf-8")
        assert self.proc.stdin
        self.proc.stdin.write(prompt)
        self.proc.stdin.close()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.proc.stdout
        for line in self.proc.stdout:
            self.raw.write(line)
            self.raw.flush()
            try:
                self.q.put(json.loads(line))
            except ValueError:
                pass
        self.q.put(None)

    def kill(self, name: str) -> None:
        subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self.proc.kill()
        except OSError:
            pass


class ClaudeRunner:
    def __init__(self, a: argparse.Namespace) -> None:
        self.a = a
        self.run_dir = a.run_dir.resolve()
        for d in ("raw", "workspace", "claude-home"):
            (self.run_dir / d).mkdir(parents=True, exist_ok=True)
        self.host_config = a.host_config.resolve()
        self.host = HostConfig.load(self.host_config, self.run_dir)
        self.check = (hostcheck.resolve(a.host_check, self.host) if a.host_check
                      else hostcheck.from_config(self.host, self.host_config.parent))
        self.state_path = self.run_dir / "runner-state.json"
        self.lock = RunLock(self.run_dir / "runner.lock")
        self.state = RunState.load(self.state_path) or RunState(
            run_id=a.run_id or self.run_dir.name, started_at=time.time())
        self.log = EventLog(self.run_dir / "runner.jsonl", self.state.run_id, writer="runner")
        self.toolserver: ToolServer | None = None
        self.name = container_name(self.state.run_id)
        self.active_s = float(self.state.extra.get("active_s", 0))
        self.uncached = int(self.state.extra.get("uncached_tokens", 0))
        self.total_tokens = int(self.state.extra.get("total_tokens", 0))
        self.pending_note = ""
        self.context = 0
        self.last_state: tuple | None = None
        self._tick_at = time.monotonic()

    # ---- helpers ----------------------------------------------------------------------

    def save(self) -> None:
        self.state.extra.update(active_s=self.active_s, uncached_tokens=self.uncached,
                                total_tokens=self.total_tokens)
        self.state.save(self.state_path)

    def agent_state(self, state: str, text: str = "", **kw) -> None:
        key = (state, text, tuple(sorted(kw.items())))
        if key != self.last_state:
            self.last_state = key
            self.log.write("agent_state", state=state, text=text, **kw)

    def tick(self) -> None:
        t = time.monotonic()
        self.active_s += t - self._tick_at
        self._tick_at = t

    def pause_clock(self) -> None:
        self.tick()

    def resume_clock(self) -> None:
        self._tick_at = time.monotonic()

    def end_reason(self) -> str | None:
        if (self.run_dir / "STOP").exists():
            return "stop"
        if self.a.budget_hours and self.active_s >= self.a.budget_hours * 3600:
            return "time_budget"
        if self.a.budget_tokens and self.uncached >= self.a.budget_tokens:
            return "token_budget"
        if self.a.until and time.time() >= self.a.until:
            return "deadline"
        return None

    def wait(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end and not self.end_reason():
            time.sleep(1)
            self.tick()

    def paused(self) -> bool:
        return (self.run_dir / "PAUSE").exists()

    # ---- prompts and run files ---------------------------------------------------------

    def brief(self) -> str:
        p = self.a.brief_file
        return p.read_text(encoding="utf-8") if p and p.exists() else ""

    def write_run_files(self) -> None:
        d = self.run_dir / "workspace" / "run"
        d.mkdir(parents=True, exist_ok=True)
        files = {"BRIEF.md": self.state.extra.get("brief", self.brief()),
                 "GOAL.md": self.state.extra.get("goal", ""),
                 "HOST.md": self.host.notes or "(no notes for this host)"}
        for n, t in files.items():
            (d / n).write_text(t.strip() + "\n", encoding="utf-8")

    def first_prompt(self) -> str:
        parts = [self.state.extra["brief"].strip(), self.state.extra["goal"].strip()]
        if self.host.notes:
            parts.append("## About this host\n\n" + self.host.notes)
        return "\n\n".join(p for p in parts if p) + NOTES_HINT

    # ---- docker command ----------------------------------------------------------------

    def write_mcp_config(self) -> Path:
        p = self.run_dir / "mcp.json"
        p.write_text(json.dumps({"mcpServers": {"harness": {
            "type": "http", "url": f"http://host.docker.internal:{self.a.port}/mcp",
            "alwaysLoad": True}}}, indent=2), encoding="utf-8")
        return p

    def docker_cmd(self, resume: str | None) -> list[str]:
        mcp = self.write_mcp_config()
        cmd = ["docker", "run", "-i", "--rm", "--name", self.name,
               "--env-file", str(self.a.env_file),
               "-v", f"{self.run_dir / 'claude-home'}:/home/agent/.claude",
               "-v", f"{self.run_dir / 'workspace'}:/home/agent/work",
               "-v", f"{mcp}:{MCP_IN_CONTAINER}:ro",
               self.a.image,
               "-p", "--input-format", "text", "--output-format", "stream-json", "--verbose",
               "--dangerously-skip-permissions", "--settings", SETTINGS_IN_IMAGE,
               "--mcp-config", MCP_IN_CONTAINER]
        if self.a.model:
            cmd += ["--model", self.a.model]
        if resume:
            cmd += ["--resume", resume]
        return cmd

    # ---- main loop ---------------------------------------------------------------------

    def run(self) -> str:
        self.lock.acquire()
        try:
            return self._run()
        finally:
            if self.toolserver:
                self.toolserver.stop()
            self.lock.release()

    def _run(self) -> str:
        if not self.a.env_file.exists():
            raise SystemExit(f"{self.a.env_file} is missing: create it with CLAUDE_CODE_OAUTH_TOKEN=... "
                             "(from `claude setup-token`)")
        if self.state.status == "ended":
            self.state.status, self.state.ended_reason = "running", None
        if "goal" not in self.state.extra:
            if not (self.a.goal or self.a.goal_file):
                raise SystemExit("a new run needs --goal or --goal-file")
            self.state.extra["goal"] = self.a.goal or self.a.goal_file.read_text(encoding="utf-8")
            self.state.extra["brief"] = self.brief()
        subprocess.run(["docker", "rm", "-f", self.name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if self.state.active_turn_id:    # a crashed runner left a turn open: close it
            self.log.write("turn_completed", turn=self.state.turns, turn_id=self.state.active_turn_id,
                           status="cut_off")
            self.log.write("marker", label="runner restarted", kind="runner_restart")
            self.state.active_turn_id = None
        self.write_run_files()
        self.toolserver = ToolServer(self.run_dir, self.state.run_id, self.a.port, self.host_config)
        try:
            self.toolserver.start()
        except ToolServerError as e:
            raise SystemExit(str(e))
        self.log.write("server_started", host=self.host.name, port=self.a.port)
        self.save()
        reason = "unknown"
        try:
            while True:
                reason = self.end_reason() or ""
                if reason:
                    break
                if self.paused():
                    self.agent_state("paused", "PAUSE file present")
                    self.pause_clock()
                    time.sleep(2)
                    self.resume_clock()
                    continue
                up, detail = self.check()
                if not up:
                    self.agent_state("host_down", detail)
                    if not self._start_host():
                        self.wait(30)
                    continue
                if not self.toolserver.alive():
                    self.log.write("marker", label="tool server died; restarting", kind="toolserver_restart")
                    self.toolserver.start()
                outcome = self.turn()
                if outcome == "ok":
                    self.state.failures = 0
                elif outcome == "failed":
                    self.state.failures += 1
                    if self.state.extra.get("last_error", "").startswith(("Failed to authenticate", "Invalid API key")):
                        reason = "auth_failed"   # a bad or expired token won't fix itself: stop, don't loop all night
                        break
                    if self.state.failures >= self.a.max_failures:
                        reason = "too_many_failures"
                        break
                    delay = min(self.a.backoff_cap, self.a.backoff_base * 2 ** (self.state.failures - 1))
                    self.agent_state("backing_off", f"{delay:.0f} s", delay_s=delay, failures=self.state.failures)
                    self.wait(delay)
                self.save()
                if not self.state.thread_id and self.state.failures and outcome == "failed":
                    continue
        except KeyboardInterrupt:
            subprocess.run(["docker", "rm", "-f", self.name], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
            self.tick()
            self.save()
            self.log.write("marker", label="runner interrupted (Ctrl-C); resumable", kind="interrupt",
                           reason="ctrl-c")
            return "interrupted"
        return self.end(reason or "unknown")

    def _start_host(self) -> bool:
        last = self.state.extra.get("host_start_failed_at")
        if not self.host.start or (last is not None and time.time() - last < 600):
            return False
        self.pause_clock()
        with (self.run_dir / "raw" / "host.log").open("a", encoding="utf-8") as out:
            try:
                rc = subprocess.run(self.host.start, cwd=self.host.config_dir, stdout=out,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    timeout=self.host.start_timeout_s).returncode
            except (subprocess.TimeoutExpired, OSError):
                rc = 1
        self.log.write("marker", label=f"host start exited {rc}", kind="host_start", ok=rc == 0)
        self.state.extra["host_start_failed_at"] = None if rc == 0 else time.time()
        self.resume_clock()
        return rc == 0

    def end(self, reason: str) -> str:
        self.tick()
        subprocess.run(["docker", "rm", "-f", self.name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.agent_state("ended", reason)
        if self.host.stop:
            try:
                subprocess.run(self.host.stop, cwd=self.host.config_dir, timeout=120,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (subprocess.TimeoutExpired, OSError):
                pass
        self.state.status, self.state.ended_reason = "ended", reason
        self.log.write("run_ended", reason=reason, turns=self.state.turns, total_tokens=self.total_tokens,
                       uncached_tokens=self.uncached, active_s=round(self.active_s))
        self.save()
        return reason

    # ---- one turn ----------------------------------------------------------------------

    def turn(self) -> str:
        """Returns ok | failed | interrupted."""
        st = self.state
        first = st.thread_id is None
        fresh = bool(st.extra.pop("fresh_session", False)) and not first
        if fresh:
            # a new session, not a resume: the brief and goal again, then the usual rebuild-from-files prompt
            prompt = self.first_prompt() + "\n\n" + CONTINUE_PROMPT
            self.log.write("marker", label="fresh session after context handoff", kind="context_reset",
                           context_tokens=self.context)
            self.context = 0
        else:
            prompt = self.first_prompt() if first else CONTINUE_PROMPT
        ask = handoff_begin_turn(st.extra) if not first and not fresh else ""
        if ask:
            prompt += "\n\n" + ask
            self.log.write("marker", label="context handoff requested", kind="context_handoff",
                           context_tokens=self.context)
        if not first:
            note = skills_note(self.run_dir / "workspace")
            if note:
                prompt += "\n\n" + note
        if not first and self.pending_note:
            prompt += "\n\n" + self.pending_note
            self.pending_note = ""
            self.log.write("marker", label="idle nudge delivered", kind="idle_nudge")
        if not first:
            un = take_update_note(self.run_dir)
            if un:
                prompt += "\n\n" + un
        if not first and self.state.extra.get("update_note"):
            prompt += "\n\n" + self.state.extra.pop("update_note")
        nudge = self.run_dir / "NUDGE"
        if not first and nudge.exists():
            # A supervisor (human or agent) wrote a note for the agent; the running turn was
            # interrupted for it, and it is delivered once with this continue prompt.
            note = nudge.read_text(encoding="utf-8").strip()
            nudge.unlink()
            if note:
                prompt += "\n\nNote from your supervisor:\n" + note
                self.log.write("marker", label="supervisor nudge", kind="supervisor_nudge", text=note[:500])
        st.turns += 1
        turn_id = f"t{st.turns}"
        st.active_turn_id = turn_id
        self.save()
        self.log.write("turn_started", turn=st.turns, turn_id=turn_id)
        self.log.write("marker", label="goal prompt" if first else "continue prompt",
                       kind="prompt_goal" if first else "prompt_continue")
        t0 = time.monotonic()
        turn_tokens = 0
        status, error, interrupt = "completed", None, None
        self.agent_state("thinking", "starting turn")
        idle = IdleWatch(self.run_dir, self.a.idle_minutes * 60)
        idle.reset()
        t = Turn(self.docker_cmd(None if (first or fresh) else st.thread_id), prompt,
                 self.run_dir / "raw" / "claude-stream.jsonl", self.run_dir / "raw" / "claude.stderr.log")
        seen_msgs: set[str] = set()
        last_msg = time.monotonic()
        last_host_check = time.monotonic()
        got_result = False
        why_handoff = False
        while True:
            self.tick()
            why = self.end_reason() or ("pause" if self.paused() else None)
            if not why and (self.run_dir / "NUDGE").exists():
                why = "nudge"
            if not why and st.extra.get("handoff") == "asked" and time.monotonic() - t0 > HANDOFF_TIMEOUT_S:
                why = "handoff_timeout"
            if not why and why_handoff:
                why = "handoff"
            if not why and idle.idle():
                why = "idle"
                self.pending_note = IDLE_NOTE.format(m=self.a.idle_minutes)
            if not why and time.monotonic() - last_host_check > self.a.host_poll:
                last_host_check = time.monotonic()
                up, detail = self.check()
                if not up:
                    why = "host_down"
            if why:
                t.kill(self.name)
                status, interrupt = "interrupted", why
                self.log.write("marker", label=f"interrupted turn: {why}", kind="interrupt", reason=why)
                break
            if time.monotonic() - last_msg > self.a.stall_timeout:
                t.kill(self.name)
                status, error = "failed", f"no output for {self.a.stall_timeout:.0f} s"
                break
            try:
                ev = t.q.get(timeout=1)
            except queue.Empty:
                continue
            if ev is None:
                if not got_result:
                    status, error = "failed", f"claude exited (code {t.proc.wait()}) without a result"
                    err = self._stderr_tail()
                    if err:
                        error += ": " + err
                break
            last_msg = time.monotonic()
            res = self.handle(ev, seen_msgs)
            turn_tokens += res.get("tokens", 0)
            if not why_handoff and handoff_trigger(st.extra, self.context, self.a.reset_context_tokens):
                why_handoff = True
            if res.get("result"):
                got_result = True
                r = ev
                if r.get("is_error") or str(r.get("subtype", "")).startswith("error"):
                    status = "failed"
                    error = str(r.get("result") or r.get("subtype"))[:500]
                    if "limit" in error.lower() and "reset" in error.lower():
                        error = "rate_limit: " + error
        t.kill(self.name)
        if handoff_end_turn(st.extra, status == "failed"):
            self.log.write("marker", label="handoff done; next turn is a fresh session", kind="context_handoff_done")
        st.active_turn_id = None
        self.state.extra["last_error"] = error or ""
        self.log.write("turn_completed", turn=st.turns, turn_id=turn_id, status=status,
                       duration_ms=round((time.monotonic() - t0) * 1000), error=error,
                       turn_total_tokens=turn_tokens)
        self.log.write("checkpoint", label=f"turn {st.turns}", ref=f"{st.thread_id}/{turn_id}")
        self.agent_state("waiting" if status != "failed" else "backing_off", f"turn {st.turns} {status}")
        self.save()
        if status == "failed":
            return "failed"
        return "interrupted" if status == "interrupted" else "ok"

    def _stderr_tail(self) -> str:
        try:
            return (self.run_dir / "raw" / "claude.stderr.log").read_text(encoding="utf-8")[-400:].strip()
        except OSError:
            return ""

    # ---- stream-json -> runner.jsonl ----------------------------------------------------

    def handle(self, ev: dict, seen: set[str]) -> dict:
        typ = ev.get("type")
        st = self.state
        if typ == "system":
            sub = ev.get("subtype")
            if sub == "init":
                if ev.get("session_id"):
                    st.thread_id = ev["session_id"]
                    self.save()   # saved at once so a crash resumes this session
                st.model = ev.get("model") or st.model
                if not st.run_started_logged:
                    st.run_started_logged = True
                    self.log.write("run_started", agent="claude-code", model=st.model, host=self.host.name,
                                   goal=st.extra["goal"].strip()[:500])
                    self.save()
                servers = ev.get("mcp_servers") or []
                bad = [s for s in servers if s.get("status") not in ("connected", None)]
                if bad:
                    self.log.write("marker", label=f"MCP server not connected: {bad}", kind="mcp_problem")
            elif sub == "compact_boundary":
                self.agent_state("compacting", "context compaction")
                self.log.write("marker", label="context compacted", kind="compaction")
            return {}
        if typ == "assistant":
            msg = ev.get("message", {})
            out = {}
            mid = msg.get("id")
            if mid and mid not in seen and msg.get("usage"):
                seen.add(mid)
                out["tokens"] = self.usage(msg["usage"], msg.get("model") or "")
            for b in msg.get("content") or []:
                if b.get("type") == "thinking" and b.get("thinking"):
                    self.agent_state("thinking")
                    self.log.write("reasoning", title="", text=b["thinking"])
                elif b.get("type") == "text" and b.get("text", "").strip():
                    self.agent_state("thinking")
                    self.log.write("agent_message", text=b["text"], phase="commentary")
                elif b.get("type") == "tool_use":
                    name = b.get("name", "")
                    self.agent_state("waiting" if "wait" in name.lower() or name == "Sleep" else "acting", name)
            return out
        if typ == "result":
            self.state.extra["last_result_cost_usd"] = ev.get("total_cost_usd")
            # the final text of a turn is the agent's final answer
            if ev.get("result") and not ev.get("is_error"):
                self.log.write("agent_message", text=str(ev["result"]), phase="final_answer")
            return {"result": True}
        return {}

    def usage(self, u: dict, model: str) -> int:
        inp, cin = u.get("input_tokens", 0) or 0, u.get("cache_read_input_tokens", 0) or 0
        cc, out = u.get("cache_creation_input_tokens", 0) or 0, u.get("output_tokens", 0) or 0
        delta_total = inp + cin + cc + out
        self.total_tokens += delta_total
        self.uncached += inp + cc + out
        self.context = inp + cc + cin
        self.log.write("usage", total_tokens=self.total_tokens, uncached_tokens=self.uncached,
                       context_window=1_000_000 if "[1m]" in model else 200_000,
                       input_tokens=inp + cc, cached_input_tokens=cin, output_tokens=out,
                       reasoning_output_tokens=0)
        return delta_total


def parse_until(s: str | None) -> float | None:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() if s else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Drive one long Claude Code session. See the module docstring.")
    ap.add_argument("--run-dir", type=Path, default=None, help="default: runs/<UTC stamp>; pass an existing one to resume")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--goal")
    g.add_argument("--goal-file", type=Path)
    ap.add_argument("--brief-file", type=Path, default=DEFAULT_BRIEF)
    ap.add_argument("--host-config", type=Path, required=True)
    ap.add_argument("--host-check", help="window | always | cmd:<command> | py:<module>:<fn>")
    ap.add_argument("--model")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--image", default=DEFAULT_IMAGE)
    ap.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE, help="docker --env-file with CLAUDE_CODE_OAUTH_TOKEN")
    ap.add_argument("--budget-hours", type=float)
    ap.add_argument("--budget-tokens", type=int, help="uncached input + output tokens")
    ap.add_argument("--until", help="wall-clock deadline, ISO 8601, e.g. 2026-10-24T15:00:00Z")
    ap.add_argument("--stall-timeout", type=float, default=1800)
    ap.add_argument("--idle-minutes", type=float, default=8,
                    help="interrupt and nudge when the host screen is unchanged and no tool was called this long (0 = off)")
    ap.add_argument("--reset-context-tokens", type=int, default=0,
                    help="when one request's context passes this, ask the agent to write its notes, then continue in "
                         "a fresh session (0 = off; e.g. 150000)")
    ap.add_argument("--host-poll", type=float, default=30)
    ap.add_argument("--backoff-base", type=float, default=30)
    ap.add_argument("--backoff-cap", type=float, default=3600)
    ap.add_argument("--max-failures", type=int, default=12)
    ap.add_argument("--run-id")
    ap.add_argument("--build", action="store_true", help="docker build the agent image first")
    a = ap.parse_args(argv)
    a.run_dir = a.run_dir or Path("runs") / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    a.until = parse_until(a.until)
    a.env_file = a.env_file.expanduser()
    if a.build:
        subprocess.run(["docker", "build", "-t", a.image, str(CLAUDE_DIR)], check=True)
    reason = ClaudeRunner(a).run()
    print(f"run ended: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
