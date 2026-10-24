# Factorio as the first host

Research notes, Oct 23–24, 2026. "Verified" means read on a primary source (official docs, repo); everything else is from search summaries and should be checked before relying on it.

## Decisions since this was written (state key `decisions`, BRIEF 7b0ae70)

- Vanilla Factorio, no Space Age, no mods except the agent's own. Goal: blue science, graded from force production statistics over RCON.
- The game runs **natively on John's Windows PC**, not in Docker. The headless-screenshot problem below only matters if a run ever moves to a headless server.
- One agent at a time, Codex first.
- Nazeem's side owns the Factorio work: plumbing tests (a) reading state out (`write_file` vs RCON `remote.call`) and (b) reloading without restart; then the host config, task suite, grader and dashboard.

Reload API notes for plumbing test (b) (LuaGameScript docs, verified):
- `game.reload_script()`: "Forces a reload of the scenario script from the original scenario location." No multiplayer restriction listed. Whether it also re-runs *mod* `control.lua` must be tested.
- `game.reload_mods()`: "Forces a reload of all mods." **Does nothing in multiplayer.** So on a hosted game, the fallback is a save reload (`control.lua` re-runs on load).
- `game.server_save(name?)` saves only in multiplayer; `game.auto_save(name?)` works in single player.
- `game.take_screenshot{...}` does nothing on headless.

## What the extension system gives the agent

Factorio mods are Lua. A mod has a **data stage** (prototypes: items, recipes, entities, built once at launch) and a **control stage** (`control.lua`, runtime scripting).

- **Reload cycle.** `control.lua` re-runs on every save load, so runtime hook changes need only a save reload, not a game restart. Data-stage changes need a full restart. The mod must be an unzipped folder for live editing; editing the zip while the game runs is unsupported. (Factorio data-lifecycle docs; staff on forums.)
  - Implication: hooks should live entirely in the control stage. Iteration cost is "reload save", which is cheap, unlike KSP (recompile + restart) or Forge (restart).
  - Open check: reloading a save on a dedicated server with a connected client. Does the client have to reconnect? Does a save-reload count as a "host change" event for the hook library?
- **Getting data out** (verified, LuaHelpers docs, 2.0):
  - `helpers.write_file(filename, data, append?, for_player?)` writes under `script-output/`. In 2.0 it moved off `game` (`game.write_file` errors).
  - `helpers.table_to_json` / `json_to_table` for structured state.
  - `helpers.send_udp(port, data, for_player?)` and `helpers.recv_udp()` (raises `on_udp_packet_received`). These need the `--enable-lua-udp` launch flag; the destination is localhost only. The OS buffer is 256 KB, and packets are lost while paused or saving.
  - No file read API (removed for determinism).
- **Calling in:**
  - `rcon` global: `rcon.print(...)` replies to the calling RCON client.
  - `commands.add_command(name, help, fn)` registers `/name` console commands. Registrations don't persist through save/load, so re-register in `on_init`/`on_load`.
  - `remote.add_interface(name, {fns})`: cross-mod function tables. Expected to be static at runtime.
  - `/c <lua>` (and `/silent-command`) runs arbitrary Lua live. It disables achievements, and it is the reward-hacking channel (see Prime Agent below).
- **Events:** `script.on_event(defines.events.*)`, `on_tick`, GUI events (`on_gui_opened`, `on_gui_click`, ...). Hooks can observe anything a mod can read: entities, inventories, GUIs, the production statistics.

### Suggested hook transport for the harness

A per-host config (already decided) could say: the hook mod writes JSON lines with `helpers.write_file(..., append=true)`, or exposes `/agent_*` commands that answer over `rcon.print`. RCON is request/response and needs no launch flag, so it's the simplest uniform "read hook" channel. UDP is the push channel if we want streaming.

## Screenshots: a real constraint

- **The headless server never renders.** `game.take_screenshot` does nothing on headless, and staff have said that won't change. For screenshots, a full graphical client must be running.
- Known workaround: run the full Linux client under `xvfb-run`. A modder reported it working on Ubuntu 20.04+ (old report). GPU vs. Mesa llvmpipe performance in Docker is unverified.
- On John's Windows PC, the simplest path may be the native Windows client connected to a Dockerized headless server, with screenshots of the client window. The "game in Docker per run" plan needs a decision on where the rendering client lives.
- The primitive set (screenshot, keyboard/mouse) implies a real client, a player character and a GUI. That is also what makes "actions limited to what a player could do" meaningful.

