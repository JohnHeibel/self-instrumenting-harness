import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { CheckpointStore } from '../src/checkpoint.ts';
import { Publisher } from '../src/publisher.ts';
import { FatalSinkError, MemorySink, PayloadTooLargeError, type Sink } from '../src/sink.ts';
import { Tailer } from '../src/tailer.ts';
import { prepare, truncateHookLoaded } from '../src/wire.ts';
import { JPEG, line, rec, tmp } from './helpers.ts';

const mk = (run: string, sink: Sink, o: any = {}) => {
  const state = o.state ?? tmp('state-');
  const store = new CheckpointStore(state, 'r1');
  return {
    store,
    pub: new Publisher({ runDir: run, sink, sleep: async () => {}, backoffMs: 1, log: o.log,
      tailer: new Tailer(run, store, { fromStart: o.fromStart, liveOnly: o.liveOnly }), ...o.pub }),
  };
};

test('no checkpoint and no flags: the full history is published (live-only is opt-in)', async () => {
  const run = tmp(); const f = path.join(run, 'events.jsonl');
  fs.writeFileSync(f, [1, 2, 3].map(s => line(rec('toolserver', s))).join(''));
  const sink = new MemorySink();
  await mk(run, sink).pub.drain();
  assert.equal(sink.records.size, 3);
  const live = new MemorySink();
  await mk(run, live, { liveOnly: true }).pub.drain();
  assert.equal(live.records.size, 0);
});

test('size is measured after JSON encoding (escapes and multibyte)', () => {
  const text = '"\\\n✓'.repeat(1000);
  const item: any = { raw: JSON.stringify(rec('toolserver', 1, { text })), record: rec('toolserver', 1, { text }) };
  const { wire, size } = prepare(item);
  assert.equal(size, Buffer.byteLength(JSON.stringify(wire)) + 1);
  assert.ok(size > item.raw.length * 1.5); // the payload string is escaped a second time
});

test('hook_loaded contents above ~1 MB are truncated and marked, source_ref kept', () => {
  const files = Array.from({ length: 40 }, (_, i) => ({ path: `f${i}.lua`, content: 'é'.repeat(40_000) }));
  const r = rec('toolserver', 1, { type: 'hook_loaded', source_ref: 'extensions/x/v1', files });
  const item: any = { raw: JSON.stringify(r), record: r };
  const { wire, size } = prepare(item);
  const p = JSON.parse(wire.payload);
  assert.equal(p.truncated, true);
  assert.equal(p.source_ref, 'extensions/x/v1');
  assert.ok(p.files.every((f: any) => f.truncated === true && f.path));
  assert.ok(size < 1.6 * 1024 * 1024, String(size));
  // small ones are untouched
  assert.equal(truncateHookLoaded(rec('toolserver', 2, { type: 'hook_loaded', files: [{ path: 'a', content: 'x' }] })), null);
});

