# Cal Hacks 13.0 Project Brief: Self-Instrumenting Agent Harness

This brief brings a new agent or teammate up to date on everything decided so far. It covers event rules, the idea, prior art, goals, evaluation, sponsor fit, and pitch. Architecture has not been designed yet. Items marked (verify) are unconfirmed.

## Event facts

- Cal Hacks 13.0, October 23 to 25, 2026, Palace of Fine Arts, San Francisco. Team of two.
- Submit on Devpost (https://cal-hacks-13-0.devpost.com/) by **9:00 AM Sunday, October 25**. Hard deadline. The submission needs a project image and a GitHub repo.
- All work must be started during the event. Code from the earlier Minecraft pilot cannot be imported. The pilot can be mentioned in the pitch as motivation.
- Judging runs Sunday 10:30 AM to 12:30 PM at an assigned table, and the whole team must be present. A live demo is required. Devpost lists a 3 minute pitch plus 2 minutes of questions. A demo video is strongly recommended, because sponsors judge asynchronously.
- General judging criteria: Application (real-world use, who it helps), Intentionality, Functionality, Creativity, Technical Complexity. A separate Most Technically Complex Hack prize also exists.

## The idea

**North star.** A layer that wraps Claude Code or Codex and builds its own interface to any program it's pointed at, then keeps improving that interface. The agent gets only screenshots, keyboard and mouse input, the host's docs, and the host's own extension system.

**Origin.** In a pilot on modded Minecraft, the agent got only vanilla tools and no modded capabilities. It wrote Forge hooks itself to read modded GUIs, built tools on top of them, and improved that layer over time. Most of the value came from this self-built interface. We call this *self-instrumentation*.

**Why it matters in the real world.** Almost every company customizes its business software with custom fields, modules, and workflows that vendor APIs and MCP servers don't cover. A customized ERP is effectively a modpack. Integrations also break when apps change. An agent that builds its own interface, and repairs it when it breaks, stays working over months. That is the real-world version of the long-horizon argument.

**Novelty claim.** As far as we know, no prior agent starts from only screenshots, input, and docs, and builds its own interface live by writing code into the host through the host's native mod or plugin system, then keeps extending it over tens of hours, in both games and business software. Evidence is interface coverage rising and tokens per hour falling.

**Do not lead with** "self-improving harness" or "writes its own MCP server for any app". Both read as prior work to judges who follow the field.

## Prior art and how we differ

Two research agents ran about 170 searches (academic, products, open source, hackathons). Neither found a project with the full combination: in-host code through the host's extension system, starting from raw access, improving over a long run, across games and business apps.

- **Universal Modder**, game-automation skill (github.com/rehan-remade/universal-modder). Closest match. Suggests the agent build an optional "agent bridge" inside its own game mod (observe, click, step) so it can play without pixels. Purpose is testing the agent's own mods in short sessions; games only; no long-run improvement of the bridge.
- **CLI-Anything** (HKU, 2026). A coding agent follows a 7-phase method to generate CLIs for desktop apps; a refine command fills coverage gaps. Generally needs source or docs; CLIs are built ahead of use.
- **Evolving Agent Interfaces / Agent S4** (Simular and others, arXiv 2610.28601, Oct 20, 2026). A meta-agent evolves computer-use interfaces from task trajectories and picks the best sets on validation tasks. Appears offline, before deployment. Desktop and science apps, no games.
- **A Decade in a Day / ANIS** (arXiv 2610.15765, Oct 2026). Generates agent-native interfaces for desktop apps; cuts tokens ~73%.
- **Prime Agent** (Prime Intellect, Aug 2026). Self-improving harness that edits its own prompts, skills, memory, subagents. Played Factorio through the prebuilt Factorio Learning Environment and reward-hacked by spawning items via RCON. README states it is not a sandbox.
- **Voyager**, and a reported 27-hour Gemini team-of-agents Minecraft run (arXiv 2610.22319, verify). Both use Mineflayer, vanilla only.
- **Continual Harness, Gemini Plays Pokemon.** Long runs editing their own harness, but on human-built game interfaces.
- **Startups for apps with no API:** Minicor, Kampala, Zatanna, Frigade (verify). Build automations or APIs ahead of time or from recorded workflows; don't instrument the app from inside while working.
- **Compiling GUI routines to code:** AutoRPA, PACE, SkillDroid, SkillCache, SpeedRunner. Prior work for the falling-cost curve.
- **Human-built game bridges we compete against:** Mineflayer, FLE, kRPC, stardew-ai-agent, gtnh-agent. Modded Minecraft with modded GUIs has no academic coverage.

## Hosts

- **Factorio** (first host). Mods are Lua, loaded from the mods folder; RCON and `helpers.write_file` give a channel between mod and harness (verify reload without restart). FLE is the human-built ceiling for vanilla tasks, so a modpack strengthens novelty. Runs natively on John's Windows PC. Starts vanilla with the goal of blue science; a modpack can come later.
- **Minecraft** (later host). Interesting case is modded: modded GUIs and state have no prebuilt agent interface. Mineflayer serves as the human-built ceiling for vanilla tasks. Open: version, mod loader, modpack, hook loading (restart vs hot reload), milestones.
- **Odoo.** Real-world anchor. Modules are plain Python, runs in Docker, official API is an accuracy oracle. In Hyphenate's sandbox list. (Verify) Community may lack full Accounting and the bank reconciliation screen; community add-ons may fill the gap; ERPNext is the fallback.
- **Kerbal Space Program 1.** Flashiest host. Plugins are compiled C# loaded at startup, no live console, so each hook iteration needs a restart. Real-time physics forces the agent to write its own autopilot. kRPC is the human-built ceiling. KSP 1 only (KSP 2's studio was shut down).
- Confirm licenses for Minecraft and KSP on the demo machine.

## Goals, in priority order

### Tier 1 — prototype
- Wraps Claude Code and Codex without modifying either.
- One fixed, small set of primitives, identical for every host: screenshot, keyboard and mouse, read docs, write and load an extension into the host.
- Agent writes its first working hook into one host and uses it to read state it couldn't get from pixels.
- Every hook is stored as a versioned, persistent library, with the reason it was created.
- Every observation and action is logged as hook-based or screenshot-based, so interface coverage is measurable from the first run.

### Tier 2 — core system
- Agent builds higher-level tools on its own hooks, so the library forms a hierarchy.
- State persists across session restarts and context resets; runs can last tens of hours.
- Recovers from crashes and resumes from a checkpoint without human help.
- Detects when a hook breaks after the host changes, and repairs it.
- Actions limited to what a normal user or player could do; hooks can read anything.
- No host-specific code written by humans; the same commit runs on every host.

### Tier 3 — hosts
- **Odoo.** Instrument a seeded company with planted problems (short payments, duplicates, a changed vendor bank account), reconcile, flag errors, graded against an answer key.
- **Minecraft.** Build hooks into a modded environment and make long-horizon progress with them.
- **KSP.** Build flight data hooks, write its own autopilot, reach orbit.
- A fourth host is a bonus if it uses yet another extension system.

### Tier 4 — evidence
- Screenshot-only baseline, same model, as the floor.
- Human-built interfaces as the ceiling: Odoo API, Mineflayer, kRPC.
- Hook accuracy checked against Odoo's API as oracle.
- Held-out tasks.
- Ablation: performance drops when its own hooks are off.
- Curves over time: success rate, tokens per task, time per task, interface coverage.
- One planted change mid-run (e.g. Odoo form change or module upgrade) with visible detection and repair.

### Tier 5 — demo and pitch
- Live dashboard: hooks appearing, coverage rising, cost falling.
- At least one host running continuously until judging.
- Recorded backup run.
- One user story through every slide: an accountant whose customized Odoo no off-the-shelf tool understands. The agent learns the setup, reconciles the month, and repairs its own hook when an upgrade breaks it.
- One slide placing the project against Universal Modder, CLI-Anything, Agent S4, Prime Agent.
- The one-sentence novelty claim, stated precisely.

### Tier 6 — sponsor fit
- Odoo finance task written up against Hyphenate's four judging questions.
- Droid as a supported backend, for Factory.
- Dashboard state in SpacetimeDB, for Spacetime.
- Pitch framed for Microsoft's forward deployed engineering team: the agent as a self-maintaining integration developer.

### Future work (pitch only)
- Closed applications via decompiling and injecting hooks. Many licenses forbid reverse engineering; enterprise buyers will raise it.
- Sharing one hook library across many agents and installs.
- More hosts: Excel, browsers, CAD tools.

## Sponsor tracks

The harness itself is the product. Each sponsor track is one demo of it.

- **Cal Hacks First Overall and Most Technically Complex.** Best fit; they judge the whole system.
- **Hyphenate, The Hyphenated Enterprise** ($8k / $5k / $3k, plus expedited interviews for top 5). Autonomous system for one corner of a finance team. Four questions: Is it real enough? Would you hire it (limited access, clean record, knowing when to ask)? Is it new? Does it have a "moment" where it catches, recovers, or defends a decision? Bring a demo video that opens with the moment, a live demo (they may try their own scenario), one line on which seats it fills, and proof it works (planted errors with an answer key). Brief: https://docs.google.com/document/d/1u4iN8R97ILY3kfHTPKC82zdutHrGx4ze5eCAP-J92w4/edit
- **Factory, Droid SDK** ($2k). Droid must do real work in the running product; README must explain where. Scores well with custom triggers, controlled permissions, visible plans/steps/diffs. Chatbot wrappers score poorly.
- **Microsoft, Built for the Real World** (Surface laptop per member). Production-minded agent for finance, productivity, healthcare, etc. Judged on customer understanding, measurable usefulness, path to production, responsible design, end-to-end function.
- **Spacetime** ($2,048 / $1,024 / $512). SpacetimeDB must be the core real-time backend.
- **Anthropic, Claude for Public Benefit.** Only if reframed around accessibility for a narrowly named community. Not the current plan.
- Ask in #0-ask-directors on Slack how many sponsor tracks one submission can enter.

## Objections and answers

- **"Why not just use the API?"** Most real software has none, and APIs often don't expose what the screen shows. Here the API only grades the agent.
- **"Isn't this CLI-Anything or Agent S4?"** Those build interfaces ahead of time from source, docs, or offline evolution. Ours instruments a running app from raw access while working, through the app's own extension system, and keeps extending it.
- **"An agent writing code into our ERP?"** Hooks are code that goes through normal review, deployed to a staging copy first. Write actions only go through operations a user could do.
- **"Did it just overfit to your demo?"** Held-out tasks and the ablation.

## Decisions already made

- ~~Minecraft replaces Factorio.~~ Reversed: **Factorio is the first host** (installed via Steam). Minecraft and KSP are later hosts.
- Factorio starts **vanilla** (base game, no Space Age, no mods except the agent's own). **Goal: reach blue science**, meaning the first chemical science pack is produced. The grader checks Factorio's production statistics over RCON.
- One agent runs at a time, never two simultaneously. **Codex goes first**; Claude Code uses the same tool server for a later run.
- The user-possible-actions rule is policy only: stated to the agent and logged, not enforced.
- Extension loading starts from a small per-host config; a docs-only discovery run on a fresh host comes later.
- The agent runs in a Docker sandbox and reaches a tool server on the host through one port. The game runs natively on the host (John's Windows PC, RTX 5070 Ti), not in Docker.
- Each agent gets one long session. A plain runner script (no model) restarts, resumes and checkpoints.
- Hooks are language-agnostic: in-game hooks use whatever the host's mod system requires, and helper tools outside the game use whatever language the agent picks. Each loaded hook is registered as its own named tool, with `call_hook` as a fallback.
- Event log: one JSONL record per tool call, tagged by channel (hook, screenshot, input). Coverage is the share of observations that came through hooks, paired with the cost curve; the evaluator also reports which kinds of game state have a working hook.
- The harness is the product. Sponsor tracks are demonstrations; Hyphenate is not the primary target.
- Safety is not the headline. The user-possible-actions rule stays as a constraint.
- Decompiling closed apps is future work only.
- No timelines in planning documents.

## Open questions

- Minecraft version, mod loader, modpack, hook loading method, milestones.
- Odoo Community plus add-ons, or ERPNext, for the accounting task.
- When Droid support lands.
- Token budget for long runs.
- Licenses for Minecraft and KSP 1 on the demo machine.
- The denominator for "interface coverage".
- **UI is central and visual.** One web app with three views over the same data:
  - **Stage:** a broadcast overlay over the game screen, in the style of ModdedBench's overlay. It shows the goal, the agent's state and reasoning, a plain-language feed, pop-ups of what the agent just observed, coverage and cost curves, and the hook library growing.
  - **Story:** a replay timeline with key-moment markers and timelapse playback. Clicking a hook shows its source, diffs and the reason it was written. **This view is built first**; it doubles as the demo video.
  - **Ops:** dense health, alerts and controls for the two of us.
  The UI only knows generic concepts (runs, tool calls by channel, hooks, reasoning, milestones); hosts supply data, not UI code. It runs on John's Mac.
- **SpacetimeDB is the UI's real-time backend.** The JSONL event log stays the source of truth. A publisher on the PC mirrors it into SpacetimeDB, and the UI subscribes for live views and queries time ranges for replay. Control actions from the Ops view go through SpacetimeDB reducers to the runner.- **The agent writes the whole path from the host to its tools.** `load_extension` is a small generic gate: it validates the agent's `extension.json`, copies its in-host part (e.g. a mod) into the folder named by the host config, runs a named reload command from the host config, and registers the agent's tools. Each tool is a program the agent wrote, run inside its sandbox; how it reaches the host (RCON, files, sockets) is the agent's choice. The host config holds only data (install folder, reload commands, window title).

