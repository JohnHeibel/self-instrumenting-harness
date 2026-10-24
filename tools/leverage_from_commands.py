"""Estimate program actions for a run that has no program_actions records, from its host_command log.

Older runs (before a host counted its own user actions) still logged every command the agent's
extension received. When the agent batches actions into a command whose parameters hold a list
(e.g. {"actions": [{"op": "walk", ...}, ...]}), each list item is one action it asked for. This
writes one `program_actions` record per such command, in the same shape the graders write
(docs/interfaces/event-log.md), with `source: "estimated_from_host_commands"` so readers can label
it as an estimate. It counts actions asked for, not done, so it overcounts failures and misses
actions sent any other way.

The output goes to a subdirectory of the run by default, so it isn't merged into the run's own
event log (and the costs tool never mixes it with real counts). Nothing here knows any program:
the list and kind keys are options.

    python tools/leverage_from_commands.py runs/factorio-trial-03 [--list-key actions] [--kind-key op]
        [--out runs/factorio-trial-03/estimates/program_actions.jsonl]

Prints a leverage table (actions per model step, tokens per action) in time bins.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
from datetime import datetime, timezone
from pathlib import Path

SOURCE = "estimated_from_host_commands"


def records(run: Path):
    """Every record in the run's top-level event log, in log order."""
    out = []
    for f in sorted(run.glob("*.jsonl")):
        for line in f.open(encoding="utf-8"):
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    out.sort(key=lambda r: (r.get("ts", ""), r.get("writer", ""), r.get("seq", 0)))
    return out


def actions_in(parameters: str, list_key: str, kind_key: str) -> list[str] | None:
    """The kinds of the actions batched in one command's parameters, or None if it holds no batch.
    Parameters may be cut short in the log; then the kinds are counted from the text."""
    if f'"{list_key}"' not in parameters:
        return None
    try:
        items = json.loads(parameters).get(list_key)
        if isinstance(items, list):
            return [str(x.get(kind_key, "?")) if isinstance(x, dict) else "?" for x in items]
        return None
    except (ValueError, AttributeError):
        return re.findall(rf'"{re.escape(kind_key)}"\s*:\s*"([^"]*)"', parameters) or None


def estimate(recs: list[dict], list_key: str, kind_key: str) -> list[dict]:
    totals: collections.Counter = collections.Counter()
    out = []
    for r in recs:
        if r.get("type") != "host_command" or not r.get("custom"):
            continue
        kinds = actions_in(r.get("parameters") or "", list_key, kind_key)
        if not kinds:
            continue
        delta = collections.Counter(kinds)
        totals.update(delta)
        out.append({"v": 1, "ts": r["ts"], "run_id": r.get("run_id"), "writer": "estimator",
                    "seq": len(out) + 1, "type": "program_actions", "count": len(kinds),
                    "kinds": dict(delta), "totals": dict(totals), "tick": r.get("tick"), "source": SOURCE})
    return out


def table(recs: list[dict], est: list[dict], bin_min: int) -> str:
    def b(ts: str) -> str:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
        return t.replace(minute=t.minute - t.minute % bin_min, second=0, microsecond=0).strftime("%m-%d %H:%M")
    steps, toks, acts = collections.Counter(), collections.Counter(), collections.Counter()
    prev = None
    for r in recs:
        if r.get("type") == "usage":
            steps[b(r["ts"])] += 1
            if prev is not None:
                toks[b(r["ts"])] += max(0, r.get("total_tokens", 0) - prev)
            prev = r.get("total_tokens", 0)
    for r in est:
        acts[b(r["ts"])] += r["count"]
    def row(label: str, s: int, t: int, a: int) -> str:
        per = f"{a / s:.1f}" if s else "-"
        tpa = f"{t / a / 1e3:.1f}k" if a and t else "-"
        return f"{label:11} {s:5} {t / 1e6:7.2f}M {a:7} {per:>8} {tpa:>8}"
    lines = [f"{'bin (UTC)':11} {'steps':>5} {'tokens':>8} {'actions':>7} {'per step':>8} {'tok/act':>8}"]
    lines += [row(k, steps[k], toks[k], acts[k]) for k in sorted(set(steps) | set(acts))]
    lines.append(row("total", sum(steps.values()), sum(toks.values()), sum(acts.values())))
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--list-key", default="actions", help="parameter key holding a batch of actions")
    ap.add_argument("--kind-key", default="op", help="key in each batched action naming its kind")
    ap.add_argument("--out", type=Path, help="default: RUN_DIR/estimates/program_actions.jsonl")
    ap.add_argument("--bin-minutes", type=int, default=30)
    a = ap.parse_args()
    recs = records(a.run_dir)
    est = estimate(recs, a.list_key, a.kind_key)
    out = a.out or a.run_dir / "estimates" / "program_actions.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for r in est:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
    print(table(recs, est, a.bin_minutes))
    print(f"wrote {len(est)} program_actions records to {out}")


if __name__ == "__main__":
    main()
