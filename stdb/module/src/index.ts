// SpacetimeDB module for the harness UI backend (docs/research/spacetimedb.md).
//
// Stores event-log records (docs/interfaces/event-log.md) generically: the module knows the
// envelope fields (run_id, writer, seq, ts, type) and keeps the full record as JSON in `payload`,
// so new record types and fields need no schema change. No host or game concepts live here.
//
// Tables
//   run              one row per run_id (derived from published records)
//   record           one row per (run_id, writer, seq); upserted, so re-publishing is safe
//   thumb            screenshot thumbnails, one row per record key, fetched lazily by key
//   publisher_cursor highest seq the publisher has sent per (run_id, writer)
//   control_request  Ops requests (pause/resume/stop/steer) that the runner polls and acknowledges
//   role             PRIVATE: identity -> role (owner, publisher, runner, ops)
import { schema, table, t, SenderError } from 'spacetimedb/server';
import type { InferSchema, ReducerCtx } from 'spacetimedb/server';

const run = table(
  { name: 'run', public: true },
  {
    run_id: t.string().primaryKey(),
    first_ts_us: t.i64(), // earliest record ts, microseconds since the Unix epoch
    last_ts_us: t.i64(), // latest record ts
    record_count: t.u64(),
    updated_at: t.timestamp(), // server time of the last batch
  }
);

const record = table(
  {
    name: 'record',
    public: true,
    indexes: [
      // Replay a time range of one run: WHERE run_id = '..' AND ts_us >= A AND ts_us < B
      { accessor: 'by_run_ts', algorithm: 'btree', columns: ['run_id', 'ts_us'] },
      // One record type of one run (milestones, hook_loaded, ...)
      { accessor: 'by_run_kind', algorithm: 'btree', columns: ['run_id', 'kind'] },
    ],
  },
  {
    rkey: t.string().primaryKey(), // "<run_id>|<writer>|<seq>"
    run_id: t.string(),
    writer: t.string(),
    seq: t.u64(),
    ts: t.string(), // the record's own ISO timestamp, kept verbatim
    ts_us: t.i64(), // the same instant in microseconds, for range queries and ordering
    kind: t.string(), // the record's `type` (named kind: `type` is awkward in SQL)
    channel: t.string(), // tool_call channel, or '' when the record has none
    has_thumb: t.bool(), // the record names an artifact; the thumb row may still be absent (sampled)
    payload: t.string(), // the complete record as JSON text
  }
);

const thumb = table(
  { name: 'thumb', public: true },
  {
    rkey: t.string().primaryKey(), // same key as the record it belongs to
    run_id: t.string().index('btree'),
    mime: t.string(),
    bytes: t.array(t.u8()),
  }
);

const publisherCursor = table(
  { name: 'publisher_cursor', public: true },
  {
    cursor_key: t.string().primaryKey(), // "<run_id>|<writer>"
    run_id: t.string(),
    writer: t.string(),
    last_seq: t.u64(),
  }
);

const controlRequest = table(
  {
    name: 'control_request',
    public: true,
    indexes: [{ accessor: 'by_run_status', algorithm: 'btree', columns: ['run_id', 'status'] }],
  },
  {
    id: t.u64().primaryKey().autoInc(),
    run_id: t.string(),
    action: t.string(), // pause | resume | stop | steer
    text: t.string(), // steer: the message for the agent; otherwise ''
    requested_by: t.identity(),
    requested_at: t.timestamp(),
    status: t.string(), // pending | done | rejected
    note: t.string(), // runner's note when it acknowledges
    handled_at: t.option(t.timestamp()),
  }
);

const role = table(
  { name: 'role' }, // private: never sent to clients
  {
    identity: t.identity().primaryKey(),
    role: t.string(),
  }
);

const spacetimedb = schema({ run, record, thumb, publisherCursor, controlRequest, role });
export default spacetimedb;

type Ctx = ReducerCtx<InferSchema<typeof spacetimedb>>;

const ROLES = ['owner', 'publisher', 'runner', 'ops'];
const ACTIONS = ['pause', 'resume', 'stop', 'steer'];

function hasRole(ctx: Ctx, allowed: string[]): boolean {
  const row = ctx.db.role.identity.find(ctx.sender);
  return row !== null && (row.role === 'owner' || allowed.includes(row.role));
}

function require(ctx: Ctx, allowed: string[]): void {
  if (!hasRole(ctx, allowed)) throw new SenderError('not permitted');
}

// The identity that publishes the module becomes the owner and can grant the other roles.
export const init = spacetimedb.init(ctx => {
  ctx.db.role.insert({ identity: ctx.sender, role: 'owner' });
});

// ---- role administration (owner only) ----

export const grantRole = spacetimedb.reducer(
  { who: t.identity(), role: t.string() },
  (ctx, { who, role: name }) => {
    const me = ctx.db.role.identity.find(ctx.sender);
    if (!me || me.role !== 'owner') throw new SenderError('owner only');
    if (!ROLES.includes(name)) throw new SenderError(`unknown role ${name}`);
    const existing = ctx.db.role.identity.find(who);
    if (existing) ctx.db.role.identity.update({ ...existing, role: name });
    else ctx.db.role.insert({ identity: who, role: name });
  }
);

export const revokeRole = spacetimedb.reducer({ who: t.identity() }, (ctx, { who }) => {
  const me = ctx.db.role.identity.find(ctx.sender);
  if (!me || me.role !== 'owner') throw new SenderError('owner only');
  if (who.equals(ctx.sender)) throw new SenderError('the owner cannot revoke itself');
  ctx.db.role.identity.delete(who);
});

