#!/usr/bin/env node
// Follows every run directory under a root: one `publisher --forever` child per run dir, new dirs picked up as they appear.
//   node supervise.mjs --root ~/sisyphus-runs --state-dir ~/sisyphus-work/stdb-state [--exclude a,b] [--scan-ms 15000] [-- extra publisher args]
// Read-only on the run dirs (the publisher never writes there); checkpoints and the token live in --state-dir.
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';

const here = path.dirname(fileURLToPath(import.meta.url));
const { values: v, positionals: extra } = parseArgs({
  allowPositionals: true,
  options: { root: { type: 'string' }, 'state-dir': { type: 'string' }, exclude: { type: 'string', default: '' }, 'scan-ms': { type: 'string', default: '15000' } },
});
if (!v.root || !v['state-dir']) { console.error('need --root and --state-dir'); process.exit(2); }
const exclude = new Set(v.exclude.split(',').filter(Boolean));
const children = new Map(); // name -> { proc, restarts }
const log = (m) => console.error(`[supervise ${new Date().toISOString()}] ${m}`);
let stopping = false;

const hasEvents = (d) => { try { return fs.readdirSync(d).some((f) => f.endsWith('.jsonl')); } catch { return false; } };

function start(name) {
  const dir = path.join(v.root, name);
  const proc = spawn(path.join(here, 'node_modules', '.bin', 'tsx'), [path.join(here, 'src', 'cli.ts'), '--run-dir', dir, '--forever', '--poll-ms', '1000', '--state-dir', v['state-dir'], ...extra], { stdio: ['ignore', 'inherit', 'inherit'] });
  children.set(name, proc);
  log(`following ${name} (pid ${proc.pid})`);
  proc.on('exit', (code, sig) => {
    children.delete(name);
    if (!stopping) log(`${name} publisher exited (${sig ?? code}); will restart on next scan`);
  });
}

function scan() {
  if (stopping) return;
  for (const e of fs.readdirSync(v.root, { withFileTypes: true })) {
    if (!e.isDirectory() || exclude.has(e.name) || e.name.startsWith('.') || children.has(e.name)) continue;
    if (hasEvents(path.join(v.root, e.name))) start(e.name);
  }
}
const stop = () => { stopping = true; for (const p of children.values()) p.kill('SIGTERM'); setTimeout(() => process.exit(0), 3000); };
process.on('SIGINT', stop); process.on('SIGTERM', stop);
scan();
setInterval(() => { try { scan(); } catch (e) { log(`scan failed: ${e.message}`); } }, Number(v['scan-ms']));
