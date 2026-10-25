# Morning report: Minecraft overnight run `mc-overnight-01`

Run: Claude Code (claude-sonnet-5-5) in Docker, modded-but-vanilla-gameplay Minecraft 1.21.1 (NeoForge + Minescript), normal difficulty, seed `sisyphus1`, cheats off. 08:42Z to 16:00Z on 2026-10-24 (01:42 to 09:00 local, local = UTC-7), 7 h 17 min. The first launch failed authentication after 5 s and was relaunched at 08:44Z; the first model request was at 08:51Z.

Sources: `runner.jsonl`, `events.jsonl`, `grader.jsonl`, `PROGRESS.md`, the agent's `workspace/` (skills, NOTES.md), the raw Claude stream, the game's own stats, advancements and logs, the supervisor log and the critic's notes.

## 1. Summary

1. **Milestones: 9 of 23** (first wood, crafting table, stone tools, furnace, chest, 10 crafted, iron ingot, iron tools, 25 crafted). The last one came at 05:26 local; nothing new in the final 3 h 34 min. No diamonds, no shelter/bed, no crops, no animals bred.
2. **Deaths: 18** by the game's own counter (not 17): drowned 6, zombie 5, skeleton 3, spider 1, pillager 1, creeper 1, fall 1. Each death dropped the whole kit.
3. **What stopped progress:** deaths wiping tools (the iron tools were lost 7 min after they were made), no combat skill (1 kill all night, a pig), no bed so every respawn landed next to water and a pillager outpost, and a slow, stall-prone bridge for the first 2 hours.
4. **Efficiency:** 84.3M tokens in total, only 0.72M uncached (0.9%), 811 model steps (about 104k tokens each, because every step re-reads the context). The game counted 5,031 player actions: 16.8k total tokens per action, 144 uncached tokens per action, 6.2 actions per step.
5. **Harness:** 6 restarts to ship fixes without losing work, 4 automatic context handoffs (150k to 218k context down to 38k), 45 nudges (31 supervisor, 14 idle), and 29 commits on `nazeem/minecraft-host`. The agent built a 69-function skill library that carried it through every death and handoff.

## 2. Milestone timeline

Times are local. "Grader" is when the grader saw it (it reads the save files, which are written about every 5 minutes, and it only started reading the world at 02:13 because of an NBT parsing bug fixed at 02:13 PDT). "Game record" is the timestamp in the world's advancement files where one exists. Tokens are totals; uncached in brackets.

| Milestone | Grader | Game record | Wall since previous | Tokens since previous (uncached) | Cumulative tokens |
|---|---|---|---|---|---|
| First wood, crafting table | 02:13 | about 02:06 (planks) | 31 min from run start (22 from first model request) | 4.27M (143k) | 4.27M |
| Stone tools, furnace, chest, 10 distinct crafted | 02:21 | 02:17 (stone tools, furnace) | 8 min | 2.95M (16k) | 7.22M |
| Iron ingot, iron tools | 03:26 | 03:22 smelt, 03:24 iron tools | 65 min | 18.89M (90k) | 26.11M |
| 25 distinct crafted | 05:26 | not recorded | 120 min | 22.45M (193k) | 48.57M |
| (nothing) | | | 214 min to end of run | 35.72M (280k) | 84.29M |

