"""A logging forwarder for the host ports the agent may reach (`[host] agent_ports`).

The agent's own in-host servers (a bridge on a port) are invisible to the event log when it calls
them from a shell. Each forwarded port listens on `port + offset`, passes bytes to the real port, and
writes one `tool_call` record per connection: channel `hook`, tool `port:<port>`, the first bytes of the
request, how long it took and how many bytes came back. A client that sends one request per connection
(urllib, curl) gives one record per request.
"""

from __future__ import annotations

import socket
import threading
import time

from .events import EventLog

PEEK = 200


def _pipe(src: socket.socket, dst: socket.socket, count: list[int]) -> None:
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            count[0] += len(data)
            dst.sendall(data)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _first_request(client: socket.socket) -> bytes:
    """The start of the request: the head and (up to PEEK bytes of) the body, which clients often send apart."""
    data = client.recv(65536)
    client.settimeout(1)
    try:
        while b"\r\n\r\n" in data or not data:
            head, _, body = data.partition(b"\r\n\r\n")
            n = 0
            for line in head.split(b"\r\n"):
                if line.lower().startswith(b"content-length:"):
                    n = int(line.split(b":")[1])
            if len(body) >= min(n, PEEK):
                break
            more = client.recv(65536)
            if not more:
                break
            data += more
    except (OSError, ValueError):
        pass
    client.settimeout(None)
    return data


def _handle(client: socket.socket, target: tuple[str, int], port: int, log: EventLog) -> None:
    t0 = time.perf_counter()
    with client:
        try:
            first = _first_request(client)
            up = socket.create_connection(target, timeout=5)
        except OSError as e:
            log.write("tool_call", tool=f"port:{port}", channel="hook", args={}, ok=False,
                      error=f"ConnectError: {e}", duration_ms=round((time.perf_counter() - t0) * 1000), via="forward")
            return
        up.settimeout(None)
        with up:
            up.sendall(first)
            sent, got = [len(first)], [0]
            th = threading.Thread(target=_pipe, args=(client, up, sent), daemon=True)
            th.start()
            _pipe(up, client, got)
            th.join(5)
    head = first[:PEEK].decode("utf-8", "replace")
    body = first.partition(b"\r\n\r\n")[2][:PEEK].decode("utf-8", "replace")
    log.write("tool_call", tool=f"port:{port}", channel="hook",
              args={"request": head.split("\r\n")[0], "body": body}, ok=got[0] > 0,
              error=None if got[0] else "EmptyResponse: the host port sent nothing back",
              duration_ms=round((time.perf_counter() - t0) * 1000), result_kind="text", result_bytes=got[0],
              via="forward")


def start(log: EventLog, ports: list[int], offset: int, host: str = "0.0.0.0",
          target_host: str = "127.0.0.1") -> list[socket.socket]:
    """Listen on port+offset for each port; returns the listening sockets (close to stop)."""
    socks = []
    for port in ports:
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, port + offset))
        s.listen(32)
        socks.append(s)

        def loop(s=s, port=port):
            while True:
                try:
                    c, _ = s.accept()
                except OSError:
                    return
                threading.Thread(target=_handle, args=(c, (target_host, port), port, log), daemon=True).start()
        threading.Thread(target=loop, daemon=True).start()
    return socks
