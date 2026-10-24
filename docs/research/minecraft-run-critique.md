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

## 2026-10-24 04:10 PDT, cycle 10: deaths
Evidence: latest.log shows "threstral drowned" at 02:43, 03:29 and 03:54 local. Deaths were invisible to grader.jsonl, runner and supervisor.
Fix: grader `agent_died` markers (needs a grader restart); runner interrupts and tells the agent (needs a runner restart); goal file "Staying alive" paragraph. Also seen: context fell to 62k at about 03:50 local, so a handoff or a fresh session happened (see next cycle for the handoff markers).

## 2026-10-24 04:40 PDT, cycle 13
Evidence: 6 deaths logged (5 drownings, 1 spider) in about 3 h; no milestone since iron_tools at 10:26Z (about 1 h 15 min); 46 Bash steps in the last 30 min, 87% skill calls, so the agent is working, but progress is repeatedly erased by deaths. Context after the handoff grew 62k -> 118k in 45 min (about 1.2k per step), so the next handoff is due near 150k, in about 25 min.
Fix (Minecraft-only): goal paragraph on respawn point (bed), night shelter, armor and food. Bridge forwarder committed (fresh runs only). Cumulative uncached tokens about 320k at 11:35Z for 8 milestones; wall-clock for 8 milestones: 3 h.

## 2026-10-24 05:25 PDT, cycle 14
Evidence: 7 `agent_died` markers (5 drownings, spider, pillager) plus about 2 before the grader restart (NOTES says about 9 deaths); no milestone since iron_tools at 10:26Z (2 h); the second handoff happened (context back to 62k); the agent rebuilt from zero, waited a night in a sealed shaft, and keeps a danger-zone list in NOTES. Cumulative 46.6M total tokens, 421k uncached, 8 milestones in 3 h 45 min.
Fix (Minecraft-only): goal paragraph "Safe breadth" (bank crafted_25/50 near the base, where dying cannot erase it) with a list of 25+ craftable items; NUDGE text sent to main.

## 2026-10-24 06:30 PDT, cycle 17 (trend table, run clock from first model request 08:51Z)
| milestone | min | total tokens | uncached |
|---|---|---|---|
| first_wood, crafting_table | 31 | 4.3M | 143k |
| stone_tools, furnace, chest, crafted_10 | 39 | 7.2M | 159k |
| iron_ingot, iron_tools | 104 | 26M | 249k |
| crafted_25 | 224 | 48M | 442k |
Wall-clock and tokens per milestone rose, not fell: 12 logged deaths (5+ drownings, spider, pillager, zombie x3 respawn-camping) erased gear several times; the agent rebuilt wood and stone tools about 3 times. Three automatic handoffs (218k, ~150k, ~150k -> 62k) worked. No death since 12:45Z (about 1 h 45 min); heartbeat file adopted by the agent after the nudge. The first milestones were stamped late (the grader only started reading the world at 09:13Z; it was broken until then), so early minutes are overstated.
Next: watch whether the safety loop stops deaths and diamonds follow; the main lever left is fewer deaths, then fewer steps per chore.

## 2026-10-24 final summary (run ended 16:00Z; 56 turns, 84.3M tokens, 722k uncached, 25.8k active s)
Milestones (9 of 24; run clock from 08:51Z): first_wood, crafting_table 31 min (4.3M tokens / 143k uncached); stone_tools, furnace, chest, crafted_10 39 min (7.2M / 159k); iron_ingot, iron_tools 104 min (26M / 249k); crafted_25 224 min (48.6M / 442k). Nothing after 12:26Z: 3.5 h without a milestone. Per-milestone cost did not fall; it rose because of deaths.
Top findings: (1) the grader was broken at the start (NBT key/value evaluation order): fixed, milestones then fired; (2) a 1200 s hook timeout on a dead bridge cost about 11 min early; (3) 35% of early steps were 27 s polls and 55% of one 42 min stretch was waiting on background jobs behind a single-queue bridge; (4) 18 logged deaths (drowned 6, zombie 5, skeleton 3, creeper, spider, pillager, fall), at least 10 by mobs; each erased gear and progress; the agent has no real combat skill (no weapon use loop, no armor, no shield); (5) context grew to 218k (200k+ per request); four automatic handoffs brought it back to 52-62k in about 21 s each; (6) the agent bypassed the tool server (728 Bash steps; 5 logged hook calls, 0 run_script), so hook coverage and reuse were invisible; derived: 78 skill writes, 598 skill calls, 52 other.
Fixes shipped (SHAs on nazeem/minecraft-host): 75870fd skills index in continue prompt + brief skill library; 82c03f1 tool timeout cap; dc05ce3 NBT fix; 68a5a0b brief: no shell around the bridge; dec8ec3 + e4062ea long-wait guidance and UPDATE_NOTE.md; 61b69e2 per-milestone costs; 419fdc9 idle-stall watch; 6548969 + b85abda context handoff; 33e5d89 death markers + host-event note; 27b447a death-loop escalation; 327815d safety loop + heartbeat; b5f0e22 bridge forwarder; a0b2789 program_actions; goal-file hints (303da01, b5fc187, a079067).
Recommendations for the next run: (a) build and test a combat skill before the first night (weapon, armor, shield, back-to-wall, flee); (b) require a bed and a stocked chest before any cave trip; (c) enable `--reset-context-tokens 150000` and `forward_offset` from the start; (d) put the death-safety and heartbeat rules in the brief at launch rather than via nudges; (e) start the grader first and check `grader_status` is ok before the agent begins; (f) bank crafting breadth early; it is the one milestone family deaths cannot erase.
