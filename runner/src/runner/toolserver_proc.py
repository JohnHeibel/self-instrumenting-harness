"""The tool server as a child process of the runner, pointed at the run dir."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from .agent import REPO


class ToolServerError(Exception):
    pass


class ToolServer:
    def __init__(self, run_dir: Path, run_id: str, port: int, host_config: Path,
                 without_tools: list[str] | None = None) -> None:
        self.run_dir, self.run_id, self.port = run_dir, run_id, port
        self.host_config = host_config
        self.without_tools = without_tools or []
        self.proc: subprocess.Popen | None = None

    def start(self, timeout: float = 30) -> None:
        if _listening(self.port):
            raise ToolServerError(f"port {self.port} is already in use; is another run going?")
        env = {**os.environ, "PYTHONPATH": str(REPO / "toolserver" / "src")}
        log = (self.run_dir / "toolserver.log").open("a", encoding="utf-8")
        cmd = [sys.executable, "-m", "toolserver.server", "--port", str(self.port),
               "--run-dir", str(self.run_dir), "--run-id", self.run_id]
        cmd += ["--host-config", str(self.host_config)]
        if self.without_tools:
            cmd += ["--without-tools", ",".join(self.without_tools)]
        # Extension tools run in the agent container, which the tool server finds by its
        # default name harness-agent-<run-id>: the name agent.container_name gives it.
        self.proc = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise ToolServerError(f"tool server exited; see {self.run_dir / 'toolserver.log'}")
            if _listening(self.port):
                return
            time.sleep(0.2)
        self.stop()
        raise ToolServerError("tool server did not start listening")

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(5)


def _listening(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
        return True
    except OSError:
        return False