test('an oversized non-hook record is skipped, logged, and the checkpoint moves past it', async () => {
  const run = tmp(); const state = tmp('state-'); const f = path.join(run, 'events.jsonl');
  fs.writeFileSync(f, line(rec('toolserver', 1)) + line(rec('toolserver', 2, { text: 'x'.repeat(5000) })) + line(rec('toolserver', 3)));
  const sink = new MemorySink(); const logs: string[] = [];
  const { pub } = mk(run, sink, { state, fromStart: true, log: (m: string) => logs.push(m), pub: { caps: { maxRecordBytes: 2000 } } });
  await pub.drain();
  assert.deepEqual([...sink.records.values()].map(r => r.seq), [1, 3]);
  assert.ok(logs.some(m => /skipping toolserver#2/.test(m)));
  assert.equal(new CheckpointStore(state, 'r1').load()!.files['events.jsonl'].offset, fs.statSync(f).size);
});

test('HTTP 413: the batch is bisected, the offending record skipped, checkpoint advances', async () => {
  const run = tmp(); const state = tmp('state-'); const f = path.join(run, 'events.jsonl');
  fs.writeFileSync(f, [1, 2, 3, 4, 5].map(s => line(rec('toolserver', s))).join(''));
  const mem = new MemorySink(); let calls = 0;
  const sink: Sink = {
    publishBatch: async r => { calls++; if (r.some(x => x.seq === 3)) throw new PayloadTooLargeError('413'); return mem.publishBatch(r); },
    publishThumbs: r => mem.publishThumbs(r),
  };
  const { pub } = mk(run, sink, { state, fromStart: true });
  await pub.drain();
  assert.deepEqual([...mem.records.values()].map(r => r.seq).sort(), [1, 2, 4, 5]);
  assert.equal(pub.skipped, 1);
  assert.equal(new CheckpointStore(state, 'r1').load()!.files['events.jsonl'].offset, fs.statSync(f).size);
  assert.ok(calls < 12);
});

test('follow mode: a 530 "not permitted" is backed off and retried until it works', async () => {
  const run = tmp();
  fs.writeFileSync(path.join(run, 'events.jsonl'), line(rec('toolserver', 1)));
  const mem = new MemorySink(); let denied = 3; const sleeps: number[] = [];
  const sink: Sink = {
    publishBatch: async r => { if (denied-- > 0) { const e = new FatalSinkError('530 not permitted'); e.permission = true; throw e; } return mem.publishBatch(r); },
    publishThumbs: r => mem.publishThumbs(r),
  };
  const { pub } = mk(run, sink, { fromStart: true, pub: { keepGoing: true, sleep: async (ms: number) => { sleeps.push(ms); } } });
  await pub.drain();
  assert.equal(mem.records.size, 1);
  assert.equal(sleeps.length, 3);
  assert.ok(sleeps[0] >= 5000);
});

test('follow mode: a bad record rejected by the reducer is skipped, not fatal', async () => {
  const run = tmp();
  fs.writeFileSync(path.join(run, 'events.jsonl'), [1, 2].map(s => line(rec('toolserver', s))).join(''));
  const mem = new MemorySink();
  const sink: Sink = {
    publishBatch: async r => { if (r.some(x => x.seq === 1)) throw new FatalSinkError('530 boom'); return mem.publishBatch(r); },
    publishThumbs: r => mem.publishThumbs(r),
  };
  const { pub } = mk(run, sink, { fromStart: true, pub: { keepGoing: true } });
  await pub.drain();
  assert.deepEqual([...mem.records.values()].map(r => r.seq), [2]);
});

test('checkpoint save retries a failing rename, then warns instead of throwing', () => {
  const state = tmp('state-'); const store = new CheckpointStore(state, 'r1');
  const warns: string[] = []; store.onWarn = m => warns.push(m);
  const orig = fs.renameSync; let fail = 2;
  (fs as any).renameSync = (...a: any[]) => { if (fail-- > 0) { const e: any = new Error('EPERM: operation not permitted'); e.code = 'EPERM'; throw e; } return (orig as any)(...a); };
  try {
    assert.equal(store.save({ 'events.jsonl': { offset: 5, ino: '1', headLen: 0, head: '' } }), true);
    assert.equal(store.load()!.files['events.jsonl'].offset, 5);
    assert.equal(warns.length, 0);
    fail = 100; // rename never works: falls back to writing in place
    assert.equal(store.save({ 'events.jsonl': { offset: 9, ino: '1', headLen: 0, head: '' } }), true);
    assert.equal(store.load()!.files['events.jsonl'].offset, 9);
    assert.equal(warns.length, 1);
  } finally { (fs as any).renameSync = orig; }
  // unwritable state dir: a warning, never an exception
  const blocked = path.join(tmp(), 'file'); fs.writeFileSync(blocked, 'x');
  const bad = new CheckpointStore(path.join(blocked, 'sub'), 'r1'); const w2: string[] = []; bad.onWarn = m => w2.push(m);
  assert.equal(bad.save({}), false);
  assert.equal(w2.length, 1);
});

test('on resume, the last records with screenshots are re-queued for upload', async () => {
  const run = tmp(); const state = tmp('state-'); fs.mkdirSync(path.join(run, 'shots'));
  for (const n of ['a', 'b']) fs.writeFileSync(path.join(run, 'shots', `${n}.jpg`), JPEG(n));
  fs.writeFileSync(path.join(run, 'events.jsonl'),
    line(rec('toolserver', 1, { artifact: 'shots/a.jpg' })) + line(rec('toolserver', 2, { artifact: 'shots/b.jpg' })) + line(rec('toolserver', 3)));
  // the first session published records but its thumbnail uploads failed ("crashed" before they landed)
  const ts = new Tailer(run, new CheckpointStore(state, 'r1'), { fromStart: true });
  const p0 = new Publisher({ runDir: run, tailer: ts, sink: { publishBatch: async () => {}, publishThumbs: async () => { throw new Error('down'); } }, maxAttempts: 1, sleep: async () => {} });
  await assert.rejects(p0.drain(), /down/);
  const sink = new MemorySink();
  const p1 = new Publisher({ runDir: run, sink, tailer: new Tailer(run, new CheckpointStore(state, 'r1')), sleep: async () => {} });
  await p1.requeueRecentThumbs();
  await p1.drain();
  assert.deepEqual([...sink.thumbs.keys()].sort(), ['r1|toolserver|1', 'r1|toolserver|2']);
  assert.equal(sink.records.size, 0); // records are not re-sent
});

test('live/latest.jpg is published under one stable key and overwritten when it changes', async () => {
  const run = tmp(); fs.mkdirSync(path.join(run, 'live'));
  fs.writeFileSync(path.join(run, 'events.jsonl'), line(rec('toolserver', 1)));
  fs.writeFileSync(path.join(run, 'live', 'latest.jpg'), JPEG('one'));
  fs.writeFileSync(path.join(run, 'live', 'other.jpg'), JPEG('other'));
  const sink = new MemorySink();
  const { pub } = mk(run, sink, { fromStart: true, pub: { caps: { liveEveryMs: 0 } } });
  await pub.drain();
  assert.deepEqual([...sink.thumbs.keys()], ['r1|live|latest']);
  assert.ok(Buffer.from(sink.thumbs.get('r1|live|latest')!.bytes).equals(JPEG('one')));
  await pub.step(); // unchanged: no second upload
  assert.equal(pub.liveFrames, 1);
  fs.writeFileSync(path.join(run, 'live', 'latest.jpg'), JPEG('two-longer'));
  await pub.step();
  assert.equal(pub.liveFrames, 2);
  assert.equal(sink.thumbs.size, 1);
  assert.ok(Buffer.from(sink.thumbs.get('r1|live|latest')!.bytes).equals(JPEG('two-longer')));
});

test('non-image content at an allowlisted image path is not uploaded', async () => {
  const run = tmp(); fs.mkdirSync(path.join(run, 'live'));
  fs.writeFileSync(path.join(run, 'events.jsonl'), line(rec('toolserver', 1)));
  fs.writeFileSync(path.join(run, 'live', 'latest.jpg'), '{"password":"not an image at all"}');
  const sink = new MemorySink();
  await mk(run, sink, { fromStart: true }).pub.drain();
  assert.equal(sink.thumbs.size, 0);
});
