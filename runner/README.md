# Runner

Drives one long Codex session for a run: one thread, driven through `codex app-server`
(JSON-RPC over stdio) inside the agent container. The runner owns the run directory and
the tool server's lifecycle, and writes `runner.jsonl` ([event log v1](../docs/interfaces/event-log.md)).

## Run it

From the repo root, on the PC (needs Docker Desktop, `~/.codex/auth.json` from `codex login`,
and the agent image; `--build` builds it). `--host-config` names the program the agent works in:
its window, how to capture it and how to tell it's up all come from `hosts/<host>.toml`.

```bash
uv run --project runner runner --build --host-config hosts/<host>.toml --run-dir runs/<run> --goal-file goals/<goal>.md --budget-hours 8
```

Resume after a crash, a reboot or Ctrl-C: run the same command with the same `--run-dir`
(the goal is only needed the first time). The runner resumes the saved thread.

```bash
uv run --project runner runner --host-config hosts/<host>.toml --run-dir runs/<run>
```

Control a running run from another shell:

| Do this | Effect |
|---|---|
| create `runs/<run>/STOP` | interrupts the current turn and ends the run (`run_ended` reason `stop`) |
| create `runs/<run>/PAUSE` | interrupts the current turn and starts no new ones until the file is removed |

Useful flags: `--model`, `--budget-hours H` (active time), `--budget-tokens N` (uncached input +
output), `--host-check` (overrides the host config's `up_check`: `window`, `always`,
`cmd:<command>`, `py:<module>:<fn>`), `--port` (tool server, default 8765), `--reasoning-summary`,
`--brief-file` (default `prompts/brief.md`).

The first prompt is the brief, then the goal, then the host notes ("## About this host"), then a
line about keeping notes. On every runner start, `workspace/run/` gets `BRIEF.md`, `GOAL.md` and
`HOST.md` (overwriting any edits; the brief is the one the run started with), and the continue
prompt tells the agent to re-read them and its `NOTES.md` after a reset.

The host config's `[host]` table: `name`, `window_title` and/or `process` (exe name) to find the
program, `capture` (`window`: its main window's client area; `process`: the bounding box of all its
visible windows, so dialogs count; `monitor`: the whole screen), and `up_check` (`window`: its
window is on screen and not minimized; `always`; or a command list, up when it exits 0, run from
the host config's directory). Optional: `start` (a command the runner runs when the host is down;
time spent starting isn't active time, and after a failed start it waits `start_retry`, 10 min,
before trying again), `stop` (run once when the run ends, not when the runner is killed),
`start_timeout_s`, `agent_ports`, and `notes` (added to the goal and continue prompts as "About this
host: ..."). Their output goes to `raw/host.log`, with `host_start`/`host_stop` markers.

In `install_dir` and in any command, `{run_dir}` is this run's directory, `{config_dir}` the host
config's, and `{python}` the Python running the harness; environment variables expand too. All
host commands, reload actions included, run from the host config's directory.

Without a game, for a smoke test:

```bash
uv run --project runner runner --host-config hosts/notepad.toml --run-dir runs/smoke --host-check always --budget-tokens 60000 --goal "Call list_hooks once, then reply done."
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
  and during a turn. `test_budgets.py` (fake clock) covers active time, the uncached-token
  budget, rate-limit backoff, workspace snapshots and the host check from the host config.
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
| `checkpoints/NNNN-<label>.tar.gz` | runner | workspace snapshots, every 30 min and at the end; not mounted, so the agent can't touch them |
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
  The time budget counts active time only: not runner downtime, host down or PAUSE, but
  backoff waits do count. The token budget counts uncached input plus output (output
  includes reasoning), since cached input grows with every request of a long thread.
- **Failures:** a turn that fails (status `failed`, the app-server exiting, no message for
  30 minutes) backs off 30 s, 60 s, 120 s, ... up to 1 h. After 12 consecutive failures the run
  ends with `too_many_failures`. A completed turn, or any turn longer than 10 minutes, resets the count.
  When Codex has reported a rate limit as reached (`account/rateLimits/updated`, or a
  `usageLimitExceeded` error), the wait runs until the full window's `resetsAt` plus 60 s.
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
| `turn_started` / `turn_completed` | `turn/start` response and `turn/completed`; extra fields `turn_id`, `status`, `duration_ms`, `error`, and `turn_total_tokens` on completion |
| `agent_state` | `turn/started` and reasoning or message items → `thinking`; tool, shell, file and web items → `acting` (text names the tool or command); `sleep` items and a harness `wait` tool → `waiting`; `contextCompaction` item → `compacting`; runner-side `host_down`, `backing_off`, `paused`, `ended` |
| `reasoning` | each entry of a completed reasoning item's `summary`, split into `title` (the leading `**...**`) and `text` |
| `agent_message` | completed `agentMessage` items; extra field `phase` (`commentary` or `final_answer`) |
| `usage` | each `thread/tokenUsage/updated` that grows the total (one per model request): that request's tokens, plus thread totals `total_tokens` and `uncached_tokens`, and `context_window` |
| `checkpoint` | after each completed turn; `ref` is `<thread_id>/<turn_id>` (a point the thread can be forked from). Workspace snapshots add `artifact` (the tarball), `files`, `bytes` |
| `marker` | runner restarts, interrupts and why, which prompt was sent (`prompt_goal` or `prompt_continue`), tool server restarts |
| `run_ended` | STOP, a budget, or too many failures; extra `turns`, `total_tokens`, `uncached_tokens`, `active_s` |
