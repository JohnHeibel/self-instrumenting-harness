"""Record a real, short app-server session as a test fixture.

Starts the tool server and the agent container, runs one trivial turn that makes one
harness tool call, and saves every JSON-RPC line both ways. Run from the repo root:

    uv run --project runner python runner/scripts/record_fixture.py [--out PATH]

Needs Docker, the agent image and ~/.codex/auth.json. Costs one tiny model turn.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

from runner import agent
from runner.appserver import AppServer
from runner.toolserver_proc import ToolServer

PROMPT = ("This is a short recorded test. Think briefly about whether 391 is prime, "
          "call the harness tool `list_hooks` once, then reply in one line with your answer.")
# Account, quota and moderation messages say nothing about the protocol we map; keep them out of git.
DROP_METHODS = ("account/", "remoteControl/", "turn/moderationMetadata")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path,
                    default=agent.REPO / "runner" / "tests" / "fixtures" / "real_session.jsonl")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--model")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="fixture-"))
    run_id = "fixture-" + time.strftime("%Y%m%dT%H%M%S")
    raw = tmp / "raw.jsonl"
    ts = ToolServer(tmp, run_id, args.port)
    ts.start()
    srv = None
    try:
        agent.remove_container(run_id)
        srv = AppServer(agent.docker_cmd(tmp, run_id, Path.home() / ".codex" / "auth.json",
                                         args.port, model=args.model),
                        raw_log=raw, stderr_path=tmp / "stderr.log")
        srv.initialize()
        thread = srv.request("thread/start", {"cwd": "/home/agent/work"}, timeout=120)["thread"]
        srv.request("turn/start", {"threadId": thread["id"],
                                   "input": [{"type": "text", "text": PROMPT}]}, timeout=120)
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            msg = srv.next_message(5)
            if msg and msg["method"] == "turn/completed":
                break
        else:
            print("turn did not complete in 300 s", file=sys.stderr)
            return 1
    finally:
        if srv:
            srv.close()
        agent.remove_container(run_id)
        ts.stop()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.loads(l) for l in raw.read_text(encoding="utf-8").splitlines()]
    lines = [r for r in lines if not (isinstance(r["msg"], dict)
                                      and str(r["msg"].get("method", "")).startswith(DROP_METHODS))]
    with args.out.open("w", encoding="utf-8") as f:
        for rec in lines:
            f.write(json.dumps(_scrub(rec), ensure_ascii=False) + "\n")
    print(f"wrote {len(lines)} messages to {args.out}")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


SECRET_KEYS = {"accessToken", "access_token", "idToken", "id_token", "refreshToken",
               "refresh_token", "apiKey", "api_key", "authorization", "email",
               "accountId", "account_id", "userId", "user_id", "creatorUserId",
               "creatorAccountId", "encrypted_content", "encryptedContent"}


def _scrub(obj):
    """Fixtures go in git: drop credentials, identities and encrypted reasoning."""
    if isinstance(obj, dict):
        return {k: ("<redacted>" if k in SECRET_KEYS else _scrub(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    return obj


if __name__ == "__main__":
    sys.exit(main())
