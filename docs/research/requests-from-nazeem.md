# Requests from Nazeem for John (append-only)

Nazeem's branch (`nazeem/minecraft-host`) adds a Claude Code agent path next to the Codex one. Nothing in
`agent/` or `runner/src/` owned by john-pc was edited; everything new sits beside it. Items below are what
we'd like John to consider so the two paths converge. Append new entries at the bottom, dated.

## 2026-10-24

1. **New files outside our area (additive only).**
   - `agent/claude/` (Dockerfile, `claude.json`, `claude-settings.json`): Claude Code agent image,
     `harness-agent:claude-<version>`. Mirrors `agent/Dockerfile` and `agent/codex-config.toml`.
   - `runner/claude_runner.py`: a standalone runner for Claude Code. It imports `runner.agent`,
     `runner.hostcheck`, `runner.state` and `runner.toolserver_proc` unchanged and does not touch `runner.run`.
     Same run directory layout, same event log v1 records, STOP/PAUSE files and budget flags.
   Request: if you change the names or signatures of those four helper modules, tell us (or keep
   `agent.container_name`, `hostcheck.from_config`/`resolve`, `RunState`/`RunLock`, `ToolServer` stable).
2. **Runner generalization (optional).** `runner/src/runner/run.py` is tied to Codex's app-server.
   If you want one runner for both agents, the seam is a small "agent driver" interface: start session,
   send turn, stream events, interrupt, usage. `claude_runner.py` is a working second implementation of
   those pieces (one `claude -p [--resume <session>]` process per turn, usage from each assistant message).
3. **Event log `run_started.agent`** already lists `claude-code`; we write exactly that. We also write
   `usage.uncached_tokens` (as the Codex runner does) and `marker` kinds `compaction` and `mcp_problem`.
   Both are backward compatible; please add them to `docs/interfaces/event-log.md` if you agree.
4. **Credentials.** The Claude image takes `CLAUDE_CODE_OAUTH_TOKEN` through `docker run --env-file
   ~/.sisyphus/claude.env` (made with `claude setup-token`), the counterpart of bind-mounting
   `~/.codex/auth.json`. The runner only checks the file exists; it never reads it.
5. **Tool-server port.** The Codex image bakes port 8765 into `config.toml`. The Claude runner writes
   `<run>/mcp.json` with the real `--port` and mounts it, so the image's baked URL is only a default.
6. **P6 (container freedom)** is not done for the Claude image: it has passwordless sudo, but no egress
   proxy or resource caps. It should follow whatever P6 settles on for the Codex image.

## 2026-10-24 (Minecraft on macOS: things the harness can't do generically yet)

1. **Stale "frontmost app" in `macos.py` `focus()`.** `NSWorkspace.frontmostApplication()` is cached per process and only
   refreshes while a run loop is pumped, so in the long-lived tool server it returns whatever was frontmost at the first call.
   Result: "could not bring the program's window to the front" on every input if the host wasn't front at that first call, and a
   silent skip of activation if it was (clicks then go to whatever window is on top). Fix: pump `NSRunLoop.currentRunLoop()`
   (e.g. `runUntilDate_` 0.05 s) inside `front()`, or ask the window list for the front-most layer-0 window instead.
   Also: `activateWithOptions_` from a background process is ignored on current macOS; a synthetic click on the title bar does raise it.
2. **Window capture shows what is on top.** `grab()` is `screencapture -R` on the window's rectangle, so a covering window ends up
   in the screenshot. `screencapture -l<windowID>` (id from `kCGWindowNumber`) captures the window itself, covered or not.
3. **`--run-dir` with a symlink prefix** (`/tmp/x` on macOS is `/private/tmp/x`): `load_extension` fails with
   "is not in the subpath of" because the workspace path isn't resolved. Resolve `run_dir` once in `server.main`.
4. **Starting an in-host part.** Minescript scripts only start when typed in game chat. Today the agent types it (T, `\name`, Enter),
   which works with `key`/`type`. A generic "start an in-host program" reload action would need host-specific input, so we left it to the agent.

- **claude_runner NUDGE file** (8af0311): a supervisor writes <run>/NUDGE to interrupt a stuck turn and deliver a one-time note on resume (marker kind supervisor_nudge). Suggest the same for the Codex runner: on the 2026-10-24 Minecraft run the agent sat 4+ min on a hung hook call before moving.

## 2026-10-24 (critic, cycle 1: skill library)

