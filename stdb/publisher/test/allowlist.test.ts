import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { CheckpointStore } from '../src/checkpoint.ts';
import { Publisher } from '../src/publisher.ts';
import { classify, resolveAllowed } from '../src/safefs.ts';
import { MemorySink } from '../src/sink.ts';
import { Tailer } from '../src/tailer.ts';
import { JPEG, line, rec, tmp } from './helpers.ts';

function dangerousRun(): string {
  const run = tmp('danger-');
  fs.mkdirSync(path.join(run, 'codex-home'));
  fs.mkdirSync(path.join(run, 'host'));
  fs.mkdirSync(path.join(run, 'raw'));
  fs.mkdirSync(path.join(run, 'shots'));
  fs.writeFileSync(path.join(run, 'codex-home', 'auth.json'), '{"token":"SECRET"}');
  fs.writeFileSync(path.join(run, 'host', 'rcon.json'), '{"password":"SECRET"}');
  fs.writeFileSync(path.join(run, 'raw', 'dump.jsonl'), line(rec('toolserver', 1)));
  fs.writeFileSync(path.join(run, 'codex-home', 'sessions.jsonl'), line(rec('toolserver', 1)));
  fs.writeFileSync(path.join(run, 'events.jsonl'), line(rec('toolserver', 1, { artifact: 'shots/a.jpg' })));
  fs.writeFileSync(path.join(run, 'shots', 'a.jpg'), JPEG());
  fs.writeFileSync(path.join(run, 'shots', 'notes.txt'), 'nope');
  fs.mkdirSync(path.join(run, 'live'));
  fs.writeFileSync(path.join(run, 'live', 'latest.jpg'), JPEG('live'));
  fs.writeFileSync(path.join(run, 'live', 'secret.jpg'), JPEG('secret'));
  fs.writeFileSync(path.join(run, 'live', 'notes.json'), '{}');
  return run;
}

test('classify: only top-level *.jsonl and shots/<image>', () => {
  assert.equal(classify('events.jsonl'), 'jsonl');
  assert.equal(classify('live/latest.jpg'), 'image');
  assert.equal(classify('shots/000001.jpg'), 'image');
  for (const bad of ['codex-home/auth.json', 'host/rcon.json', 'raw/x.jsonl', 'auth.json', 'shots/x.json',
    'shots/sub/x.jpg', '../events.jsonl', 'shots/../host/rcon.json', 'events.jsonl.bak', '.jsonl', 'shots/..jpg', 'live/latest.png', 'live/other.jpg', 'live/latest.jpg.bak', 'live/sub/latest.jpg', 'live/latest.jsonl', 'live', 'Live/../host/rcon.json']) {
    assert.equal(classify(bad), null, bad);
  }
});

test('secrets in a real-looking run dir are never opened, even with a hostile record', async () => {
  const run = dangerousRun();
  // a record whose artifact tries to reach the secrets
  fs.appendFileSync(path.join(run, 'events.jsonl'),
    line(rec('toolserver', 2, { artifact: '../codex-home/auth.json' })) +
    line(rec('toolserver', 3, { artifact: 'shots/../host/rcon.json' })) +
    line(rec('toolserver', 4, { artifact: '/etc/passwd' })));
  const opened: string[] = [];
  const orig = fs.promises.open;
  const origOpenSync = fs.openSync;
  const origRead = fs.promises.readFile;
  const origReadSync = fs.readFileSync;
  (fs.promises as any).open = (p: any, ...a: any[]) => { opened.push(String(p)); return (orig as any)(p, ...a); };
  (fs as any).openSync = (p: any, ...a: any[]) => { opened.push(String(p)); return (origOpenSync as any)(p, ...a); };
  (fs.promises as any).readFile = (p: any, ...a: any[]) => { opened.push(String(p)); return (origRead as any)(p, ...a); };
  (fs as any).readFileSync = (p: any, ...a: any[]) => { opened.push(String(p)); return (origReadSync as any)(p, ...a); };
  try {
    const sink = new MemorySink();
    const state = tmp('state-');
    const pub = new Publisher({ runDir: run, sink, tailer: new Tailer(run, new CheckpointStore(state, 'r1'), { fromStart: true }) });
    await pub.drain();
    assert.equal(sink.records.size, 4);
    assert.equal(sink.thumbs.size, 2); // shots/a.jpg and the live frame
  } finally {
    (fs.promises as any).open = orig; (fs as any).openSync = origOpenSync;
    (fs.promises as any).readFile = origRead; (fs as any).readFileSync = origReadSync;
  }
  const real = fs.realpathSync(run);
  const ours = opened.filter(p => p.startsWith(run) || p.startsWith(real));
  assert.ok(ours.length > 0, 'the spy saw the opens');
  for (const p of ours) {
    assert.match(path.relative(real, fs.realpathSync(p)).replace(/\\/g, '/'), /^(?:[^/]+\.jsonl|shots\/[^/]+\.jpg|live\/latest\.jpg)$/, p);
    assert.doesNotMatch(p, /secret\.jpg|notes\.json|auth\.json|rcon\.json|codex-home|[\\/]host[\\/]|[\\/]raw[\\/]|sessions\.jsonl|notes\.txt/);
  }
});

test('symlinks that escape the run dir or point at non-allowlisted files are rejected', async (t) => {
  const run = dangerousRun();
  const outside = tmp('outside-');
  fs.writeFileSync(path.join(outside, 'x.jsonl'), line(rec('toolserver', 9)));
  try {
    fs.symlinkSync(path.join(outside, 'x.jsonl'), path.join(run, 'evil.jsonl'));
    fs.symlinkSync(path.join(run, 'codex-home', 'auth.json'), path.join(run, 'auth.jsonl'));
    fs.symlinkSync(path.join(run, 'host', 'rcon.json'), path.join(run, 'shots', 'rcon.png'));
    fs.symlinkSync(path.join(run, 'events.jsonl'), path.join(run, 'alias.jsonl'));
  } catch { t.skip('symlinks unavailable'); return; }
  assert.equal(await resolveAllowed(run, 'evil.jsonl'), null);
  assert.equal(await resolveAllowed(run, 'auth.jsonl'), null);
  assert.equal(await resolveAllowed(run, 'shots/rcon.png'), null);
  assert.ok(await resolveAllowed(run, 'alias.jsonl')); // in-dir symlink to an allowed file is fine
  assert.ok(await resolveAllowed(run, 'events.jsonl'));
  assert.equal(await resolveAllowed(run, 'codex-home/auth.json'), null);
  assert.equal(await resolveAllowed(run, 'host/rcon.json'), null);
});

test('a directory link (junction on Windows) at shots/ or live/ pointing outside is rejected', async (t) => {
  const run = tmp('junc-'); const outside = tmp('outside-');
  fs.writeFileSync(path.join(outside, 'a.jpg'), JPEG('o')); fs.writeFileSync(path.join(outside, 'latest.jpg'), JPEG('o'));
  try {
    fs.symlinkSync(outside, path.join(run, 'shots'), 'junction');
    fs.symlinkSync(outside, path.join(run, 'live'), 'junction');
  } catch { t.skip('links unavailable'); return; }
  assert.equal(await resolveAllowed(run, 'shots/a.jpg'), null);
  assert.equal(await resolveAllowed(run, 'live/latest.jpg'), null);
});
