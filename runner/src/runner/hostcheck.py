"""Pluggable "is the host up?" checks. The runner never starts a turn while the host is down.

A check is a no-argument callable returning (up, detail). Choose one with --host-check:
  factorio-window   a visible window whose title contains "Factorio" (default; Windows only)
  window:<title>    same, any title substring
  always            always up (tests, or hosts without a window)
  cmd:<command>     up when the shell command exits 0
  py:<module>:<fn>  any importable callable
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from typing import Callable

HostCheck = Callable[[], "tuple[bool, str]"]


def window_check(title: str) -> HostCheck:
    def check() -> tuple[bool, str]:
        if sys.platform != "win32":
            return True, "window check needs Windows; assuming up"
        from toolserver.capture import _find_window
        rect = _find_window(title)
        return (rect is not None, f"window '{title}' " + ("found" if rect else "not found or minimized"))
    return check


def cmd_check(command: str) -> HostCheck:
    def check() -> tuple[bool, str]:
        try:
            rc = subprocess.run(command, shell=True, timeout=30, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL).returncode
        except subprocess.TimeoutExpired:
            return False, f"`{command}` timed out"
        return rc == 0, f"`{command}` exited {rc}"
    return check


def resolve(spec: str) -> HostCheck:
    if spec == "always":
        return lambda: (True, "always up")
    if spec == "factorio-window":
        return window_check("Factorio")
    if spec.startswith("window:"):
        return window_check(spec[len("window:"):])
    if spec.startswith("cmd:"):
        return cmd_check(spec[len("cmd:"):])
    if spec.startswith("py:"):
        mod, fn = spec[len("py:"):].rsplit(":", 1)
        return getattr(importlib.import_module(mod), fn)
    raise ValueError(f"unknown host check {spec!r}")
