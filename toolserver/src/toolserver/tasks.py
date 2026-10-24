"""Background work for the agent: tasks (scripts that keep running after the step that started
them), rules (checks the harness runs on a schedule), notices (what they report back), and the
hands lock (one pair of hands: only one actor at a time).

Nothing here knows the program. A task is the agent's own script; a rule's check is the agent's
own script that calls the agent's own tools; the harness only schedules, waits, delivers and logs.

A task that needs the hands while they are busy waits in a queue and starts as soon as they are
free; a task started with `after` waits for that task to end well. If it doesn't, everything queued
behind it is held (not dropped) until the agent resumes or drops it. So the agent can line up its next
chores and think while they run, instead of leaving its hands idle.

Notices reach the agent three ways: as a footer on its next tool result, from wait(), and, for
urgent ones, by the runner waking it (the WAKE file in the run dir, which the runner watches).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from mcp.server.fastmcp.exceptions import ToolError

from . import scripts
from .events import EventLog

if TYPE_CHECKING:
    from .extensions import Extensions

WAKE = "WAKE"                  # run-dir file the runner watches; holds the urgent notices not yet seen
WAKE_TAKEN = "WAKE_TAKEN"      # keys of notices the runner delivered in a wake prompt: they count as seen
MAX_TASK_MINUTES = 480
MAX_TASKS = 8
MAX_RULES = 32
QUIET = {"", "none", "null", "false", "0", "[]", "{}", '""'}
FOOTER_CHARS = 800
MAX_QUEUED = 8
IDLE_NOTE_AFTER_S = 90          # hands idle this long: say so on the agent's tool results
ONEOFF_SETTLE_S = 15            # the host's activity reports lag by up to this long
REVIEW_EVERY_S = 2 * 3600      # suggest a tool review this often when no milestone has come
REVIEW_ASK = ("Against the brief's foundation (get anywhere, get anything, change many things at once, see an "
              "area) and its rules on repeats (actions and questions), which of your tools is weakest, failed or "
              "cost the most? Improve that one before pushing on. (costs has more per tool.)")
IDLE_NOTE_EVERY_S = 180         # ...at most this often
STALL_REPEAT = 10               # a task holding the hands made the same acting call this many times...
STALL_NO_ACT_S = 120            # ...or has made no acting call for this long: tell the agent, once each
REPEAT_KEY_CHARS = 90             # failures alike over this many leading characters count as the same error


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
        n = Notice(len(self.items) + 1, time.time(), source, text.strip()[:800], urgent)
        self.items.append(n)
        self.log.write("notice", notice=n.id, source=source, text=n.text, urgent=urgent)
        if urgent:
            self._write_wake()
        self._changed.set()
        self._changed = asyncio.Event()
        return n

    def unseen(self) -> list[Notice]:
        self._absorb_taken()
        return [n for n in self.items if not n.seen]

    def _absorb_taken(self) -> None:
        """Notices the runner already delivered in a wake prompt are seen: don't wake for them again."""
        p = self.run_dir / WAKE_TAKEN
        try:
            st = p.stat()
            mtime = (st.st_mtime_ns, st.st_size)
        except OSError:
            return
        if mtime == getattr(self, "_taken_mtime", None):
            return
        self._taken_mtime = mtime
        try:
            keys = set(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return
        for n in self.items:
            if not n.seen and _key(n) in keys:
                n.seen = True

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
        while True:     # an urgent notice from anything ends the wait too: it shouldn't sit unseen
            hits = [n for n in self.unseen() if not sources or _from(n, sources) or n.urgent]
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

    async def until_done_or_urgent(self, job: asyncio.Future, timeout_s: float) -> bool:
        """Wait for `job` to end, but return early (False) when an urgent notice is posted meanwhile,
        so a wait on one task doesn't keep the agent from hearing that something needs it now."""
        mark, deadline = len(self.items), time.monotonic() + timeout_s
        while not job.done():
            if any(n.urgent and not n.seen for n in self.items[mark:]):
                return False
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            changed = asyncio.ensure_future(self._changed.wait())
            await asyncio.wait({asyncio.shield(job), changed}, timeout=left, return_when=asyncio.FIRST_COMPLETED)
            changed.cancel()
        return True

    def _write_wake(self) -> None:
        self._absorb_taken()
        urgent = [{"source": n.source, "text": n.text, "key": _key(n)} for n in self.items if n.urgent and not n.seen]
        p = self.run_dir / WAKE
        if urgent:
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(urgent), encoding="utf-8")
            tmp.replace(p)
        else:
            p.unlink(missing_ok=True)


