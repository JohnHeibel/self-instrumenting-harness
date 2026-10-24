#!/usr/bin/env -S npx tsx
import fs from 'node:fs';
import path from 'node:path';
import { parseArgs } from 'node:util';
import { CheckpointStore, defaultStateDir } from './checkpoint.ts';
import { Publisher } from './publisher.ts';
import { FileSink, FatalSinkError, MemorySink, SpacetimeSink, mintIdentity, type Sink } from './sink.ts';
import { Tailer } from './tailer.ts';

const HELP = `publisher --run-dir <dir> [options]

  --run-dir <dir>      run directory to follow (env PUBLISHER_RUN_DIR)
  --from-start         replay every file from byte 0, ignoring the checkpoint, then keep following
  --once               publish what exists and exit (no following)
  --server <url>       SpacetimeDB server (env STDB_SERVER, default http://localhost:3000)
  --db <name>          database name (env STDB_MODULE, default harness-events); --module is an alias
  --token <token>      bearer token (env STDB_TOKEN, else <state-dir>/token; minted if absent)
  --run-id <id>        checkpoint name (default: the run directory's name)
  --state-dir <dir>    checkpoints (env PUBLISHER_STATE_DIR, default ~/.sisyphus-publisher)
  --sink <spec>        'file:<path>' writes wire records to a file; 'memory' discards (dry run)
  --max-records <n>    records per reducer call (default 200)
  --max-bytes <n>      payload bytes per call (default 524288)
  --flush-ms <n>       flush interval (default 1000)
  --poll-ms <n>        poll interval (default 250)
  --thumb-every <n>    upload every Nth thumbnail (default 1)
`;

async function main() {
  const { values: v } = parseArgs({
    options: {
      'run-dir': { type: 'string' }, 'from-start': { type: 'boolean' }, once: { type: 'boolean' },
      server: { type: 'string' }, module: { type: 'string' }, db: { type: 'string' }, token: { type: 'string' },
      'run-id': { type: 'string' }, 'state-dir': { type: 'string' }, sink: { type: 'string' },
      'max-records': { type: 'string' }, 'max-bytes': { type: 'string' }, 'flush-ms': { type: 'string' },
      'poll-ms': { type: 'string' }, 'thumb-every': { type: 'string' }, help: { type: 'boolean' },
    },
  });
  if (v.help) { console.log(HELP); return; }
  const runDirArg = v['run-dir'] ?? process.env.PUBLISHER_RUN_DIR;
  if (!runDirArg) { console.error(HELP); process.exit(2); }
  const runDir = path.resolve(runDirArg);
  if (!fs.statSync(runDir, { throwIfNoEntry: false })?.isDirectory()) {
    console.error(`not a directory: ${runDir}`); process.exit(2);
  }
  const log = (m: string) => console.error(`[publisher ${new Date().toISOString()}] ${m}`);
  const stateDir = v['state-dir'] ?? defaultStateDir();
  const runId = v['run-id'] ?? path.basename(runDir);
  const server = v.server ?? process.env.STDB_SERVER ?? 'http://localhost:3000';
  const moduleName = v.db ?? v.module ?? process.env.STDB_MODULE ?? 'harness-events';
  const num = (s: string | undefined) => (s === undefined ? undefined : Number(s));

  let sink: Sink;
  if (v.sink?.startsWith('file:')) sink = new FileSink(v.sink.slice(5));
  else if (v.sink === 'memory') sink = new MemorySink();
  else {
    let token = v.token ?? process.env.STDB_TOKEN;
    const tokenFile = path.join(stateDir, 'token');
    if (!token && fs.existsSync(tokenFile)) token = fs.readFileSync(tokenFile, 'utf8').trim();
    if (!token) {
      const id = await mintIdentity(server);
      token = id.token;
      fs.mkdirSync(stateDir, { recursive: true });
      fs.writeFileSync(tokenFile, token, { mode: 0o600 });
      log(`minted a new identity ${id.identity}; the module owner must run grantRole(${id.identity}, "publisher")`);
    }
    sink = new SpacetimeSink({ server, module: moduleName, token });
  }

  const store = new CheckpointStore(stateDir, runId);
  const tailer = new Tailer(runDir, store, { fromStart: v['from-start'], log });
  const pub = new Publisher({
    runDir, tailer, sink, log,
    caps: {
      ...(v['max-records'] && { maxRecords: num(v['max-records']) }),
      ...(v['max-bytes'] && { maxBytes: num(v['max-bytes']) }),
      ...(v['flush-ms'] && { flushMs: num(v['flush-ms']) }),
      ...(v['thumb-every'] && { thumbEvery: num(v['thumb-every']) }),
    } as any,
  });
  log(`run dir ${runDir}; checkpoint ${store.file}; ${v['from-start'] ? 'replaying from start' : 'resuming'}`);

  let stop = false;
  const onSig = () => { stop = true; };
  process.on('SIGINT', onSig);
  process.on('SIGTERM', onSig);

  try {
    if (v.once) {
      await pub.drain();
    } else {
      const pollMs = num(v['poll-ms']) ?? 250;
      while (!stop) {
        const n = await pub.step();
        if (n === 0) await new Promise(r => setTimeout(r, pollMs));
      }
      await pub.flush();
      await pub.pumpThumbs();
    }
  } catch (e) {
    if (e instanceof FatalSinkError) {
      log(`fatal: ${e.message}. If this says "not permitted", the owner must grant this identity the publisher role.`);
      process.exit(1);
    }
    throw e;
  }
  log(`done: ${pub.published} records, ${pub.thumbsUploaded} thumbnails`);
}

main().catch(e => { console.error(e); process.exit(1); });
