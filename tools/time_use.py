"""Where a run's wall-clock time went, in bins: hands busy (acting on the program), tools running with
the hands free, nothing running (the model thinking between steps), and the agent not running at all.

    python tools/time_use.py runs/<run> [--bin-min 10]

Reads events.jsonl and runner.jsonl; the same split the `costs` tool shows the agent.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "toolserver" / "src"))
from toolserver import costs  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--bin-min", type=float, default=10)
    a = ap.parse_args()
    runner = costs._all(a.run_dir / "runner.jsonl")
    events = costs._all(a.run_dir / "events.jsonl")
    stamps = [costs._t(r) for r in events + runner]
    if not stamps:
        sys.exit("no records")
    start, end = min(stamps), max(stamps)
    sp = costs.spans(a.run_dir, end)
    rows, totals = [], {"wall": 0.0, "hands": 0.0, "tools": 0.0, "idle": 0.0, "off": 0.0}
    t = start
    while t < end:
        u = costs.time_use(a.run_dir, t, min(t + a.bin_min * 60, end), sp, runner)
        rows.append((t, u))
        for k in totals:
            totals[k] += u[k]
        t += a.bin_min * 60
    print(f"{'from (UTC)':<11}{'hands busy':>11}{'tools only':>11}{'idle':>7}{'longest idle':>14}{'agent off':>11}")
    for t, u in rows + [(None, totals)]:
        on = u["wall"] - u["off"]
        pct = (lambda k: f"{u[k] / on:.0%}") if on > 0 else (lambda k: "-")
        label = datetime.fromtimestamp(t, timezone.utc).strftime("%H:%M") if t else "all"
        longest = costs.longest_idle(sp, u.get("off_spans", []), t, t + u["wall"]) if t else 0
        print(f"{label:<11}{pct('hands'):>11}{pct('tools'):>11}{pct('idle'):>7}"
              f"{(f'{longest / 60:.1f} min' if t else ''):>14}{u['off'] / 60:>9.0f} m")


if __name__ == "__main__":
    main()
