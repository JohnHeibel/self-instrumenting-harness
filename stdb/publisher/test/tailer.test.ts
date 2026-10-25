import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { CheckpointStore } from '../src/checkpoint.ts';
import { Tailer } from '../src/tailer.ts';
import { line, rec, tmp } from './helpers.ts';

const seqs = (items: { record: any }[]) => items.map(i => i.record.seq);

test('partial trailing lines are held back until complete', async () => {
  const run = tmp(); const f = path.join(run, 'events.jsonl');
  const full = line(rec('toolserver', 2));
  fs.writeFileSync(f, line(rec('toolserver', 1)) + full.slice(0, 20));
  const t = new Tailer(run, null, { fromStart: true });
  assert.deepEqual(seqs(await t.poll()), [1]);
  assert.deepEqual(seqs(await t.poll()), []);
  fs.appendFileSync(f, full.slice(20));
  assert.deepEqual(seqs(await t.poll()), [2]);
});

test('CRLF, blank lines and junk lines are tolerated; multibyte offsets are exact', async () => {
  const run = tmp(); const f = path.join(run, 'events.jsonl');
  const a = rec('toolserver', 1, { text: 'héllo ✓' });
  fs.writeFileSync(f, JSON.stringify(a) + '\r\n\nnot json\n' + JSON.stringify({ run_id: 'r1' }) + '\n' + line(rec('toolserver', 2)));
  const t = new Tailer(run, null, { fromStart: true });
  const items = await t.poll();
  assert.deepEqual(seqs(items), [1, 2]);
  assert.equal(items[1].endOffset, fs.statSync(f).size);
});

test('new files appearing later are followed', async () => {
  const run = tmp();
  fs.writeFileSync(path.join(run, 'events.jsonl'), line(rec('toolserver', 1)));
  const t = new Tailer(run, null, { fromStart: true });
  assert.equal((await t.poll()).length, 1);
  fs.writeFileSync(path.join(run, 'grader.jsonl'), line(rec('grader', 1)));
  assert.deepEqual((await t.poll()).map(i => i.file), ['grader.jsonl']);
});

test('truncation re-reads from the start', async () => {
  const run = tmp(); const f = path.join(run, 'events.jsonl');
  fs.writeFileSync(f, line(rec('toolserver', 1)) + line(rec('toolserver', 2)) + line(rec('toolserver', 3)));
  const t = new Tailer(run, null, { fromStart: true });
  assert.equal((await t.poll()).length, 3);
  fs.writeFileSync(f, line(rec('toolserver', 4)));
  assert.deepEqual(seqs(await t.poll()), [4]);
});

test('rotation to a same-or-larger file is detected', async () => {
  const run = tmp(); const f = path.join(run, 'events.jsonl');
  const store = new CheckpointStore(tmp('state-'), 'r1');
  fs.writeFileSync(f, [1, 2].map(s => line(rec('toolserver', s))).join(''));
  const t = new Tailer(run, store, { fromStart: true });
  t.commit(await t.poll());
  fs.rmSync(f);
  fs.writeFileSync(f, [11, 12, 13].map(s => line(rec('toolserver', s, { label: 'longer' }))).join(''));
  assert.deepEqual(seqs(await t.poll()), [11, 12, 13]);
});

test('checkpoint resume: no duplicates, picks up appended lines, survives restart', async () => {
  const run = tmp(); const f = path.join(run, 'events.jsonl');
  const state = tmp('state-');
  fs.writeFileSync(f, [1, 2, 3].map(s => line(rec('toolserver', s))).join(''));
  const t1 = new Tailer(run, new CheckpointStore(state, 'r1'), { liveOnly: true });
  // --live-only without a checkpoint skips existing lines
  assert.deepEqual(seqs(await t1.poll()), []);
  fs.appendFileSync(f, line(rec('toolserver', 4)) + line(rec('toolserver', 5)));
  const got = await t1.poll();
  assert.deepEqual(seqs(got), [4, 5]);
  t1.commit(got.slice(0, 1)); // only 4 was published before the "crash"
  const t2 = new Tailer(run, new CheckpointStore(state, 'r1'));
  assert.deepEqual(seqs(await t2.poll()), [5]);
});

test('--from-start ignores the checkpoint', async () => {
  const run = tmp(); const f = path.join(run, 'events.jsonl');
  const state = tmp('state-');
  fs.writeFileSync(f, [1, 2].map(s => line(rec('toolserver', s))).join(''));
  const t1 = new Tailer(run, new CheckpointStore(state, 'r1'), { fromStart: true });
  t1.commit(await t1.poll());
  const t2 = new Tailer(run, new CheckpointStore(state, 'r1'), { fromStart: true });
  assert.deepEqual(seqs(await t2.poll()), [1, 2]);
  const t3 = new Tailer(run, new CheckpointStore(state, 'r1'));
  assert.deepEqual(seqs(await t3.poll()), []);
});

test('a file the checkpoint does not know starts at byte 0', async () => {
  const run = tmp(); const state = tmp('state-');
  fs.writeFileSync(path.join(run, 'events.jsonl'), line(rec('toolserver', 1)));
  const t1 = new Tailer(run, new CheckpointStore(state, 'r1'), { fromStart: true });
  t1.commit(await t1.poll());
  fs.writeFileSync(path.join(run, 'runner.jsonl'), line(rec('runner', 1)));
  const t2 = new Tailer(run, new CheckpointStore(state, 'r1'));
  assert.deepEqual((await t2.poll()).map(i => i.file), ['runner.jsonl']);
});

test('lines longer than the read chunk are still read whole', async () => {
  const run = tmp();
  fs.writeFileSync(path.join(run, 'events.jsonl'), line(rec('toolserver', 1, { text: 'x'.repeat(5000) })) + line(rec('toolserver', 2)));
  const t = new Tailer(run, null, { fromStart: true, chunkBytes: 256 });
  assert.deepEqual(seqs(await t.poll()), [1, 2]);
});
