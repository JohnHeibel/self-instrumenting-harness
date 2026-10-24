# Event log (interface, v1)

Each run is a directory, `runs/<run>/`, holding one append-only JSONL file per writer, so writers never interleave lines:

| File | Writer |
|---|---|
| `events.jsonl` | Tool server (`toolserver/src/toolserver/events.py`) |
| `runner.jsonl` | Runner |
| `grader.jsonl` | Grader |

Readers merge the `*.jsonl` files at the **top level** of the run directory only (subdirectories such as `raw/` and `codex-home/` hold raw logs and are not part of the event log), ordered by `ts` and then by `writer` and `seq`. Saved files such as screenshot thumbnails sit beside them (see `artifact`). The grader, the UI and the coverage and cost curves read only these files, so changes here need agreement (AGENTS.md: shared interfaces).

## Every record

| Field | Type | Meaning |
|---|---|---|
| `v` | int | Schema version, currently 1 |
| `ts` | string | UTC, ISO 8601 with milliseconds, `Z` suffix |
| `run_id` | string | One per run |
| `writer` | string | `toolserver`, `runner` or `grader` |
| `seq` | int | 1, 2, 3, … per writer, no gaps |
| `type` | string | One of the record types below |

## Record types

**`tool_call`**: one per call the agent makes to the tool server.

| Field | Meaning |
|---|---|
| `tool` | Tool name as the agent called it |
| `channel` | `screenshot`, `input`, `hook`, `docs`, `extension` or `meta` |
| `args` | Arguments as received |
| `ok`, `error` | `error` is `"Type: message"` when `ok` is false |
| `duration_ms` | Wall time inside the server |
| `result_kind`, `result_bytes` | `image`, `text`, `json` or `none`, and its size |
| `via` | Hook calls only: `direct` (its own named tool), `call_hook` (fallback) or `health_check` (run by `load_extension`) |
| `hook` | Hook calls only: `{name, version, extension}` |

Screenshot calls also carry `source` (`window` or `monitor`), `captured_size` and `image_size`.

**`hook_loaded`**: written when `load_extension` registers or replaces a hook.