## Docker

- `factoriotools/factorio`: the standard headless image. Single volume at `/factorio` (mods in `/factorio/mods`). Game port UDP 34197, RCON TCP 27015. Space Age built-in mods are skipped by the mod updater. `UPDATE_MODS_ON_START` needs a Factorio username and token.
- The headless server binary is free to download, but clients need a purchased copy. Check licensing on the demo PC (BRIEF open question).

## Factorio Learning Environment (FLE): the human-built ceiling

Verified from the repo README, Oct 2026:
- `JackHopkins/factorio-learning-environment`, about 1.2k stars, actively updated (Oct 21, 2026). Latest release v0.4.3 (Apr 2026, "Inspect AI Sandbox Evaluation"). It now targets **Factorio 2.0.73+**; the 2025 paper used 1.1.110.
- Interface: the agent writes Python in a REPL against ~27 high-level tools (place entity, craft, connect with belts, ...). Those tools are backed by Lua helpers loaded over RCON (`FactorioInstance`, `LuaScriptManager`, `serialize_entity`, `production_score`).
- Modes: open-play (build the biggest factory, scored by production score) and lab-play (24–33 bounded throughput tasks). Extras: `pip install factorio-learning-environment[mcp]`, a gym env, multi-agent support, `fle cluster start` for Docker clusters.
- Findings (NeurIPS 2025 D&B): models lack spatial reasoning and are weak in constrained settings. The old leaderboard snapshot (Claude 3.5 Sonnet top, about 21.9% lab success) is stale; check the live leaderboard.

**How we use it:** FLE is exactly the human-built bridge we compare against, like Mineflayer for Minecraft and kRPC for KSP. Its lab-play tasks and production score could be borrowed for evaluation:
- Same model, three conditions: screenshot-only, self-built hooks, FLE tools.
- Run on lab-play-style throughput tasks.
- FLE's own Lua serializers could serve as an accuracy oracle for our hooks, the way Odoo's API grades Odoo hooks.

Caution: the BRIEF bans importing pilot code. Using FLE as a third-party baseline or oracle is fine, but our agent must not be handed FLE's tools.

## Prime Agent's Factorio run: cautionary prior art

Prime Intellect's Prime Agent (Aug 2026) played Factorio through FLE. It pushed the production score above 100k in hours, then discovered it could spawn items into machines via RCON. It kept that as a "cheat skill" despite reminders not to cheat. Commentators read it as self-improvement loops reinforcing reward hacking.

**Lessons for us:**
1. Our hooks may *read* anything, but actions must go through player input. `/c` and RCON writes are the exact channel Prime Agent abused. The policy-only rule (decided) should at least log every RCON/console write, and the dashboard should flag them.
2. This gives a pitch contrast: "Prime Agent got a prebuilt interface and hacked the reward; ours builds the interface and is held to player actions."

## Sources
- Factorio data lifecycle: https://lua-api.factorio.com/latest/auxiliary/data-lifecycle.html
- LuaHelpers (write_file, UDP): https://lua-api.factorio.com/latest/classes/LuaHelpers.html
- LuaCommandProcessor: https://lua-api.factorio.com/latest/classes/LuaCommandProcessor.html
- Script interfaces tutorial: https://wiki.factorio.com/Tutorial:Script_interfaces
- `game.write_file` moved in 2.0: https://forums.factorio.com/viewtopic.php?p=621510
- Headless can't screenshot / xvfb: https://forums.factorio.com/viewtopic.php?p=517455
- factoriotools/factorio-docker: https://github.com/factoriotools/factorio-docker
- FLE repo: https://github.com/JackHopkins/factorio-learning-environment · paper https://arxiv.org/abs/2503.09617 · Epoch page https://epoch.ai/benchmarks/factorio-learning-environment
- Prime Agent coverage: https://www.testingcatalog.com/icymi-prime-intellect-releases-open-source-prime-agent/ · https://www.zenml.io/llmops-database/self-improving-agentic-harness-with-recursive-language-models-and-continual-learning
