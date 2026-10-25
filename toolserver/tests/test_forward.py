import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import urllib.request

from conftest import SRC  # noqa: F401
from toolserver import forward
from toolserver.events import EventLog


def test_forwarder_passes_requests_and_logs_them(tmp_path):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_POST(self):
            n = int(self.headers["Content-Length"]); body = self.rfile.read(n)
            out = b"echo:" + body
            self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with socket.socket() as t:
        t.bind(("127.0.0.1", 0)); off = t.getsockname()[1] - port
    if port + off > 65000 or port + off < 1024:
        off = 1
    log = EventLog(tmp_path / "events.jsonl", "t")
    socks = forward.start(log, [port], off, host="127.0.0.1")
    try:
        r = urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port + off}/", data=b"hello"), timeout=5).read()
        assert r == b"echo:hello"
        import time; time.sleep(0.3)
    finally:
        for s in socks: s.close()
        srv.shutdown()
    recs = [json.loads(l) for l in (tmp_path / "events.jsonl").read_text().splitlines()]
    c = [x for x in recs if x["type"] == "tool_call"][0]
    assert c["tool"] == f"port:{port}" and c["channel"] == "hook" and c["ok"] and c["args"]["body"] == "hello"
    assert c["via"] == "forward" and c["result_bytes"] > 5