Evidence (mc-overnight-01, first 20 min): each step costs about 45-55k tokens of context re-read; the agent's own `mc_exec` hook used a
1200 s urlopen timeout against a bridge that was not up, so two calls hung 250 s and 143 s and died with exit 137 (about 400 s lost in 20 min).
Nothing in the brief says to give hooks short timeouts or to keep reusable scripts in a known place.
1. `prompts/brief.md`: new "Skill library" section (skills/INDEX.md, save at the third repeat, short timeouts on own tools). Additive.
2. `runner/claude_runner.py`: `skills_note()` appends the agent's `skills/INDEX.md` (capped at 3000 chars) to the continue prompt, so the
   index is back in context after a restart or compaction. The Codex runner could do the same. Test: `runner/tests/test_skills_note.py`.
3. Proposal: event-log record types `skill_saved` (`name`, `kind`: script|hook, `path`, `description`) and `skill_used` (`name`, `via`), so the
   dashboard can plot reuse. Until then `tools/critic/analyze.py` derives them from `run_script` calls with `file` and from `hook_loaded`.
4. Proposal: hooks that proxy to an in-host server could get a default timeout cap in `extensions.py` (e.g. 60 s unless the manifest says otherwise) so a hung bridge cannot hold a step for minutes.

## 2026-10-24 (critic, cycle 2: cap on extension tool timeouts) toolserver change, please review
Evidence: mc-overnight-01's `mc_exec` declared `timeout_s: 1200`; its backend (bridge on :8777) was down, so calls blocked the turn for
251 s, 143 s and more (about 655 s in 20 min, 3 of 3 calls failed with exit 137), and the same hook inside `run_script` stalled it too.
Change: `HostConfig.max_tool_timeout_s` (default 90, `[extensions] max_tool_timeout_s`, or `toolserver --max-tool-timeout N`) clamps
each extension tool's declared `timeout_s` at manifest parse time (so `run_script` sees the clamped value too); a timeout now reads
"<tool> timed out after N s; check its backend is running". Test: `toolserver/tests/test_tool_timeout_cap.py`. Takes effect at tool server restart.

## 2026-10-24 (critic, cycle 3: Bash bypasses the log) prompts/brief.md
Evidence (mc-overnight-01, 02:10-02:21): in 12 min the model made 35 requests; the tool server logged 9 calls. The agent built `skills/sk.py` and drives its
in-host bridge from Bash, so none of that is in events.jsonl: hook share reads 0%, reuse is unmeasurable, and the rules ("every call to your extensions is recorded and reviewed") cannot be checked.
Change: one bullet in the brief's Skill library section: reach the in-program part through registered tools or `run_script`, not ad-hoc shell. Request: consider also a runner-side counter of Bash calls (the stream has them) as a `marker` or `agent_state`, so coverage can include them; `tools/critic/analyze.py` now prints the model-side tool mix from runner.jsonl.

## 2026-10-24 (critic, cycle 4: poll steps) prompts/brief.md
Evidence (mc-overnight-01, first 70 min): of 94 Bash steps, 33 (35%) were `sk.wait(27)`-style polls that returned "still busy" or a status; several were issued twice in the same step.
At 100-130k tokens of context per request this is the largest avoidable cost, and about 27 s of wall clock each. The agent capped its waits at 28 s itself (it believed calls must end before its 30 s hook timeout).
Change: brief bullet "Wait in one long step, not many short ones" (block until done or several minutes; shell up to 10 min, run_script up to 20; no duplicate calls in one step).
Related: the new 90 s cap on extension tool timeouts means a hook cannot be the long wait; long waits belong in run_script or the shell, with short hook calls inside.

## 2026-10-24 (critic, cycle 4b: delivering guidance to a live run) runner/claude_runner.py
The brief is frozen into runner state at run start, so edits to prompts/brief.md never reach a live run. `take_update_note()` delivers `<run>/UPDATE_NOTE.md`
once with the next continue prompt (then renames it `.sent`). Any supervisor can use it without touching runner-state.json while the runner is up. Test in `runner/tests/test_skills_note.py`.

Note on overlap (critic, cycle 5): `NUDGE` interrupts the running turn and resumes with the note; `UPDATE_NOTE.md` is the non-interrupting variant, delivered with the next continue prompt at a natural turn boundary (cheap, no lost work). Keep both: NUDGE for urgent corrections, UPDATE_NOTE for guidance that can wait.

