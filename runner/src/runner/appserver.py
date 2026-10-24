"""JSON-RPC client for `codex app-server` (one JSON object per line over stdio).

The app-server is a child process: normally `docker run -i ... codex app-server`,
in tests a fake that replays a script. A reader thread puts every incoming message
on a queue; the runner pulls notifications with `next_message` and makes requests
with `request`, which buffers notifications that arrive while it waits.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

CLIENT_INFO = {"name": "harness-runner", "title": "Self-instrumenting harness runner", "version": "0.1.0"}


class AppServerError(Exception):
    """The app-server returned a JSON-RPC error, timed out, or exited."""


class AppServerExited(AppServerError):
    pass


class AppServer:
    def __init__(self, cmd: list[str], raw_log: Path | None = None,
                 on_server_request: Callable[[dict], Any] | None = None,
                 stderr_path: Path | None = None, env: dict | None = None) -> None:
        self.cmd = cmd
        self._raw = raw_log.open("a", encoding="utf-8") if raw_log else None
        self._raw_lock = threading.Lock()
        self._stderr = stderr_path.open("a", encoding="utf-8") if stderr_path else subprocess.DEVNULL
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=self._stderr, text=True, encoding="utf-8",
                                     errors="replace", bufsize=1, env=env)
        self._q: queue.Queue = queue.Queue()
        self._pending: deque = deque()
        self._next_id = 0
        self._on_server_request = on_server_request or _default_server_request
        self._send_lock = threading.Lock()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    # Wire ---------------------------------------------------------------------

    def _log(self, direction: str, msg: dict | str) -> None:
        if self._raw:
            with self._raw_lock:
                self._raw.write(json.dumps({"t": round(time.time(), 3), "dir": direction, "msg": msg},
                                           ensure_ascii=False) + "\n")
                self._raw.flush()

    def _read(self) -> None:
        assert self.proc.stdout
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                self._log("in-unparsed", line)
                continue
            self._log("in", msg)
            self._q.put(msg)
        self._q.put(None)  # EOF

    def _send(self, msg: dict) -> None:
        self._log("out", msg)
        try:
            with self._send_lock:
                assert self.proc.stdin
                self.proc.stdin.write(json.dumps(msg) + "\n")
                self.proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as e:
            raise AppServerExited(f"app-server stdin closed: {e}") from e

    # API ----------------------------------------------------------------------

    def notify(self, method: str, params: dict | None = None) -> None:
        msg: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)

    def send_request(self, method: str, params: dict | None = None) -> int:
        self._next_id += 1
        self._send({"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params or {}})
        return self._next_id

    def request(self, method: str, params: dict | None = None, timeout: float = 60) -> Any:
        rid = self.send_request(method, params)
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise AppServerError(f"{method}: no response in {timeout:.0f}s")
            msg = self._get(left)
            if msg is None:
                continue
            if msg.get("id") == rid and "method" not in msg:
                if "error" in msg:
                    raise AppServerError(f"{method}: {msg['error']}")
                return msg.get("result")
            self._pending.append(msg)

    def next_message(self, timeout: float) -> dict | None:
        """Next notification (server requests are answered automatically). None on timeout."""
        deadline = time.monotonic() + timeout
        while True:
            if self._pending:
                msg = self._pending.popleft()
            else:
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                msg = self._get(left)
                if msg is None:
                    return None
            if "method" in msg and "id" in msg:
                self._answer(msg)
                continue
            if "method" in msg:
                return msg
            # A response nobody is waiting for (e.g. to turn/interrupt): drop it.

    def _get(self, timeout: float) -> dict | None:
        try:
            msg = self._q.get(timeout=timeout)
        except queue.Empty:
            return None
        if msg is None:
            self._q.put(None)  # keep reporting EOF
            try:
                code = self.proc.wait(3)
            except subprocess.TimeoutExpired:
                code = None
            raise AppServerExited(f"app-server exited (code {code}){self._stderr_tail()}")
        return msg

    def _stderr_tail(self) -> str:
        path = getattr(self._stderr, "name", None)
        if not path:
            return ""
        try:
            self._stderr.flush()
            lines = [l for l in Path(path).read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
        except OSError:
            return ""
        return f": {lines[-1][:300]}" if lines else ""

    def _answer(self, req: dict) -> None:
        try:
            result = self._on_server_request(req)
            self._send({"jsonrpc": "2.0", "id": req["id"], "result": result})
        except Exception as e:  # unknown request: tell the server, don't hang it
            self._send({"jsonrpc": "2.0", "id": req["id"],
                        "error": {"code": -32601, "message": f"runner cannot handle {req.get('method')}: {e}"}})

    def initialize(self) -> dict:
        res = self.request("initialize", {"clientInfo": CLIENT_INFO,
                                          "capabilities": {"experimentalApi": True}})
        self.notify("initialized")
        return res

    def alive(self) -> bool:
        return self.proc.poll() is None

    def close(self, grace: float = 5) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(grace)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(5)
        if self._raw:
            self._raw.close()
        if hasattr(self._stderr, "close"):
            self._stderr.close()


def _default_server_request(req: dict) -> Any:
    """The container is the sandbox and approvals are off, so approve anything asked."""
    method = req.get("method", "")
    if method.endswith("requestApproval"):
        return {"decision": "accept"}
    if method in ("applyPatchApproval", "execCommandApproval"):
        return {"decision": "approved"}
    raise ValueError("unsupported")
