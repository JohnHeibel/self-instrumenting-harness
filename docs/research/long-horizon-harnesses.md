# Long-horizon agents and harness design

What's known (as of Oct 2026) about keeping an agent productive for many hours. This feeds Tier 2 of the BRIEF: persistence, crash recovery, tens-of-hours runs.

## How long can agents work?

- **METR time horizons:**
  - Time Horizon 1.1 (Jan 2026) has 31 long tasks, each 8h+ for a human.
  - Feb 2026: Claude Opus 4.6 had about a 14.5h 50%-horizon (GPT-5.2 about 6.5h).
  - Third-party reports put Claude Mythos Preview around 17h (Apr 2026, unverified).
  - METR says >16h is unreliable on the current suite, and that a horizon is not literally how long an agent can run unattended.
- **Practical runs:**
  - Anthropic's long-running builder on Opus 4.6 ran coherently for more than 2h without sprint scaffolding.
  - The Gemini Minecraft team ran 27h (with restarts and deaths) to beat the game.
  - A paper on months-long assisted development ("Memory as Infrastructure", Aug 2026) describes a 633k-line codebase worked across many compactions, with an open-source "SIx Harness".
- **Implication:** a single session won't last until judging. The harness must expect context resets, crashes and restarts, and make them cheap. The BRIEF's Tier 2 (state persists across restarts, resume from checkpoint) is the right priority.

## Anthropic's harness lessons (two engineering posts)

**"Effective harnesses for long-running agents":**
- An **initializer agent** runs once and sets up the environment:
  - a machine-readable feature list (JSON, which the model was less likely to clobber than Markdown)
  - a progress log
  - an init script
  - a first git commit
- A **coding agent** runs each later session through a fixed routine:
  1. read the progress log and git history
  2. run init and a smoke test
  3. pick **one** failing item
  4. implement it, verify it, commit, and update the log
- State lives in files and git, not in conversation memory.

**"Harness design for long-running apps"** (read in full):
- Planner, generator and evaluator roles. The evaluator is a *separate* agent testing the running app (Playwright), because self-evaluation is consistently lenient.
- **Compaction vs. reset:**
  - Compaction summarizes history in place and keeps continuity.
  - A reset starts a fresh agent with a structured handoff file, which cures "context anxiety" (wrapping up early near the limit).
  - Sonnet 4.5 needed resets; Opus 4.5+ didn't, so compaction became enough.
- Agents communicate through files. "Sprint contracts": agree on what done means and how it's tested before each chunk.
- Each harness component encodes an assumption about model limits. Remove components one at a time when models improve; removing several at once hid which parts were load-bearing.

## Map onto our harness

| Lesson | Our version |
|---|---|
| Progress file + JSON feature list + git | Hook library as a git repo with versioned hooks and the reason each was created (Tier 1). Add a JSON "interface map" of known screens, entities and actions, each marked hooked or pixels-only. That map *is* the coverage denominator candidate. |
| Fixed session start routine | On every (re)start: read the hook library index, run every hook's self-test against the live host, mark broken ones, then continue. This doubles as the **break detection** for Tier 2 ("repair when the host changes"). |
| Separate evaluator | Hook accuracy checked against an oracle (Odoo API; FLE's Lua serializers or our own debug dump for Factorio) by a separate process, not by the agent's own judgment. |
| Compaction vs reset | Use the SDK's auto-compaction. Also write a handoff file on `PreCompact`/`Stop` hooks (Claude Code), so a crash or a Codex session can resume from files alone. |
| Log every observation and action | Claude Code `PostToolUse` hooks / Codex app-server event stream → an event log tagged `hook` vs `screenshot`. Feeds the dashboard curves. |

## Self-improving loops: what to borrow, what to avoid

- **Borrow:**
  - Continual Harness: reset-free edits within one run, drawn from the trajectory.
  - Agent S4: hold out validation tasks before keeping an interface change.
  - DGM: keep an archive and allow branching instead of overwriting.
- **Avoid:** Prime Agent's failure mode, where a self-improvement loop entrenches a reward hack. The Factorio equivalent is spawning items via `/c` or RCON. Log and surface any non-player write.

## Context-management papers (titles only; not read)
- CliffCompaction: cost-efficient compaction for long-horizon coding agents (arXiv 2609.26779)
- The Compaction Cliff in long-running agent memory (arXiv 2608.22752)
- An empirical study of harness design for coding agents (arXiv 2609.20804)
- TokenPilot: cache-efficient context management (arXiv 2606.17016)
- awesome-harness-engineering list: https://github.com/ai-boost/awesome-harness-engineering

## Sources
- METR time horizons: https://metr.org/notes/2026-01-22-time-horizon-limitations/ · https://en.wikipedia.org/wiki/METR
- Anthropic, harness design for long-running apps: https://www.anthropic.com/engineering/harness-design-long-running-apps
- Anthropic, effective harnesses for long-running agents: https://anthropic.com/engineering/effective-harnesses-for-long-running-agents
- Anthropic, managed agents: https://anthropic.com/engineering/managed-agents
- Memory as Infrastructure: https://arxiv.org/pdf/2609.05510
