import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { CheckpointStore } from '../src/checkpoint.ts';
import { Publisher, splitBatches } from '../src/publisher.ts';
import { FatalSinkError, MemorySink, SpacetimeSink, type Sink } from '../src/sink.ts';
import { Tailer } from '../src/tailer.ts';
import { copyFixture, line, lineCount, rec, tmp } from './helpers.ts';

const mk = (run: string, sink: Sink, caps = {}, extra: any = {}) => {
  const state = extra.state ?? tmp('state-');
  return new Publisher({
    runDir: run, sink, caps, sleep: async () => {}, backoffMs: 1,
    tailer: new Tailer(run, new CheckpointStore(state, 'r1'), { fromStart: true }), ...extra,
  });
};

test('splitBatches honors record and byte caps', () => {
  const items = Array.from({ length: 10 }, () => ({ size: 100 }));
  assert.deepEqual(splitBatches(items, 4, 10_000).map(b => b.length), [4, 4, 2]);
  assert.deepEqual(splitBatches(items, 100, 350).map(b => b.length), [3, 3, 3, 1]);
  assert.deepEqual(splitBatches([{ size: 999 }, { size: 1 }], 10, 100).map(b => b.length), [1, 1]); // oversized goes alone
});

test('fixture replay publishes every line once, in capped batches', async () => {
  const run = copyFixture();
  const sink = new MemorySink();
  const pub = mk(run, sink, { maxRecords: 25 });
  await pub.drain();
  assert.equal(sink.records.size, lineCount(run));
  assert.ok(sink.batchSizes.every(n => n <= 25), String(sink.batchSizes));
  assert.equal(sink.batchSizes.reduce((a, b) => a + b, 0), lineCount(run));
  const sample = [...sink.records.values()].find(r => r.has_thumb)!;
  assert.equal(JSON.parse(sample.payload).seq, sample.seq);
  assert.equal(sample.ts_us, Date.parse(sample.ts) * 1000);
});

test('republishing is safe: same keys, no growth', async () => {
  const run = copyFixture();
  const sink = new MemorySink();
  await mk(run, sink).drain();
  const n = sink.records.size;
  await mk(run, sink).drain(); // a second publisher replays the same run
  assert.equal(sink.records.size, n);
});

test('restart resumes from the checkpoint without duplicates', async () => {
  const run = tmp(); const state = tmp('state-'); const f = path.join(run, 'events.jsonl');
  fs.writeFileSync(f, [1, 2, 3].map(s => line(rec('toolserver', s))).join(''));
  const sink1 = new MemorySink();
  await mk(run, sink1, {}, { state }).drain();
  assert.equal(sink1.records.size, 3);
  fs.appendFileSync(f, [4, 5].map(s => line(rec('toolserver', s))).join(''));
  const sink2 = new MemorySink();
  const p2 = new Publisher({ runDir: run, sink: sink2, sleep: async () => {},
    tailer: new Tailer(run, new CheckpointStore(state, 'r1')) }); // resume, not from start
  await p2.drain();
  assert.deepEqual([...sink2.records.values()].map(r => r.seq), [4, 5]);
});

test('connection errors are retried with backoff; the offset is committed only after success', async () => {
  const run = tmp(); const state = tmp('state-');
  fs.writeFileSync(path.join(run, 'events.jsonl'), [1, 2].map(s => line(rec('toolserver', s))).join(''));
  const sink = new MemorySink();
  let fails = 3;
  const flaky: Sink = {
    publishBatch: async r => { if (fails-- > 0) throw new Error('ECONNREFUSED'); return sink.publishBatch(r); },
    publishThumbs: r => sink.publishThumbs(r),
  };
  const sleeps: number[] = [];
  const pub = mk(run, flaky, {}, { state, sleep: async (ms: number) => { sleeps.push(ms); } });
  await pub.drain();
  assert.equal(sink.records.size, 2);
  assert.deepEqual(sleeps, [1, 2, 4]);
  assert.equal(new CheckpointStore(state, 'r1').load()!.files['events.jsonl'].offset, fs.statSync(path.join(run, 'events.jsonl')).size);
});

