# Research library

Background research gathered Oct 23–24, 2026, while waiting for the work split. Each file lists its sources and marks what's verified vs. from search summaries.

| File | Covers |
|---|---|
| [agent-tools.md](agent-tools.md) | (John's) How Codex and Claude Code expose MCP tools, deferred tools, mid-session tool lists, logging hooks |
| [factorio.md](factorio.md) | Factorio mod API (reload cycle, RCON, write_file, UDP), the headless-screenshot constraint, Docker, FLE as the ceiling baseline, Prime Agent's RCON reward hack |
| [prior-art.md](prior-art.md) | Re-check of the BRIEF's prior-art list (one item not found), plus Alita, LATM, DGM, Continual Harness, CUA-Skill |
| [long-horizon-harnesses.md](long-horizon-harnesses.md) | METR horizons, Anthropic's long-running harness lessons, compaction vs. reset, how they map onto our hook library |
| [backends-and-primitives.md](backends-and-primitives.md) | Claude Code / Agent SDK, Codex exec vs app-server, Droid exec, computer-use MCP options on Windows/Docker |
| [live-company-scenario.md](live-company-scenario.md) | **Proposal:** the agent works as the AP/bookkeeping clerk of a simulated company on Business Central (Odoo as fallback), continuously until judging, with exact ground truth from a world simulator |
| [real-world-applications.md](real-world-applications.md) | Non-game showcase: ERP-Bench on Odoo 19 (recommended), other professional domains ranked, a proposed experiment, an open policy question |
| [long-horizon-showcase.md](long-horizon-showcase.md) | **Proposal (replaces the two above as the non-game showcase):** the agent works a 24–48 h shift alone as the board operator of the Tennessee Eastman chemical plant behind an Ignition SCADA HMI, building its own hooks and supervisory scripts; verified tep-studio and Ignition Maker facts, published TE costs, measured trip times under instrument and valve faults |
| [other-hosts-and-sponsors.md](other-hosts-and-sponsors.md) | Odoo + OCA reconcile, Minecraft and KSP reload costs, SpacetimeDB 2.0, Hyphenate |

## Top findings

1. **Factorio hooks reload cheaply.** `control.lua` re-runs on save load, so a hook iteration is a save reload, not a restart. Keep hooks in the control stage and ship the mod unzipped.
2. **The headless server can't take screenshots.** This is now mostly moot: John decided the game runs natively on his Windows PC. It only matters if a run moves to a headless server. Also, `game.reload_mods()` does nothing in multiplayer, so on a hosted game, reloading a hook means `game.reload_script()` (to be tested) or a save reload.
3. **FLE now runs on Factorio 2.0.73+.** It's the human-built ceiling (and possibly an oracle), and it has lab-play throughput tasks we could reuse. Don't hand its tools to our agent.
4. **Prime Agent reward-hacked Factorio through RCON item spawning.** It's our strongest argument for "hooks read anything, actions are player-only". Log and flag every console or RCON write.
5. **Log at the primitive server.** One MCP server that logs every call gives identical coverage accounting for Claude Code, Codex and Droid. Both Codex and Claude Code also have `PostToolUse` hooks (see John's [agent-tools.md](agent-tools.md)).
6. **Prior art is mostly confirmed.** Agent S4 (offline interface evolution) and Continual Harness (online, reset-free self-editing harness) are the closest neighbors. **"A Decade in a Day / ANIS" (arXiv 2610.15765) couldn't be found.** Keep it off slides until someone locates it. The Gemini 27h Minecraft paper is real (arXiv 2610.22319).
7. **Plan for restarts.** Use Anthropic's long-running pattern: state in files and git, a fixed start routine, one item at a time, and a separate evaluator. Re-running every hook's self-test at session start doubles as break detection.
8. **Odoo Community can do bank reconciliation** with OCA `account_reconcile_oca` (18.0/19.0). ERPNext stays a fallback.
9. **SpacetimeDB 2.0 dropped global reducer callbacks.** Build the dashboard on row subscriptions or event tables.

## Open questions this raises (for the work-split discussion)

- Where does the rendering Factorio client run, and how do screenshots and input reach it from the agent sandbox?
- Coverage denominator candidate: an agent-maintained "interface map" (screens, GUIs, entity types, actions), each marked hooked or pixels-only. Or, for Factorio, the set of GUI types and entity prototypes the task touches.
- Is reusing FLE's lab-play task definitions and scoring OK for the evaluation, given the no-imported-code rule? (It's third-party eval code, not pilot code.)
