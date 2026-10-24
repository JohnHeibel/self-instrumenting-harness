"""The `costs` tool: the agent's own spending, read from the run's logs, so it can judge which tools
to improve by numbers instead of by feel.

events.jsonl (tool calls: duration, result size, errors) and, when present, runner.jsonl (usage records:
cumulative model tokens) in the run directory.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _records(path: Path, since: datetime) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            r = json.loads(line)
            if datetime.fromisoformat(r["ts"].replace("Z", "+00:00")) >= since:
                out.append(r)
        except (ValueError, KeyError):
            continue
    return out


def _kb(n: float) -> str:
    return f"{n / 1024:.1f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"


def report(run_dir: Path, minutes: float = 60, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(minutes=minutes)
    calls = [r for r in _records(run_dir / "events.jsonl", since) if r.get("type") == "tool_call"]
    calls = [c for c in calls if c.get("tool") != "costs"]
    steps = [c for c in calls if c.get("via") != "script"]   # calls inside run_script cost no step
    inner = len(calls) - len(steps)
    lines = [f"Last {minutes:g} min: {len(steps)} tool calls, {sum(1 for c in steps if not c.get('ok'))} failed"
             + (f"; {inner} more calls ran inside scripts." if inner else ".")]

    usage = [r for r in _records(run_dir / "runner.jsonl", since) if r.get("type") == "usage"]
    if len(usage) >= 2:
        total = usage[-1].get("total_tokens", 0) - usage[0].get("total_tokens", 0)
        uncached = usage[-1].get("uncached_tokens", 0) - usage[0].get("uncached_tokens", 0)
        n = len(usage) - 1   # one usage record per model request: your steps, shell commands included
        lines.append(f"Model: {n} steps, {total:,} tokens ({uncached:,} not cached), about {total // n:,} per step. "
                     f"Context now: {usage[-1].get('input_tokens', 0):,} tokens.")

    if calls:
        by: dict[str, dict] = {}
        for c in calls:
            name = c.get("tool", "?")
            b = by.setdefault(name, {"n": 0, "fail": 0, "bytes": 0, "ms": 0, "images": 0})
            b["n"] += 1
            b["fail"] += 0 if c.get("ok") else 1
            b["ms"] += c.get("duration_ms") or 0
            if c.get("result_kind") == "image":
                b["images"] += 1
            else:
                b["bytes"] += c.get("result_bytes") or 0
        lines.append("By tool (most calls first): name: calls, failures, average time, average result size")
        for name, b in sorted(by.items(), key=lambda kv: -kv[1]["n"])[:12]:
            size = f"{b['images']} images" if b["images"] else f"avg {_kb(b['bytes'] / b['n'])}"
            lines.append(f"  {name}: {b['n']}, {b['fail']} failed, {b['ms'] / b['n'] / 1000:.1f} s, {size}")
        texts = sorted((c for c in calls if c.get("result_kind") != "image"),
                       key=lambda c: -(c.get("result_bytes") or 0))[:3]
        if texts:
            lines.append("Largest text results: " + "; ".join(
                f"{c.get('tool')} {json.dumps(c.get('args', {}))[:80]} -> {_kb(c.get('result_bytes') or 0)}"
                for c in texts))
        stamps = sorted(datetime.fromisoformat(c["ts"].replace("Z", "+00:00")) for c in steps)
        gaps = sorted((b - a).total_seconds() for a, b in zip(stamps, stamps[1:]))
        if gaps:
            lines.append(f"Time between calls (your thinking): median {gaps[len(gaps) // 2]:.0f} s, "
                         f"longest {gaps[-1]:.0f} s.")
    lines += _milestones(run_dir)
    return "\n".join(lines)


def _milestones(run_dir: Path) -> list[str]:
    """What each milestone cost, from the grader's records: minutes since the previous one, model
    requests and uncached tokens spent in between. Judge the trend: it should fall."""
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    ms = [r for r in _records(run_dir / "grader.jsonl", epoch) if r.get("type") == "milestone"]
    usage = [r for r in _records(run_dir / "runner.jsonl", epoch) if r.get("type") == "usage"]
    if not ms or not usage:
        return []
    t = lambda r: datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))
    # milestones the grader found in one pass (same second) share a cost: show them as a group
    groups: list[list[dict]] = []
    for m in sorted(ms, key=t):
        if groups and (t(m) - t(groups[-1][-1])).total_seconds() < 2:
            groups[-1].append(m)
        else:
            groups.append([m])
    out = ["Milestones (cost since the previous one; the trend should fall):"]
    prev_t, prev_u = t(usage[0]), usage[0]
    for g in groups[-8:]:
        at = t(g[0])
        u = [r for r in usage if t(r) <= at]
        u = u[-1] if u else usage[0]
        out.append(f"  {', '.join(m.get('id', '?') for m in g)}: {(at - prev_t).total_seconds() / 60:.0f} min, "
                   f"{max(0, len([r for r in usage if prev_t < t(r) <= at]))} requests, "
                   f"{max(0, u.get('uncached_tokens', 0) - prev_u.get('uncached_tokens', 0)):,} uncached tokens")
        prev_t, prev_u = at, u
    return out
