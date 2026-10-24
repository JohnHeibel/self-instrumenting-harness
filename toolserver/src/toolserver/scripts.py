"""run_script: one tool call that runs the agent's own Python in its sandbox, with its extension tools
callable as functions. Loops, conditions and waits over many tool calls cost the agent one step
instead of one step per call; only what the script prints comes back.

The script runs where the agent's tools run (Executor), from the workspace root. Each inner tool call
runs the tool's own `run` command in its extension directory, exactly as a direct call would, and is
reported on stderr so the tool server logs it as a tool_call with via="script".
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .extensions import Extensions

MARK = "@@harness-call@@ "
MAX_SCRIPT_S = 3600

# Runs in the sandbox: python3 -c RUNNER, with {"code", "tools"} on stdin.
RUNNER = r'''
import json, os, subprocess, sys, time
_spec = json.load(sys.stdin)
_root = os.getcwd()
class ToolFailed(Exception):
    pass
def call(name, args=None, **kw):
    """Call one of your tools by name; returns its parsed JSON result (or text). Raises ToolFailed."""
    if name not in _spec["tools"]:
        raise ToolFailed(f"no tool named {name!r}; yours: {sorted(_spec['tools'])}")
    t = _spec["tools"][name]
    a = dict(args or {}, **kw)
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
exec(compile(_spec["code"], "<script>", "exec"), {"__name__": "__main__", "call": call, "ToolFailed": ToolFailed})
'''

DOC = """Run a Python script in your sandbox that calls your own tools, as ONE step.

Every step you take re-reads your whole context, so the number of steps is your main cost. Use this
whenever a task needs several tool calls: loops, retries, waiting until something is true, acting on
what a previous call returned. Inside the script:
  call("tool_name", arg=value, ...)  -> the tool's parsed JSON result (or text); raises ToolFailed
  ToolFailed                          -> the exception a failed tool call raises
Only what the script prints comes back to you, so print a short summary, not raw results. The script
runs from your workspace root and can import your own modules. timeout_s: up to 3600; give it a
timeout close to how long the work should take, and make loops stop when progress stalls. An
uncaught exception returns its traceback along with what was printed so far."""


async def run(ext: "Extensions", code: str, timeout_s: float) -> tuple[bool, str, list[dict]]:
    """Returns (ok, text for the agent, inner calls)."""
    tools = {t.name: {"run": t.run, "dir": l.manifest.dir_rel, "timeout_s": t.timeout_s}
             for l in ext.loaded.values() for t in l.manifest.tools}
    timeout_s = max(1.0, min(float(timeout_s), MAX_SCRIPT_S))
    t0 = time.monotonic()
    code_rc, out, err = await ext.executor.run([ext.executor_python, "-c", RUNNER], "",
                                               json.dumps({"code": code, "tools": tools}), timeout_s)
    calls, rest = [], []
    for line in err.splitlines():
        if line.startswith(MARK):
            try:
                calls.append(json.loads(line[len(MARK):]))
                continue
            except ValueError:
                pass
        rest.append(line)
    from .extensions import MAX_RESULT_CHARS, _tail
    out = out.strip()
    if len(out) > MAX_RESULT_CHARS:
        out = out[:MAX_RESULT_CHARS] + f"\n… [{len(out) - MAX_RESULT_CHARS} more characters cut]"
    took = f"{time.monotonic() - t0:.1f} s, {len(calls)} tool calls"
    if code_rc == 0:
        return True, (out or "(the script printed nothing)") + f"\n[script ok: {took}]", calls
    tail = _tail("\n".join(rest))
    return False, f"{out}\n[script failed: {took}]\n{tail}".strip(), calls