## 2026-10-24 (critic, cycle 5: milestone costs in `costs`) toolserver/src/toolserver/costs.py
The brief tells the agent to "judge by numbers", but `costs` only showed a time window. It now ends with the last 8 grader milestones: minutes since the previous one, model requests and
uncached tokens in between (milestones found in the same pass are grouped). Generic: it only reads `milestone` records in grader.jsonl (Factorio's too). Test: `toolserver/tests/test_costs.py`. Effective at tool server restart.

## 2026-10-24 (critic, cycle 6: idle-stall watch) runner/claude_runner.py, prompts/brief.md
Evidence (mc-overnight-01, 09:21-10:03Z): 42 min with no milestone; 23 min of it (55%) were gaps over 60 s between model messages, spent "waiting for the job notification" on background shell jobs
(`sk.wait(580)` in `run_in_background`, Monitor timers of 280-590 s). The in-host bridge queues every call behind the running job, so state reads timed out (10-40 s) while a long
get_blocks/scan job held the main thread (one job, 8+ minutes, no progress record), and the agent could only see it by screenshot. The agent's own waiting hides the stall from the supervisor.
Change: (1) `IdleWatch`: if `live/latest.jpg` is byte-identical and `events.jsonl` has not grown for `--idle-minutes` (default 8, 0 = off), the runner interrupts the turn (which also stops stuck background shell jobs), logs `marker kind=interrupt reason=idle`, and the continue prompt carries an idle note (`kind=idle_nudge`). Generic: needs only the spectator image the tool server already writes. (2) Brief bullet "Long jobs report progress". Tests: `runner/tests/test_skills_note.py`.
Caveat: a restart costs a re-read of the context (cached if under 5 min old).

## 2026-10-24 (critic, cycle 9: context handoff to a fresh session) runner/claude_runner.py (opt-in flag)
Evidence (mc-overnight-01): context grew from 37k at start to 212k by 2.5 h; each model request re-reads that whole context, so tokens per request went 45k -> 206k and
total tokens per milestone exploded: first_wood 4.3M, stone batch +2.9M, iron_ingot/iron_tools +19M (uncached only 249k: the cache works, but cache reads are still tokens, time and rate-limit).
The context never compacted (200k shown as the window; the CLI did not compact within -p).
Change: `--reset-context-tokens N` (default 0 = off). When one request's context passes N, the runner interrupts, resumes with a handoff ask (update NOTES.md and skills/INDEX.md, end with
'handoff ready'; 5 min cap), then starts the next turn as a fresh session (brief + goal + continue prompt + skills index). Markers: `context_handoff`, `context_handoff_done`, `context_reset`.
The fresh context is about 40k, so steps cost about 5x less until it grows again. This is Anthropic's "reset with a structured handoff" pattern from docs/research/long-horizon-harnesses.md.
State machine tested in `runner/tests/test_skills_note.py`; the turn-loop wiring has not been run end to end (no fake claude CLI in runner/tests), so first use should be watched.

## 2026-10-24 (critic, cycle 10: deaths as host events) graders/minecraft, runner/claude_runner.py, docs/interfaces/event-log.md
Evidence: three drownings (02:43, 03:29, 03:54 local) in a flooded cave; the second cost all iron and stone gear (about 30 min) right after iron_tools; nothing in the logs flagged it.
1. Grader writes `marker` records `kind: agent_died` (`cause`, `player`, `game_time`, `key`, `count`) from the game log's death lines. Please add the kind to the `marker` row in docs/interfaces/event-log.md; Factorio could write the same kind (character death).
2. Runner: `new_host_events()` reads those markers; a new death interrupts the turn (reason `host_event`) and the next continue prompt carries a generic "you died; re-check inventory; re-plan" note. Tests in runner/tests/test_skills_note.py and graders/minecraft/tests.

## 2026-10-24 (critic, cycle 11: compaction vs handoff, naming)
John's `--compact-tokens` (b9752e2) makes Codex compact one thread past N context tokens; our `--reset-context-tokens N` in `claude_runner.py` (also accepted as `--compact-tokens`) replaces the session instead.
- Compaction keeps one thread, the model writes its own summary; nothing for the harness to do, but the summary is unstructured and lossy (and with Claude Code `-p` the CLI did not compact before 218k in our run).
- Handoff (ours): the agent first updates NOTES.md and skills/INDEX.md (structured, in files that survive crashes too), then a fresh session reads brief + goal + notes + the skills index. Live result: 218k -> 62k context in 21 s (handoff ask 21 s, no lost work), skills carried over (80% of later Bash steps called saved skills).
- Trade-offs: handoff costs one extra short turn and relies on the notes being good (the brief already requires them); compaction is cheaper to implement. They compose: compact at a lower N, hand off at a higher one.

