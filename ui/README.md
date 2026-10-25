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

## Demo view (default)

`#v=demo` (the default): header (run switcher, model · agent, replay speed, clock, "?" with the parts linked to their source), the recorded video as the hero with chapter marks (milestones, tool versions, deaths) on the scrubber, the goal with every milestone, the newest reasoning, the four headline numbers, the task log, and tiles for the tools the agent built (click one for its versions, code and the change each version made; `#tool=extension:<name>` opens one).

**Video**: put the recording in the run directory with `video.json`: `{"file": "obs.mp4", "start_utc": "2026-10-24T23:55:00.723Z"}` (`file` may also be `video/<name>`; mp4 or webm). `start_utc` is the UTC time of the video's first frame; the player seeks the video to the event-log time. At speeds up to 16× it plays at that rate; faster, it is seeked. Without a video the run's saved frames show.

**Data**: SpacetimeDB on `ws://127.0.0.1:3000` by default (`VITE_SPACETIMEDB_HOST` to point elsewhere, `off` for files only); when it can't be reached the run files over HTTP are used. Videos are always served by the dev server.

## Layout and numbers

`#v=s1|s2|s3` (default s1): the broadsheet with the two step panels. **Reasoning**: the agent's own words (`agent_message`) and new goals (`goal`), numbered by step (the count of `usage` records by then); Claude Code's thinking blocks are redacted in the raw stream (empty text, signature only), so nothing else is available. **Tool calls**: every call the agent made itself (not those inside its scripts or tasks) with step, time, tool (`run_script <kept name>`), short arguments, failed, duration; identical consecutive calls folded. Stats on the left over Sisyphus (the slope runs from the bottom edge to the top edge; the mountain's base, top and right side are off screen), the milestone count opening to the whole set. Palettes: s1 Gruvbox, s2 Flexoki, s3 Rosé Pine Dawn (hex values in styles.css). Older directions stay available: `a`, `b`, `c`, `a1`–`a3`.

| Shown | Source |
|---|---|
| headline (docs/BRIEF.md "Metrics") | `Run.metrics`, `Run.segments`, `Run.metricSeries`, matching `~/sisyphus-work/stats.py`: cache-weighted tokens = Σ usage (`input_tokens` − `cached_input_tokens`) + 0.1 × `cached_input_tokens` + `output_tokens` (costs.weighted(); spec input includes cached), or `input_tokens` + 0.1 × cached + output for older Claude runs whose input excludes cached (input < cached). **cw tokens / milestone** = cw so far ÷ milestones reached, charted as cw spent in each segment between milestones (usage in (previous, milestone]; the first from the first runner record; the open segment as an outline). **cw tokens / action** (log) and **actions / step** (Σ `program_actions.count` ÷ usage records) in 15-minute bins from the first runner record, bars per bin, line for the run so far. **hands busy**: costs.py rules over time_use.py's window, same bins. Milestone ticks on every axis; the previous comparable run (`#cmp=<name>`, else the run number minus one) as a thin reference line |
| diagnostics (ledger, last row) | cw tokens; raw tokens / hour and raw tokens / step (time_use.py); context / request = newest usage `input_tokens` (input + cached for the older Claude runs) |
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
