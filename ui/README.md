# Story view

A replay-and-live view over a run's event log (`docs/interfaces/event-log.md`). React, TypeScript, Vite. It knows only the generic record types, so it shows any host the same way.

    npm install
    npm run dev                 # http://localhost:5173, reads ../runs
    HARNESS_RUNS=/path/to/runs npm run dev
    npx tsc -b && npm run lint

Open `#run=<name>`. `t=live` follows the newest moment; `t=<seconds>` is a position in the run. With no run, the page lists them.

## Live and replay

A run that is still writing opens at its newest moment and follows it (the header says **Live**). Scrub back and it says **Behind by 12m** with a **Jump to live** button. When the agent's last screenshot is more than 30 s old, the picture is the **spectator frame** (`live/latest.jpg`, the host window every 5 s, never shown to the agent), labelled as such, with the agent's last look inset.

## Data sources (`src/data/`)

| Source | When | How |
|---|---|---|
| Run files over HTTP (default) | no env set | The dev server (`vite.config.ts`) merges a run's top-level `*.jsonl` (and `estimates/*.jsonl`), serves `/api/runs/<run>/records[?since=<ts>]` (polled every 2 s) and, from `runs/`, only `<run>/shots/<image>` and exactly `<run>/live/latest.jpg`. Everything else under `runs/` is 404 (a run directory holds credentials). |
| SpacetimeDB | `VITE_SPACETIMEDB_HOST` set (e.g. `ws://127.0.0.1:3000`), optional `VITE_SPACETIMEDB_DB_NAME` (default `harness-events`) | The SQL-over-HTTP API of the module in `stdb/` (see `stdb/SCHEMA.md`). Replay loads the run in `ts_us` windows, live polls for newer records, thumbnails are fetched by key on first view, and the spectator frame is the key `<run>|live|latest`. |

    VITE_SPACETIMEDB_HOST=ws://127.0.0.1:3000 npm run dev

Both produce the same records, so nothing above `src/data/source.ts` knows which is in use.

## Layout and numbers

One 1920×1080 stage, scaled to fit, framed by two line-drawn Doric columns. Flat panels on a 16px gutter: the game (agent screenshot, or the spectator frame when live and the last look is old) as the hero; under it the four efficiency charts and the scrubber; on the right the current goal (`goal` record with how long ago it was set, else the first line of `run_started.goal`) with milestones reached of announced, what the agent is doing now (one state: ended, no records for two minutes, or its `agent_state`), the ledger, the tools it built, its recent calls and the Sisyphus panel. Every number comes from a record; where a run has no record of a kind it shows a dash.

| Shown | Source |
|---|---|
| charts behind the four numbers | `Run.metricSeries`: the same four numbers per 10-minute bin from the run's start (time_use.py's rows, bars) and for the run so far at each bin's end (line; its end is the big number); grader milestones as ticks; tokens / action on a log scale. The previous comparable run (`#cmp=<name>`, else the run name with its trailing number one lower) is a thin reference line, aligned at its start |
| tokens / hour, tokens / step, tokens / action, hands busy | `Run.metrics`, the same computation as `tools/time_use.py`'s "all" line: window from the first runner/tool-server record to now; tokens and steps are `usage` records in it (growth of `total_tokens`); actions are real `program_actions.count`; hands busy is hands time ÷ (wall − off), with `costs.py` span rules |
| steps, actions, failed calls, scripts run, tokens in / cached in / out, tasks, deaths, stalls, handoffs, nudges | `Run.metrics` / `Run.ledger`: `usage` count; Σ `program_actions.count`; `tool_call` ok=false (health checks excluded); agent `run_script` calls; Σ `usage.input_tokens` / `cached_input_tokens` / `output_tokens`; `task_started`; `marker` `agent_died`; `task_stalled`; `marker` `context_handoff_done`; `marker` `idle_nudge` + `supervisor_nudge` |
| agent, model, effort, host | `run_started` (`effort` / `agent_version` only if recorded; a dash otherwise) |
| tools | `Run.kits`: per extension every `hook_loaded` version on the run's time axis, `hook_restored`, calls and last call per hook; kept scripts from `run_script` "kept as scripts/…" summaries, runs from `run_script name=` and `task_started` |
| now | newest `agent_state`, open background task (`task_started` without `task_ended`), newest `agent_message` |

The single accent (terracotta) marks state changes only: a milestone reached, a tool improved, a death or break.

## Backdrop

`src/Backdrop.tsx` is the only file that names the background animation. To swap it, change its one import and element. The current one is `src/backdrop/sisyphus-v3.js`, a `<sisyphus-backdrop-v3>` web component; `styles.css` (`.figure .backdrop`) crops it to the figure, stone and slope in a panel at the foot of the side column, at full opacity, recoloured to light stone on the page ground. It holds a still pose under reduced motion.

## Fake runs for development

    uv run --project toolserver python tools/fakerun/generate.py --out runs/fake-rich --rich
    python3 tools/fakerun/simulate_live.py --src runs/fake-rich --out runs/fake-live --skip-minutes 500