## 2026-10-24 (critic, cycle 12: make bridge traffic visible) proposal, toolserver
Evidence (mc-overnight-01, 4 h): events.jsonl shows 0 hook calls and 0 `run_script` calls after the first 20 minutes, while the agent made about 450 Bash steps, 85% of them calls into its own library (`skills/sk.py` -> host bridge on :8777, saved scripts, `job.py`). The brief asks for registered tools; the agent prefers Bash because it needs no manifest. Hook share, reuse and rules review all read as zero though the behaviour is exactly what we want.
Proposal (generic): a logging forwarder in the tool server for each `agent_ports` entry (listen on a new port, forward to the host port, write one `tool_call` record per request: channel `hook`, tool `port:<n>`, args = first 200 bytes of the body, duration, bytes, ok), and tell the agent the forwarded address in the host notes instead of the raw one. No agent-side change needed beyond the address; works for Factorio RCON-style HTTP bridges too. Until then `tools/critic/analyze.py` derives skill writes/calls from the raw stream.

Implemented (critic, cycle 12): `toolserver/src/toolserver/forward.py` + `[host] forward_offset` (default 0 = off) + `tests/test_forward.py`. With `forward_offset = 10000` and `agent_ports = [8777]` the tool server listens on 18777, passes bytes to 8777 and writes one `tool_call` (channel `hook`, tool `port:8777`, `via: forward`, first request line and 200 bytes of body, duration, bytes back, EmptyResponse/ConnectError as failures) per connection. hosts/minecraft.toml turns it on and its notes give the agent the forwarded address. Fresh runs only: the live agent has 8777 hard-coded and its brief is frozen.

## 2026-10-24 (critic, cycle 15: death loops) runner/claude_runner.py
Evidence: three deaths ("was slain by Zombie") at 12:44:01, 12:44:41 and 12:45:01Z: the agent respawned into waiting mobs and died again within seconds. The runner's death note now escalates when 3 or more deaths fall within 10 minutes (`recent_deaths`, `DEATH_LOOP_NOTE`: leave at once, seal yourself in, stay safe until day, then re-plan). Test: runner/tests/test_skills_note.py.

## 2026-10-24 (critic, cycle 16: safety loop and heartbeat) prompts/brief.md, runner/claude_runner.py
Evidence: about 10 deaths in 5 h; the agent's long jobs had no safety check per slice; after each death neither the agent nor the harness knew where it was or what it lost (the world's playerdata is saved only every few minutes, so the grader cannot warn live).
Change: brief bullet "Every long job checks safety each slice and saves a heartbeat" (generic: any state that can end or undo progress); the agent writes `state/heartbeat.json`; the runner appends its last content and age to the host-event (death) note (`heartbeat_text`). Live low-health or night warnings from the grader were rejected: playerdata is up to 5 min stale, which is longer than a night or a fight.

## 2026-10-24 (critic, cycle 18: program_actions for Minecraft) graders/minecraft
`graders/minecraft/grader.py` writes `program_actions` in John's shape (`count`, `kinds`, `totals`, `tick`, `source`) from the game's own statistics: `mine` (minecraft:mined), `craft` (crafted), `use` (used: placing blocks and using items), `kill` (killed). Walking and pick-ups are left out. Statistics are saved about every 5 minutes, so records come in lumps. Resume-safe, a lower count restarts from the new value (same rule as Factorio). Test in graders/minecraft/tests.

## 2026-10-24 (critic, final: overlaps with John's latest main changes)
- **Idle watch (ours, `IdleWatch`, `--idle-minutes`) vs "queue background work and nudge idle hands" (add3def) and the time-use tracking (cd98d6d):** same problem from two sides. Ours notices a stall from outside (unchanged host picture + no logged tool call) and interrupts; John's nudges when the hands are idle. Suggest one detector: keep John's hands-busy/idle classification as the signal and our interrupt-and-note as the action; our picture-hash signal is useful where tool calls are invisible (agent working through Bash and its own bridge, as here).
- **Context handoff (ours, `--reset-context-tokens`/`--compact-tokens`) vs "status after reset" (dc0def6):** compatible. Our fresh-session continue prompt could include John's status block; the skills/INDEX.md and `UPDATE_NOTE.md` injections are separate and additive.
- **`run_script keep=` (ecce031) vs our skills/INDEX.md:** the brief text now says kept scripts are the skills and the index adds tools, helper modules and when-to-use. The agent here never used `run_script` (0 calls after minute 20; 728 Bash steps, 598 of them calls into its own library), so kept-script reuse reads zero. The bridge forwarder (b5f0e22) or counting Bash in the runner are the two ways to make that traffic visible.
- Conflict resolutions in merges: toolserver/tests/test_costs.py (kept both), prompts/brief.md (kept both sets of bullets), toolserver/src/toolserver/extensions.py (our timeout clamp plus John's `read_only` default of False).
