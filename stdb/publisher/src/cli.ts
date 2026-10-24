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
  --from-start         ignore the checkpoint and replay every file from byte 0, then keep following
                       (with no checkpoint the default is already byte 0: the full history)
  --live-only          with no checkpoint, skip what is already in the files and publish only new lines
  --forever            never exit on errors: log, back off (5 s to 60 s) and restart the whole pipeline
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
      'run-dir': { type: 'string' }, 'from-start': { type: 'boolean' }, 'live-only': { type: 'boolean' }, forever: { type: 'boolean' }, once: { type: 'boolean' },
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

  const caps = {
    ...(v['max-records'] && { maxRecords: num(v['max-records']) }),
    ...(v['max-bytes'] && { maxBytes: num(v['max-bytes']) }),
    ...(v['flush-ms'] && { flushMs: num(v['flush-ms']) }),
    ...(v['thumb-every'] && { thumbEvery: num(v['thumb-every']) }),
  } as any;
  const pollMs = num(v['poll-ms']) ?? 250;

  let stop = false;
  const onSig = () => { stop = true; };
  process.on('SIGINT', onSig);
  process.on('SIGTERM', onSig);
  const nap = (ms: number) => new Promise<void>(r => setTimeout(r, ms));

  async function makeSink(): Promise<Sink> {
    if (v.sink?.startsWith('file:')) return new FileSink(v.sink.slice(5));
    if (v.sink === 'memory') return new MemorySink();
    let token = v.token ?? process.env.STDB_TOKEN;
    const tokenFile = path.join(stateDir, 'token');
    if (!token && fs.existsSync(tokenFile)) token = fs.readFileSync(tokenFile, 'utf8').trim();
    if (!token) {
      const id = await mintIdentity(server);
      token = id.token;
      fs.mkdirSync(stateDir, { recursive: true });
      fs.writeFileSync(tokenFile, token, { mode: 0o600 });
      log(`minted a new identity ${id.identity}; the module owner must run: spacetime call ${moduleName} grant_role 0x${id.identity} publisher --server <server>`);
    }
    return new SpacetimeSink({ server, module: moduleName, token });
  }

  let first = true;
  async function session(): Promise<void> {
    const sink = await makeSink();
    const store = new CheckpointStore(stateDir, runId);
    const hadCheckpoint = !!store.load();
    const tailer = new Tailer(runDir, store, {
      fromStart: first ? v['from-start'] : false, // after a restart the checkpoint is authoritative
      liveOnly: v['live-only'], log,
    });
    const pub = new Publisher({ runDir, tailer, sink, log, caps, keepGoing: !v.once, runId });
    log(`run dir ${runDir}; checkpoint ${store.file}; ${v['from-start'] && first ? 'replaying from start' : hadCheckpoint ? 'resuming from checkpoint' : v['live-only'] ? 'live only (no history)' : 'starting at byte 0 (full history)'}`);
    first = false;
    if (hadCheckpoint) await pub.requeueRecentThumbs();
    try {
      if (v.once) {
        await pub.drain();
      } else {
        while (!stop) {
          const n = await pub.step();
          if (n === 0) await nap(pollMs);
        }
        await pub.flush();
        await pub.pumpThumbs();
      }
    } finally {
      log(`${pub.published} records, ${pub.skipped} skipped, ${pub.thumbsUploaded} thumbnails, ${pub.liveFrames} live frames this session`);
    }
  }

  if (v.forever && !v.once) {
    let delay = 5000;
    while (!stop) {
      const t0 = Date.now();
      try { await session(); } catch (e) {
        log(`session failed: ${(e as Error).message}; restarting in ${delay / 1000} s`);
      }
      if (stop) break;
      delay = Date.now() - t0 > 120_000 ? 5000 : Math.min(delay * 2, 60_000); // a long healthy run resets the backoff
      await nap(delay);
    }
    return;
  }
  try {
    await session();
  } catch (e) {
    if (e instanceof FatalSinkError) {
      log(`fatal: ${e.message}. If this says "not permitted", the owner must grant this identity the publisher role.`);
      process.exit(1);
    }
    throw e;
  }
}

main().catch(e => { console.error(e); process.exit(1); });
