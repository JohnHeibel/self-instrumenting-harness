"""Minimal Source RCON client (the protocol Factorio's --rcon-port speaks). Stdlib only.

    with Rcon("127.0.0.1", 27015, password) as r:
        print(r.command("/time"))
"""

from __future__ import annotations

import socket
import struct

AUTH, AUTH_RESPONSE, EXEC, RESPONSE = 3, 2, 2, 0


class RconError(Exception):
    pass


class Rcon:
    def __init__(self, host: str, port: int, password: str, timeout: float = 10) -> None:
        self.host, self.port, self.password, self.timeout = host, port, password, timeout
        self.sock: socket.socket | None = None
        self._id = 0

    def __enter__(self) -> "Rcon":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def connect(self) -> None:
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        rid = self._send(AUTH, self.password)
        while True:
            got, kind, _ = self._recv()
            if kind == AUTH_RESPONSE:
                if got == -1 or got != rid:
                    self.close()
                    raise RconError("RCON authentication failed (wrong password?)")
                return

    def close(self) -> None:
        if self.sock:
            self.sock.close()
            self.sock = None

    def command(self, cmd: str) -> str:
        """Run one command and return its output. Factorio answers each command with one
        packet (it rejects the empty marker packet other servers use to end a split reply)."""
        if not self.sock:
            self.connect()
        rid = self._send(EXEC, cmd)
        while True:
            got, _, body = self._recv()
            if got == rid:
                return body

    def _send(self, kind: int, body: str) -> int:
        self._id += 1
        data = struct.pack("<ii", self._id, kind) + body.encode("utf-8") + b"\x00\x00"
        self.sock.sendall(struct.pack("<i", len(data)) + data)
        return self._id

    def _recv(self) -> tuple[int, int, str]:
        (size,) = struct.unpack("<i", self._read(4))
        data = self._read(size)
        rid, kind = struct.unpack("<ii", data[:8])
        return rid, kind, data[8:-2].decode("utf-8", errors="replace")

    def _read(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise RconError("RCON connection closed")
            buf += chunk
        return buf