| Field | Meaning |
|---|---|
| `hook`, `version` | Tool name and version, 1, 2, 3, … (the version of the extension that provides it) |
| `extension` | The agent's extension that provides this tool; one extension can provide several |
| `reason` | Why the agent wrote or changed it, in its own words |
| `language` | Main language, e.g. `lua`, `python`, `csharp` |
| `kind` | `in_host` (runs inside the host, e.g. a mod) or `helper` (runs outside it, in the agent's sandbox) |
| `depends_on` | Names of hooks it calls; this is the library hierarchy |
| `files` | `[{path, content}]`. Source inline so a replay is self-contained; content is capped at 64 KB per file |
| `source_ref` | Where this version is archived, relative to the run directory, e.g. `extensions/factory_probe/v2` |

**`hook_status`**: `hook`, `status` (`ok`, `broken` or `repaired`), `detail`. Written by health checks and repairs.

**`hook_restored`**: `extension`, `version`, `hooks`. Written when a restarted tool server registers an extension's tools again (the last version loaded; nothing is reinstalled). Versions keep counting across restarts, so `hook_loaded` versions never repeat within a run.

**`server_started`**: `host`, `port`.

**Background work** (toolserver/src/toolserver/tasks.py). Calls made by a background task are `tool_call` records with `via` = `task:<id>`.

| Type | Fields |
|---|---|
| `task_started` | `task` (`t1`, `t2`, …), `name` (kept script, file or `code`), `hands` (it may act, holding the agent's one pair of hands), `minutes`, `args`, `why` (e.g. which rule started it) |
| `task_queued` | `task`, `name`, `hands`, `after` (the task it waits to end ok) or `behind` (who held the hands), `args`, `why`: a task waiting to start |
| `task_held`, `task_resumed` | `task`, `name`, `after`; held adds `why` (e.g. `t3 ended failed`). A task waiting on one that failed or was stopped is held, with everything behind it, until the agent resumes it (optionally `after` another task) or drops it |
| `task_stalled` | `task`, `name`, `kind` (`repeat`: the same acting call made 10+ times; `quiet`: no acting call for 2+ min), `acts`, `calls`, `held_s`. A running task holding the hands that seems stuck; the agent gets an urgent notice, once per kind per task |
| `task_repeat_failure` | `task`, `name`, `times`, `summary`. A task failed with the same error (numbers masked) as an earlier run of the same script; the agent gets an urgent nudge to change approach |
| `task_ended` | `task`, `name`, `status` (`ok`, `failed`, `stopped`; a queued or held task the agent dropped ends `stopped` with `calls` 0), `duration_ms`, `calls`, `summary`. `task_started` of a queued task adds `after` and `queued_s` |
| `rule_set`, `rule_removed`, `rules_restored` | `rule`, its `check` (a kept script name, or `code` with the code), `args`, `every_s`, `do` (`tell`, `wake`, `run:<script>`), `times`, `cooldown_s`; removal adds `why` and `fired` |
| `rule_fired` | `rule`, `text` (what the check reported), `do`, `count` |
| `notice` | `notice` (number), `source` (`t3`, `rule:<name>`, `script`), `text`, `urgent`: what was reported to the agent. Urgent notices also go to the run dir's `WAKE` file until delivered, and the runner wakes the agent for them |

### Fields any `tool_call` may carry, for the UI

| Field | Meaning |
|---|---|
| `summary` | One short plain-language line for feeds and pop-ups, e.g. `6 assemblers idle near mine_2` |
| `artifact` | Path relative to the run dir of a saved file, e.g. `shots/000123.jpg` (screenshot thumbnail, at most 640 px wide) |

### Written by the runner

| Type | Fields |
|---|---|
| `run_started` | `agent` (`codex`, `claude-code`), `model`, `host` (`factorio`, …), `goal` (the run's overall objective) |
| `agent_state` | `state`: `thinking`, `acting`, `waiting`, `compacting`, `host_down`, `input_blocked` (the tool server wrote `INPUT_BLOCKED`: input can't reach the host), `backing_off` (with `delay_s`, `failures`), `paused` or `ended`; plus `text` |
| `reasoning` | `title`, `text`: the model's reasoning summary when one is available |
| `agent_message` | `text`, `phase` (`commentary` or `final_answer`): what the agent said |
| `goal` | `text`, `serves`: the agent's current subgoal and what it is for (from a `set_goal` tool, planned) |
| `usage` | After **every model request**: `total_tokens` (the thread total so far, including cached input), `context_window`, plus the deltas since the previous `usage`: `input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_output_tokens`. Turns can last hours, so per-request records keep the cost curve live |
| `turn_started`, `turn_completed` | `turn`, `turn_id`. `turn_completed` adds `status` (`completed`, `interrupted`, `failed`, or `cut_off` when the runner died mid-turn and closed it on restart), `duration_ms`, `error`, and `turn_total_tokens` |
| `checkpoint` | `label`, `ref` |
| `marker` | `label`, `kind`: a moment worth a timeline marker that no other record implies. Kinds so far: `planted_break`, `runner_restart`, `interrupt` (with `reason`; `wake` when the agent was woken for an urgent notice), `prompt_goal`, `prompt_continue`, `prompt_wake`, `toolserver_restart` |
| `run_ended` | `reason`, `turns`, `total_tokens` |

### Written by the grader

| Type | Fields |
|---|---|
| `milestones_defined` | `milestones`: ordered `[{id, name}]` for this host and goal, written once at the start of the run |
| `milestone` | `id`, `evidence`: written when it's reached, judged from host state, never from what the agent says. Factorio's automated-recipe milestones count only machines whose supply traces back to extraction (`evidence.supply_chain`, with `all_machines`, `all_finished` and `excluded` machines and why) |
| `integrity` | `signal`, `active`, `detail`, `evidence`: written when a signal turns on or off (Factorio: `console_command_used`, `cheat_mode`, `editor_mode`, `grader_unavailable`, and the fair-play checks `position_jump` (the character moved farther between samples than it can walk or drive), `conservation` (items exist that nothing produced) and `instant_crafting` (hand crafting finished faster than recipe times allow)). The fair-play signals stay on once found; each is written again, still active, when its `evidence.count` of instances grows, with the recent instances in `evidence` |
| `host_command` | `command_id`, `command`, `parameters`, `by` (`rcon` or `player <name>`), `custom` (a mod-registered command, i.e. the agent's own helpers), `tick`: each console command the host saw, so the log shows which channel changed state |
| `program_actions` | `count`, `kinds` (`{kind: n}`), `totals`, `tick`, `source`: actions done in the program since the previous record, counted by the program itself from its own user-action events, never from what the agent sends or says. Any host-side writer may emit it (Factorio's grader: `build`, `mine`, `craft`, `rotate` from the game's player events; walking, scripted item moves, recipes and research aren't counted, so it undercounts). `kinds` is a free-form map each host's writer defines; readers must not assume its names. `source` says where the count came from; `estimated_from_host_commands` marks an estimate made afterwards by `tools/leverage_from_commands.py` from batched `host_command` parameters (actions asked for, not done), written to `estimates/` with `writer` `estimator` so it isn't merged with real counts |
| `program_activity` | `kinds` (`{kind: n}`), `totals`, `tick`, `source`: signs the user is doing something that isn't counted as an action (Factorio's grader: `move`, position changes; `inventory`, main-inventory changes, however driven). Not part of leverage; the tool server's stuck-task check reads it so a long walk or a restock doesn't look stuck |
| `grader_status` | `ok`, `detail`: written when the host becomes reachable or unreachable for the grader |

## Derived metrics

- **Interface coverage** over a time window is hook observations ÷ (hook observations + screenshots), where a hook observation is a `tool_call` with `channel = "hook"`. Always show it next to the cost curve, because an agent can raise the ratio just by looking less.
- **Cost per milestone** is the sum of `usage` tokens between consecutive `milestone` records.
- **Leverage** over a time window is `program_actions` counts ÷ model steps (`usage` records), with tokens per action next to it: how much the program gets done per step. It's the number the agent's tools should raise: milestones get harder as a run goes on, but the cost of an equivalent action should fall. The `costs` tool reports it.
- **Time use** over a window splits wall-clock time while the agent runs into *hands busy* (a background task holding the hands, a foreground `run_script` with `held_hands`, or an acting call made directly: `channel` `input`, or `hook` with `hook.acts` true), *tools only* (any other tool call or reading task running), and *idle* (nothing running: the model thinking between steps, or a shell command, which the tool server doesn't see). Time when the runner's `agent_state` isn't thinking/acting/compacting/waiting is *off*. Idle should fall as the agent overlaps its thinking with background work. `costs` reports it; `tools/time_use.py` bins a whole run.
- **State coverage** comes from the grader, not this log: which kinds of game state have at least one working hook.

All additions are backward compatible: readers ignore types and fields they don't know. Every writer uses the same `run_id`; `seq` counts per writer.
