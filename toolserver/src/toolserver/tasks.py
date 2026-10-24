"""Background work for the agent: tasks (scripts that keep running after the step that started
them), rules (checks the harness runs on a schedule), notices (what they report back), and the
hands lock (one pair of hands: only one actor at a time).

Nothing here knows the program. A task is the agent's own script; a rule's check is the agent's
own script that calls the agent's own tools; the harness only schedules, waits, delivers and logs.

A task that needs the hands while they are busy waits in a queue and starts as soon as they are
free; a task started with `after` waits for that task to end well (and is skipped if it doesn't). So
the agent can line up its next chores and think while they run, instead of leaving its hands idle.

Notices reach the agent three ways: as a footer on its next tool result, from wait(), and, for
urgent ones, by the runner waking it (the WAKE file in the run dir, which the runner watches).
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from mcp.server.fastmcp.exceptions import ToolError

from . import scripts
from .events import EventLog

if TYPE_CHECKING:
    from .extensions import Extensions

WAKE = "WAKE"                  # run-dir file the runner watches; holds the urgent notices not yet seen
MAX_TASK_MINUTES = 480
MAX_TASKS = 8
MAX_RULES = 32
QUIET = {"", "none", "null", "false", "0", "[]", "{}", '""'}
FOOTER_CHARS = 800
MAX_QUEUED = 8
IDLE_NOTE_AFTER_S = 90          # hands idle this long: say so on the agent's tool results
IDLE_NOTE_EVERY_S = 180         # ...at most this often


class HandsBusy(ToolError):
    pass


class Hands:
    """One pair of hands. A background task that acts holds them for as long as it runs, and a
    foreground script holds them while it runs; anything else that acts is refused meanwhile.
    Reading never needs them."""

    def __init__(self) -> None:
        self.holder: str | None = None
        self.what = ""
        self.last_busy = time.time()       # when something last acted or held the hands
        self.on_release: Callable[[], None] | None = None   # Work starts the next queued task

    def take(self, who: str, what: str) -> None:
        if self.holder not in (None, who):
            raise HandsBusy(self.busy_note())
        self.holder, self.what = who, what
        self.last_busy = time.time()

    def release(self, who: str) -> None:
        if self.holder == who:
            self.holder, self.what = None, ""
            self.last_busy = time.time()
            if self.on_release:
                self.on_release()

    def check(self, who: str | None) -> None:
        """Before an acting call by `who` (None: the agent directly)."""
        if self.holder not in (None, who):
            raise HandsBusy(self.busy_note())
        self.last_busy = time.time()

    def idle_s(self) -> float:
        """How long nothing has acted or held the hands (0 while something holds them)."""
        return 0.0 if self.holder else time.time() - self.last_busy

    def free_for(self, who: str | None) -> bool:
        return self.holder in (None, who)

    def busy_note(self) -> str:
        return (f"your hands are busy: {self.holder} ({self.what}) holds them. Reading still works; "
                f"wait for it, or stop it with task(\"{self.holder}\", stop=True)")


@dataclass
class Notice:
    id: int
    ts: float
    source: str
    text: str
    urgent: bool = False
    seen: bool = False


class Board:
    """Notices for the agent, delivered once."""

    def __init__(self, log: EventLog, run_dir: Path) -> None:
        self.log, self.run_dir = log, run_dir
        self.items: list[Notice] = []
        self._changed = asyncio.Event()

    def post(self, source: str, text: str, urgent: bool = False) -> Notice:
        n = Notice(len(self.items) + 1, time.time(), source, text.strip()[:500], urgent)
        self.items.append(n)
        self.log.write("notice", notice=n.id, source=source, text=n.text, urgent=urgent)
        if urgent:
            self._write_wake()
        self._changed.set()
        self._changed = asyncio.Event()
        return n

    def unseen(self) -> list[Notice]:
        return [n for n in self.items if not n.seen]

    def mark_seen(self, notices: list[Notice]) -> None:
        for n in notices:
            n.seen = True
        self._write_wake()

    def footer(self) -> str:
        """The unseen notices as one short block (and marks them seen), or ''."""
        new = self.unseen()
        if not new:
            return ""
        self.mark_seen(new)
        return "[notices: " + _join(new, FOOTER_CHARS) + "]"

    async def wait(self, sources: list[str], timeout_s: float) -> list[Notice]:
        """Unseen notices from `sources` (any source if empty), waiting up to timeout_s for one."""
        deadline = time.monotonic() + timeout_s
        while True:
            hits = [n for n in self.unseen() if not sources or _from(n, sources)]
            if hits:
                self.mark_seen(hits)
                return hits
            left = deadline - time.monotonic()
            if left <= 0:
                return []
            try:
                await asyncio.wait_for(self._changed.wait(), left)
            except asyncio.TimeoutError:
                return []

    def _write_wake(self) -> None:
        urgent = [{"source": n.source, "text": n.text} for n in self.items if n.urgent and not n.seen]
        p = self.run_dir / WAKE
        if urgent:
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(urgent), encoding="utf-8")
            tmp.replace(p)
        else:
            p.unlink(missing_ok=True)


def _from(n: Notice, sources: list[str]) -> bool:
    return n.source in sources or n.source.split(":", 1)[-1] in sources


def _join(notices: list[Notice], limit: int) -> str:
    out = "; ".join(f"{n.source}: {n.text}" for n in notices)
    return out if len(out) <= limit else out[:limit] + f"… ({len(notices)} notices)"


@dataclass
class Task:
    id: str
    name: str                      # kept script name, file, or "code"
    hands: bool
    started: float
    deadline_s: float
    wake_on_end: bool
    handle: scripts.Handle = field(default_factory=scripts.Handle)
    status: str = "running"        # queued | running | ok | failed | stopped | skipped
    after: str = ""                # queued until this task ends ok
    queued_at: float = 0.0
    start_args: tuple = ()         # (code, file, args, why) kept while queued
    result: scripts.Result | None = None
    logs: list[str] = field(default_factory=list)
    calls: int = 0
    job: asyncio.Task | None = None

    def brief(self) -> str:
        if self.status == "queued":
            behind = f"after {self.after}" if self.after else "for your hands"
            return f"{self.id} {self.name}: queued {behind} ({time.time() - self.queued_at:.0f} s)"
        if self.status == "skipped":
            return f"{self.id} {self.name}: skipped ({self.after} didn't end ok)"
        took = time.time() - self.started if self.result is None else self.result.seconds
        hands = ", hands" if self.hands and self.status == "running" else ""
        return f"{self.id} {self.name}: {self.status} ({took:.0f} s, {self.calls} calls{hands})"


@dataclass
class Rule:
    name: str
    check: str                     # code, or a kept script's name (see `kind`)
    kind: str                      # "code" | "name"
    args: dict
    every_s: float
    do: str                        # tell | wake | run:<kept script>
    times: int                     # 0: no limit
    cooldown_s: float
    fired: int = 0
    last_fired: float = 0.0
    checks: int = 0
    errors: int = 0
    last_error: str = ""
    job: asyncio.Task | None = None

    def brief(self) -> str:
        what = f"script {self.check}" if self.kind == "name" else "code"
        err = f", {self.errors} check errors (last: {self.last_error[:80]})" if self.errors else ""
        return (f"{self.name}: every {self.every_s:g} s, {what} -> {self.do}; fired {self.fired}"
                f"{'/' + str(self.times) if self.times else ''}{err}")

    def to_json(self) -> dict:
        return {k: getattr(self, k) for k in ("name", "check", "kind", "args", "every_s", "do", "times",
                                              "cooldown_s", "fired")}


class Work:
    """Tasks and rules for one tool server."""

    def __init__(self, ext: "Extensions", log: EventLog, run_dir: Path, hands: Hands, board: Board,
                 harness_url: Callable[[], str], builtins: Callable[[], list[str]]) -> None:
        self.ext, self.log, self.run_dir, self.hands, self.board = ext, log, run_dir, hands, board
        self.harness_url, self.builtins = harness_url, builtins
        self.tasks: dict[str, Task] = {}
        self.rules: dict[str, Rule] = {}
        self._n = 0
        self._rules_path = run_dir / "rules.json"
        hands.on_release = self.pump

    def can_act(self, who: str | None) -> bool:
        """May `who` act at all? Rules' checks and tasks started without hands only read."""
        if who and who.startswith("rule:"):
            return False
        t = self.tasks.get(who or "")
        if t is not None and not t.hands:
            return False
        return self.hands.free_for(who)

    # ------------------------------------------------------------------ tasks

    def start(self, code: str, file: str, name: str, args: dict | None, minutes: float, hands: bool,
              wake_on_end: bool = False, why: str = "", after: str = "") -> Task:
        """Start a task now, or queue it: behind `after` (a task id), or for the hands if they're busy."""
        if after and after not in self.tasks:
            raise ToolError(f"after: no task {after!r}. " + self.listing())
        queued = [t for t in self.tasks.values() if t.status == "queued"]
        must_queue = (after and self.tasks[after].status in ("queued", "running")) or \
            (hands and (not self.hands.free_for(None) or any(q.hands for q in queued)))
        if must_queue and len(queued) >= MAX_QUEUED:
            raise ToolError(f"{len(queued)} tasks are already queued (at most {MAX_QUEUED}). " + self.listing())
        if after and not must_queue and self.tasks[after].status != "ok":
            raise ToolError(f"after: {after} has already ended {self.tasks[after].status}, so this wouldn't run")
        self._n += 1
        tid = f"t{self._n}"
        t = Task(tid, name, hands, time.time(), max(1.0, min(float(minutes), MAX_TASK_MINUTES)) * 60, wake_on_end,
                 after=after, queued_at=time.time(), start_args=(code, file, args, why))
        self.tasks[tid] = t
        if must_queue:
            t.status = "queued"
            self.log.write("task_queued", task=tid, name=name, hands=hands, after=after or None,
                           behind=None if after else self.hands.holder, args=args or {}, why=why or None)
            return t
        self._begin(t)
        return t

    def _begin(self, t: Task) -> None:
        running = [x for x in self.tasks.values() if x.status == "running"]
        if len(running) >= MAX_TASKS:
            raise ToolError(f"{len(running)} tasks are already running (at most {MAX_TASKS}); stop one first")
        if t.hands:
            self.hands.take(t.id, t.name)       # raises HandsBusy
        code, file, args, why = t.start_args
        t.status, t.started = "running", time.time()
        waited = t.started - t.queued_at
        self.log.write("task_started", task=t.id, name=t.name, hands=t.hands, minutes=t.deadline_s / 60,
                       args=args or {}, why=why or None, after=t.after or None,
                       queued_s=round(waited, 1) if waited >= 0.5 else None)
        t.job = asyncio.create_task(self._run(t, code, file, args))

    def pump(self) -> None:
        """Start queued tasks whose turn has come: in the order they were queued, the first one that
        needs the hands gets them once they're free; one queued behind a task that didn't end ok is
        skipped."""
        for t in [x for x in self.tasks.values() if x.status == "queued"]:
            dep = self.tasks.get(t.after) if t.after else None
            if dep is not None and dep.status in ("queued", "running"):
                continue
            if dep is not None and dep.status != "ok":
                t.status = "skipped"
                self.log.write("task_ended", task=t.id, name=t.name, status="skipped", duration_ms=0, calls=0,
                               summary=f"{dep.id} ended {dep.status}")
                self.board.post(t.id, f"skipped: {dep.id} ended {dep.status}", urgent=t.wake_on_end)
                continue
            if t.hands and not self.hands.free_for(None):
                return                          # keep the order: nothing jumps a task waiting for the hands
            try:
                self._begin(t)
            except ToolError as e:
                t.status = "failed"
                self.log.write("task_ended", task=t.id, name=t.name, status="failed", duration_ms=0, calls=0,
                               summary=str(e)[:200])
                self.board.post(t.id, f"failed to start: {e}", urgent=True)

    async def _run(self, t: Task, code: str, file: str, args: dict | None) -> None:
        def on_event(kind: str, data: dict) -> None:
            if kind == "call":
                t.calls += 1
                self.log.write("tool_call", tool=data.get("tool"), channel="hook", args=data.get("args"),
                               ok=data.get("ok"), error=data.get("error"), duration_ms=data.get("ms"),
                               result_kind="text", result_bytes=data.get("bytes"), summary=data.get("summary"),
                               via=f"task:{t.id}", hook=self.ext.hook_info(data.get("tool", "")))
            elif kind == "log":
                t.logs = (t.logs + [data.get("text", "")])[-20:]
            elif kind == "notify":
                self.board.post(t.id, data.get("text", ""), urgent=bool(data.get("wake")))
        note = "" if t.hands else "this task was started without hands (hands=False), so it may only read"
        try:
            r = await scripts.execute(self.ext, code, t.deadline_s, file, args, self.harness_url(), self.builtins(),
                                      who=t.id, can_act=t.hands, hands_note=note, handle=t.handle,
                                      on_event=on_event)
        except asyncio.CancelledError:
            r = scripts.Result(False, "", "the tool server stopped", [], time.time() - t.started, stopped=True)
        except Exception as e:      # the task must always end, and release the hands
            r = scripts.Result(False, "", f"{type(e).__name__}: {e}", [], time.time() - t.started)
        finally:
            self.hands.release(t.id)
        t.result = r
        t.status = "ok" if r.ok else ("stopped" if r.stopped else "failed")
        summary = (r.out.strip().splitlines() or [""])[-1][:200] if r.ok else r.detail[:200]
        self.log.write("task_ended", task=t.id, name=t.name, status=t.status, duration_ms=round(r.seconds * 1000),
                       calls=len(r.calls), summary=summary)
        self.board.post(t.id, f"{t.status}: {summary or '(no output)'}", urgent=t.wake_on_end and not r.stopped)
        self.pump()

    async def stop(self, tid: str, why: str = "stopped") -> str:
        ids = [k for k, t in self.tasks.items() if t.status in ("running", "queued")] if tid == "all" else [tid]
        if tid != "all" and tid not in self.tasks:
            raise ToolError(f"no task {tid!r}. " + self.listing())
        for k in ids:                           # unqueue first, so stopping a running one starts nothing
            t = self.tasks[k]
            if t.status == "queued":
                t.status = "stopped"
                self.log.write("task_ended", task=t.id, name=t.name, status="stopped", duration_ms=0, calls=0,
                               summary="stopped while queued")
        for k in ids:
            t = self.tasks[k]
            if t.status == "running":
                await t.handle.stop(self.ext, why)
        for k in ids:
            job = self.tasks[k].job
            if job:
                try:
                    await asyncio.wait_for(asyncio.shield(job), 20)
                except asyncio.TimeoutError:
                    pass
        return "\n".join(self.tasks[k].brief() for k in ids) or "no tasks running"

    def describe(self, tid: str) -> str:
        t = self.tasks.get(tid)
        if t is None:
            raise ToolError(f"no task {tid!r}. " + self.listing())
        lines = [t.brief()]
        if t.logs:
            lines.append("log: " + " | ".join(t.logs[-8:]))
        if t.result is not None:
            lines.append(t.result.text())
        elif t.status in ("running", "queued"):
            lines.append(f"(Checking again costs a step. wait(on=[\"{t.id}\"]) returns when it ends; meanwhile "
                         f"queue the next chore with run_script(..., background=True, after=\"{t.id}\").)")
        return "\n".join(lines)

    def idle_note(self) -> str:
        """A line for the agent's tool results when nothing has acted for a while and nothing is queued."""
        idle = self.hands.idle_s()
        if idle < IDLE_NOTE_AFTER_S or any(t.status == "queued" and t.hands for t in self.tasks.values()):
            return ""
        if time.time() - getattr(self, "_last_idle_note", 0.0) < IDLE_NOTE_EVERY_S:
            return ""
        self._last_idle_note = time.time()
        return (f"[hands idle {idle / 60:.1f} min: nothing is acting on the program. Start or queue the next "
                f"chore (run_script(..., background=True)) and plan the one after while it runs.]")

    def listing(self) -> str:
        if not self.tasks:
            return "No tasks. Start one with run_script(..., background=True)."
        recent = list(self.tasks.values())[-12:]
        return "Tasks:\n" + "\n".join("  " + t.brief() for t in recent)

    # ------------------------------------------------------------------ rules

    def set_rule(self, r: Rule, restored: bool = False) -> None:
        old = self.rules.pop(r.name, None)
        if old and old.job:
            old.job.cancel()
        if len(self.rules) >= MAX_RULES:
            raise ToolError(f"at most {MAX_RULES} rules; remove one first")
        self.rules[r.name] = r
        r.job = asyncio.create_task(self._watch(r))
        if not restored:
            self.log.write("rule_set", rule=r.name, check=r.check if r.kind == "name" else "code",
                           code=r.check if r.kind == "code" else None, args=r.args, every_s=r.every_s,
                           do=r.do, times=r.times, cooldown_s=r.cooldown_s)
        self._save_rules()

    def remove_rule(self, name: str, why: str = "removed") -> None:
        r = self.rules.pop(name, None)
        if r is None:
            raise ToolError(f"no rule {name!r}. " + self.rule_listing())
        if r.job:
            r.job.cancel()
        self.log.write("rule_removed", rule=name, why=why, fired=r.fired)
        self._save_rules()

    def rule_listing(self) -> str:
        if not self.rules:
            return "No rules. Set one with watch(name, check=..., do=...)."
        return "Rules:\n" + "\n".join("  " + r.brief() for r in self.rules.values())

    async def _watch(self, r: Rule) -> None:
        who = f"rule:{r.name}"
        while True:
            await asyncio.sleep(r.every_s)
            code, file = (r.check, "") if r.kind == "code" else ("", f"{scripts.KEPT_DIR}/{r.check}.py")
            try:
                res = await scripts.execute(self.ext, code, max(5.0, min(60.0, r.every_s * 4)), file, r.args,
                                            self.harness_url(), self.builtins(), who=who, can_act=False,
                                            hands_note="a rule's check may only read; act in its do script")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                res = scripts.Result(False, "", f"{type(e).__name__}: {e}", [], 0.0)
            r.checks += 1
            if not res.ok:
                r.errors += 1
                r.last_error = res.detail
                if r.errors in (1, 5, 25):   # say so, but don't flood
                    self.board.post(who, f"check failed ({r.errors}x): {res.detail[:200]}")
                continue
            text = res.out.strip()
            if text.lower() in QUIET or time.time() - r.last_fired < r.cooldown_s:
                continue
            await self._fire(r, text)
            if r.times and r.fired >= r.times:
                self.remove_rule(r.name, f"fired {r.fired} times")
                return

    async def _fire(self, r: Rule, text: str) -> None:
        r.fired += 1
        r.last_fired = time.time()
        self.log.write("rule_fired", rule=r.name, text=text[:500], do=r.do, count=r.fired)
        who = f"rule:{r.name}"
        if r.do.startswith("run:"):
            script = r.do[4:]
            try:
                t = self.start("", f"{scripts.KEPT_DIR}/{script}.py", script, {**r.args, "event": text[:500]},
                               20, hands=True, why=f"rule {r.name} fired")
                verb = "queued" if t.status == "queued" else "started"
                self.board.post(who, f"{text[:200]} -> {verb} {t.id} ({script})")
            except ToolError as e:
                self.board.post(who, f"{text[:200]} -> couldn't start {script}: {e}", urgent=True)
        else:
            self.board.post(who, text, urgent=r.do == "wake")
        self._save_rules()

    def _save_rules(self) -> None:
        tmp = self._rules_path.with_suffix(".tmp")
        tmp.write_text(json.dumps([r.to_json() for r in self.rules.values()], indent=1), encoding="utf-8")
        tmp.replace(self._rules_path)

    def restore_rules(self) -> None:
        """Rules survive a tool server restart (tasks don't: their processes end with it)."""
        try:
            saved = json.loads(self._rules_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for d in saved:
            try:
                self.set_rule(Rule(**d), restored=True)
            except (TypeError, ToolError):
                continue
        if self.rules:
            self.log.write("rules_restored", rules=sorted(self.rules))


def parse_rule(name: str, check: str, script: str, args: dict | None, every_s: float, do: str, times: int,
               cooldown_s: float, workspace: Path) -> Rule:
    if not scripts.NAME_RE.fullmatch(name or ""):
        raise ToolError("name: lower_snake_case, at most 49 characters")
    if bool(check) == bool(script):
        raise ToolError("give one of: check (code that prints or returns something when the rule should fire) "
                        "or script (a kept script that does)")
    if script and not (workspace / scripts.KEPT_DIR / f"{script}.py").is_file():
        raise ToolError(f"no kept script named {script!r}. " + scripts.listing(workspace))
    if do not in ("tell", "wake") and not do.startswith("run:"):
        raise ToolError('do: "tell", "wake" or "run:<kept script>"')
    if do.startswith("run:") and not (workspace / scripts.KEPT_DIR / f"{do[4:]}.py").is_file():
        raise ToolError(f"do: no kept script named {do[4:]!r}")
    return Rule(name, script or check, "name" if script else "code", dict(args or {}),
                max(1.0, min(float(every_s), 3600.0)), do, max(0, int(times)), max(0.0, float(cooldown_s)))


def tail_goal(events: Path) -> dict[str, Any] | None:
    """The latest `goal` record in the tool server's log."""
    try:
        with events.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 4_000_000))
            lines = f.read().splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if b'"type": "goal"' in line:
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None
