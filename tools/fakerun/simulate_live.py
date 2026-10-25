"""Replay a finished fake run as if it were happening now, to test the UI's live mode.

Records are appended to a new run directory when their (shifted) timestamp comes due, screenshots are copied in as
their records appear, and `live/latest.jpg` (the spectator frame) is rewritten every 5 s from the newest screenshot.
The record at --skip-minutes is stamped "now"; earlier ones are stamped in the past, so a live view has history.

    uv run --project toolserver python tools/fakerun/generate.py --out runs/fake-src --rich --hours 0.3
    python3 tools/fakerun/simulate_live.py --src runs/fake-src --out runs/fake-live [--speed 1] [--skip-minutes 0]

--speed N plays N times faster than real time. --skip-minutes M starts M minutes of source time in (the earlier
records are written at once), so a live view has history to show straight away.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

FILES = ["events.jsonl", "runner.jsonl", "grader.jsonl"]


def parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--skip-minutes", type=float, default=0.0)
    a = ap.parse_args()

    recs: list[tuple[datetime, str, dict]] = []
    for f in FILES:
        for line in (a.src / f).read_text().splitlines():
            r = json.loads(line)
            recs.append((parse(r["ts"]), f, r))
    recs.sort(key=lambda x: x[0])
    t0 = recs[0][0] + timedelta(minutes=a.skip_minutes)
    wall0 = datetime.now(timezone.utc)

    if a.out.exists():
        shutil.rmtree(a.out)
    (a.out / "shots").mkdir(parents=True)
    (a.out / "live").mkdir()
    handles = {f: (a.out / f).open("a", encoding="utf-8") for f in FILES}
    newest_shot: Path | None = None
    next_frame = 0.0
    i = 0
    print(f"writing {a.out}; {len(recs)} records; Ctrl-C to stop")
    while i < len(recs):
        now = datetime.now(timezone.utc)
        due = t0 + (now - wall0) * a.speed  # source time that has "happened" by now
        while i < len(recs) and recs[i][0] <= due:
            src_t, f, r = recs[i]
            r = {**r, "run_id": a.out.name, "ts": (wall0 + (src_t - t0) / a.speed).isoformat(timespec="milliseconds").replace("+00:00", "Z")}
            if r.get("artifact", "").startswith("shots/"):
                shutil.copy(a.src / r["artifact"], a.out / r["artifact"])
                newest_shot = a.out / r["artifact"]
            handles[f].write(json.dumps(r) + "\n")
            handles[f].flush()
            i += 1
        if newest_shot and time.monotonic() >= next_frame:
            tmp = a.out / "live" / "latest.tmp"
            shutil.copy(newest_shot, tmp)
            tmp.replace(a.out / "live" / "latest.jpg")
            next_frame = time.monotonic() + 5
        time.sleep(0.2)
    print("source exhausted; the run is over")


if __name__ == "__main__":
    main()
