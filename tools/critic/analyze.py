#!/usr/bin/env python3
"""Window analysis of a run: tool mix by channel, coverage, failures, repeated sequences, tokens per
step and per milestone, idle gaps. Host-generic. Usage: analyze.py RUN_DIR [--minutes N] [--since ISO]"""
import argparse, collections, json, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

def load(p):
    out = []
    if p.exists():
        for l in p.read_text(errors="replace").splitlines():
            try: out.append(json.loads(l))
            except ValueError: pass
    return out

def T(r): return datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("run"); ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--since"); a = ap.parse_args()
    d = Path(a.run).expanduser()
    ev, rn, gr = (load(d / f) for f in ("events.jsonl", "runner.jsonl", "grader.jsonl"))
    now = datetime.now(timezone.utc)
    since = datetime.fromisoformat(a.since.replace("Z", "+00:00")) if a.since else now - timedelta(minutes=a.minutes)
    calls = [e for e in ev if e.get("type") == "tool_call" and T(e) >= since]
    steps = [c for c in calls if c.get("via") != "script"]
    print(f"window since {since:%H:%M:%S}Z: {len(calls)} calls ({len(steps)} agent steps, {len(calls)-len(steps)} inside scripts)")
    ch = collections.Counter(c.get("channel") for c in steps)
    print("channels (steps):", dict(ch))
    scr, hook = ch.get("screenshot", 0), ch.get("hook", 0)
    if scr + hook: print(f"hook share of observations: {hook/(scr+hook):.0%} ({hook} hook vs {scr} screenshot)")
    by = collections.defaultdict(lambda: [0, 0, 0, 0])
    for c in calls:
        b = by[c["tool"]]; b[0] += 1; b[1] += 0 if c.get("ok") else 1; b[2] += c.get("duration_ms") or 0; b[3] += c.get("result_bytes") or 0
    print("tool            n fail  sec  KB")
    for k, b in sorted(by.items(), key=lambda kv: -kv[1][0])[:20]:
        print(f"{k:22s}{b[0]:4d}{b[1]:4d}{b[2]/1000:6.0f}{b[3]/1024:7.0f}")
    errs = collections.Counter((c["tool"], (c.get("error") or "")[-120:].replace("\n", " ")) for c in calls if not c.get("ok"))
    if errs:
        print("top errors:")
        for (t, e), n in errs.most_common(8): print(f"  {n}x {t}: {e}")
    # repeated trigrams of tool names among steps
    names = [c["tool"] for c in steps]
    tri = collections.Counter(tuple(names[i:i+3]) for i in range(len(names)-2))
    print("repeated 3-sequences:", [(" > ".join(k), n) for k, n in tri.most_common(5) if n >= 3])
    # usage
    us = [r for r in rn if r.get("type") == "usage" and T(r) >= since]
    if len(us) > 1:
        tot = us[-1]["total_tokens"] - us[0]["total_tokens"]; unc = us[-1]["uncached_tokens"] - us[0]["uncached_tokens"]
        print(f"model: {len(us)-1} requests, {tot:,} tokens, {unc:,} uncached, {tot//(len(us)-1):,}/request, context now {us[-1].get('input_tokens',0)+us[-1].get('cached_input_tokens',0):,}")
    acts = collections.Counter(r.get("text") for r in rn if r.get("type") == "agent_state" and r.get("state") == "acting" and T(r) >= since)
    print("model-side tool mix (includes Bash/Read/Edit, which events.jsonl cannot see):", dict(acts.most_common(10)))
    tc = [r for r in rn if r.get("type") == "turn_completed" and T(r) >= since]
    print(f"turns completed: {len(tc)}", collections.Counter(r.get("status") for r in tc))
    for r in rn:
        if r.get("type") == "marker" and T(r) >= since and r.get("kind") in ("compaction", "mcp_problem", "host_start"):
            print("  marker", r["ts"][11:19], r.get("kind"), r.get("label"))
    # milestones
    allus = [r for r in rn if r.get("type") == "usage"]
    t0 = T(rn[0]) if rn else None
    deaths = [g for g in gr if g.get("type") == "marker" and g.get("kind") == "agent_died"]
    resets = sum(1 for r in rn if r.get("type") == "marker" and r.get("kind") == "context_reset")
    nudges = sum(1 for r in rn if r.get("type") == "marker" and r.get("kind") in ("supervisor_nudge", "idle_nudge"))
    print(f"deaths {len(deaths)} {dict(collections.Counter(g.get('cause') for g in deaths))}; context resets {resets}; nudges {nudges}")
    print("milestones:")
    for g in gr:
        if g.get("type") in ("milestone", "milestone_reached", "milestone_achieved"):
            t = T(g); u = [r for r in allus if T(r) <= t]
            print(f"  {g['ts'][11:19]} {g.get('id') or g.get('milestone')}  tokens@={u[-1]['total_tokens'] if u else 0:,} uncached@={u[-1]['uncached_tokens'] if u else 0:,}")
    # idle gaps between consecutive calls
    ts = sorted(T(c) for c in steps)
    gaps = [(ts[i+1]-ts[i]).total_seconds() for i in range(len(ts)-1)]
    if gaps: print(f"gap between steps: median {sorted(gaps)[len(gaps)//2]:.1f}s, max {max(gaps):.0f}s, >60s: {sum(g>60 for g in gaps)}")
    # skills: saved scripts reused via run_script(file=...) and hooks called directly (derived; no skill_* records yet)
    used = collections.Counter((c.get("args") or {}).get("file") for c in steps if c.get("tool") == "run_script" and (c.get("args") or {}).get("file"))
    inline = sum(1 for c in steps if c.get("tool") == "run_script" and not (c.get("args") or {}).get("file"))
    hooks = collections.Counter(c["tool"] for c in steps if c.get("channel") == "hook")
    print(f"skills: run_script inline {inline}, from file {sum(used.values())} {dict(used.most_common(5))}; hook calls {dict(hooks.most_common(6))}")
    # Bash steps from the raw stream: skills saved (writes under skills/) and reused (calls into it)
    raw = d / "raw" / "claude-stream.jsonl"
    if raw.exists():
        import re
        n = saved = used = 0
        for l in raw.read_text(errors="replace").splitlines():
            try: e = json.loads(l)
            except ValueError: continue
            if e.get("type") != "assistant" or not e.get("timestamp") or datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")) < since: continue
            for c in (e.get("message", {}).get("content") or []):
                if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") == "Bash":
                    cmd = c["input"].get("command", ""); n += 1
                    if re.search(r"(>>?|sed -i|tee)\s*\S*(skills/|prelude|INDEX)", cmd) or re.search(r"open\(.{0,30}(prelude|skills)", cmd): saved += 1
                    elif re.search(r"import (sk|job)\b|skills/", cmd): used += 1
        print(f"bash steps {n}: skill writes {saved}, skill calls {used}, other {n-saved-used}")
    hl = [e for e in ev if e.get("type") == "hook_loaded" and T(e) >= since]
    print(f"hooks loaded in window: {len(hl)}", [(h.get('hook'), h.get('version')) for h in hl][:15])
main()
