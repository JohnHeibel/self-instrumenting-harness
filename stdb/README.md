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

    spacetime start --listen-addr 127.0.0.1:3000
    cd stdb/module && npm install
    spacetime publish harness-events --module-path . --server local --yes

**The owner identity.** `spacetime login` is only for Maincloud; a local server needs no account. `--server local` publishes as the CLI's saved identity for that server (the CLI creates one on first use; `spacetime login show` prints it). That identity becomes the **owner**, and only the owner can run `grant_role`, so run `grant_role` from the same machine and the same CLI profile that ran `publish`. Do not use `--anonymous` for the real database: every call is a new identity and nobody would be the owner.

### Exposing the server to the Mac (john-pc)

The tables are public to anyone who can reach the port (agent reasoning included). Do not listen on `0.0.0.0` on a shared network. Bind to the PC's Tailscale address and scope the firewall to Tailscale:

    # Windows (PowerShell, as Administrator): find the Tailscale IP, bind to it, allow only Tailscale peers
    $ts = (tailscale ip -4)
    spacetime start --listen-addr "${ts}:3000"
    New-NetFirewallRule -DisplayName "SpacetimeDB (Tailscale only)" -Direction Inbound -Protocol TCP -LocalPort 3000 -RemoteAddress 100.64.0.0/10 -Action Allow

A server bound to the Tailscale address does not answer on 127.0.0.1, so the publisher on the PC also uses `--server http://<tailscale-ip>:3000`. The Mac reads `ws://<tailscale-ip>:3000`. (Untested on the real machines.)

### The publisher

    cd stdb/publisher && npm install
    npm run publisher -- --run-dir C:\path\to\runs\<run>

First start prints `minted a new identity <hex>`, stores its token in the state dir (`%USERPROFILE%\.sisyphus-publisher\token`) and then fails with 530 "not permitted" (in follow mode it keeps retrying every few seconds). Grant the role once, from the owner's CLI profile:

    spacetime call harness-events grant_role 0x<hex> publisher --server local

In follow mode the publisher picks the grant up on its own; with `--once` start it again.

Behaviour worth knowing:

- **With no checkpoint the publisher starts at byte 0 and publishes the whole history**, then follows. `--live-only` is the opt-in to skip what is already in the files. `--from-start` ignores an existing checkpoint and replays everything (safe: upserts by key).
- `--once` publishes what exists and exits (replays, tests). Without it the publisher follows until Ctrl-C.
- **It reads only** top-level `*.jsonl`, images under `shots/`, and exactly `live/latest.jpg`. Nothing else in a run directory is opened (`codex-home/`, `host/` hold credentials). Allowlist with tests; the e2e plants credential files next to the run.
- `live/latest.jpg` (the spectator frame) is uploaded to the `thumb` table under the stable key `<run_id>|live|latest` and overwritten whenever it changes.
- The checkpoint only advances after the module accepted the batch. On restart the last 20 screenshot records are re-queued, so thumbnails lost in a crash are re-uploaded.
- **Never gives up:** 530 "not permitted", network errors and 5xx are backed off and retried; a failed checkpoint write is a warning (the rename is retried for Windows EPERM). A record whose JSON-encoded size is over 1.5 MB, or that the server rejects (HTTP 413 or a reducer error), is logged and skipped, and the checkpoint moves past it. `hook_loaded` records whose `files[].content` total over 1 MB are truncated (`truncated: true` on the record and the files; `source_ref` keeps the full source).
- Options: `--server`, `--db` (alias `--module`), `--token`, `--state-dir`, `--max-records` (default 200 per call), `--max-bytes`, `--flush-ms` (default 1000), `--thumb-every N`, `--forever`. Run `npm run publisher -- --help`.

### Keeping it running unattended on Windows

`--forever` restarts the whole pipeline in-process after any error (backoff 5 s to 60 s). `stdb\publisher\run-forever.cmd <run-dir>` also restarts the process if Node itself exits. To start it at logon and survive reboots, use Task Scheduler (PowerShell, as the user that runs the harness):

    $a = New-ScheduledTaskAction -Execute "cmd.exe" -Argument '/c C:\path\to\harness\stdb\publisher\run-forever.cmd C:\path\to\runs\<run>' -WorkingDirectory C:\path\to\harness\stdb\publisher
    $t = New-ScheduledTaskTrigger -AtLogOn
    Register-ScheduledTask -TaskName "harness-publisher" -Action $a -Trigger $t

