"""run_script: one tool call that runs the agent's own Python in its sandbox, with its extension tools
callable as functions. Loops, conditions and waits over many tool calls cost the agent one step
instead of one step per call; only what the script prints (or its main() returns) comes back.

The script runs where the agent's tools run (Executor), from the workspace root. Each inner tool call
runs the tool's own `run` command in its extension directory, exactly as a direct call would, and is
reported on stderr so the tool server logs it as a tool_call with via="script".
"""

from __future__ import annotations

import ast
import json
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .extensions import Extensions

MARK = "@@harness-call@@ "
ERR = "@@script-error@@ "
MAX_SCRIPT_S = 1200   # the runner's Codex config allows MCP calls 1500 s

# Runs in the sandbox: python3 -c RUNNER, with {"code" or "file", "args", "tools"} on stdin.
RUNNER = r'''
import json, os, subprocess, sys, time
_spec = json.load(sys.stdin)
_root = os.getcwd()
class ToolFailed(Exception):
    pass
def call(name, args=None, /, **kw):
    """Call one of your tools by name; returns its parsed JSON result (or text). Raises ToolFailed.
    Positional-only, so a tool may itself take arguments called `name` or `args`."""
    a = dict(args or {}, **kw)
    if name not in _spec["tools"]:
        if name not in _spec["builtins"]:
            raise ToolFailed(f"no tool named {name!r}; callable here: {sorted(_spec['tools']) + _spec['builtins']}")
        import urllib.request
        req = urllib.request.Request(_spec["harness_url"], data=json.dumps({"name": name, "args": a}).encode(),
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
builtins.call, builtins.ToolFailed = call, ToolFailed
_g = {"__name__": "__main__", "__file__": _path, "call": call, "ToolFailed": ToolFailed, "args": _spec.get("args") or {}}
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
Inside the script:
  call("tool_name", arg=value, ...)  -> that tool's parsed JSON result (or text); raises ToolFailed
  Your extension tools and the harness tools (input tools, set_goal, costs, read_docs, list_hooks)
  can be called; not screenshot, load_extension or run_script.
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


async def run(ext: "Extensions", code: str, timeout_s: float, file: str = "", args: dict | None = None,
              harness_url: str = "", builtins: list[str] | None = None) -> tuple[bool, str, list[dict]]:
    """Returns (ok, text for the agent, inner calls)."""
    from .extensions import MAX_RESULT_CHARS, _tail
    tools = {t.name: {"run": t.run, "dir": l.manifest.dir_rel, "timeout_s": t.timeout_s}
             for l in ext.loaded.values() for t in l.manifest.tools}
    timeout_s = max(1.0, min(float(timeout_s), MAX_SCRIPT_S))
    t0 = time.monotonic()
    spec = {"code": code, "file": file, "args": args or {}, "tools": tools,
            "harness_url": harness_url, "builtins": sorted(builtins or [])}
    rc, out, err = await ext.executor.run([ext.executor_python, "-c", RUNNER], "", json.dumps(spec), timeout_s)
    calls, errors, rest = [], [], []
    for line in err.splitlines():
        if line.startswith(MARK):
            try:
                calls.append(json.loads(line[len(MARK):]))
                continue
            except ValueError:
                pass
        if line.startswith(ERR):
            errors.append(line[len(ERR):])
            continue
        rest.append(line)
    out = out.replace("\r\n", "\n").strip()
    if len(out) > MAX_RESULT_CHARS:
        out = out[:MAX_RESULT_CHARS] + f"\n… [{len(out) - MAX_RESULT_CHARS} more characters cut]"
    took = f"{time.monotonic() - t0:.1f} s, {len(calls)} tool calls"
    if rc == 0:
        return True, (out or "(the script printed nothing)") + f"\n[script ok: {took}]", calls
    detail = errors[-1] if errors else _tail("\n".join(rest))
    return False, f"{out}\n[script failed: {took}] {detail}".strip(), calls
