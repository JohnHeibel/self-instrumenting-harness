# Lessons from ModdedBench (the Minecraft pilot)

ModdedBench (github.com/JohnHeibel/ModdedBench, private) is the earlier, Minecraft-only pilot. The event rules forbid importing its code. This file records what worked and what hurt, in our own words, so we can build the general version better. The summary comes from a read-only review on 2026-10-23.

## Running one long session
- **The runner keeps a single thread and resumes it.** It saves the session ID the moment the stream reports it, so a killed first turn resumes instead of starting over. It enforces token and time budgets mid-turn by killing the CLI process; the thread stays resumable. A STOP file ends a run cleanly. After failures it backs off with growing waits, which sleeps through usage limits. It never starts a turn while the game is down, because that only burns tokens.
- **The agent can stay inside one turn for hours** by calling a blocking "wait" tool, since MCP can't push messages to the agent. Turns become a recovery mechanism rather than units of work.
- **Durable memory lives outside the context:** notes, a goal stack, and git history. A heartbeat file tells a freshly compacted context what to re-read. Pause the game while the agent compacts, which took 5–7 minutes with Codex.
- **Snapshots go where the agent can't reach them** (world, notes, agent commits, runner state), and the agent's brief doesn't mention them.

## Hooks and reloading
- **Reload must be atomic.** If any part of a reload fails, keep the previous tools registered and surface the error in a status tool. Keep live state outside reloaded code.
- **Make the reload loop cheap.** Code that reloads instantly (Python on the harness side) gets used far more than code that needs a game restart (Java mods took minutes). A reflection-style "probe" tool that reads live game state without a deploy removed many slow rebuilds.
- **Deploying into the game goes through one gate on the host.** It accepts only expected files, archives what it deploys, rolls back if the game fails to come up, and returns a verdict with the log tail.

## Testing the harness without a human
- **Tier 0 (no game, every commit, CI):** unit tests against fake game backends, plus a runner test against a fake agent CLI. Also replay tests against recorded Codex and Claude stream fixtures (compaction, a cut stream, a timeout), a check that every tool imports and registers, and a check that docs match code.
- **Tier 1 (live "courses"):** a throwaway game stack with privileged setup commands that are compiled out of real runs. Each trial rebuilds a fixture, calls the agent's real tools, and is graded from the game's authoritative state. Output is a PASS/FAIL table with evidence files, run N times per case.
- **Tier 2 (model challenges):** a fresh checkout with empty state and one capped agent turn, graded only from game state, never from what the agent says. Graders are hidden from the agent.
- **Replays:** test design changes by replaying the logs of real long runs.

## What hurt
- **Tool "receipts" claimed success that hadn't happened.** The rule became: completion is a fresh observation, never a tool's say-so or the agent's.
- **Ambiguous timeouts.** Fixed by giving each request explicit states (queued → running → done, or late), and a request that timed out never runs afterwards.
- **Context bloat from large tool results.** Compact output with defaults omitted cut result size about 4×.
- **Notes as memory** needed relevance ranking and output caps.
- **An over-eager safety guard** blocked legitimate actions and was removed.
- **Ops fragility:** a bad state file blocked every start, two runners started on one checkout, mounts broke, and a rotating login token forked when copied.

## What we're adopting
1. The spike runner already writes machine-checkable verdicts with evidence (`spikes/codex_dynamic_tools/run.py`). Course and challenge runners will follow the same pattern for Factorio, graded from production statistics over RCON.
2. `load_extension` will validate, then swap atomically, and report errors in a status tool.
3. The runner keeps one resumable Codex thread, with mid-turn budgets, a STOP file, backoff, and no turns while the game is down.
4. A test tier with no game runs on every change: the tool server's own tests, plus later fixtures for the runner.
5. Hook results include a fresh observation, and the grader never trusts what the agent says.
