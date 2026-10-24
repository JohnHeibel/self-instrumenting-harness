"""The `costs` tool: the agent's own spending, read from the run's logs, so it can judge which tools
to improve by numbers instead of by feel.

events.jsonl (tool calls: duration, result size, errors) and, when present, runner.jsonl (usage records:
cumulative model tokens) in the run directory, and `program_actions` records from any log there
(written by host-side code from the program's own counts): actions done in the program per step,
the number that shows how much each step gets done.

It also splits wall-clock time (time_use): how much of it the agent's hands were busy acting on the
program, how much its tools were running with its hands free (reading, waiting), and how much nothing
was running at all, which is the model thinking between steps (or a shell command). Idle hands while the
model thinks is time the program could have been worked on.
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


def program_actions(run_dir: Path, since: datetime) -> tuple[int, dict[str, int]] | None:
    """Actions done in the program since `since`, from `program_actions` records in any of the run's
    logs, with counts by kind. None if no log in the run has such records (the host doesn't count)."""
    found, count, kinds = False, 0, {}
    for path in sorted(run_dir.glob("*.jsonl")):
        if path.name == "events.jsonl":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if '"program_actions"' not in text:
            continue
        for line in text.splitlines():
            if '"program_actions"' not in line:
                continue
            try:
                r = json.loads(line)
                if r.get("type") != "program_actions":
                    continue
                found = True
                if datetime.fromisoformat(r["ts"].replace("Z", "+00:00")) < since:
                    continue
            except (ValueError, KeyError):
                continue
            count += int(r.get("count") or 0)
            for k, v in (r.get("kinds") or {}).items():
                kinds[k] = kinds.get(k, 0) + int(v)
    return (count, kinds) if found else None


CACHED_WEIGHT = 0.1   # cached input costs about a tenth of fresh input; cache-weighted tokens count it so


def weighted(r: dict) -> float:
    """Cache-weighted tokens of one model request (a usage record): fresh input and output in full, cached
    input at CACHED_WEIGHT. Context re-read from cache each step barely counts, so this tracks what a step
    really costs, not how long the run has been going."""
    inp, cached = int(r.get("input_tokens") or 0), int(r.get("cached_input_tokens") or 0)
    return inp - cached + CACHED_WEIGHT * cached + int(r.get("output_tokens") or 0)


def milestones(run_dir: Path) -> list[tuple[float, str]]:
    """(time, id) of each milestone the run's grader recorded, first time each, in order."""
    seen: dict[str, float] = {}
    for path in sorted(run_dir.glob("*.jsonl")):
        if path.name == "events.jsonl":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if '"milestone"' not in text:
            continue
        for r in _all(path):
            if r.get("type") == "milestone" and r.get("id") and r["id"] not in seen:
                seen[r["id"]] = _t(r)
    return sorted((t, m) for m, t in seen.items())


def milestone_costs(run_dir: Path, usage: list[dict]) -> list[tuple[str, float, float, float]]:
    """For each milestone: (id, seconds since the run's first usage record, cache-weighted tokens spent to
    reach it, cache-weighted tokens since the one before)."""
    usage = [r for r in usage if r.get("type") == "usage"]
    if not usage:
        return []
    start, out, prev = _t(usage[0]), [], 0.0
    for t, m in milestones(run_dir):
        spent = sum(weighted(r) for r in usage if _t(r) <= t)
        out.append((m, t - start, spent, spent - prev))
        prev = spent
    return out


AGENT_STATES = {"thinking", "acting", "compacting", "waiting"}   # runner states while the agent runs


def _t(r: dict) -> float:
    return datetime.fromisoformat(r["ts"].replace("Z", "+00:00")).timestamp()


def _all(path: Path) -> list[dict]:
    return _records(path, datetime.min.replace(tzinfo=timezone.utc))


def _covered(spans: list[tuple[float, float]], a: float, b: float) -> float:
    """Seconds of [a, b] covered by the union of spans."""
    total, end = 0.0, a
    for s, e in sorted((max(s, a), min(e, b)) for s, e in spans if e > a and s < b):
        if e > end:
            total += e - max(s, end)
            end = e
    return total


def _not_running(runner: list[dict], a: float, b: float) -> list[tuple[float, float]]:
    """Spans of [a, b] where the runner said the agent wasn't running (host down, paused, backing off...)."""
    states = [(_t(r), r.get("state")) for r in runner if r.get("type") == "agent_state"]
    out = [(a, states[0][0])] if states and states[0][0] > a else []   # before the run began
    for (t0, st), nxt in zip(states, states[1:] + [(b, None)]):
        if st not in AGENT_STATES:
            out.append((t0, nxt[0]))
    return out


def spans(run_dir: Path, now: float) -> dict[str, list[tuple[float, float]]]:
    """From events.jsonl: `hands` spans (something acting on the program: a background task holding the
    hands, a foreground script that held them, an acting tool called directly) and `tools` spans (any
    tool call of the agent's or background task running, acting or not)."""
    hands, tools, open_tasks = [], [], {}
    for r in _all(run_dir / "events.jsonl"):
        typ = r.get("type")
        if typ == "task_started":
            open_tasks[r.get("task")] = (_t(r), bool(r.get("hands")))
        elif typ == "task_ended" and r.get("task") in open_tasks:
            start, held = open_tasks.pop(r.get("task"))
            tools.append((start, _t(r)))
            if held:
                hands.append((start, _t(r)))
        elif typ == "tool_call" and r.get("tool") != "costs":
            via = r.get("via") or ""
            if via == "script" or via.startswith("task:"):
                continue                              # inside a script or task: already covered by it
            end = _t(r)
            span = (end - (r.get("duration_ms") or 0) / 1000, end)
            tools.append(span)
            if r.get("channel") == "input" or (r.get("channel") == "hook" and (r.get("hook") or {}).get("acts", True)):
                hands.append(span)
            elif r.get("tool") == "run_script" and r.get("held_hands", (r.get("inner_calls") or 0) > 0):
                hands.append(span)
    for start, held in open_tasks.values():           # still running
        tools.append((start, now))
        if held:
            hands.append((start, now))
    return {"hands": hands, "tools": tools}


def time_use(run_dir: Path, a: float, b: float, sp: dict | None = None, runner: list[dict] | None = None) -> dict:
    """Seconds of [a, b] by what was going on: hands (acting on the program), tools (running, hands free),
    idle (nothing running while the agent ran: the model thinking), off (the agent wasn't running)."""
    sp = sp or spans(run_dir, b)
    runner = _all(run_dir / "runner.jsonl") if runner is None else runner
    off_spans = _not_running(runner, a, b)
    off = _covered(off_spans, a, b)
    # Only while the agent ran: a task still going while the run is off doesn't count.
    hands = _covered(sp["hands"] + off_spans, a, b) - off
    busy = _covered(sp["hands"] + sp["tools"] + off_spans, a, b) - off
    idle = max(0.0, (b - a) - _covered(sp["hands"] + sp["tools"] + off_spans, a, b))
    return {"wall": b - a, "hands": hands, "tools": busy - hands, "idle": idle, "off": off,
            "off_spans": off_spans}


def longest_idle(sp: dict, off_spans: list, a: float, b: float) -> float:
    """The longest stretch of [a, b] with nothing running and the agent running."""
    longest, end = 0.0, a
    for s, e in sorted((max(s, a), min(e, b)) for s, e in sp["hands"] + sp["tools"] + off_spans if e > a and s < b):
        longest = max(longest, s - end)
        end = max(end, e)
    return max(longest, b - end)


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
    n = total = 0
    w = 0.0
    if len(usage) >= 2:
        total = usage[-1].get("total_tokens", 0) - usage[0].get("total_tokens", 0)
        uncached = usage[-1].get("uncached_tokens", 0) - usage[0].get("uncached_tokens", 0)
        n = len(usage) - 1   # one usage record per model request: your steps, shell commands included
        w = sum(weighted(r) for r in usage[1:])
        lines.append(f"Model: {n} steps, {total:,} tokens ({uncached:,} not cached), about {total // n:,} per step; "
                     f"cache-weighted (cached input at {CACHED_WEIGHT:g}): {w:,.0f}, about {w / n:,.0f} per step. "
                     f"Context now: {usage[-1].get('input_tokens', 0):,} tokens.")

    actions = program_actions(run_dir, since)
    if actions is not None:
        count, kinds = actions
        line = f"Done in the program: {count:,} actions"
        if kinds:
            line += " (" + ", ".join(f"{k} {v:,}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])) + ")"
        if n:
            line += f", {count / n:.1f} per step" + (
                f", about {w / count:,.0f} cache-weighted tokens per action ({total // count:,} raw)" if count else "")
        lines.append(line + ".")

    reached = milestone_costs(run_dir, _all(run_dir / "runner.jsonl"))
    if reached:
        lines.append("Milestones (whole run), cache-weighted tokens to reach each, and since the one before: " +
                     "; ".join(f"{m} at {s / 60:.0f} min: {spent:,.0f} (+{step:,.0f})" for m, s, spent, step in reached)
                     + ".")

    a, b = since.timestamp(), now.timestamp()
    sp = spans(run_dir, b)
    use = time_use(run_dir, a, b, sp)
    active = use["wall"] - use["off"]
    if active > 0 and (sp["hands"] or sp["tools"]):
        pct = lambda k: f"{use[k] / active:.0%}"
        lines.append(f"Time (while you ran): hands busy {pct('hands')} (acting on the program), tools running "
                     f"with hands free {pct('tools')}, nothing running {pct('idle')} (you thinking between "
                     f"steps); longest stretch with nothing running {longest_idle(sp, use['off_spans'], a, b) / 60:.1f} min.")

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
    return "\n".join(lines)
