#!/usr/bin/env node
// End-to-end check against a LOCAL SpacetimeDB instance. No cloud, no login to maincloud.
//   node stdb/e2e/run.mjs [run-dir ...]      (default: ../self-instrumenting-harness/runs/fake-factorio and fake-spreadsheet
//                                              next to this repo's main checkout, or pass paths)
// Starts its own `spacetime start` on 127.0.0.1:3917 with a throwaway data dir, publishes the module,
// runs the publisher on each run dir, and compares what is in the database with the JSONL on disk.
import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const stdb = path.resolve(here, '..');
const PORT = process.env.E2E_PORT ?? '3917';
const BASE = `http://127.0.0.1:${PORT}`;
const DB = 'harness-events-e2e';
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'stdb-e2e-'));
process.env.PATH = [path.join(os.homedir(), '.local', 'bin'), process.env.PATH].join(path.delimiter);
const WIN = process.platform === 'win32';

const runDirs = process.argv.slice(2);
if (runDirs.length === 0) {
  // Fake runs: $HARNESS_RUNS, else <repo>/runs, else the main checkout's runs/ (this may be a git worktree).
  const common = spawnSync('git', ['rev-parse', '--path-format=absolute', '--git-common-dir'], { cwd: here, encoding: 'utf8' }).stdout?.trim();
  const candidates = [process.env.HARNESS_RUNS, path.resolve(stdb, '..', 'runs'), common && path.resolve(common, '..', 'runs')].filter(Boolean);
  const runsRoot = candidates.find((d) => fs.existsSync(path.join(d, 'fake-factorio')));
  if (!runsRoot) throw new Error(`fake runs not found; set HARNESS_RUNS to the runs/ directory (looked in ${candidates.join(', ')})`);
  runDirs.push(path.join(runsRoot, 'fake-factorio'), path.join(runsRoot, 'fake-spreadsheet'));
}

