"""Spectator frames: a picture of the host every few seconds for people watching the run.

Written to <run>/live/latest.jpg (and latest.json with the time), outside the agent's
workspace, and never logged as an agent observation. If the host's window isn't found, no
frame is written, so the rest of the desktop is never captured."""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .capture import Target, screenshot

INTERVAL_S = 5
WIDTH = 640


def write_frame(target: Target, out: Path) -> bool:
    shot = screenshot(target, max_width=WIDTH, thumb_width=WIDTH)
    if shot.capture is None:   # host not on screen: this would be the whole monitor
        return False
    out.mkdir(parents=True, exist_ok=True)
    _replace(out / "latest.jpg", shot.thumb_jpeg)
    meta = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "width": shot.capture.image_width, "height": shot.capture.image_height}
    _replace(out / "latest.json", json.dumps(meta).encode())
    return True


def _replace(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    for _ in range(5):   # Windows refuses while a reader has the old file open
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.1)


def start(target: Target, run_dir: Path, interval: float = INTERVAL_S) -> threading.Thread:
    out = run_dir / "live"

    def loop() -> None:
        while True:
            try:
                write_frame(target, out)
            except Exception:   # a failed frame must never affect the tool server
                pass
            time.sleep(interval)

    t = threading.Thread(target=loop, name="spectator", daemon=True)
    t.start()
    return t
