"""run_script: one tool call that runs the agent's own Python in its sandbox, with its extension tools
callable as functions. Loops, conditions and waits over many tool calls cost the agent one step
instead of one step per call; only what the script prints (or its main() returns) comes back.

The script runs where the agent's tools run (Executor), from the workspace root. Each inner tool call
runs the tool's own `run` command in its extension directory, exactly as a direct call would, and is
reported on stderr so the tool server logs it as a tool_call with via="script".
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .extensions import Extensions

MARK = "@@harness-call@@ "
ERR = "@@script-error@@ "
PID = "@@harness-pid@@ "
LOG = "@@harness-log@@ "
NOTIFY = "@@harness-notify@@ "
MAX_SCRIPT_S = 1200   # the runner's Codex config allows MCP calls 1500 s

# Runs in the sandbox: python3 -c RUNNER, with {"code" or "file", "args", "tools"} on stdin.
RUNNER = r'''
import json, os, subprocess, sys, time
_spec = json.load(sys.stdin)
_root = os.getcwd()
def _emit(mark, obj):
    print(mark + json.dumps(obj), file=sys.stderr, flush=True)
_emit("@@harness-pid@@ ", {"pid": os.getpid()})
class ToolFailed(Exception):
    pass
def log(*parts):
    """Report progress while the script runs (task() shows the latest lines; keep them short)."""
    _emit("@@harness-log@@ ", {"text": " ".join(str(p) for p in parts)[:500]})
def notify(text, wake=False):
    """Leave yourself a notice, shown on your next tool result; wake=True also wakes you if you are thinking."""
    _emit("@@harness-notify@@ ", {"text": str(text)[:500], "wake": bool(wake)})
def call(name, args=None, /, **kw):
    """Call one of your tools by name; returns its parsed JSON result (or text). Raises ToolFailed.
    Positional-only, so a tool may itself take arguments called `name` or `args`."""
    a = dict(args or {}, **kw)
    if name in _spec["tools"] and _spec["tools"][name].get("acts") and not _spec.get("can_act", True):
        raise ToolFailed(f"{name} acts, and {_spec.get('hands_note') or 'this script may only read'}")
    if name not in _spec["tools"]:
        if name not in _spec["builtins"]:
            raise ToolFailed(f"no tool named {name!r}; callable here: {sorted(_spec['tools']) + _spec['builtins']}")
        import urllib.request
        req = urllib.request.Request(_spec["harness_url"], data=json.dumps({"name": name, "args": a,
                                     "who": _spec.get("who")}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=1500) as r:
            res = json.loads(r.read())
        if not res.get("ok"):
            raise ToolFailed(f"{name} failed: {res.get('error')}")
        try:
            return json.loads(res["text"])
        except ValueError:
            return res["text"]
    t = _spec["tools"][name]
    t0 = time.monotonic()
    try:
        p = subprocess.run(t["run"], cwd=os.path.join(_root, t["dir"]), input=json.dumps(a), capture_output=True,
                           text=True, timeout=t["timeout_s"])
        ok, out, err = p.returncode == 0, p.stdout.strip(), p.stderr.strip()[-2000:]
    except subprocess.TimeoutExpired:
        ok, out, err = False, "", f"timed out after {t['timeout_s']} s"
    print("@@harness-call@@ " + json.dumps({"tool": name, "args": a, "ok": ok, "ms": int((time.monotonic() - t0) * 1000),
          "bytes": len(out), "error": None if ok else err[-500:], "summary": out[:200]}), file=sys.stderr, flush=True)
    if not ok:
        raise ToolFailed(f"{name} failed: {err}")
    try:
        return json.loads(out)
    except ValueError:
        return out
if _spec.get("file"):
    _path = os.path.join(_root, _spec["file"])
    with open(_path, encoding="utf-8") as f:
        _src = f.read()
    sys.path.insert(0, os.path.dirname(_path))
else:
    _path, _src = "<script>", _spec["code"]
sys.path.insert(0, _root)
sys.path.insert(0, os.path.join(_root, "scripts"))   # kept scripts can import each other
import builtins   # so modules a script imports (kept scripts too) can use call() and ToolFailed
builtins.call, builtins.ToolFailed, builtins.log, builtins.notify = call, ToolFailed, log, notify
_g = {"__name__": "__main__", "__file__": _path, "call": call, "ToolFailed": ToolFailed, "log": log, "notify": notify,
      "args": _spec.get("args") or {}}
try:
    exec(compile(_src, _path, "exec"), _g)
    if callable(_g.get("main")):
        _r = _g["main"](**_g["args"])
        if _r is not None:
            print(json.dumps(_r, separators=(",", ":"), default=str))
except BaseException as e:
    import traceback
    _frames = [f for f in traceback.extract_tb(e.__traceback__) if f.filename == _path]
    _at = ""
    if _frames:
        _ln = _frames[-1].lineno
        _lines = _src.splitlines()
        _at = f" at line {_ln}: " + (_lines[_ln - 1].strip() if 0 < _ln <= len(_lines) else "")
    sys.stdout.flush()
    print("@@script-error@@ " + f"{type(e).__name__}: {str(e)[:1500]}{_at}", file=sys.stderr, flush=True)
    sys.exit(1)
'''

DOC = """Run Python in your sandbox that calls your own tools, as ONE step.