let server;
const results = [];
const ok = (msg) => { results.push(msg); console.log('ok  ' + msg); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function sh(cmd, args, opts = {}) {
  const r = spawnSync(cmd, args, { encoding: 'utf8', ...opts });
  if (r.status !== 0 && !opts.allowFail) throw new Error(`${cmd} ${args.join(' ')} failed:\n${r.stdout}\n${r.stderr}`);
  return r;
}

async function sql(token, query) {
  const res = await fetch(`${BASE}/v1/database/${DB}/sql`, { method: 'POST', headers: token ? { Authorization: `Bearer ${token}` } : {}, body: query });
  if (!res.ok) throw new Error(`sql ${query}: ${res.status} ${await res.text()}`);
  const [t] = await res.json();
  const cols = t.schema.elements.map((e) => e.name.some);
  return t.rows.map((row) => Object.fromEntries(cols.map((c, i) => [c, row[i]])));
}

async function call(token, reducer, args) {
  const res = await fetch(`${BASE}/v1/database/${DB}/call/${reducer}`, { method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, body: JSON.stringify(args) });
  return { status: res.status, text: await res.text() };
}

async function mint() {
  return (await fetch(`${BASE}/v1/identity`, { method: 'POST' })).json();
}

function publisher(runDir, stateDir, extra = [], opts = {}) {
  const args = ['--import', 'tsx', path.join(stdb, 'publisher/src/cli.ts'), '--run-dir', runDir, '--server', BASE, '--module', DB, '--state-dir', stateDir, ...extra];
  return spawn(process.execPath, args, { cwd: path.join(stdb, 'publisher'), stdio: ['ignore', 'pipe', 'pipe'], ...opts });
}
function runPublisher(runDir, stateDir, extra) {
  return new Promise((resolve, reject) => {
    const p = publisher(runDir, stateDir, extra);
    let out = '';
    p.stdout.on('data', (d) => (out += d));
    p.stderr.on('data', (d) => (out += d));
    p.on('exit', (code) => (code === 0 ? resolve(out) : reject(new Error(`publisher exit ${code}\n${out}`))));
  });
}

function readJsonl(dir) {
  const recs = [];
  for (const f of fs.readdirSync(dir).filter((n) => n.endsWith('.jsonl')).sort()) {
    for (const line of fs.readFileSync(path.join(dir, f), 'utf8').split('\n')) if (line.trim()) recs.push({ rec: JSON.parse(line), line });
  }
  return recs;
}

// has_thumb means an image under shots/ (checkpoint records carry a tarball artifact)
const isShot = (rec) => typeof rec.artifact === 'string' && /^shots\/[^/]+\.(jpe?g|png|webp|gif)$/i.test(rec.artifact);

function tally(items, key) {
  const m = {};
  for (const i of items) m[key(i)] = (m[key(i)] ?? 0) + 1;
  return m;
}

async function verifyRun(token, dir, label) {
  const src = readJsonl(dir);
  const runId = src[0].rec.run_id;
  const rows = await sql(token, `SELECT * FROM record WHERE run_id = '${runId}'`);
  assert.equal(rows.length, src.length, `${label}: record count`);
  assert.deepEqual(tally(rows, (r) => r.writer), tally(src, (s) => s.rec.writer), `${label}: per-writer counts`);
  assert.deepEqual(tally(rows, (r) => r.kind), tally(src, (s) => s.rec.type), `${label}: per-type counts`);
  const bySeq = new Map(rows.map((r) => [r.rkey, r]));
  for (const { rec } of src) {
    const row = bySeq.get(`${rec.run_id}|${rec.writer}|${rec.seq}`);
    assert.ok(row, `${label}: missing ${rec.writer}#${rec.seq}`);
    assert.deepEqual(JSON.parse(row.payload), rec, `${label}: payload ${rec.writer}#${rec.seq}`);
    assert.equal(row.ts_us, Date.parse(rec.ts) * 1000);
    assert.equal(row.has_thumb, isShot(rec), `${label}: has_thumb ${rec.writer}#${rec.seq}`);
  }
  const runRow = (await sql(token, `SELECT * FROM run WHERE run_id = '${runId}'`))[0];
  assert.equal(Number(runRow.record_count), src.length, `${label}: run.record_count`);
  const withArt = src.filter((s) => isShot(s.rec));
  const thumbs = (await sql(token, `SELECT rkey, run_id, mime FROM thumb WHERE run_id = '${runId}'`)).filter((t) => !t.rkey.endsWith('|live|latest'));
  assert.equal(thumbs.length, withArt.length, `${label}: thumbnail count`);
  // byte-exact check of one thumbnail fetched lazily by key
  const sample = withArt[Math.floor(withArt.length / 2)].rec;
  const [t] = await sql(token, `SELECT * FROM thumb WHERE rkey = '${runId}|${sample.writer}|${sample.seq}'`);
  const onDisk = fs.readFileSync(path.join(dir, sample.artifact));
  assert.ok(Buffer.from(t.bytes, 'hex').equals(onDisk), `${label}: thumbnail bytes`);
  // a time-range query returns only that range
  const mid = src[Math.floor(src.length / 2)].rec;
  const lo = Date.parse(mid.ts) * 1000;
  const range = await sql(token, `SELECT * FROM record WHERE run_id = '${runId}' AND ts_us >= ${lo} AND ts_us < ${lo + 600_000_000}`);
  const expectRange = src.filter((s) => { const u = Date.parse(s.rec.ts) * 1000; return u >= lo && u < lo + 600_000_000; }).length;
  assert.equal(range.length, expectRange, `${label}: range query`);
  return { runId, records: src.length, thumbs: thumbs.length, range: expectRange, types: Object.keys(tally(src, (s) => s.rec.type)).length };
}

async function main() {
  console.log(`tmp: ${tmp}`);
  const busy = await fetch(`${BASE}/v1/ping`).then(() => true, () => false);
  if (busy) throw new Error(`port ${PORT} is already serving something; stop it or set E2E_PORT`);
  server = spawn('spacetime', ['start', '--data-dir', path.join(tmp, 'data'), '--listen-addr', `127.0.0.1:${PORT}`], { stdio: 'ignore', detached: !WIN, windowsHide: true });
  for (let i = 0; i < 60; i++) { try { if ((await fetch(`${BASE}/v1/ping`)).ok) break; } catch {} await sleep(500); }
  sh('spacetime', ['publish', DB, '--module-path', path.join(stdb, 'module'), '--server', BASE, '--yes']);
  ok(`module built and published to a fresh local instance (${BASE})`);

  // roles
  const pub = await mint();
  const stateDir = path.join(tmp, 'state');
  fs.mkdirSync(stateDir, { recursive: true });
  fs.writeFileSync(path.join(stateDir, 'token'), pub.token);
  const denied = await runPublisher(runDirs[0], stateDir, ['--once', '--from-start']).then(() => 'published', () => 'failed');
  const none = (await sql(null, 'SELECT * FROM record')).length;
  assert.equal(none, 0, 'nothing is stored before the role is granted');
  assert.equal(denied, 'failed');
  ok('publisher without the role is refused (exits non-zero), database stays empty');
  sh('spacetime', ['call', DB, 'grant_role', `0x${pub.identity}`, 'publisher', '--server', BASE]);

  const summary = [];
  for (const dir of runDirs) {
    const name = path.basename(dir);
    const st = path.join(tmp, 'state-' + name);
    fs.mkdirSync(st, { recursive: true });
    fs.writeFileSync(path.join(st, 'token'), pub.token);
    const t0 = Date.now();
    await runPublisher(dir, st, ['--once', '--from-start']);
    const secs = ((Date.now() - t0) / 1000).toFixed(1);
    const s = await verifyRun(pub.token, dir, name);
    summary.push({ name, ...s, secs });
    ok(`${name}: ${s.records} records in ${s.types} types, ${s.thumbs} thumbnails match the JSONL (${secs}s); payloads identical; time-range query returns ${s.range}`);
    // idempotent re-publish and checkpoint resume
    await runPublisher(dir, st, ['--once', '--from-start']);
    await verifyRun(pub.token, dir, name + ' (republished)');
    ok(`${name}: --from-start republish changes nothing (counts still equal)`);
    await runPublisher(dir, st, ['--once']);
    await verifyRun(pub.token, dir, name + ' (resumed)');
    ok(`${name}: restart from checkpoint changes nothing`);
  }

  // a fresh publisher with no flags and no checkpoint publishes the full history
  {
    const st = path.join(tmp, 'state-default'); fs.mkdirSync(st, { recursive: true }); fs.writeFileSync(path.join(st, 'token'), pub.token);
    const dir = runDirs[0];
    await runPublisher(dir, st, ['--once']);
    const before = (await sql(pub.token, `SELECT * FROM record WHERE run_id = '${readJsonl(dir)[0].rec.run_id}'`)).length;
    assert.equal(before, readJsonl(dir).length);
    ok('no checkpoint and no flags: the full history was published');
  }

  // live tail with partial lines and a restart, on a copy of the last run
  const srcDir = runDirs[runDirs.length - 1];
  const liveDir = path.join(tmp, 'live-' + path.basename(srcDir));
  fs.mkdirSync(path.join(liveDir, 'shots'), { recursive: true });
  const files = fs.readdirSync(srcDir).filter((n) => n.endsWith('.jsonl'));
  const full = Object.fromEntries(files.map((f) => [f, fs.readFileSync(path.join(srcDir, f), 'utf8')]));
  for (const n of fs.readdirSync(path.join(srcDir, 'shots'))) fs.copyFileSync(path.join(srcDir, 'shots', n), path.join(liveDir, 'shots', n));
  const cut = Object.fromEntries(files.map((f) => [f, Math.floor(full[f].length / 3)])); // mid-line on purpose
  for (const f of files) fs.writeFileSync(path.join(liveDir, f), full[f].slice(0, cut[f]));
  // spectator frame: exactly live/latest.jpg is published; other files under live/ are not
  const liveFrame = fs.readFileSync(path.join(srcDir, 'shots', fs.readdirSync(path.join(srcDir, 'shots'))[0]));
  fs.mkdirSync(path.join(liveDir, 'live'), { recursive: true });
  fs.writeFileSync(path.join(liveDir, 'live/latest.jpg'), liveFrame);
  fs.writeFileSync(path.join(liveDir, 'live/other.jpg'), Buffer.concat([liveFrame, Buffer.from('CANARY-LIVE-OTHER')]));
  // canaries: credentials that must never be read or published
  fs.mkdirSync(path.join(liveDir, 'codex-home'), { recursive: true });
  fs.mkdirSync(path.join(liveDir, 'host'), { recursive: true });
  fs.writeFileSync(path.join(liveDir, 'codex-home/auth.json'), '{"token":"CANARY-CODEX-AUTH-8841"}');
  fs.writeFileSync(path.join(liveDir, 'host/rcon.json'), '{"password":"CANARY-RCON-PW-2207"}');
  const lst = path.join(tmp, 'state-live');
  fs.mkdirSync(lst, { recursive: true });
  fs.writeFileSync(path.join(lst, 'token'), pub.token);
  let p = publisher(liveDir, lst, ['--flush-ms', '200', '--poll-ms', '100']);
  await sleep(2500);
  p.kill('SIGINT'); await new Promise((r) => p.on('exit', r));
  const pos = Object.fromEntries(files.map((f) => [f, cut[f]]));
  const step = (f, to) => { const end = Math.min(full[f].length, Math.max(pos[f], to)); fs.appendFileSync(path.join(liveDir, f), full[f].slice(pos[f], end)); pos[f] = end; };
  for (const f of files) step(f, cut[f] + 5000); // ends mid-line again
  p = publisher(liveDir, lst, ['--flush-ms', '200', '--poll-ms', '100']); // restarted: must resume from checkpoint
  await sleep(2500);
  for (const f of files) step(f, Math.floor(full[f].length * 0.8));
  await sleep(1500);
  for (const f of files) step(f, full[f].length);
  await sleep(3000);
  p.kill('SIGINT'); await new Promise((r) => p.on('exit', r));
  const live = await verifyRun(pub.token, liveDir, 'live tail');
  const lf = await sql(pub.token, `SELECT * FROM thumb WHERE rkey = '${live.runId}|live|latest'`);
  assert.equal(lf.length, 1, 'spectator frame published under its stable key');
  assert.ok(Buffer.from(lf[0].bytes, 'hex').equals(liveFrame), 'spectator frame bytes');
  ok('live/latest.jpg published under <run_id>|live|latest (live/other.jpg ignored)');
  ok(`live tail: appended in pieces (mid-line splits, one restart), ${live.records} records and ${live.thumbs} thumbnails match`);
  const all = JSON.stringify(await sql(pub.token, `SELECT * FROM record WHERE run_id = '${live.runId}'`)).concat(JSON.stringify(await sql(pub.token, 'SELECT run_id, mime FROM thumb')));
  assert.ok(!all.includes('CANARY'), 'canary credentials must not reach the database');
  const thumbBytes = await sql(pub.token, 'SELECT * FROM thumb');
  for (const t of thumbBytes) assert.ok(!Buffer.from(t.bytes, 'hex').includes('CANARY'));
  ok('credential files (codex-home/auth.json, host/rcon.json) beside the run were not published');

  // role table is private; ops controls
  const priv = await fetch(`${BASE}/v1/database/${DB}/sql`, { method: 'POST', headers: { Authorization: `Bearer ${pub.token}` }, body: 'SELECT * FROM role' });
  assert.ok(!priv.ok);
  ok('role table is not readable by clients');
  const ops = await mint(); const runner = await mint(); const rando = await mint();
  sh('spacetime', ['call', DB, 'grant_role', `0x${ops.identity}`, 'ops', '--server', BASE]);
  sh('spacetime', ['call', DB, 'grant_role', `0x${runner.identity}`, 'runner', '--server', BASE]);
  assert.notEqual((await call(rando.token, 'request_control', ['r', 'pause', ''])).status, 200);
  assert.notEqual((await call(ops.token, 'request_control', ['r', 'dance', ''])).status, 200);
  assert.notEqual((await call(ops.token, 'request_control', ['r', 'steer', ' '])).status, 200);
  assert.equal((await call(ops.token, 'request_control', ['r', 'pause', ''])).status, 200);
  assert.equal((await call(ops.token, 'request_control', ['r', 'steer', 'focus on the grader'])).status, 200);
  let pending = await sql(runner.token, `SELECT * FROM control_request WHERE run_id = 'r' AND status = 'pending'`);
  assert.deepEqual(pending.map((x) => x.action).sort(), ['pause', 'steer']);
  assert.notEqual((await call(ops.token, 'ack_control', [pending[0].id, 'done', ''])).status, 200);
  assert.equal((await call(runner.token, 'ack_control', [pending[0].id, 'done', 'paused'])).status, 200);
  assert.notEqual((await call(runner.token, 'ack_control', [pending[0].id, 'rejected', 'again'])).status, 200); // already handled
  pending = await sql(runner.token, `SELECT * FROM control_request WHERE run_id = 'r' AND status = 'pending'`);
  assert.equal(pending.length, 1);
  assert.notEqual((await call(pub.token, 'grant_role', [`0x${rando.identity}`, 'ops'])).status, 200);
  ok('ops controls: roles enforced, requests recorded, runner poll sees pending, ack closes them');

  console.log('\nsummary: ' + JSON.stringify(summary));
  console.log(`ALL ${results.length} CHECKS PASSED`);
}

try { await main(); } catch (e) { console.error('\nFAILED:', e); process.exitCode = 1; } finally {
  if (server) {
    if (WIN) spawnSync('taskkill', ['/pid', String(server.pid), '/T', '/F']);
    else { try { process.kill(-server.pid, 'SIGTERM'); } catch {} try { server.kill('SIGTERM'); } catch {} }
  }
  if (!process.env.E2E_KEEP) fs.rmSync(tmp, { recursive: true, force: true });
}
