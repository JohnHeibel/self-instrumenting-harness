"""A run's headline numbers over time, in bins: model tokens per hour, where wall-clock time went (hands
busy acting on the program, tools running with the hands free, nothing running = the model thinking,
agent not running), and how much got done for it (actions in the program per step, tokens per action,
actions per minute of busy hands).

    python tools/time_use.py runs/<run> [--bin-min 10]

Reads events.jsonl, runner.jsonl (usage records) and any log with program_actions records; the same
split the `costs` tool shows the agent. "-" where a number has nothing to divide by.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "toolserver" / "src"))
from toolserver import costs  # noqa: E402


def usage_points(runner: list[dict]) -> list[tuple[float, int, int]]:
    """(time, tokens, uncached tokens) used by each model request, from the cumulative usage records."""
    out, prev_t, prev_u = [], 0, 0
    for r in runner:
        if r.get("type") != "usage":
            continue
        tot, unc = int(r.get("total_tokens") or 0), int(r.get("uncached_tokens") or 0)
        d_t = tot - prev_t if tot >= prev_t else tot      # counters restarted: count from zero
        d_u = unc - prev_u if unc >= prev_u else unc
        out.append((costs._t(r), d_t, d_u))
        prev_t, prev_u = tot, unc
    return out


def action_points(run_dir: Path) -> list[tuple[float, int]]:
    out = []
    for path in sorted(run_dir.glob("*.jsonl")):
        if path.name == "events.jsonl":
            continue
        for r in costs._all(path):
            if r.get("type") == "program_actions":
                out.append((costs._t(r), int(r.get("count") or 0)))
    return out


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
    usage, actions = usage_points(runner), action_points(a.run_dir)
    keys = ("wall", "hands", "tools", "idle", "off", "tokens", "uncached", "steps", "actions")
    rows, totals = [], dict.fromkeys(keys, 0.0)
    t = start
    while t < end:
        b = min(t + a.bin_min * 60, end)
        u = costs.time_use(a.run_dir, t, b, sp, runner)
        inside = [p for p in usage if t <= p[0] < b or (b == end and p[0] == end)]
        u["tokens"], u["uncached"] = sum(p[1] for p in inside), sum(p[2] for p in inside)
        u["steps"] = len(inside)
        u["actions"] = sum(n for ts, n in actions if t <= ts < b or (b == end and ts == end))
        rows.append((t, u))
        for k in keys:
            totals[k] += u[k]
        t += a.bin_min * 60

    head = (f"{'from':<6}{'tok/h':>8}{'uncached/h':>11}{'steps':>6}{'actions':>8}{'act/step':>9}"
            f"{'tok/act':>8}{'hands':>7}{'tools':>7}{'idle':>6}{'longest idle':>13}{'off':>5}{'act/busy-min':>13}")
    print(head)
    for t, u in rows + [(None, totals)]:
        on = u["wall"] - u["off"]
        pct = (lambda k: f"{u[k] / on:.0%}") if on > 0 else (lambda k: "-")
        label = datetime.fromtimestamp(t, timezone.utc).strftime("%H:%M") if t else "all"
        if t and u["wall"] < a.bin_min * 60 - 1:
            label += "*"                                  # a short last bin: per-hour numbers are noisy
        hours = u["wall"] / 3600
        tok_h = f"{u['tokens'] / hours / 1e6:.1f}M" if hours else "-"
        unc_h = f"{u['uncached'] / hours / 1e3:.0f}k" if hours else "-"
        per_step = f"{u['actions'] / u['steps']:.1f}" if u["steps"] else "-"
        per_act = f"{u['tokens'] / u['actions'] / 1e3:.0f}k" if u["actions"] else "-"
        per_busy = f"{u['actions'] / (u['hands'] / 60):.1f}" if u["hands"] >= 30 else "-"
        longest = costs.longest_idle(sp, u.get("off_spans", []), t, t + u["wall"]) if t else 0
        print(f"{label:<6}{tok_h:>8}{unc_h:>11}{u['steps']:>6.0f}{u['actions']:>8.0f}{per_step:>9}{per_act:>8}"
              f"{pct('hands'):>7}{pct('tools'):>7}{pct('idle'):>6}"
              f"{(f'{longest / 60:.1f} min' if t else ''):>13}{u['off'] / 60:>4.0f}m{per_busy:>13}")


if __name__ == "__main__":
    main()
