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
| headline (docs/BRIEF.md "Metrics") | `Run.metrics`, `Run.segments`, `Run.metricSeries`, matching `~/sisyphus-work/stats.py`: cache-weighted tokens = Σ usage `input_tokens` + `output_tokens` + 0.1 × `cached_input_tokens`. **cw tokens / milestone** = cw so far ÷ milestones reached, charted as cw spent in each segment between milestones (usage in (previous, milestone]; the first from the first runner record; the open segment as an outline). **cw tokens / action** (log) and **actions / step** (Σ `program_actions.count` ÷ usage records) in 15-minute bins from the first runner record, bars per bin, line for the run so far. **hands busy**: costs.py rules over time_use.py's window, same bins. Milestone ticks on every axis; the previous comparable run (`#cmp=<name>`, else the run number minus one) as a thin reference line |
| diagnostics (ledger, last row) | cw tokens; raw tokens / hour and raw tokens / step (time_use.py); context / request = newest usage `input_tokens` + `cached_input_tokens` |
| steps, actions, failed calls, scripts run, tokens in / cached in / out, tasks, deaths, stalls, handoffs, nudges | `Run.metrics` / `Run.ledger`: `usage` count; Σ `program_actions.count`; `tool_call` ok=false (health checks excluded); agent `run_script` calls; Σ `usage.input_tokens` / `cached_input_tokens` / `output_tokens`; `task_started`; `marker` `agent_died`; `task_stalled`; `marker` `context_handoff_done`; `marker` `idle_nudge` + `supervisor_nudge` |
| agent, model, effort, host | `run_started` (`effort` / `agent_version` only if recorded; a dash otherwise) |
| tools | `Run.kits`: per extension every `hook_loaded` version on the run's time axis, `hook_restored`, calls and last call per hook; kept scripts from `run_script` "kept as scripts/…" summaries, runs from `run_script name=` and `task_started` |
| now | newest `agent_state`, open background task (`task_started` without `task_ended`), newest `agent_message` |

The single accent (terracotta) marks state changes only: a milestone reached, a tool improved, a death or break. Type: Marcellus (display), Archivo at a condensed width (labels), Azeret Mono (data). Data panels are cool slate, the goal and Now panels warm stone.

## Backdrop

`src/Backdrop.tsx` is the only file that names the background animation. To swap it, change its one import and element. The current one is `src/backdrop/sisyphus-v3.js`, a `<sisyphus-backdrop-v3>` web component; `styles.css` (`.figure .backdrop`) crops it to the figure, stone and slope in a panel at the foot of the side column, at full opacity, recoloured one quiet step above the page ground. He pushes only while the agent's hands are busy (`Run.handsActive`: a hands span from the costs.py rules in the last 5 s of run time, and the runner not off), and otherwise eases to a standstill. The playback rate (`src/pushRate.ts`) chases 1 or 0 as a critically damped spring: about 3 s over the full range in either direction, easing in and out; a flip mid-ramp turns from the current speed; activity must be gone 0.4 s of wall time before he starts to slow. It follows the view's position, so replay at speed and scrubbing drive it too. He holds still under reduced motion.

## Fake runs for development

    uv run --project toolserver python tools/fakerun/generate.py --out runs/fake-rich --rich
    python3 tools/fakerun/simulate_live.py --src runs/fake-rich --out runs/fake-live --skip-minutes 500
