"""A fake `codex app-server` that replays scripted JSON-RPC. No model, no Docker.

    python fake_app_server.py <script.json> <state_dir>

The script describes what each launch of the process does (launch N uses
processes[N], the last one repeating), and what each turn of that launch does:

{
  "thread_id": "thr_1",
  "processes": [
    {"turns": [[step, ...], ...]}     # turns beyond the list repeat the last one
  ]
}

Steps:
  {"usage": 1200}                 thread/tokenUsage/updated with that thread total
  {"notify": method, "params": {}} any notification ({threadId}/{turnId} are filled in)
  {"message": "text"}             an agentMessage item (started + completed)
  {"reasoning": ["**T**\\n\\nx"]}   a reasoning item with these summary parts
  {"tool": "list_hooks"}          an mcpToolCall item on server harness
  {"sleep": 0.2}                  pause
  {"hang": true}                  wait until turn/interrupt, then complete as interrupted
  {"exit": 1}                     exit the process mid-turn (a crash)
  {"complete": "completed"}       turn/completed with that status (default at the end)
  {"fail": "message"}             error notification + turn/completed status failed
  {"duration_ms": 700000}         durationMs reported on turn/completed

Every request received is appended to <state_dir>/requests.jsonl, so tests can check
what the runner sent (e.g. thread/resume with the saved id).
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

script = json.loads(Path(sys.argv[1]).read_text())
state_dir = Path(sys.argv[2])
state_dir.mkdir(parents=True, exist_ok=True)
launch_file = state_dir / "launches"
launch = int(launch_file.read_text()) if launch_file.exists() else 0
launch_file.write_text(str(launch + 1))
procs = script["processes"]
proc = procs[min(launch, len(procs) - 1)]
THREAD = script.get("thread_id", "thr_fake")

out_lock = threading.Lock()
interrupted = threading.Event()
turn_counter = 0
total_tokens = int(script.get("start_total", 0))


def send(msg: dict) -> None:
    with out_lock:
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()


def notify(method: str, params: dict) -> None:
    send({"jsonrpc": "2.0", "method": method, "params": params})


def usage(total: int, turn_id: str) -> None:
    global total_tokens
    last = total - total_tokens
    total_tokens = total
    b = lambda n: {"totalTokens": n, "inputTokens": n, "cachedInputTokens": n // 2,
                   "cacheWriteInputTokens": 0, "outputTokens": 0, "reasoningOutputTokens": 0}
    notify("thread/tokenUsage/updated", {"threadId": THREAD, "turnId": turn_id,
                                         "tokenUsage": {"total": b(total), "last": b(last),
                                                        "modelContextWindow": 272000}})


def item(kind: str, turn_id: str, **fields) -> None:
    it = {"type": kind, "id": f"item_{time.monotonic_ns()}", **fields}
    base = {"threadId": THREAD, "turnId": turn_id}
    notify("item/started", {**base, "item": it, "startedAtMs": int(time.time() * 1000)})
    notify("item/completed", {**base, "item": it, "completedAtMs": int(time.time() * 1000)})


def run_turn(turn_id: str, steps: list) -> None:
    turn = {"id": turn_id, "items": [], "status": "inProgress"}
    notify("turn/started", {"threadId": THREAD, "turn": turn})
    status, error, duration = "completed", None, None
    for step in steps:
        if "usage" in step:
            usage(step["usage"], turn_id)
        elif "notify" in step:
            params = json.loads(json.dumps(step.get("params", {})).replace("{threadId}", THREAD)
                                .replace("{turnId}", turn_id))
            notify(step["notify"], params)
        elif "message" in step:
            item("agentMessage", turn_id, text=step["message"], phase="final_answer")
        elif "reasoning" in step:
            item("reasoning", turn_id, summary=step["reasoning"], content=[])
        elif "tool" in step:
            item("mcpToolCall", turn_id, server="harness", tool=step["tool"], status="completed",
                 arguments={})
        elif "sleep" in step:
            if interrupted.wait(step["sleep"]):
                status = "interrupted"
                break
        elif "hang" in step:
            interrupted.wait()
            status = "interrupted"
            break
        elif "exit" in step:
            sys.stdout.flush()
            import os
            os._exit(step["exit"])
        elif "complete" in step:
            status = step["complete"]
        elif "fail" in step:
            notify("error", {"threadId": THREAD, "turnId": turn_id, "willRetry": False,
                             "error": {"message": step["fail"]}})
            status, error = "failed", {"message": step["fail"]}
        elif "duration_ms" in step:
            duration = step["duration_ms"]
    notify("turn/completed", {"threadId": THREAD, "turn": {**turn, "status": status, "error": error,
                                                           "durationMs": duration}})


def handle(req: dict) -> None:
    global turn_counter
    with (state_dir / "requests.jsonl").open("a") as f:
        f.write(json.dumps({"launch": launch, **req}) + "\n")
    method, rid, params = req.get("method"), req.get("id"), req.get("params") or {}
    if rid is None:
        return  # client notification (initialized)
    if method == "initialize":
        result = {"userAgent": "fake", "codexHome": "/fake", "platformFamily": "unix", "platformOs": "linux"}
    elif method == "thread/start":
        result = {"thread": {"id": THREAD}, "model": "fake-model"}
        send({"jsonrpc": "2.0", "id": rid, "result": result})
        notify("thread/started", {"thread": {"id": THREAD}})
        return
    elif method == "thread/resume":
        if params.get("threadId") != THREAD:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32600, "message": "no such thread"}})
            return
        send({"jsonrpc": "2.0", "id": rid, "result": {"thread": {"id": THREAD}, "model": "fake-model"}})
        if total_tokens:
            usage(total_tokens, "")
        return
    elif method == "turn/start":
        turns = proc["turns"]
        steps = turns[min(turn_counter, len(turns) - 1)]
        turn_counter += 1
        turn_id = f"turn_{launch}_{turn_counter}"
        interrupted.clear()
        send({"jsonrpc": "2.0", "id": rid, "result": {"turn": {"id": turn_id, "items": [],
                                                               "status": "inProgress"}}})
        threading.Thread(target=run_turn, args=(turn_id, steps), daemon=True).start()
        return
    elif method == "turn/interrupt":
        interrupted.set()
        result = {}
    else:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"fake: {method}"}})
        return
    send({"jsonrpc": "2.0", "id": rid, "result": result})


total_tokens = int(proc.get("resume_total", script.get("start_total", 0)))
for line in sys.stdin:
    if line.strip():
        handle(json.loads(line))