Every step you take re-reads your whole context, so the number of steps is your main cost. A known
chore (anything that needs several tool calls: loops, retries, waiting until something holds, acting
on what an earlier call returned) should be one run_script call, decided once.
  code: the script. By default it runs once and is gone: that's right for a one-off.
  keep: a name (lower_snake_case) to also keep `code` as scripts/<name>.py, when the run succeeds.
        Keep only what you will run again, give it a docstring whose first line says what it does,
        and parameters through args, so one kept script serves many cases. Keeping a name again
        replaces it.
  name: run a kept script again by name (with new args). Call run_script with no arguments to list
        your kept scripts and what each does; extend one of them before writing a new one.
  file: run any other script in your workspace (path relative to it).
  args: a dict, available to the script as `args`; if the script defines main(), it is called as
        main(**args) and its return value is printed as JSON.
  background: start it and return at once with a task id ("t3"), so you keep working while it
        runs (for up to `minutes`, default 60, at most 480). Use it for anything long: a chore,
        a guard loop, waiting on something slow. Its end arrives as a notice on your next tool
        result; task("t3") shows its progress, wait(on=["t3"]) waits for it, task("t3", stop=True)
        stops it. hands (default true): a task that acts holds your hands while it runs, so
        nothing else can act meanwhile (one pair of hands); hands=false makes a task that only
        reads, which can run beside it. wake_on_end: wake you when it ends. If your hands are
        busy, a task that needs them is queued and starts as soon as they're free.
  after: with background, a task id: queue this task to start when that one ends ok. If that one
        fails or is stopped, this one is held, with whatever is behind it, until you resume it or
        drop it (task). Line up your next chores this way, so your hands keep working while you think.
Inside the script:
  call("tool_name", arg=value, ...)  -> that tool's parsed JSON result (or text); raises ToolFailed
  log("text")       progress that task() shows while it runs
  notify("text", wake=False)  a notice on your next tool result (wake=True: also wake you)
  Your extension tools and the harness tools (input tools, set_goal, costs, read_docs, list_hooks,
  task, watch, status) can be called; not screenshot, load_extension, run_script or wait.
