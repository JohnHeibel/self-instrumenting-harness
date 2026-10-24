# Event log (interface, v1)

One JSONL file per run, `runs/<run>/events.jsonl`, append-only. The tool server writes it (`toolserver/src/toolserver/events.py`); the runner may append its own records to the same file. The grader, the dashboard and the coverage and cost curves read only this file, so changes here need agreement (AGENTS.md: shared interfaces).

## Every record

| Field | Type | Meaning |
|---|---|---|
| `v` | int | Schema version, currently 1 |
| `ts` | string | UTC, ISO 8601 with milliseconds, `Z` suffix |
| `run_id` | string | One per run |
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
| `via` | Hook calls only: `direct` (its own named tool) or `call_hook` (fallback) |
| `hook` | Hook calls only: `{name, version}` |

Screenshot calls also carry `source` (`window` or `monitor`), `captured_size` and `image_size`.

**`hook_loaded`**: `hook`, `version`. Written when `load_extension` registers or replaces a hook.

**`server_started`**: `host`, `port`.

**Planned, written by the runner:**
- `usage`: per turn, with `input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_output_tokens`, and running totals.
- `turn_started` / `turn_completed`.
- `task_started` / `task_completed`, for measurement tasks.
- `checkpoint`.

## Derived metrics

- **Interface coverage** over a time window is hook observations ÷ (hook observations + screenshots), where a hook observation is a `tool_call` with `channel = "hook"`. Always show it next to the cost curve, because an agent can raise the ratio just by looking less.
- **Cost per task** is the sum of `usage` tokens between `task_started` and `task_completed`.
- **State coverage** comes from the grader, not this log: which kinds of game state have at least one working hook.