Open at the end: shelter, crops, animals bred, diamonds, enchanting table, 50 distinct crafted (the game's stats show 48 distinct items crafted, if the grader counts the same way), Nether, blaze rod, eyes of ender, stronghold, End, dragon.

Observations: wall time and tokens per milestone rose, they did not fall (31 min, 8 min, 65 min, 120 min, then 214 min with none), because deaths erased gear and the agent rebuilt wood and stone tools about 3 times.

## 3. Deaths

All 18 are in `grader.jsonl` (`agent_died` markers) and in `latest.log`. "Life" is time since the previous death (the first is since the first model request at 01:51).

| # | Local time | Cause | Life | Tokens in that life | What it cost |
|---|---|---|---|---|---|
| 1 | 02:43 | drowned | 52 min | 14.9M | Gear from first lava/water cave (30,34,-25); stone tools, furnace, chest contents |
| 2 | 03:29 | drowned | 46 min | 11.8M | Iron pickaxe, sword, shovel, bucket, shield, shears (made 7 min earlier); dug into an aquifer at (1,38,-32) |
| 3 | 03:54 | drowned | 26 min | 3.2M | Wood/stone kit rebuilt; same flooded area |
| 4 | 03:57 | drowned | 2 min | 0.6M | Respawned next to the water pit |
| 5 | 04:26 | spider | 29 min | 4.8M | Kit; night on the surface |
| 6 | 04:40 | pillager | 14 min | 3.6M | Kit; respawn point is near an outpost at (40,69,-19) |
| 7 | 04:47 | drowned | 7 min | 2.1M | Kit; water pit (20..42, 55..64, -62..-42) |
| 8 | 05:42 | zombie | 54 min | 9.4M | Kit; open ground at night |
| 9 | 05:43 | zombie | 1 min | 0.3M | Respawn camping |
| 10 | 05:43 | zombie | 1 min | 0.3M | Respawn camping |
| 11 | 05:44 | zombie | 1 min | 0.4M | Respawn camping |
| 12 | 05:44 | zombie | 0 min | 0.5M | Five deaths in 3 min; triggered the death-loop escalation (27b447a) |
| 13 | 07:08 | creeper | 83 min | 14.6M | Kit; surface near the table after the agent loosened its hostile guard |
| 14 | 07:20 | skeleton | 13 min | 2.1M | Kit; tunnelling at night while wrongly marked as sheltered |
| 15 | 07:23 | skeleton | 3 min | 2.3M | Kit; the job kept running after the first death |
| 16 | 07:56 | fall | 33 min | 6.2M | Iron pickaxe (the only one of the later lives); `tunnel_to` dropped down a shaft to y=-18 |
| 17 | 07:59 | drowned | 3 min | 0.5M | Respawned at (28,69,-36) next to water |
| 18 | 08:26 | skeleton | 27 min | 2.9M | Kit; the last life was alive at 09:00 |

Pattern: 8 of 18 deaths came within 3 min of the previous one (respawn near water, mobs or the outpost). Cost per death: the 5 to 15 min needed to find wood, stone and coal again plus the rebuilt tools; Death 2 (iron tools) and death 16 (iron pickaxe) were the two that cost a milestone-class asset. Dropped items despawn in 5 min; the agent recovered gear at most once.

The supervisor log says "drowned x7" and "about 19 deaths"; the game's counter and the grader both say 18, with 6 drownings.

## 4. Leverage and efficiency

| Measure | Value |
|---|---|
| Total tokens | 84,286,154 |
| Uncached tokens | 722,633 (0.86%) |
| Model steps (requests with output) | 811 |
| Mean / median / max context per step | 104k / 101k / 218k |
| Tool calls | 908 (728 Bash, 59 screenshots, 24 Monitor, 29 type, 31 key, 17 click, 5 mc_exec, 1 run_script, others) |
| Actions counted by the game | 5,031 = mine 2,137, craft 965, use 1,928, kill 1 |
| Total tokens per action | 16.8k |
| Uncached tokens per action | 144 |
| Actions per model step | 6.2 (6.9 per Bash call) |
| Tokens per milestone | 9.4M |
| Active time between steps | 25,656 s; 65% of it in 160 gaps over 60 s (waits on game jobs and idle periods); median gap 10 s |
| Other game stats | 1.56 km walked, 5,290 damage taken (about 2,645 hearts), 2,603 jumps; mined 1,078 stone, 77 coal, 43 iron ore, 12 copper ore, 0 diamond |

The cache works: the cost is mostly cached re-reads, not new input. The fair measure is uncached tokens, and by that measure milestones were cheap (16k for the stone batch). Wall clock and rate-limit pressure follow total tokens.

**Context handoffs** (the runner asks the agent to update NOTES.md and skills/INDEX.md, then starts a fresh session):

| Handoff (UTC) | Context before | Context at first step after | Extra uncached cost | Handoff time |
|---|---|---|---|---|
| 10:36 | 218k | 38k | about 9k | 21 s |
| 12:01 | 153k | 38k | about 9k | 14 s |
| 13:12 | 152k | 38k | about 9k | 13 s |
| 14:39 | 154k | 39k | about 10k | 12 s |

Context regrew at about 0.7 to 0.8k per step. Mean context was 132k before the first handoff and 79 to 99k after. Estimate: the 594 steps after the first handoff used 55.7M tokens; at 218k per step they would have used about 130M, so the handoffs saved roughly 70M tokens (more than half) and the context stayed under the window. Skills and notes carried over: 93% of later Bash calls were skill calls.

**Screenshots vs hook reads.** `events.jsonl` shows 59 screenshots against 5 hook calls (8% coverage, all 5 `mc_exec` calls failed) and 1 `run_script`. That undercounts. After the first hour the agent drove the game through Bash into its own `sk.py` and the bridge on port 8777, invisible to the event log: 675 of 728 Bash calls (93%) and 74% of all tool calls were skill calls. Screenshots were 6.5% of tool calls and about 1.3k tokens each (about 77k tokens, negligible against context re-reads). The logging forwarder (b5f0e22) would make these calls visible but only applies to fresh runs.

## 5. What the agent built

`workspace/skills/`: `prelude.py` (627 lines, 69 functions, runs inside the game process via the bridge), `sk.py` (sandbox side: run, bg, status, wait, heartbeat), `job.py` (background job with progress polling) and `INDEX.md`. It rewrote the `mc_exec` bridge 5 times (v1 to v5), then replaced the registered hook with its own Bash-driven path. It made 28 writes to its skills and notes files. Call counts come from the raw stream.

| Group | Skills | Calls | Verdict |
|---|---|---|---|
| Bridge and plumbing | `sk.run/bg/status/wait/hb`, `job.job`, `look_at`, Java reflection helpers (`jget`, `jcall`, `slot_click`), `open_inv`, `close_screen` | many | Worked after the early stalls; a wedged job blocked the single queue several times |
| Movement | `goto` (69 calls; stuck detector added only after 3 nudges), `tunnel_to` (39), `pillar_up` (31), `bfs_path/walk_path`, `safe_descend/plug_behind`, `swim_out/wet/nearwater` | | `goto` repeatedly stuck for 90 s; `tunnel_to` ignored vertical drops and caused the fall death; `bfs_path` often returned None |
| Gathering | `chop` (42), `wood_start` (24), `get_blocks` (41), `get_ore` (37), `collect` (40), `mine` (best tool, 8 s cap), `dig`, `hunt` (14) | | `wood_start` and `chop` worked; `get_blocks` does not pick up all drops; `hunt` chases far animals; ore matching hit copper instead of iron once |
| Crafting and smelting | `craft`, `craft_all` (11), `place_near` (41), `open_table`, `stone_start`, `stone_craft`, `tools_tier`, `smelt` (14), `iron_kit` (5), `new_furnace`, `use_at` | | Worked: crafted 965 items, 48 distinct types |
| Safety and survival | `guard`, `W()` guards (NIGHT, HOSTILE, DEAD), `is_night`, `shelter` (20 calls), `bunker` (11), `seal_in` (9), `dawn`, `respawn` (15), heartbeat | | Added after deaths. `shelter()` breached into a cave and failed; `bunker/pillar` need dirt in the hotbar; `seal_in` and the NIGHT guard were used often; deaths continued at night anyway (skeletons at 07:20 and 07:23, 08:26) |

**Missing: a combat skill.** The only attack code is in `hunt` for passive animals. Hostile mobs trigger a flee-or-seal guard (`HOSTILE` when health is under 10 and a hostile is within 8 blocks), never a fight. The game recorded 1 kill (a pig) and 18 deaths, 10 of them to mobs. There is no armor routine either: no armor of any kind was crafted (the game's stats show 3 iron pickaxes, 1 iron sword, 1 shovel and a shield, but no helmet, chestplate, leggings or boots).

**Rules the agent wrote for itself** (NOTES.md and INDEX.md):
- Keep every bridge job under 100 s and poll status within 60 s; one stuck job blocks all calls; kill it with `\killjob`.
- Water test uses `startswith('minecraft:water')` (`waterlogged=false` gave false positives).
- Night is `day_ticks % 24000` between 12,500 and 23,300; never be on the surface at night; seal into solid rock at dusk with `seal_in`.
- Danger zones: the water pit at x 20 to 42, z -62 to -42; the flooded cave near (1,38,-32); the pillager outpost at (40,69,-19). The respawn point is near both.
- Never set `SHELTERED=True` by hand; after any death kill the bridge job and run `respawn()`.
- Never `tunnel_to` with a big vertical drop; use `safe_descend`.
- Supervisor rules in INDEX.md: kit discipline (store spare ingots and tools in the chest), a hazard check in every step, mine horizontally at y 50 to 60 with torches, bank breadth near the base.

## 6. Supervision

- **Nudges: 31 supervisor nudges plus 14 idle nudges** (the idle-watch note). About 17 of the 31 came from the overnight supervisor agent, the rest from the main session. By topic: bridge setup and hangs 5, stuck loops (`mine`, `goto`, wedged job) 5, deaths and safety 8, idling in a shelter 3, "no milestone, do X" pushes 5, pace and plans 3, a polling-waste note 1, other 1.
- **Interrupts: 52** = 27 by nudge, 14 by death (host event), 6 by runner restart, 4 by handoff, 1 by the deadline.
- **Restarts: 6 runner restarts** at 08:56Z (NUDGE file), 09:12Z (90 s tool cap, skills index), 10:05Z (idle watch), 10:36Z (context handoff), 10:57Z (death detection), 13:08Z (death note with heartbeat), plus the failed first launch. Nothing was lost in any of them.
- **Stalls found and fixed:**
  - `mc_exec` hung until its 1200 s timeout on a dead bridge (251 s and 143 s hangs, about 6.5 of the first 20 min): tool timeouts now capped at 90 s.
  - Grader read no world for 30 min (NBT parse bug): fixed, milestones then fired retroactively.
  - Waiting on background jobs: 55% of one 42-min stretch was gaps over 60 s and the agent ended turns waiting for notifications (09:47, 11:41, 15:19Z); the idle watch interrupts after 8 min with an identical screen and no event growth.
  - One job on the single bridge queue blocked reads for 8+ min; the agent now uses `job.job` with progress, and a `\killjob` rule.
  - Short 27 s polls were 35% of early Bash steps; replaced by guidance to wait in one long step.
  - Repeated loops on the same block or path (`mine 11 52 -32`, `goto`): the agent added per-call caps and a stuck detector after nudges.
  - Death loops: five zombie deaths in 3 min; the death note now escalates.

## 7. Harness improvements made overnight

All on `nazeem/minecraft-host` (author Nazeem Kamel, local PDT times).

| Commit | Time | Change |
|---|---|---|
| 8af0311, 150ea9f | 01:57 | NUDGE file: interrupts the running turn and delivers a supervisor note once |
| 75870fd | 02:04 | Skill-library guidance; skills/INDEX.md re-injected in the continue prompt; run analyzer |
| 82c03f1 | 02:12 | Cap extension tool timeouts at 90 s (host-generic) with a clear error |
| dc05ce3 | 02:13 | Grader: NBT reader read payload before name; milestones could not fire |
| 68a5a0b, dec8ec3 | 02:21, 02:31 | Brief: use registered tools not shell for the bridge; wait in one long step, not short polls |
| e4062ea | 02:32 | UPDATE_NOTE.md: non-interrupting guidance delivered with the next continue prompt |
| 61b69e2 | 02:42 | `costs`: per-milestone minutes, requests, uncached tokens |
| 303da01 | 02:53 | Minecraft goal: efficiency hints |
| 419fdc9 | 03:04 | Idle-stall watch (8 min) with continue note; progress reporting and sliced bridge jobs |
| 54c9521, a0d6565 | 03:24, 07:07 | Analyzer: skill writes and calls from Bash steps; deaths, resets, nudges |
| 6548969 | 03:35 | Context handoff to a fresh session (`--reset-context-tokens`) |
| 33e5d89 | 03:56 | Death detection: grader `agent_died` markers, runner host-event note, goal safety paragraph |
| 6d0c829, b85abda | 04:16, 04:17 | `--compact-tokens` alias; compaction vs handoff note |
| e2b895f, b5f0e22 | 04:25, 04:26 | Logging forwarder for agent ports (opt-in, enabled for Minecraft; fresh runs only) |
| b5fc187, a079067 | 04:36, 05:17 | Goal: respawn and night guidance; safe breadth |
| 27b447a | 05:47 | Death-loop escalation (3 deaths in 10 min) |
| 327815d | 06:08 | Safety loop and heartbeat guidance; death note includes last heartbeat |
| 0de5fb6 | 06:28 | Critique trend table |
| a0b2789 | 07:28 | `program_actions` in the Minecraft grader from game stats (mine, craft, use, kill) |

Earlier the same night: 7ac460b and 77b4f8e (Minecraft host, grader, goal), 48e288a (macOS focus and capture requests).

**Overlap with what John shipped on main overnight:**

| Ours | John's (main) | Status |
|---|---|---|
| Context handoff, 6548969 | Compaction past 200k, b9752e2 (`--compact-tokens`) | Both merged in our branch; they compose (compact lower, hand off higher) |
| `program_actions` from game stats, a0b2789 | Leverage metric 511980d, estimator e0a685b, leverage chart 6f219dd | Merged (4659a72, 5ca129b); same record shape |
| Skills index and agent's `skills/` library | `run_script` kept scripts, ecce031 | Merged, library text aligned in 5ca129b; the agent still prefers Bash, so kept scripts were not used |
| NUDGE, idle watch, death host events | Background tasks, wait, notices, wake for urgent notices (4a1a2a8, f81f0a6, dc0def6), "one pair of hands" prompts (f3fa2f8) | Merged into the branch by 39bc9e6 (morning merge of origin/main); untested with the Claude runner |
| Starting save control | `hostctl` starting save and map-gen settings (50eac10) | In main, merged at 2fb59d0 |

## 8. Recommendations for the next run

1. **Add a combat skill** (sword, shield, armor, fight with back to a wall, retreat at health under 8), and craft and wear iron armor before cave work. 10 of 18 deaths were mobs and the agent only flees.
2. **Kit discipline:** make a chest and a bed at the first base (3 wool plus 3 planks) so the respawn point is not next to the water pit and outpost; store spare ingots and tools; carry a pickaxe, sword, food and a stack of cobble only. Fetch dropped items within 5 min.
3. **Mine horizontally** at y 50 to 60 with torches (branch tunnel), not deep pits or big vertical `tunnel_to`; no digging toward water. The fall and 6 drownings came from vertical and aquifer digging.
4. **Make the early bridge reliable:** the first 2 hours lost time to bridge hangs and one-queue blocking. Ship the `sk.py` plus sliced-job pattern in the host notes, and use the forwarder so bridge calls show up in the metrics.
5. **Use John's background-task features** (merged in 39bc9e6: tasks, notices, wake for urgent notices, rules instead of polling) so the agent can wait on a job without ending its turn, which should replace the 14 idle nudges and the 8-minute idle watch.
6. **Turn on handoff at 150k** by default (it saved about 70M tokens) and compare it with John's compaction.
7. **Difficulty:** deaths are most of the story. Dropping to easy would cut mob deaths but changes the benchmark, and the grader flags it (`integrity.difficulty_changed`), so the result would not be comparable. If you do it, run it as a separate labelled variant.
8. **Fix the metrics:** count skill calls and screenshots from the raw stream until the forwarder is on; record exact milestone times from advancements rather than the grader's read time.

## 9. Decisions for Nazeem

1. Run the next run on the merged branch (39bc9e6, includes John's background tasks) or hold those back for a clean comparison?
2. Keep normal difficulty for the benchmark, or run an easy-difficulty variant (flagged as different)?
3. Add a bed, chest and armor step to the goal file, and a combat-skill requirement, before the next run?
4. Turn the logging forwarder and the 150k handoff on by default?
5. Start the next run from the same save (iron tools gone, 9 milestones counted) or a fresh world?