def _fresh_since() -> dict:
    return {"tasks": 0, "failures": {}, "examples": {}, "looks": 0, "results": {}}


def _ends(text: str, tid: str, head: int = 170, tail: int = 130) -> str:
    """A long failure cut to its start and end, where the error and the reason usually are."""
    text = " ".join(text.split())
    if len(text) <= head + tail + 40:
        return text
    return f"{text[:head]} … {text[-tail:]} (cut; task(\"{tid}\") shows all)"


def _key(n: Notice) -> str:
    return f"{n.ts:.6f}:{n.id}"


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
    status: str = "running"        # queued | held | running | ok | failed | stopped
    after: str = ""                # queued until this task ends ok
    held_why: str = ""             # why it's held: the task before it didn't end ok
    queued_at: float = 0.0
    start_args: tuple = ()         # (code, file, args, why) kept while queued
    result: scripts.Result | None = None
    logs: list[str] = field(default_factory=list)
    calls: int = 0
    job: asyncio.Task | None = None
    acts: int = 0                  # acting calls it made
    reads: int = 0                 # calls since its last acting call
    last_act: float = 0.0
    repeats: dict = field(default_factory=dict)   # acting call -> times made
    stall_told: set = field(default_factory=set)  # which stall notices it got

    def brief(self) -> str:
        if self.status == "queued":
            behind = f"after {self.after}" if self.after else "for your hands"
            return f"{self.id} {self.name}: queued {behind} ({time.time() - self.queued_at:.0f} s)"
        if self.status == "held":
            return f"{self.id} {self.name}: held ({self.held_why})"
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
        self.failures: dict[tuple, int] = {}    # (task name, error with numbers masked) -> times
        self.rules: dict[str, Rule] = {}
        self._n = 0
        self._rules_path = run_dir / "rules.json"
        self.since = _fresh_since()                   # what happened since the last tool review
        self.last_review = time.time()
        self.oneoffs: list[tuple[float, float, bool | None]] = []
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
        queued = [t for t in self.tasks.values() if t.status in ("queued", "held")]
        if after and self.tasks[after].status in ("failed", "stopped"):
            raise ToolError(f"after: {after} has already ended {self.tasks[after].status}, so this wouldn't run; "
                            "start it without after, or after another task")
        waiting_for_hands = any(q.hands and q.status == "queued" for q in queued)   # held ones don't count
        must_queue = (after and self.tasks[after].status in ("queued", "held", "running")) or \
            (hands and (not self.hands.free_for(None) or waiting_for_hands))
        if must_queue and len(queued) >= MAX_QUEUED:
            raise ToolError(f"{len(queued)} tasks are already queued or held (at most {MAX_QUEUED}). "
                            + self.listing())
        self._n += 1
        tid = f"t{self._n}"
        t = Task(tid, name, hands, time.time(), max(1.0, min(float(minutes), MAX_TASK_MINUTES)) * 60, wake_on_end,
                 after=after, queued_at=time.time(), start_args=(code, file, args, why))
        self.tasks[tid] = t
        if must_queue:
            t.status = "queued"
            self.log.write("task_queued", task=tid, name=name, hands=hands, after=after or None,
                           behind=None if after else self.hands.holder, args=args or {}, why=why or None)
            self.pump()                         # behind a held task: held at once
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

    def _behind(self, tid: str) -> list[Task]:
        """Queued or held tasks that wait on `tid`, directly or through others, in queue order."""
        out, ids = [], {tid}
        for t in self.tasks.values():
            if t.status in ("queued", "held") and t.after in ids:
                out.append(t)
                ids.add(t.id)
        return out

    def _hold_behind_failures(self) -> None:
        for root in [x for x in self.tasks.values() if x.status in ("failed", "stopped")]:
            newly = [t for t in self._behind(root.id) if t.status == "queued"]
            if not newly:
                continue
            summary = (root.result.detail if root.result and not root.result.ok else "")[:160]
            why = f"{root.id} ended {root.status}"
            for t in newly:
                t.status, t.held_why = "held", why
                self.log.write("task_held", task=t.id, name=t.name, after=t.after, why=why)
            ids = ", ".join(t.id for t in newly)
            first = newly[0].id
            self.board.post(root.id, f"{why}{': ' + summary if summary and root.status == 'failed' else ''}. "
                            f"Held {ids} behind it: if they still fit, task(\"{first}\", resume=True) "
                            f"(with after= to run it after a fix); if not, task(\"{first}\", stop=True) drops them.",
                            urgent=root.status == "failed")

    def resume(self, tid: str, after: str = "") -> str:
        """Let a held task (and the ones held behind it) run again: now, or after another task."""
        t = self.tasks.get(tid)
        if t is None:
            raise ToolError(f"no task {tid!r}. " + self.listing())
        if t.status != "held":
            raise ToolError(f"{tid} is {t.status}, not held. " + self.listing())
        if after:
            if after not in self.tasks or after == tid or after in {x.id for x in self._behind(tid)}:
                raise ToolError(f"after: {after!r} isn't a task this one can wait on. " + self.listing())
            if self.tasks[after].status in ("failed", "stopped"):
                raise ToolError(f"after: {after} ended {self.tasks[after].status}")
        group = [t] + [x for x in self._behind(tid) if x.status == "held"]
        t.after = after
        for x in group:
            x.status, x.held_why = "queued", ""
            self.log.write("task_resumed", task=x.id, name=x.name, after=x.after or None)
        self.pump()
        return "\n".join(self.tasks[x.id].brief() for x in group)

    def pump(self) -> None:
        """Start queued tasks whose turn has come: in the order they were queued, the first one that
        needs the hands gets them once they're free. One queued behind a task that didn't end ok is
        held, with everything behind it, and the agent is told once per chain."""
        self._hold_behind_failures()
        for t in [x for x in self.tasks.values() if x.status == "queued"]:
            dep = self.tasks.get(t.after) if t.after else None
            if dep is not None and dep.status != "ok":
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
                hook = self.ext.hook_info(data.get("tool", ""))
                self.log.write("tool_call", tool=data.get("tool"), channel="hook", args=data.get("args"),
                               ok=data.get("ok"), error=data.get("error"), duration_ms=data.get("ms"),
                               result_kind="text", result_bytes=data.get("bytes"), summary=data.get("summary"),
                               via=f"task:{t.id}", hook=hook)
                self.watch_call(t, data.get("tool", ""), data.get("args"), bool(hook and hook["acts"]))
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
        summary = (r.out.strip().splitlines() or [""])[-1][:200] if r.ok else _ends(r.detail, t.id)
        self.log.write("task_ended", task=t.id, name=t.name, status=t.status, duration_ms=round(r.seconds * 1000),
                       calls=len(r.calls), summary=summary)
        again = ""
        self.since["tasks"] += 1
        if t.status == "failed":
            masked = re.sub(r"[0-9a-f]{8,}", "#", summary)           # ids, hashes
            # positions, ticks, counts; and only the start, where the error is (state dumps after it vary)
            key = (t.name, re.sub(r"-?\d+(\.\d+)?", "#", masked)[:REPEAT_KEY_CHARS])
            self.failures[key] = n = self.failures.get(key, 0) + 1
            self.since["failures"][key] = self.since["failures"].get(key, 0) + 1
            self.since["examples"][key] = f"{summary} ({t.name})"
            if n >= 2:
                again = (f" This is the {n}{'nd' if n == 2 else 'rd' if n == 3 else 'th'} time {t.name} failed with "
                         f"this same error: running it again unchanged won't help. Look at why (read the state "
                         f"where it fails) or change the approach.")
                self.log.write("task_repeat_failure", task=t.id, name=t.name, times=n, summary=summary)
        self.board.post(t.id, f"{t.status}: {summary or '(no output)'}{again}",
                        urgent=(t.wake_on_end and not r.stopped) or bool(again))
        self.pump()

    async def stop(self, tid: str, why: str = "stopped") -> str:
        waiting = ("queued", "held")
        ids = [k for k, t in self.tasks.items() if t.status in ("running",) + waiting] if tid == "all" else [tid]
        if tid != "all" and tid not in self.tasks:
            raise ToolError(f"no task {tid!r}. " + self.listing())
        if tid != "all" and self.tasks[tid].status in waiting:
            ids += [x.id for x in self._behind(tid)]   # dropping a queued chore drops what waits on it
        for k in ids:                           # unqueue first, so stopping a running one starts nothing
            t = self.tasks[k]
            if t.status in waiting:
                t.status = "stopped"
                self.log.write("task_ended", task=t.id, name=t.name, status="stopped", duration_ms=0, calls=0,
                               summary=f"dropped while {'held' if t.held_why else 'queued'}")
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
        if t.status == "running" and t.hands:
            top = max(t.repeats.values(), default=0)
            lines.append(f"acting calls: {t.acts} ({len(t.repeats)} different, the most repeated {top}x); "
                         f"{t.reads} reads since the last one")
        if t.logs:
            lines.append("log: " + " | ".join(t.logs[-8:]))
        if t.result is not None:
            lines.append(t.result.text())
        elif t.status == "held":
            lines.append(f"(Held: {t.held_why}. task(\"{t.id}\", resume=True) runs it, after= another task if "
                         f"you like; task(\"{t.id}\", stop=True) drops it and what's behind it.)")
        elif t.status in ("running", "queued"):
            lines.append(f"(Checking again costs a step. wait(on=[\"{t.id}\"]) returns when it ends; meanwhile "
                         f"queue the next chore with run_script(..., background=True, after=\"{t.id}\").)")
        return "\n".join(lines)

    def watch_call(self, t: Task, tool: str, args: Any, acts: bool, now: float | None = None) -> None:
        """Track what a task does with the hands, to spot one that holds them without getting anywhere."""
        now = now or time.time()
        if acts:
            t.acts, t.reads, t.last_act = t.acts + 1, 0, now
            key = f"{tool} {json.dumps(args, sort_keys=True, default=str)[:300]}"
            t.repeats[key] = t.repeats.get(key, 0) + 1
        else:
            t.reads += 1
        self.stall_check(now)

    def stall_check(self, now: float | None = None) -> None:
        """Tell the agent, once per kind, when a task holding the hands seems stuck: it keeps making the
        same acting call, or it has gone a while with only reads (waiting, polling) or nothing at all."""
        now = now or time.time()
        for t in self.tasks.values():
            if t.status != "running" or not t.hands:
                continue
            held = (now - t.started) / 60
            key, n = max(t.repeats.items(), key=lambda kv: kv[1], default=("", 0))
            if n >= STALL_REPEAT and "repeat" not in t.stall_told:
                t.stall_told.add("repeat")
                self._stalled(t, "repeat", f"{t.id} ({t.name}) has held your hands {held:.1f} min and keeps making "
                              f"the same acting call ({n} times: {key[:160]}). It may be stuck in a retry loop. "
                              f"Check what it is waiting for and stop it (task(\"{t.id}\", stop=True)) if it "
                              f"isn't getting anywhere.")
            # Acting is seen two ways: the task's acting calls through the harness, and the program's own
            # action counts (when the host reports them), which also catch tools that reach the program
            # directly. With neither (no calls, no counts) there is nothing to judge by, so say nothing.
            counted, last_prog = self.program_activity()
            if not counted and t.acts + t.reads == 0:
                continue
            quiet = now - max(t.last_act or t.started, last_prog if last_prog > t.started else 0)
            if quiet >= STALL_NO_ACT_S and "quiet" not in t.stall_told:
                t.stall_told.add("quiet")
                self._stalled(t, "quiet", f"{t.id} ({t.name}) has held your hands {held:.1f} min but made no acting "
                              f"call{' and the program saw no player activity' if counted else ''} for {quiet / 60:.1f} "
                              f"min ({t.reads} reads since). If it is waiting or polling, "
                              f"the hands sit idle meanwhile: stop it and do the waiting outside the hands (a "
                              f"hands=False task, or a rule), or let it finish if the wait is nearly over.")

    def program_activity(self) -> tuple[bool, float]:
        """(whether the host counts actions in the program at all, time of the latest sign of the user
        doing something), from `program_actions` and `program_activity` records in the run's logs, read
        incrementally."""
        st = self.__dict__.setdefault("_prog", {"counted": False, "last": 0.0, "pos": {}, "checked": 0.0, "scanned": False})
        if time.time() - st["checked"] < 5:
            return st["counted"], st["last"]
        st["checked"] = time.time()
        for path in Path(self.run_dir).glob("*.jsonl"):
            if path.name == "events.jsonl":
                continue
            try:
                size = path.stat().st_size
                pos = st["pos"].get(path.name, 0)
                if size <= pos:
                    st["pos"][path.name] = min(pos, size)
                    continue
                with path.open("rb") as f:
                    f.seek(pos)
                    chunk = f.read(size - pos)
            except OSError:
                continue
            end = chunk.rfind(b"\n") + 1               # leave a half-written last line for next time
            st["pos"][path.name] = pos + end
            for line in chunk[:end].splitlines():
                if b'"program_actions"' not in line and b'"program_activity"' not in line and b'"milestone"' not in line:
                    continue
                try:
                    r = json.loads(line)
                    ts = datetime.fromisoformat(r["ts"].replace("Z", "+00:00")).timestamp()
                except (ValueError, KeyError):
                    continue
                busy = ((r.get("type") == "program_actions" and r.get("count")) or
                        (r.get("type") == "program_activity" and r.get("kinds")))   # moving etc.: not an action
                if r.get("type") == "program_actions":
                    st["counted"] = True
                if busy:
                    st["last"] = max(st["last"], ts)
                    st.setdefault("times", []).append(ts)
                    del st["times"][:-500]
                elif r.get("type") == "milestone" and st["scanned"]:   # not those from before this server
                    st.setdefault("new_milestones", []).append(str(r.get("id", "?")))
        st["scanned"] = True
        return st["counted"], st["last"]

    def note_result(self, tool: str, chars: int) -> None:
        """A result of one of the agent's own tools, returned straight into its context (for the review)."""
        c = self.since["results"].setdefault(tool, [0, 0])
        c[0], c[1] = c[0] + 1, c[1] + chars

    def note_oneoff(self, t0: float, t1: float, acted: bool | None) -> None:
        """A one-off foreground script ran from t0 to t1. acted: whether any of its calls through the harness
        acted (None: it made none). Judged later, once the host has reported what happened meanwhile."""
        self.oneoffs.append((t0, t1, acted))

    def _judge_oneoffs(self, now: float) -> None:
        """Count one-off scripts that only looked: no acting call, and the program saw nobody do anything
        meanwhile (for the review)."""
        counted, _ = self.program_activity()
        times = self._prog.get("times", [])
        keep = []
        for t0, t1, acted in self.oneoffs:
            if acted:
                continue
            if counted and now < t1 + ONEOFF_SETTLE_S:
                keep.append((t0, t1, acted))          # the host reports with a lag: wait for it
                continue
            if counted:
                looked = not any(t0 <= ts <= t1 + ONEOFF_SETTLE_S for ts in times)
            else:
                looked = acted is False               # only harness calls to go by, and all of them read
            self.since["looks"] += looked
        self.oneoffs = keep

    def review_check(self, now: float | None = None) -> None:
        """At each milestone, or every REVIEW_EVERY_S without one, ask the agent to review its own tools,
        with what happened since the last review: the facts, and the agent decides what to fix."""
        now = now or time.time()
        self._judge_oneoffs(now)
        new = self._prog.pop("new_milestones", [])
        if new:
            why = f"Milestone reached ({', '.join(new)})."
        elif now - self.last_review >= REVIEW_EVERY_S:
            why = f"It has been {(now - self.last_review) / 3600:.0f} h since the last milestone or tool review."
        else:
            return
        facts = self._review_facts()
        self.last_review, self.since = now, _fresh_since()
        self.log.write("tool_review_suggested", why=why, facts=facts)
        lines = "".join(f"\n- {f}" for f in facts) or "\n- (nothing recorded)"
        self.board.post("harness", f"{why} Time to review your own tools. Since the last review:{lines}\n"
                        f"{REVIEW_ASK}")

    def _review_facts(self) -> list[str]:
        s, facts = self.since, []
        if s["tasks"]:
            line = f"tasks: {s['tasks']} ended, {sum(s['failures'].values())} failed"
            top = sorted(s["failures"].items(), key=lambda kv: -kv[1])[:3]
            if top:
                line += "; most often: " + "; ".join(f"{n}x {s['examples'][k][:110]}" for k, n in top)
            facts.append(line)
        if s["looks"]:
            facts.append(f"one-off scripts that only looked at the program: {s['looks']}")
        big = sorted(((c[1] / c[0], t, c[0]) for t, c in s["results"].items()
                      if c[0] and c[1] / c[0] >= 2000), reverse=True)[:2]
        if big:
            facts.append("results of your own tools returned straight to you: " +
                         ", ".join(f"{t} {avg / 1000:.1f}k characters on average over {n} calls" for avg, t, n in big))
        return facts

    def _stalled(self, t: Task, kind: str, text: str) -> None:
        self.log.write("task_stalled", task=t.id, name=t.name, kind=kind, acts=t.acts, calls=t.calls,
                       held_s=round(time.time() - t.started))
        self.board.post(t.id, text, urgent=True)

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
