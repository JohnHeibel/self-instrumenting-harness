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

## Metrics

Leverage (right column): actions the program counted per model step and tokens per action, in 30-minute bins, from `program_actions` (grader) and `usage` (runner), with the trailing 30 minutes large and the kind breakdown on hover. Estimated bins (`source: estimated_from_host_commands`, read from `<run>/estimates/*.jsonl`) are hatched or dashed. A first real record that covers far more than a bin is shown as one "before tracking" average block. `components/Leverage.tsx` takes only a `Run` and a time, so the Stage overlay can mount it as is.

Time use (right column): how wall-clock time while the agent ran split between hands busy, tools only and idle (`costs.py` / `tools/time_use.py` rules, event-log.md "Time use"), as one proportional bar with h:mm and %, plus the share per 30-minute bin. It is derived in `Run.timeUse` from `tool_call`, `task_started`/`task_ended` and `agent_state` records, so it needs no new record type; a run with no tool calls shows "Not recorded for this run".

Tokens per milestone and wall-clock time per milestone (falling is good, with a fitted trend), coverage over the last hour, and distinct hooks built over time.

## Backdrop

`src/Backdrop.tsx` is the only file that names the background animation. To swap it, change its one import and element. The current one is `src/backdrop/sisyphus.js`, a `<sisyphus-backdrop>` web component at ~13% opacity; it holds a still pose under reduced motion.

## Fake runs for development

    uv run --project toolserver python tools/fakerun/generate.py --out runs/fake-rich --rich
    python3 tools/fakerun/simulate_live.py --src runs/fake-rich --out runs/fake-live --skip-minutes 500