// ---- publishing (publisher role) ----

const RecordIn = t.object('RecordIn', {
  run_id: t.string(),
  writer: t.string(),
  seq: t.u64(),
  ts: t.string(),
  ts_us: t.i64(),
  kind: t.string(),
  channel: t.string(),
  has_thumb: t.bool(),
  payload: t.string(),
});

const ThumbIn = t.object('ThumbIn', {
  rkey: t.string(),
  run_id: t.string(),
  mime: t.string(),
  bytes: t.array(t.u8()),
});

// Upsert a batch of records. Idempotent: the same (run_id, writer, seq) replaces its row.
export const publishBatch = spacetimedb.reducer(
  { records: t.array(RecordIn) },
  (ctx, { records }) => {
    require(ctx, ['publisher']);
    const runs = new Map<string, { first: bigint; last: bigint; added: bigint }>();
    const cursors = new Map<string, { run_id: string; writer: string; seq: bigint }>();
    for (const r of records) {
      const rkey = `${r.run_id}|${r.writer}|${r.seq}`;
      const row = {
        rkey,
        run_id: r.run_id,
        writer: r.writer,
        seq: r.seq,
        ts: r.ts,
        ts_us: r.ts_us,
        kind: r.kind,
        channel: r.channel,
        has_thumb: r.has_thumb,
        payload: r.payload,
      };
      const existing = ctx.db.record.rkey.find(rkey);
      let added = 0n;
      if (existing) ctx.db.record.rkey.update(row);
      else {
        ctx.db.record.insert(row);
        added = 1n;
      }
      const agg = runs.get(r.run_id);
      if (!agg) runs.set(r.run_id, { first: r.ts_us, last: r.ts_us, added });
      else {
        if (r.ts_us < agg.first) agg.first = r.ts_us;
        if (r.ts_us > agg.last) agg.last = r.ts_us;
        agg.added += added;
      }
      const ck = `${r.run_id}|${r.writer}`;
      const c = cursors.get(ck);
      if (!c || r.seq > c.seq) cursors.set(ck, { run_id: r.run_id, writer: r.writer, seq: r.seq });
    }
    for (const [runId, agg] of runs) {
      const existing = ctx.db.run.run_id.find(runId);
      if (existing) {
        ctx.db.run.run_id.update({
          ...existing,
          first_ts_us: agg.first < existing.first_ts_us ? agg.first : existing.first_ts_us,
          last_ts_us: agg.last > existing.last_ts_us ? agg.last : existing.last_ts_us,
          record_count: existing.record_count + agg.added,
          updated_at: ctx.timestamp,
        });
      } else {
        ctx.db.run.insert({
          run_id: runId,
          first_ts_us: agg.first,
          last_ts_us: agg.last,
          record_count: agg.added,
          updated_at: ctx.timestamp,
        });
      }
    }
    for (const [cursorKey, c] of cursors) {
      const existing = ctx.db.publisherCursor.cursor_key.find(cursorKey);
      if (!existing) {
        ctx.db.publisherCursor.insert({
          cursor_key: cursorKey,
          run_id: c.run_id,
          writer: c.writer,
          last_seq: c.seq,
        });
      } else if (c.seq > existing.last_seq) {
        ctx.db.publisherCursor.cursor_key.update({ ...existing, last_seq: c.seq });
      }
    }
  }
);

// Upsert thumbnails. Kept apart from records so clients fetch them lazily by key.
export const publishThumbs = spacetimedb.reducer(
  { thumbs: t.array(ThumbIn) },
  (ctx, { thumbs }) => {
    require(ctx, ['publisher']);
    for (const th of thumbs) {
      const row = { rkey: th.rkey, run_id: th.run_id, mime: th.mime, bytes: th.bytes };
      if (ctx.db.thumb.rkey.find(th.rkey)) ctx.db.thumb.rkey.update(row);
      else ctx.db.thumb.insert(row);
    }
  }
);

// ---- Ops controls (ops role requests; runner role acknowledges) ----
// The module only records requests. The runner polls `control_request` for its run
// (WHERE run_id = '..' AND status = 'pending'), acts, then calls ackControl.

export const requestControl = spacetimedb.reducer(
  { run_id: t.string(), action: t.string(), text: t.string() },
  (ctx, { run_id, action, text }) => {
    require(ctx, ['ops']);
    if (!ACTIONS.includes(action)) throw new SenderError(`unknown action ${action}`);
    if (action === 'steer' && text.trim() === '') throw new SenderError('steer needs text');
    ctx.db.controlRequest.insert({
      id: 0n,
      run_id,
      action,
      text: action === 'steer' ? text : '',
      requested_by: ctx.sender,
      requested_at: ctx.timestamp,
      status: 'pending',
      note: '',
      handled_at: undefined,
    });
  }
);

export const ackControl = spacetimedb.reducer(
  { id: t.u64(), status: t.string(), note: t.string() },
  (ctx, { id, status, note }) => {
    require(ctx, ['runner']);
    if (status !== 'done' && status !== 'rejected') throw new SenderError('status must be done or rejected');
    const req = ctx.db.controlRequest.id.find(id);
    if (!req) throw new SenderError('no such request');
    if (req.status !== 'pending') throw new SenderError('request already handled');
    ctx.db.controlRequest.id.update({ ...req, status, note, handled_at: ctx.timestamp });
  }
);
