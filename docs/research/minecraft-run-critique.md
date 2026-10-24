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

## 2026-10-24 02:25 PDT, cycle 3
Evidence: grader works after restart: first_wood and crafting_table fired at 09:13:29Z (about 31 min in; both retroactive, from stats), at 4.27M total / 143k uncached tokens. Window 02:10-02:21: 35 requests, 3.7M tokens (106k/request, context 117k), only 21k uncached; 9 logged harness calls vs 7 Bash; steps median 75 s apart (agent now works through Bash + its own sk.py, calls to the bridge not in events.jsonl).
Cost: metrics blind spot (hook share 0%, reuse invisible, rules review impossible). Total-token per milestone is dominated by context re-read (100k per request); uncached is the fair measure, so report uncached per milestone.
Fix: brief bullet to route bridge calls through registered tools or run_script; analyzer shows model-side mix and skill reuse. Skill library exists (skills/INDEX.md), good sign: the continue-prompt injection will pick it up.
Trend (uncached tokens at milestone): first_wood/crafting_table 143k at t+31 min; baseline for later cycles.

## 2026-10-24 02:45 PDT, cycle 4
Evidence: milestones at 09:21Z: stone_tools, furnace, chest, crafted_10 (uncached 159k, total 7.2M). Bash results are tiny (94 calls, 27 KB) but each step re-reads 100-130k context; screenshots are 23 of the images (about 30k tokens). 33 of 94 Bash steps (35%) are 27 s wait polls, many duplicated in the same step; the agent self-capped at 28 s.
Cost: about 33 steps x 120k tokens = 4M cached-read tokens and 15 min of wall clock in 70 min. Uncached cost is small, but wall-clock matters.
Fix: generic brief bullet (long waits) and a Minecraft goal paragraph (poll for ~4 min inside one call). Takes effect at the next runner restart: this one is worth a deliberate restart (the brief is copied into runner state at first run, so the NUDGE restart will NOT refresh it unless the continue prompt carries it; see below).
Note: `state.extra["brief"]` is frozen at run start, so brief edits only reach the live run via the continue prompt. Proposed: put critic-approved advice into update_note.

## 2026-10-24 03:00 PDT, cycle 5
Evidence: after the 02:4x nudge the agent switched to long waits (Monitor/sleep 100-590 s, `sk.wait(100)`); last 15 min: 42 requests at 138k tokens, 24k uncached, context 150k and growing about 2-3k per step, so a compaction is likely within the hour (skills index + NOTES.md are ready for it). Still 0 logged hook calls; all game control goes through Bash -> sk.py -> bridge.
Fix: `costs` now shows per-milestone minutes, requests and uncached tokens, so the agent sees whether it is getting faster. UPDATE_NOTE (non-interrupting) and NUDGE (interrupting) documented as complementary.
Watch next: behaviour across the compaction; whether the 90 s hook cap bites (agent uses sk.py, so it does not).

## 2026-10-24 03:00 PDT, cycle 6 (observation)
Evidence: no new milestone for 30 min after crafted_10 (09:21Z); agent mining iron in a dark cave (live frame shows inventory open, dark screen), 1 raw iron after about 25 min. Skill functions are growing in prelude.py (wood_start, stone_start, get_ore, tunnel_to) and INDEX.md is kept, which is the intended behaviour.
Fix (Minecraft-only, goals/minecraft.md): efficiency hints (torches, branch tunnel at y=16, batch smelting). Frozen at run start, so it needs delivery via NUDGE/UPDATE_NOTE; text for main is in the message sent this cycle.

## 2026-10-24 03:20 PDT, cycle 7: idle stall
Evidence: 42 min after 09:21Z with no milestone; 22.8 min (55%) in model gaps over 60 s (background-job waits). The player lost the stone tools and chest contents (inventory back to wooden tools at 09:53; likely died near the lava the agent noted) and re-ran stone_start. A bridge job (`get_blocks(..., 20, 60)`) held the main thread 8+ min; because the bridge has a single queue, reads timed out and the agent diagnosed with screenshots, then `\killjob` and restart (about 6 min).
Cost: about 25 min of 42 wall-clock; tokens were cheap (waits are one step each) but progress was zero.
Fixes: runner IdleWatch + idle note (restart needed, default on at 8 min); brief bullet on progress reporting; goal paragraph on sliced jobs and heartbeat in the bridge.

## 2026-10-24 03:35 PDT, cycle 8
Evidence: after the idle-watch restart (10:05Z) and the nudges the agent built a `job` helper that prints progress (mine/goto positions every 15 s), mines iron (12 raw iron by 10:21Z) and is smelting. Context 201k, so each request re-reads about 200k cached tokens (11 requests per 10 min, 11k uncached): the context never compacted (window is larger than the 200k shown). No milestone since 09:21Z (iron_ingot expected next).
Derived skill metrics (analyze.py from raw stream, last 60 min): 101 Bash steps = 15 skill writes, 80 skill calls, 6 other, 0 logged hook or run_script calls. The skill library is real but invisible to events.jsonl (see requests for `skill_saved`/`skill_used`).
Trend: tokens per step rising with context (45k -> 200k); steps per milestone not yet falling; wall-clock per milestone: first_wood 31 min, stone-tier batch +8 min, iron batch pending (>60 min). Next lever: fewer, bigger steps per chore now that helpers exist, and context hygiene (stale screenshots).

## 2026-10-24 03:50 PDT, cycle 9
Evidence: iron_ingot + iron_tools at 10:26Z (+65 min after the stone batch): 90k uncached tokens but 18.9M total tokens (steps now carry a 200k+ context). Cumulative: 26.1M total, 249k uncached at 10:26Z.
Cost: wall-clock and rate-limit pressure grow with context; model requests are about 200k tokens each, 5x the start.
Fix: opt-in `--reset-context-tokens` handoff to a fresh session (see requests). Recommend a deliberate restart with `--reset-context-tokens 150000`: the notes and skills index already exist, so a handoff should be cheap and safe. The run is now at 212k.