test('a failing sink leaves the checkpoint untouched', async () => {
  const run = tmp(); const state = tmp('state-');
  fs.writeFileSync(path.join(run, 'events.jsonl'), line(rec('toolserver', 1)));
  const dead: Sink = { publishBatch: async () => { throw new Error('down'); }, publishThumbs: async () => {} };
  const pub = mk(run, dead, {}, { state, maxAttempts: 2 });
  await assert.rejects(pub.drain(), /down/);
  assert.equal(new CheckpointStore(state, 'r1').load(), null);
});

test('permission errors are fatal, not retried', async () => {
  const run = tmp();
  fs.writeFileSync(path.join(run, 'events.jsonl'), line(rec('toolserver', 1)));
  const calls: string[] = [];
  const sink = new SpacetimeSink({ server: 'http://x', module: 'm', token: 't',
    fetchImpl: (async (url: string) => { calls.push(url); return new Response('not permitted', { status: 530 }); }) as any });
  await assert.rejects(mk(run, sink).drain(), FatalSinkError);
  assert.equal(calls.length, 1);
  assert.equal(calls[0], 'http://x/v1/database/m/call/publish_batch');
});

test('SpacetimeSink sends the reducer args as a JSON array with a bearer token', async () => {
  let seen: any;
  const sink = new SpacetimeSink({ server: 'http://x/', module: 'm', token: 'tok',
    fetchImpl: (async (_u: string, init: any) => { seen = init; return new Response('', { status: 200 }); }) as any });
  await sink.publishBatch([{ run_id: 'r', writer: 'w', seq: 1, ts: 't', ts_us: 1, kind: 'k', channel: '', has_thumb: false, payload: '{}' }]);
  assert.equal(seen.headers.authorization, 'Bearer tok');
  assert.equal(JSON.parse(seen.body)[0][0].seq, 1);
});

test('thumbnails upload once per record, only after the record, and only from shots/', async () => {
  const run = copyFixture();
  const order: string[] = [];
  const mem = new MemorySink();
  const sink: Sink = {
    publishBatch: async r => { order.push('batch'); return mem.publishBatch(r); },
    publishThumbs: async r => { order.push('thumbs'); return mem.publishThumbs(r); },
  };
  const pub = mk(run, sink);
  await pub.drain();
  const withArtifact = [...mem.records.values()].filter(r => r.has_thumb);
  assert.ok(withArtifact.length >= 3);
  assert.equal(mem.thumbs.size, withArtifact.filter(r => fs.existsSync(path.join(run, JSON.parse(r.payload).artifact))).length);
  assert.ok(order.indexOf('batch') < order.indexOf('thumbs'));
  const uploaded = pub.thumbsUploaded;
  await pub.drain(); // nothing new: no more uploads
  assert.equal(pub.thumbsUploaded, uploaded);
});

test('thumbnail dedup within a run and sampling (thumbEvery)', async () => {
  const run = tmp(); fs.mkdirSync(path.join(run, 'shots'));
  fs.writeFileSync(path.join(run, 'shots', 'a.jpg'), 'AAA');
  fs.writeFileSync(path.join(run, 'events.jsonl'), [1, 2, 3, 4].map(s => line(rec('toolserver', s, { artifact: 'shots/a.jpg' }))).join(''));
  const m1 = new MemorySink();
  await mk(run, m1, { thumbEvery: 2 }).drain();
  assert.equal(m1.records.size, 4);
  assert.deepEqual([...m1.thumbs.keys()].sort(), ['r1|toolserver|1', 'r1|toolserver|3']);
  // oversized thumbnails are skipped
  const m2 = new MemorySink();
  await mk(run, m2, { maxThumbBytes: 2 }).drain();
  assert.equal(m2.thumbs.size, 0);
});

test('a screenshot that lands after its record is uploaded on a later poll', async () => {
  const run = tmp(); fs.mkdirSync(path.join(run, 'shots'));
  fs.writeFileSync(path.join(run, 'events.jsonl'), line(rec('toolserver', 1, { artifact: 'shots/late.jpg' })));
  const sink = new MemorySink();
  const pub = mk(run, sink);
  await pub.drain();
  assert.equal(sink.thumbs.size, 0);
  fs.writeFileSync(path.join(run, 'shots', 'late.jpg'), 'LATE');
  await pub.pumpThumbs();
  assert.equal(sink.thumbs.size, 1);
});
