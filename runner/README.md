# Runner

Drives one long Codex session for a run: one thread, driven through `codex app-server`
(JSON-RPC over stdio) inside the agent container. The runner owns the run directory and
the tool server's lifecycle, and writes `runner.jsonl` ([event log v1](../docs/interfaces/event-log.md)).

## Run it

From the repo root, on the PC (needs Docker Desktop, `~/.codex/auth.json` from `codex login`,
and the agent image; `--build` builds it):

```bash
uv run --project runner runner --build --run-dir runs/factorio-01 --goal-file goals/blue-science.md --budget-hours 8
```

Resume after a crash, a reboot or Ctrl-C: run the same command with the same `--run-dir`
(the goal is only needed the first time). The runner resumes the saved thread.

```bash
uv run --project runner runner --run-dir runs/factorio-01
```

Control a running run from another shell:

| Do this | Effect |
|---|---|
| create `runs/<run>/STOP` | interrupts the current turn and ends the run (`run_ended` reason `stop`) |
| create `runs/<run>/PAUSE` | interrupts the current turn and starts no new ones until the file is removed |

Useful flags: `--model`, `--budget-tokens N` (on Codex's thread total, which includes cached
input), `--host-check` (`factorio-window` default, `window:<title>`, `always`, `cmd:<command>`,
`py:<module>:<fn>`), `--port` (tool server, default 8765), `--reasoning-summary`.

Without a game, for a smoke test:

```bash
uv run --project runner runner --run-dir runs/smoke --host-check always --budget-tokens 60000 --goal "Call list_hooks once, then reply done."
```

## Tests (tier 0: no model, no game, no Docker)

```bash
cd runner
uv run pytest -q
```

- `tests/fake_app_server.py` replays scripted JSON-RPC (see its docstring). The runner tests
  cover goal then continue prompts, resume after a crash in the first turn, resume from the
  state file after a runner restart, STOP, PAUSE, token and time budgets interrupting
  mid-turn, backoff growth and give-up, the long-turn reset, and the host being down before
  and during a turn.
- `tests/fixtures/real_session*.jsonl` are real Codex 0.164.0 app-server sessions, scrubbed
  of account data. `test_fixtures.py` checks the event mapping parses them. To re-record
  one (costs one tiny model turn):

  ```bash
  uv run --project runner python runner/scripts/record_fixture.py
  ```

## What's in a run directory

| Path | Written by | |
|---|---|---|
| `runner.jsonl` | runner | event log records (schema: docs/interfaces/event-log.md) |
| `events.jsonl` | tool server | tool calls |
| `runner-state.json` | runner | thread id, turn count, failures, token totals; what resume needs |
| `runner.lock` | runner | pid; a second runner on the same dir refuses to start |
| `codex-home/` | Codex | mounted at `/home/agent/.codex`: config, thread history (`sessions/`) |
| `workspace/` | agent | mounted at `/home/agent/work`: the agent's notes, survives restarts |
| `raw/appserver.jsonl` | runner | every JSON-RPC line both ways, for debugging and fixtures |
| `toolserver.log`, `raw/appserver.stderr.log` | | process output |

Raw and Codex files sit in subdirectories, so readers that merge the top-level `*.jsonl` files don't pick them up.

## Behavior

- **One thread.** `thread/start` once. The thread id is saved before anything else, so a crash
  at any point resumes it with `thread/resume`. The first turn sends the goal. Every later
  turn sends the fixed `CONTINUE_PROMPT` (`src/runner/run.py`): rebuild your picture from your
  notes, `list_hooks` and a fresh observation, then continue.
- **Budgets** are checked every second during a turn. Hitting one sends `turn/interrupt`,
  which Codex honors within about a second (the turn completes with status `interrupted`).
  The time budget is wall-clock time since the run started, so it includes runner downtime.
- **Failures:** a turn that fails (status `failed`, the app-server exiting, no message for
  30 minutes) backs off 30 s, 60 s, 120 s, ... up to 1 h. After 12 consecutive failures the run
  ends with `too_many_failures`. A completed turn, or any turn longer than 10 minutes, resets the count.
  An interrupt the runner asked for (STOP, PAUSE, budget, host down) is not a failure.
- **Host down:** checked before every turn and every 30 s during one. No turn starts while
  it's down. If it goes down mid-turn, the turn is interrupted.
- **Crash cleanup:** on restart the runner kills a tool server left by a crashed runner (pid in
  the state file), removes a leftover agent container (`harness-agent-<run>`), closes the
  cut-off turn with its own `usage` and a `turn_completed` with status `cut_off`, and writes
  a `marker` (`kind: runner_restart`).

## Mapping from the app-server to runner.jsonl

| runner.jsonl | From |
|---|---|
| `run_started` | after the first `thread/start`; `model` from its response |
| `turn_started` / `turn_completed` | `turn/start` response and `turn/completed`; extra fields `turn_id`, `status`, `duration_ms`, `error` |
| `agent_state` | `turn/started` and reasoning or message items → `thinking`; tool, shell, file and web items → `acting` (text names the tool or command); `sleep` items and a harness `wait` tool → `waiting`; `contextCompaction` item → `compacting`; runner-side `host_down`, `backing_off`, `paused`, `ended` |
| `reasoning` | each entry of a completed reasoning item's `summary`, split into `title` (the leading `**...**`) and `text` |
| `agent_message` | completed `agentMessage` items; extra field `phase` (`commentary` or `final_answer`) |
| `usage` | at turn end: the difference in `thread/tokenUsage/updated` totals across the turn; `total_tokens` is Codex's thread total; extra `turn_total_tokens`, `context_window` |
| `checkpoint` | after each completed turn; `ref` is `<thread_id>/<turn_id>` (a point the thread can be forked from) |
| `marker` | runner restarts, interrupts and why, which prompt was sent (`prompt_goal` or `prompt_continue`), tool server restarts |
| `run_ended` | STOP, a budget, or too many failures; extra `turns`, `total_tokens` |
