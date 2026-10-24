# SpacetimeDB module contract (v0.1)

Source: `stdb/module/src/index.ts`. Verified against SpacetimeDB 2.13.0 on a local instance. The publisher codes against this file. If a column or reducer changes, this file changes with it.

## Module and connection

- Database name: `harness-events` (any name works; the publisher takes `--db`, default `harness-events`).
- Local instance: `spacetime start --listen-addr 127.0.0.1:3000`. The HTTP base is `http://127.0.0.1:3000`, the WebSocket base is `ws://127.0.0.1:3000`. (The e2e test uses port 3917.)
- **The publisher talks plain HTTP, no SDK needed:**
  - Identity: `POST {base}/v1/identity` returns `{"identity": "<64 hex>", "token": "<jwt>"}`. Save the token (e.g. in the publisher's state dir) and reuse it, because the identity is derived from it. Every call sends `Authorization: Bearer <token>`.
  - Call a reducer: `POST {base}/v1/database/{db}/call/{reducer}` with a JSON **array of the arguments in order**. Structs are JSON objects with the column names, arrays are JSON arrays, `u8[]` is an array of numbers 0 to 255, `u64` and `i64` are plain JSON numbers (the largest `ts_us` is about 1.8e15, safe in JS). Success is HTTP 200 with an empty body. A failed reducer returns HTTP 530 with the message as text (e.g. `not permitted`). A malformed body is a 4xx. Treat 5xx (except 530), 429 and network errors as retryable, 530 and other 4xx as fatal for that batch.
  - SQL (tests, runner polling): `POST {base}/v1/database/{db}/sql` with the query as the body. Returns `[{"schema":{"elements":[{"name":{"some":"col"},...}]},"rows":[[...]]}]`. Public tables only unless called as the owner.
- **Reducer names are snake_case** (`publish_batch`, not `publishBatch`).

## Roles (private table `role`)

The identity that publishes the module is `owner`. The owner grants roles to other identities:

    spacetime call harness-events grant_role 0x<identity-hex> publisher --server http://127.0.0.1:3000

Roles: `owner` (can do everything), `publisher` (publish_batch, publish_thumbs), `runner` (ack_control), `ops` (request_control). A call without the role fails with `not permitted`. A fresh publisher identity must be granted `publisher` once; until then every publish returns 530.

## Reducers the publisher calls

### `publish_batch(records: RecordIn[])`

Upserts records keyed by `(run_id, writer, seq)`, so repeating a batch changes nothing (it does not double-count). Also maintains `run` and `publisher_cursor`. Keep a batch to at most about 500 records or 768 KB of JSON (the HTTP body limit is unverified, probably 2 MB).

`RecordIn` fields, all required:

| Field | Type | From the event log record |
|---|---|---|
| `run_id` | string | `run_id` |
| `writer` | string | `writer` |
| `seq` | u64 | `seq` |
| `ts` | string | `ts` verbatim |
| `ts_us` | i64 | `ts` as microseconds since the Unix epoch (`Date.parse(ts) * 1000`; ts has millisecond precision) |
| `kind` | string | `type` |
| `channel` | string | `channel`, or `""` when the record has none |
| `has_thumb` | bool | `true` when the record has an `artifact` |
| `payload` | string | the complete record as JSON text, exactly as read (all fields, unknown ones too) |

Example call body: `[[{"run_id":"r","writer":"toolserver","seq":1,"ts":"2026-10-25T06:00:00.000Z","ts_us":1792908000000000,"kind":"tool_call","channel":"hook","has_thumb":false,"payload":"{...}"}]]`

### `publish_thumbs(thumbs: ThumbIn[])`

Upserts thumbnails. Keep a call to at most about 512 KB of JSON (about 100 KB of image bytes becomes 300 KB or more as a JSON number array, so send a few per call).

`ThumbIn`: `rkey` (string, the record key `"<run_id>|<writer>|<seq>"` of the record whose `artifact` this is), `run_id` (string), `mime` (string, `image/jpeg` or `image/png`), `bytes` (u8[]).

## Tables (all public except `role`)

| Table | Columns | Notes |
|---|---|---|
| `run` | `run_id` (PK), `first_ts_us` i64, `last_ts_us` i64, `record_count` u64, `updated_at` timestamp | one row per run, kept by `publish_batch` |
| `record` | `rkey` (PK) string, `run_id`, `writer`, `seq` u64, `ts` string, `ts_us` i64, `kind`, `channel`, `has_thumb` bool, `payload` string | `rkey` = `"<run_id>\|<writer>\|<seq>"`. Indexes: `by_run_ts (run_id, ts_us)`, `by_run_kind (run_id, kind)` |
| `thumb` | `rkey` (PK), `run_id` (indexed), `mime`, `bytes` u8[] | separate and lazy: subscribe with `WHERE rkey = '..'` |
| `publisher_cursor` | `cursor_key` (PK, `"<run_id>\|<writer>"`), `run_id`, `writer`, `last_seq` u64 | highest seq published; lets a publisher that lost its local state know where it stopped |
| `control_request` | `id` u64 (PK, auto), `run_id`, `action`, `text`, `requested_by` identity, `requested_at` timestamp, `status`, `note`, `handled_at` timestamp or null | Ops requests, see below |
| `role` | `identity` (PK), `role` | private |

Timestamps in HTTP SQL results come back as `[micros]`.

## Ops controls (for the runner and the Ops view)

- `request_control(run_id: string, action: string, text: string)` needs role `ops`. `action` is `pause`, `resume`, `stop` or `steer`; `steer` needs non-empty `text` (the message for the agent); other actions store `text = ""`. Inserts a `control_request` with `status = "pending"`.
- `ack_control(id: u64, status: string, note: string)` needs role `runner`. `status` is `done` or `rejected`. Sets `handled_at`.
- The module only records requests and enforces nothing on the runner. The runner polls: `SELECT * FROM control_request WHERE run_id = '<run>' AND status = 'pending'` (HTTP SQL, or a subscription), acts, then calls `ack_control`. The runner's identity needs role `runner`.

## Query shapes for the UI (subscriptions use the same SQL, no ORDER BY, LIMIT or COUNT; sort in the client)

- Runs list: `SELECT * FROM run`
- Replay a time range: `SELECT * FROM record WHERE run_id = 'R' AND ts_us >= A AND ts_us < B`
- One record type: `SELECT * FROM record WHERE run_id = 'R' AND kind = 'milestone'`
- Live: subscribe to `SELECT * FROM record WHERE run_id = 'R' AND ts_us >= <now minus a window>`; inserts stream in. Order client-side by `ts_us`, then `writer`, then `seq`.
- A thumbnail: `SELECT * FROM thumb WHERE rkey = 'R|toolserver|123'`
- Control requests of a run: `SELECT * FROM control_request WHERE run_id = 'R'`
