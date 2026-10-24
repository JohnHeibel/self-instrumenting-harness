"""Spike: does Codex see a tool added mid-session as a direct, named tool?

Starts the tool server on the host, runs one `codex exec` in the agent container,
asks it to load a hook and then call the new tool, and writes a machine-checkable
verdict. Run from the repo root on the host (the Windows PC):

    uv run --project toolserver python spikes/codex_dynamic_tools/run.py [--model M] [--variant no-omit]

Exit code: 0 PASS, 1 FAIL, 2 INCONCLUSIVE, 3 setup error. Everything lands in
runs/spike-codex-dynamic-tools/<timestamp>-<variant>/.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PROBE = "probe_hello"
# The agent image is tagged with the Codex version its Dockerfile installs, as the runner does.
CODEX_VERSION = next(l.split("=", 1)[1].strip() for l in (REPO / "agent" / "Dockerfile").read_text().splitlines()
                     if l.startswith("ARG CODEX_VERSION="))
PROMPT = f"""This is an automated test of your tool setup. Do exactly this:
1. In your workspace (/home/agent/work), create extensions/probe/probe.py: it reads JSON from stdin
   and prints a JSON object {{"summary": "probe ok", "query": <the "query" argument>}}.
2. Create extensions/probe/extension.json for an extension named "probe" with reason "harness test",
   and one tool named "{PROBE}" with description "Echoes its query", input_schema with a string
   property "query", and run ["python3", "probe.py"]. No in_host part.
3. Call the tool `load_extension` with manifest="extensions/probe/extension.json".
4. Then call the new tool `{PROBE}` directly by its name with query="ping". Do not use call_hook,
   a shell, tool search, or code execution to reach it. If it is not available to you as a tool, say so.
3. Finish with two lines:
   RESULT: <exact text {PROBE} returned, or NONE>
   EXPOSURE: <how {PROBE} was offered to you: top-level tool / found via tool search / inside a code or exec tool / not available>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help="Codex model; default is Codex's default")
    ap.add_argument("--variant", choices=["default", "no-omit"], default="default",
                    help="no-omit drops omit_tools_from, to see Codex's default exposure")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--image", default=f"harness-agent:codex-{CODEX_VERSION}")
    ap.add_argument("--skip-build", action="store_true")
    ap.add_argument("--codex-home", type=Path, default=Path.home() / ".codex",
                    help="host Codex home holding auth.json")
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = REPO / "runs" / "spike-codex-dynamic-tools" / f"{stamp}-{args.variant}"
    (run_dir / "codex-sessions").mkdir(parents=True)
    print(f"run dir: {run_dir}")

    auth = args.codex_home / "auth.json"
    if not auth.exists():
        print(f"setup error: {auth} not found; run `codex login` on the host first")
        return 3

    if not args.skip_build:
        if subprocess.run(["docker", "build", "-t", args.image, str(REPO / "agent")]).returncode:
            print("setup error: docker build failed")
            return 3

    server = _start_server(run_dir, args.port)
    try:
        (run_dir / "workspace").mkdir(exist_ok=True)
        cmd = ["docker", "run", "--rm", "--name", f"harness-agent-{run_dir.name}",
               "-v", f"{auth}:/home/agent/.codex/auth.json",
               "-v", f"{run_dir / 'workspace'}:/home/agent/work",
               "-v", f"{run_dir / 'codex-sessions'}:/home/agent/.codex/sessions",
               args.image, "codex", "exec", "--json", "--skip-git-repo-check",
               "-c", f'mcp_servers.harness.url="http://host.docker.internal:{args.port}/mcp"']
        if args.variant == "no-omit":
            cmd += ["-c", "mcp_servers.harness.omit_tools_from=[]"]
        if args.model:
            cmd += ["-m", args.model]
        cmd.append(PROMPT)
        (run_dir / "codex-command.json").write_text(json.dumps(cmd, indent=2))
        with (run_dir / "codex.jsonl").open("w", encoding="utf-8") as out, \
                (run_dir / "codex.stderr.log").open("w", encoding="utf-8") as err:
            try:
                rc = subprocess.run(cmd, stdout=out, stderr=err, timeout=args.timeout).returncode
            except subprocess.TimeoutExpired:
                rc = "timeout"
    finally:
        server.terminate()
        server.wait(10)

    verdict = check(run_dir, codex_exit=rc, variant=args.variant, model=args.model)
    (run_dir / "verdict.json").write_text(json.dumps(verdict, indent=2))
    print(json.dumps(verdict, indent=2))
    return {"PASS": 0, "FAIL": 1}.get(verdict["verdict"], 2)


