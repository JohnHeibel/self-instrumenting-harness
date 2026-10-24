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