Your workspace root and scripts/ are importable, so a script can build on your kept scripts and
modules. Write steps as "make sure X holds" (check, then act), so a rerun after a fix is safe, and
verify the outcome inside the script.
Only what the script prints (or main returns) comes back: return a short summary, not raw results.
If the script fails you get what it printed, the error and the script line it happened on, and how
many tool calls it made. timeout_s: up to 1200; give every loop a count or a deadline, and stop
when progress stalls."""


KEPT_DIR = "scripts"
NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,48}")


def purpose(source: str) -> str:
    """The first line of a script's docstring, or '' if it has none (or doesn't parse)."""
    try:
        doc = ast.get_docstring(ast.parse(source)) or ""
    except SyntaxError:
        return ""
    return doc.strip().splitlines()[0].strip() if doc.strip() else ""


def kept(workspace: Path) -> list[tuple[str, str]]:
    """(name, purpose) of each kept script, by name."""
    d = workspace / KEPT_DIR
    out = []
    for p in sorted(d.glob("*.py")) if d.is_dir() else []:
        if NAME_RE.fullmatch(p.stem):
            out.append((p.stem, purpose(p.read_text(encoding="utf-8", errors="replace")) or "(no docstring)"))
    return out


def listing(workspace: Path) -> str:
    items = kept(workspace)
    if not items:
        return "No kept scripts yet. Keep one with run_script(code=..., keep=\"name\")."
    return f"Kept scripts ({len(items)}; run one with name=..., args=...):\n" + \
        "\n".join(f"  {n}: {p[:150]}" for n, p in items)


@dataclass
class Result:
    ok: bool
    out: str                 # what the script printed (or main returned), cut to MAX_RESULT_CHARS
    detail: str              # on failure: the error and the script line, or its stderr tail
    calls: list[dict]        # its tool calls, as reported
    seconds: float
    stopped: bool = False    # killed by stop() or its deadline

    def text(self) -> str:
        took = f"{self.seconds:.1f} s, {len(self.calls)} tool calls"
        if self.ok:
            return (self.out or "(the script printed nothing)") + f"\n[script ok: {took}]"
        return f"{self.out}\n[script failed: {took}] {self.detail}".strip()


class Handle:
    """A running script; stop() kills it where it runs."""

    def __init__(self) -> None:
        self.pid: int | None = None
        self.proc = None
        self.stop_reason: str | None = None

    async def stop(self, ext: "Extensions", reason: str = "stopped") -> None:
        self.stop_reason = self.stop_reason or reason
        if self.pid:
            # Killing `docker exec` doesn't kill what it started, so kill the script in the sandbox.
            await ext.executor.run([ext.executor_python, "-c",
                                    f"import os, signal; os.kill({self.pid}, signal.SIGTERM)"], "", "", 15)
        if self.proc and self.proc.returncode is None:
            self.proc.kill()


def tool_table(ext: "Extensions") -> dict:
    return {t.name: {"run": t.run, "dir": l.manifest.dir_rel, "timeout_s": t.timeout_s, "acts": not t.read_only}
            for l in ext.loaded.values() for t in l.manifest.tools}


async def execute(ext: "Extensions", code: str, timeout_s: float, file: str = "", args: dict | None = None,
                  harness_url: str = "", builtins: list[str] | None = None, *, who: str | None = None,
                  can_act: bool = True, hands_note: str = "", handle: Handle | None = None,
                  on_event=None) -> Result:
    """Run a script in the sandbox. on_event(kind, data) gets its "call", "log" and "notify" events as
    they happen, so a long script can be followed live."""
    from .extensions import MAX_RESULT_CHARS, _tail
    handle = handle or Handle()
    timeout_s = max(1.0, float(timeout_s))
    t0 = time.monotonic()
    spec = {"code": code, "file": file, "args": args or {}, "tools": tool_table(ext), "who": who,
            "can_act": can_act, "hands_note": hands_note, "harness_url": harness_url,
            "builtins": sorted(builtins or [])}
    proc = handle.proc = await ext.executor.spawn([ext.executor_python, "-c", RUNNER], "")
    proc.stdin.write(json.dumps(spec).encode())
    await proc.stdin.drain()
    proc.stdin.close()
    calls: list[dict] = []
    errors: list[str] = []
    rest: list[str] = []
    marks = ((MARK, "call"), (LOG, "log"), (NOTIFY, "notify"), (PID, "pid"))

    async def read_err() -> None:
        while raw := await proc.stderr.readline():
            line = raw.decode(errors="replace").rstrip("\r\n")
            for mark, kind in marks:
                if line.startswith(mark):
                    try:
                        data = json.loads(line[len(mark):])
                    except ValueError:
                        break
                    if kind == "pid":
                        handle.pid = data.get("pid")
                    else:
                        if kind == "call":
                            calls.append(data)
                        if on_event:
                            try:
                                on_event(kind, data)
                            except Exception:
                                pass
                    break
            else:
                if line.startswith(ERR):
                    errors.append(line[len(ERR):])
                else:
                    rest.append(line)

    err_task = asyncio.create_task(read_err())
    out_task = asyncio.create_task(proc.stdout.read())
    try:
        await asyncio.wait_for(proc.wait(), timeout_s)
    except asyncio.TimeoutError:
        await handle.stop(ext, f"timed out after {timeout_s:g} s")
    except asyncio.CancelledError:
        await handle.stop(ext, "cancelled")
        raise
    try:
        await asyncio.wait_for(proc.wait(), 10)
        out = (await asyncio.wait_for(out_task, 5)).decode(errors="replace")
        await asyncio.wait_for(err_task, 5)
    except asyncio.TimeoutError:
        out = ""
    out = out.replace("\r\n", "\n").strip()
    if len(out) > MAX_RESULT_CHARS:
        out = out[:MAX_RESULT_CHARS] + f"\n… [{len(out) - MAX_RESULT_CHARS} more characters cut]"
    seconds = time.monotonic() - t0
    if handle.stop_reason:
        return Result(False, out, handle.stop_reason, calls, seconds, stopped=True)
    if proc.returncode == 0:
        return Result(True, out, "", calls, seconds)
    return Result(False, out, errors[-1] if errors else _tail("\n".join(rest)), calls, seconds)


async def run(ext: "Extensions", code: str, timeout_s: float, file: str = "", args: dict | None = None,
              harness_url: str = "", builtins: list[str] | None = None, **kw) -> tuple[bool, str, list[dict]]:
    """A foreground script, capped at MAX_SCRIPT_S. Returns (ok, text for the agent, inner calls)."""
    r = await execute(ext, code, min(float(timeout_s), MAX_SCRIPT_S), file, args, harness_url, builtins, **kw)
    return r.ok, r.text(), r.calls