def _start_server(run_dir: Path, port: int) -> subprocess.Popen:
    env = {**os.environ, "PYTHONPATH": str(REPO / "toolserver" / "src")}
    log = (run_dir / "toolserver.log").open("w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", "toolserver.server", "--port", str(port),
                             "--run-dir", str(run_dir), "--run-id", run_dir.name],
                            env=env, stdout=log, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise SystemExit(f"setup error: tool server exited; see {run_dir / 'toolserver.log'}")
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            return proc
        except OSError:
            time.sleep(0.3)
    proc.kill()
    raise SystemExit("setup error: tool server did not start listening")


def check(run_dir: Path, **context) -> dict:
    events = _jsonl(run_dir / "events.jsonl")
    calls = [e for e in events if e.get("type") == "tool_call"]
    loaded = any(e.get("type") == "hook_loaded" and e.get("hook") == PROBE for e in events)
    # The agent wrote the extension itself; its tool ran inside the container.
    direct = [c for c in calls if c["tool"] == PROBE and c.get("via") == "direct" and c["ok"]]
    fallback = [c for c in calls if c["tool"] == PROBE and c.get("via") == "call_hook"]

    item_types: Counter = Counter()
    codex_mcp_calls, final_message = [], None
    for ev in _jsonl(run_dir / "codex.jsonl"):
        if ev.get("type") == "item.completed":
            item = ev.get("item", {})
            item_types[item.get("type")] += 1
            if item.get("type") == "mcp_tool_call":
                codex_mcp_calls.append(f'{item.get("server")}/{item.get("tool")}')
            if item.get("type") == "agent_message":
                final_message = item.get("text")

    # The rollout records what the model actually called: a harness tool by name,
    # or a generic exec / tool_search wrapper.
    model_calls = []
    for f in sorted((run_dir / "codex-sessions").rglob("*.jsonl")):
        for rec in _jsonl(f):
            p = rec.get("payload") or {}
            if p.get("type") in ("function_call", "custom_tool_call", "tool_search_call"):
                model_calls.append(".".join(filter(None, [p.get("namespace"), p.get("name") or p.get("type")])))
    wrapped = [n for n in model_calls if n.split(".")[-1] in ("exec", "tool_search", "tool_search_call")]
    probe_named = [n for n in model_calls if PROBE in n]

    reasons = []
    if not calls:
        verdict = "FAIL"; reasons.append("the model called no harness tool at all; it was likely not offered any "
                                         "(check exposure: omit_tools_from, tool search, code mode)")
    elif not loaded:
        verdict = "FAIL"; reasons.append("load_extension was never called successfully")
    elif not direct and fallback:
        verdict = "FAIL"; reasons.append(f"{PROBE} was only reached through call_hook")
    elif not direct:
        verdict = "FAIL"; reasons.append(f"{PROBE} was never called")
    elif fallback:
        verdict = "INCONCLUSIVE"; reasons.append("called directly, but call_hook was also used")
    elif wrapped and not probe_named:
        verdict = "INCONCLUSIVE"; reasons.append(f"server saw a direct call, but the model called wrappers {wrapped}")
    elif not model_calls:
        verdict = "INCONCLUSIVE"; reasons.append("server saw a direct call, but no rollout was found to confirm how the model called it")
    else:
        verdict = "PASS"; reasons.append(f"model called {probe_named} by name after it was loaded mid-session")

    return {"verdict": verdict, "reasons": reasons, **context,
            "server_tool_calls": [(c["tool"], c.get("via"), c["ok"]) for c in calls],
            "codex_item_types": dict(item_types), "codex_mcp_calls": codex_mcp_calls,
            "model_function_calls": model_calls, "final_message": final_message}


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


if __name__ == "__main__":
    sys.exit(main())
