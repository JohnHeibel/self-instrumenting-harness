"""runner-state.json: what the runner needs to resume a run. Written atomically."""

from __future__ import annotations

import ctypes
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class RunState:
    run_id: str
    started_at: float                 # unix time; the time budget counts from here
    status: str = "running"           # running | ended
    ended_reason: str | None = None
    thread_id: str | None = None      # saved the moment Codex reports it
    model: str | None = None
    goal_sent: bool = False           # the goal prompt reached Codex (turn/start accepted)
    run_started_logged: bool = False
    turns: int = 0
    failures: int = 0                 # consecutive
    active_turn_id: str | None = None # set while a turn runs; left behind by a crash
    extra: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "RunState | None":
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {k: data.pop(k) for k in list(data) if k in cls.__dataclass_fields__}
        st = cls(**known)
        st.extra.update(data)
        return st

    def save(self, path: Path) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        os.replace(tmp, path)


class RunLock:
    """One runner per run dir (two runners on one run dir would fight over it)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def acquire(self) -> None:
        if self.path.exists():
            try:
                pid = int(self.path.read_text().strip())
            except ValueError:
                pid = 0
            if pid and pid != os.getpid() and pid_alive(pid):
                raise SystemExit(f"another runner (pid {pid}) holds {self.path}")
        self.path.write_text(str(os.getpid()))

    def release(self) -> None:
        try:
            if self.path.read_text().strip() == str(os.getpid()):
                self.path.unlink()
        except FileNotFoundError:
            pass


def kill_tree(pid: int) -> None:
    import signal
    import subprocess
    if sys.platform == "win32":  # the venv python.exe is a launcher with a child; kill both
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        # os.kill(pid, 0) would terminate the process on Windows; ask the kernel instead.
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
