# SpacetimeDB backend for the replay/live UI

The JSONL event log (`docs/interfaces/event-log.md`) stays the source of truth. This directory mirrors it into SpacetimeDB (v2.13.0) so the UI can subscribe for live views and query time ranges for replay.

| Part | Where | What |
|---|---|---|
| Module | `module/` | TypeScript module: tables, the publish reducers, roles, Ops control requests |
| Publisher | `publisher/` | Node program that tails a run directory and calls the module's reducers over HTTP |
| Contract | `SCHEMA.md` | Tables, columns, reducer arguments, query shapes for the UI |
| E2E check | `e2e/run.mjs` | Starts its own local instance, publishes the module, runs the publisher on fake runs and compares counts |

The module stores records generically, keyed by `(run_id, writer, seq)`, with the complete record as JSON in `payload`. New record types and fields need no schema change. It contains no host or game concepts.

## Run it locally (Mac or the PC)

Requirements: the `spacetime` CLI 2.13.x (official installer from spacetimedb.com/docs: macOS and Linux `curl -sSf https://install.spacetimedb.com | sh`, Windows `iwr https://windows.spacetimedb.com -useb | iex`), and Node 22 or newer.

    spacetime start --listen-addr 0.0.0.0:3000          # 127.0.0.1:3000 if only this machine reads it
    cd stdb/module && npm install
    spacetime publish harness-events --module-path . --server local --yes

The identity that publishes becomes the **owner**. Now the publisher:

    cd stdb/publisher && npm install
    npm run publisher -- --run-dir C:\path\to\runs\<run> --from-start

First start prints `minted a new identity <hex>` and stores its token in the state dir (`~/.sisyphus-publisher/token`). The publisher cannot write until the owner grants it the role (one time per identity):

    spacetime call harness-events grant_role 0x<hex> publisher --server local

Then start the publisher again. It resumes from its checkpoint, one per run in the state dir.

Behaviour worth knowing:

- **First run on a run that is already in progress: pass `--from-start`.** With no checkpoint and no flag the publisher follows only new lines (live tail) and skips what already exists.
- `--from-start` ignores the checkpoint and republishes everything. That is safe: records are upserted by key, so counts do not change.
- `--once` publishes what exists and exits (use it for replays and tests). Without it the publisher keeps following until Ctrl-C.
- It reads only the top-level `*.jsonl` files and the thumbnails named by records' `artifact` under `shots/`. Nothing else in a run directory is opened (`codex-home/`, `host/` and other subdirectories hold credentials). This is an allowlist with tests, and the e2e run plants credential files next to the run and checks they never reach the database.
- Partial last lines wait for their newline; the checkpoint only advances after the module accepted the batch.
- Options: `--server`, `--module`, `--token`, `--state-dir`, `--max-records` (default 200 per call), `--max-bytes`, `--flush-ms` (default 1000), `--thumb-every N`. Run `npm run publisher -- --help`.

## Environment variables

| Variable | Used by | Meaning |
|---|---|---|
| `STDB_SERVER` | publisher | server URL, default `http://localhost:3000` |
| `STDB_MODULE` | publisher | database name, default `harness-events` |
| `STDB_TOKEN` | publisher | bearer token, otherwise read from or minted into the state dir |
| `PUBLISHER_RUN_DIR`, `PUBLISHER_STATE_DIR` | publisher | defaults for `--run-dir` and `--state-dir` |
| `VITE_SPACETIMEDB_HOST`, `VITE_SPACETIMEDB_DB_NAME` | UI | the usual SpacetimeDB client names (a proposal; the UI is not on main yet, so confirm what it reads). For the Mac to read john-pc: `ws://<john-pc-ip>:3000` and `harness-events` |

Ports: SpacetimeDB listens on 3000 (HTTP and WebSocket). Reaching it from the Mac needs `--listen-addr 0.0.0.0:3000` and an inbound Windows firewall rule for TCP 3000 (untested).

## Local versus Maincloud

- **Local** (development, and the demo if the Mac can reach the PC): no account, no quotas, data lives in the instance's data dir.
- **Maincloud** (needs `spacetime login`, so not used here): `spacetime publish harness-events --module-path module --server maincloud`, then run the publisher with `STDB_SERVER=https://maincloud.spacetimedb.com`. Same module, same publisher. Free plan from the research doc: about 3M reducer calls a month, about 1 GB of storage, about 12.5 GB of egress, idle databases pause after a week.

## Limits and costs

- Subscription SQL has no ORDER BY, LIMIT or COUNT: sort and count in the client.
- Calls: the publisher sends one `publish_batch` per second at most, whatever the record rate (default 200 records per call), plus thumbnail calls. A live run of any speed stays near 1 to 2 calls a second, about 2.6 to 5M calls a month if it ran non-stop; for a long unattended run raise `--flush-ms`.
- Measured on the fake runs: 4,134 records and 111 thumbnails in 0.6 s; 7,047 records and 251 thumbnails in 1.8 s.
- Thumbnails live in their own table and are fetched by key. The toolserver already attaches `artifact` to only some screenshots; `--thumb-every N` thins them further. A thumbnail the publisher skipped has a record with `has_thumb = true` and no `thumb` row; the UI must show a placeholder.
- A record line larger than the reducer or HTTP body limit would be refused (the limit is unverified, probably 2 MB). `hook_loaded` files are capped at 64 KB each by the event log.
- Republishing the module keeps data if you only add tables, indexes and reducers; changing columns needs `--delete-data` (fine in development).

## Ops controls

`request_control(run_id, action, text)` (role `ops`; action `pause`, `resume`, `stop` or `steer`) inserts a `control_request` row with `status = pending`. The module does not act on it. The runner polls `SELECT * FROM control_request WHERE run_id = '<run>' AND status = 'pending'` (HTTP SQL at `POST /v1/database/harness-events/sql`, or a subscription), acts, then calls `ack_control(id, 'done' | 'rejected', note)` (role `runner`). Grant roles with `grant_role` as above. Nothing in the runner uses this yet.

## Tests

    cd stdb/publisher && npm test        # unit tests: tailing, partial lines, rotation, checkpoints, batching, allowlist; no server
    node stdb/e2e/run.mjs                # needs the spacetime CLI; uses port 3917 and a temp data dir

The e2e run covers: module publish, a publisher without the role is refused, both fake runs replayed and compared record by record (counts per writer and type, payload equality, `ts_us`, thumbnail bytes, a time-range query), republish and checkpoint restart change nothing, a live tail on a growing copy (mid-line splits and a restart), credential files not published, role table private, Ops controls and role enforcement.
