"""Logging reverse proxy in front of Odoo, for the hero run. Host side only; stdlib only.

    python hosts/odoo/apiproxy.py --run-dir RUN [--listen-port 8070] [--upstream http://127.0.0.1:8069]

The agent's odoo.json points at this port (host.docker.internal:8070). Everything is forwarded unchanged to the
upstream. Each JSON-2 call made with the agent's API key (from the keys file; compared, never printed or logged)
is appended to <run>/injector/odoo_calls.jsonl, one line:
  {"ts": ISO UTC, "model": ..., "method": ..., "ids": [...], "mutating": bool, "ok": bool, "status": int, "error": str|null}
ids: the record ids in the request ("ids"), the ids a create returned, or the ids in a search_read/read result
(first 50). Calls with other keys (and the web client) are forwarded but not logged. The simulator, intake and
auditor do not use this port. (The program_actions count comes from the grader, which reads the database.)
"""

from __future__ import annotations

import argparse
import hmac
import http.client
import json
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "graders" / "odoo"))
from odoo_client import DEFAULT_KEYS, load_keys  # noqa: E402

READ_METHODS = {"read", "search", "search_read", "search_count", "fields_get", "name_search", "web_search_read",
                "web_read", "read_group", "name_get", "default_get", "check_access_rights", "check_access",
                "search_fetch", "fetch"}
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
       "transfer-encoding", "upgrade", "content-length", "host"}


def ids_of(value) -> list[int]:
    if isinstance(value, int) and not isinstance(value, bool):
        return [value]
    if isinstance(value, list):
        out = []
        for v in value:
            if isinstance(v, int) and not isinstance(v, bool):
                out.append(v)
            elif isinstance(v, dict) and isinstance(v.get("id"), int):
                out.append(v["id"])
        return out
    if isinstance(value, dict) and isinstance(value.get("id"), int):
        return [value["id"]]
    return []


def describe(path: str, req_body: bytes, status: int, resp_body: bytes) -> dict | None:
    parts = urlsplit(path).path.strip("/").split("/")
    if len(parts) != 4 or parts[0] != "json" or parts[1] != "2":
        return None
    model, method = parts[2], parts[3]
    try:
        req = json.loads(req_body or b"{}")
    except ValueError:
        req = {}
    try:
        resp = json.loads(resp_body or b"null")
    except ValueError:
        resp = None
    ids = ids_of(req.get("ids")) if isinstance(req, dict) else []
    ok = 200 <= status < 300
    if ok and not ids:
        ids = ids_of(resp)
    err = None
    if not ok and isinstance(resp, dict):
        err = str(resp.get("message") or resp.get("name") or "")[:200]
    return {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z", "model": model,
            "method": method, "ids": ids[:50], "mutating": method not in READ_METHODS, "ok": ok, "status": status,
            "error": err}


def make_handler(upstream: tuple[str, int], agent_key: str, log_path: Path):
    lock = threading.Lock()

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):  # quiet
            pass

        def _proxy(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
            headers["Host"] = f"{upstream[0]}:{upstream[1]}"
            auth = self.headers.get("Authorization", "")
            is_agent = bool(agent_key) and auth.lower().startswith("bearer ") and \
                hmac.compare_digest(auth[7:].strip().encode(), agent_key.encode())
            try:
                c = http.client.HTTPConnection(*upstream, timeout=600)
                c.request(self.command, self.path, body=body or None, headers=headers)
                r = c.getresponse()
                data = r.read()
                status, rh = r.status, r.getheaders()
                c.close()
            except OSError as e:
                status, data, rh = 502, json.dumps({"message": f"proxy: upstream unreachable: {e}"}).encode(), []
            if is_agent:
                rec = describe(self.path, body, status, data)
                if rec:
                    with lock, log_path.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(rec) + "\n")
            self.send_response(status)
            for k, v in rh:
                if k.lower() not in HOP:
                    self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _proxy

    return H


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--listen-port", type=int, default=8070)
    ap.add_argument("--listen-host", default="0.0.0.0")   # the agent's container reaches the host's address
    ap.add_argument("--upstream", default="http://127.0.0.1:8069")
    ap.add_argument("--keys", type=Path, default=DEFAULT_KEYS)
    a = ap.parse_args()
    u = urlsplit(a.upstream)
    log = a.run_dir / "injector" / "odoo_calls.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    key = load_keys(a.keys)["agent"]["key"]
    srv = ThreadingHTTPServer((a.listen_host, a.listen_port), make_handler((u.hostname, u.port or 80), key, log))
    srv.daemon_threads = True
    print(f"proxy {a.listen_host}:{a.listen_port} -> {a.upstream}, logging the agent's calls to {log}", flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
