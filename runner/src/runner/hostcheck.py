"""Is the host up? The runner never starts a turn while the host is down.

A check is a no-argument callable returning (up, detail). It comes from the host config's
`[host] up_check`:
  "window"   the host's window (by window_title/process) is on screen and not minimized
  "always"   always up (hosts without a window)
  [argv...]  up when that command exits 0 (run from the host config's directory)

--host-check overrides it for a run: window | always | cmd:<command> | py:<module>:<fn>.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path
from typing import Callable

from toolserver.extensions import HostConfig

HostCheck = Callable[[], "tuple[bool, str]"]


def window_check(hc: HostConfig) -> HostCheck:
    def check() -> tuple[bool, str]:
        if sys.platform != "win32":
            return True, "window check needs Windows; assuming up"
        from toolserver.capture import Target, host_up
        return host_up(Target(hc.window_title, hc.process, hc.capture))
    return check


def cmd_check(command: str | list[str], cwd: Path | None = None) -> HostCheck:
    shown = command if isinstance(command, str) else " ".join(command)

    def check() -> tuple[bool, str]:
        try:
            rc = subprocess.run(command, shell=isinstance(command, str), cwd=cwd, timeout=30,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
        except subprocess.TimeoutExpired:
            return False, f"`{shown}` timed out"
        except OSError as e:
            return False, f"`{shown}` could not run: {e}"
        return rc == 0, f"`{shown}` exited {rc}"
    return check


def from_config(hc: HostConfig, config_dir: Path | None = None) -> HostCheck:
    spec = hc.up_check
    if isinstance(spec, list):
        if not spec:
            raise ValueError("host config: [host] up_check is an empty command")
        return cmd_check([str(a) for a in spec], config_dir)
    if spec == "window":
        if not (hc.window_title or hc.process):
            raise ValueError('host config: up_check = "window" needs [host] window_title or process')
        return window_check(hc)
    if spec == "always":
        return lambda: (True, "always up")
    raise ValueError(f'host config: [host] up_check must be "window", "always" or a command list, not {spec!r}')


def resolve(spec: str, hc: HostConfig) -> HostCheck:
    """A --host-check override."""
    if spec in ("window", "always"):
        return from_config(HostConfig(**{**hc.__dict__, "up_check": spec}))
    if spec.startswith("cmd:"):
        return cmd_check(spec[len("cmd:"):])
    if spec.startswith("py:"):
        mod, fn = spec[len("py:"):].rsplit(":", 1)
        return getattr(importlib.import_module(mod), fn)
    raise ValueError(f"unknown host check {spec!r}")
