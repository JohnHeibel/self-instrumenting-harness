# Minecraft run critique (append-only, critic agent)

## 2026-10-24 02:10 PDT, cycle 1 (run mc-overnight-01, first 20 min of play)
Evidence: 59 steps; 32 input, 17 screenshot, 4 hook (19% hook share); 4 of 4 `mc_exec` failed; model 55k tokens per request (context 37k at start of every resumed turn, first request uncached).
Time lost: `mc_exec` 1200 s urlopen timeout on a dead bridge: 251 s and 143 s hangs then exit 137 (about 6.5 min of 20). One `run_script` timed out at 40 s with 0 tool calls (a 6 s mining press blocked on the same hung bridge). Starting the bridge took 3 calls per attempt (key t, type, key enter) repeated 4 times, plus permission dialogs eating input.
Cost of context: tokens are dominated by context re-read, not images (screenshot about 1.3k tokens at 1280x761).
Fixes (host-generic, effect at next runner restart): brief "Skill library" section with short-timeout rule; continue prompt re-injects skills/INDEX.md (`skills_note`). Added `tools/critic/analyze.py` for window stats. Requests drafted in requests-from-nazeem.md.
Candidate for a deliberate restart: low value alone; worth bundling with the next fix. Mc-specific: tell the agent (host notes) that a bridge script started from chat dies with the world/permission dialogs; not yet tuned.

## 2026-10-24 02:30 PDT, cycle 2
Evidence: mc_exec 3/3 failed, 655 s of tool time in the last 20 min (hook declared timeout_s 1200, bridge down); run_script with 40 s timeout blocked 0 calls. Model: 78k tokens per request at 96k context, 40k uncached in 35 requests (cache works). Agent has since built skills/sk.py + prelude.py (host functions) on its own and documents timeouts of 30 s in NOTES.
Fix: toolserver caps declared tool timeouts (default 90 s, config/flag) with a clear error; test added. Needs tool server restart (the runner starts it, so a runner restart picks it up).
Grader: still "no readable world" at 08:56Z; milestones cannot fire until the save path exists (check graders/minecraft path and the world folder name next cycle).

## 2026-10-24 02:15 PDT, cycle 2b: grader never read the world (systematic)
Evidence: grader.jsonl has only `grader_status ok=false "no readable world"` since 08:43Z although the save exists; `nbt.load(level.dat)` raised "bad NBT tag 116". Cause: `out[string()] = payload(et)` evaluates the value before the key, so every compound read the payload before the name. No milestone could ever fire (milestones are the key metric).
Fix: read the name first (graders/minecraft/nbt.py) plus a test with a compound; 7 grader tests pass. The grader is a separate process: restart it (not the runner) to pick the fix up.
Also: the failure detail hid the parse error; next improvement is to include the exception text in the status.
