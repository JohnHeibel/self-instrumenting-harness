# Minecraft run critique (append-only, critic agent)

## 2026-10-24 02:10 PDT, cycle 1 (run mc-overnight-01, first 20 min of play)
Evidence: 59 steps; 32 input, 17 screenshot, 4 hook (19% hook share); 4 of 4 `mc_exec` failed; model 55k tokens per request (context 37k at start of every resumed turn, first request uncached).
Time lost: `mc_exec` 1200 s urlopen timeout on a dead bridge: 251 s and 143 s hangs then exit 137 (about 6.5 min of 20). One `run_script` timed out at 40 s with 0 tool calls (a 6 s mining press blocked on the same hung bridge). Starting the bridge took 3 calls per attempt (key t, type, key enter) repeated 4 times, plus permission dialogs eating input.
Cost of context: tokens are dominated by context re-read, not images (screenshot about 1.3k tokens at 1280x761).
Fixes (host-generic, effect at next runner restart): brief "Skill library" section with short-timeout rule; continue prompt re-injects skills/INDEX.md (`skills_note`). Added `tools/critic/analyze.py` for window stats. Requests drafted in requests-from-nazeem.md.
Candidate for a deliberate restart: low value alone; worth bundling with the next fix. Mc-specific: tell the agent (host notes) that a bridge script started from chat dies with the world/permission dialogs; not yet tuned.