Do the same for `spacetime start --listen-addr <tailscale-ip>:3000` (its own task, started first; set `--data-dir` so data survives). Set `STDB_SERVER` and `STDB_MODULE` as user environment variables, or pass the options after the run dir. Each new run directory needs its own publisher (checkpoints are per run).

## Environment variables

| Variable | Used by | Meaning |
|---|---|---|
| `STDB_SERVER` | publisher | server URL, default `http://localhost:3000` |
| `STDB_MODULE` | publisher | database name, default `harness-events` |
| `STDB_TOKEN` | publisher | bearer token, otherwise read from or minted into the state dir |
| `PUBLISHER_RUN_DIR`, `PUBLISHER_STATE_DIR` | publisher | defaults for `--run-dir` and `--state-dir` |
| `HARNESS_RUNS`, `E2E_PORT`, `E2E_KEEP` | e2e | where the fake runs are, the port (default 3917), keep the temp dir |
| `VITE_SPACETIMEDB_HOST`, `VITE_SPACETIMEDB_DB_NAME` | UI | the usual SpacetimeDB client names (a proposal; the UI is not on main yet, so confirm what it reads). For the Mac to read john-pc: `ws://<tailscale-ip>:3000` and `harness-events` |

## Local versus Maincloud

- **Local** (development, and the demo if the Mac can reach the PC): no account, no quotas, data lives in the instance's data dir.
- **Maincloud** (needs `spacetime login`, so not used here): `spacetime publish harness-events --module-path module --server maincloud`, then run the publisher with `STDB_SERVER=https://maincloud.spacetimedb.com`. Same module, same publisher. Free plan from the research doc: about 3M reducer calls a month, about 1 GB of storage, about 12.5 GB of egress, idle databases pause after a week.

## Limits and costs

- Subscription SQL has no ORDER BY, LIMIT or COUNT: sort and count in the client.
- Calls: the publisher sends one `publish_batch` per second at most, whatever the record rate (default 200 records per call), plus thumbnail calls. A live run of any speed stays near 1 to 2 calls a second, about 2.6 to 5M calls a month if it ran non-stop; for a long unattended run raise `--flush-ms`.
- Measured on the fake runs: 4,134 records and 111 thumbnails in 0.6 s; 7,047 records and 251 thumbnails in 1.8 s.
- Thumbnails live in their own table and are fetched by key. Every screenshot call carries an `artifact`; `--thumb-every N` thins the uploads. `checkpoint` records also carry an `artifact` (a tarball): it is not an image under `shots/`, so `has_thumb` is false for them. A thumbnail the publisher skipped has a record with `has_thumb = true` and no `thumb` row; the UI must show a placeholder.
- The HTTP body limit is unverified (probably 2 MB). The publisher measures the JSON-encoded size, truncates oversized `hook_loaded` file contents and skips (and logs) anything still over 1.5 MB or answered with 413.
- Republishing the module keeps data if you only add tables, indexes and reducers; changing columns needs `--delete-data` (fine in development).

## Ops controls

`request_control(run_id, action, text)` (role `ops`; action `pause`, `resume`, `stop` or `steer`) inserts a `control_request` row with `status = pending`. The module does not act on it. The runner polls `SELECT * FROM control_request WHERE run_id = '<run>' AND status = 'pending'` (HTTP SQL at `POST /v1/database/harness-events/sql`, or a subscription), acts, then calls `ack_control(id, 'done' | 'rejected', note)` (role `runner`). Grant roles with `grant_role` as above. Nothing in the runner uses this yet.

## Tests

    cd stdb/publisher && npm test        # unit tests: tailing, partial lines, rotation, checkpoints, batching, allowlist; no server
    node stdb/e2e/run.mjs                # needs the spacetime CLI; port 3917, temp data dir; works on Windows and macOS

The e2e run covers: module publish, a publisher without the role is refused, both fake runs replayed and compared record by record (counts per writer and type, payload equality, `ts_us`, thumbnail bytes, a time-range query), republish and checkpoint restart change nothing, a live tail on a growing copy (mid-line splits and a restart), credential files not published, role table private, Ops controls and role enforcement.
