import fs from 'node:fs';
import { FatalSinkError, PayloadTooLargeError, type Sink } from './sink.ts';
import type { TailItem, Tailer } from './tailer.ts';
import { readImage, resolveAllowed } from './safefs.ts';
import { artifactOf, liveThumbKey, mimeOf, prepare, rkeyOf, type WireRecord, type WireThumb } from './wire.ts';

export interface Caps {
  maxRecords: number; // records per reducer call
  maxBytes: number; // payload bytes per reducer call (the HTTP body limit is likely 2 MB)
  flushMs: number; // flush a non-full batch after this long
  thumbEvery: number; // upload every Nth thumbnail (free-tier egress); 1 = all
  maxThumbBytes: number; // skip thumbnails larger than this
  thumbBatchBytes: number; // thumbnail bytes per reducer call
  maxRecordBytes: number; // a record whose JSON-encoded wire form is larger than this is skipped and logged
  maxLiveBytes: number; // spectator frame (live/latest.jpg) size limit
  liveEveryMs: number; // how often to look for a new spectator frame
}

export const DEFAULT_CAPS: Caps = {
  maxRecords: 200, // SCHEMA.md allows ~500
  maxBytes: 512 * 1024, // SCHEMA.md allows ~768 KB
  flushMs: 1000,
  thumbEvery: 1,
  maxThumbBytes: 128 * 1024,
  maxRecordBytes: 1536 * 1024,
  maxLiveBytes: 160 * 1024,
  liveEveryMs: 2000,
  thumbBatchBytes: 96 * 1024, // raw bytes; a JSON number array is ~4x, and a call should stay under ~512 KB
};

export interface PublisherOptions {
  runDir: string;
  tailer: Tailer;
  sink: Sink;
  caps?: Partial<Caps>;
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
  log?: (m: string) => void;
  maxAttempts?: number; // per sink call; default: retry forever
  backoffMs?: number; // initial, doubles up to 30 s
  /** Follow mode: a permission error (530 "not permitted") is backed off and retried instead of ending the run. */
  keepGoing?: boolean;
  runId?: string; // fallback run id for the spectator frame when no record has been seen
}

interface PendingThumb { rkey: string; run_id: string; artifact: string; attempts: number }
interface Entry { it: TailItem; wire: WireRecord | null; size: number } // wire null: skipped record

/** Split into batches that respect both caps. A single oversized record still goes alone. */
export function splitBatches<T extends { size: number }>(items: T[], maxRecords: number, maxBytes: number): T[][] {
  const out: T[][] = [];
  let cur: T[] = [];
  let bytes = 0;
  for (const it of items) {
    if (cur.length && (cur.length >= maxRecords || bytes + it.size > maxBytes)) {
      out.push(cur); cur = []; bytes = 0;
    }
    cur.push(it); bytes += it.size;
  }
  if (cur.length) out.push(cur);
  return out;
}

export class Publisher {
  readonly caps: Caps;
  private queue: Entry[] = [];
  private queuedBytes = 0;
  private lastFlush: number;
  private thumbQueue: PendingThumb[] = [];
  private thumbSeen = new Set<string>(); // rkeys already uploaded or given up on
  private thumbCounter = 0;
  private sleep: (ms: number) => Promise<void>;
  private now: () => number;
  private log: (m: string) => void;
  private runIdSeen: string | null = null;
  private liveSig = '';
  private lastLiveCheck = -Infinity;
  published = 0;
  skipped = 0;
  thumbsUploaded = 0;
  liveFrames = 0;

  constructor(private o: PublisherOptions) {
    this.caps = { ...DEFAULT_CAPS, ...o.caps };
    this.sleep = o.sleep ?? (ms => new Promise(r => setTimeout(r, ms)));
    this.now = o.now ?? Date.now;
    this.log = o.log ?? (() => {});
    this.lastFlush = this.now();
  }

  /** On resume: queue thumbnails of the last `n` published records that have one (they may not have been uploaded). */
  async requeueRecentThumbs(n = 20): Promise<void> {
    const items = (await this.o.tailer.recentItems()).filter(i => artifactOf(i.record));
    items.sort((a, b) => Date.parse(a.record.ts as string) - Date.parse(b.record.ts as string));
    for (const it of items.slice(-n)) {
      const rkey = rkeyOf(it.record as any);
      if (this.thumbSeen.has(rkey)) continue;
      this.thumbSeen.add(rkey);
      this.thumbQueue.push({ rkey, run_id: it.record.run_id as string, artifact: artifactOf(it.record)!, attempts: 0 });
    }
  }

  /** One poll: read new lines, flush if a cap or the interval says so. Returns lines read. */
  async step(): Promise<number> {
    const items = await this.o.tailer.poll();
    this.enqueue(items);
    const due = this.now() - this.lastFlush >= this.caps.flushMs;
    if (this.queue.length >= this.caps.maxRecords || this.queuedBytes >= this.caps.maxBytes || (due && this.queue.length)) {
      await this.flush();
    }
    await this.pumpThumbs();
    if (this.now() - this.lastLiveCheck >= this.caps.liveEveryMs) await this.pumpLive();
    return items.length;
  }

  /** Read until caught up, then flush everything (replay / tests / --once). */
  async drain(): Promise<void> {
    while ((await this.o.tailer.poll().then(items => this.enqueue(items))) > 0) {
      if (this.queue.length >= this.caps.maxRecords) await this.flush();
    }
    await this.flush();
    for (let i = 0; i < 2 && this.thumbQueue.length; i++) await this.pumpThumbs();
    await this.pumpLive();
  }

  private enqueue(items: TailItem[]): number {
    for (const it of items) {
      this.runIdSeen ??= it.record.run_id as string;
      const { wire, size } = prepare(it);
      if (size > this.caps.maxRecordBytes) {
        this.log(`skipping ${wire.writer}#${wire.seq} (${wire.kind}): ${size} bytes after JSON encoding exceeds ${this.caps.maxRecordBytes}`);
        this.skipped++;
        this.queue.push({ it, wire: null, size: 0 });
        continue;
      }
      this.queue.push({ it, wire, size });
      this.queuedBytes += size;
    }
    return items.length;
  }

  async flush(): Promise<void> {
    this.lastFlush = this.now();
    while (this.queue.length) {
      const [batch] = splitBatches(this.queue, this.caps.maxRecords, this.caps.maxBytes);
      await this.sendRecords(batch.filter(e => e.wire));
      this.queue.splice(0, batch.length);
      this.queuedBytes = this.queue.reduce((n, e) => n + e.size, 0);
      this.o.tailer.commit(batch.map(e => e.it));
      for (const e of batch) if (e.wire) this.noteThumb(e.it);
    }
  }

  /** Send entries; if the server says the body is too big, or rejects it, bisect to isolate and skip the bad record. */
  private async sendRecords(es: Entry[]): Promise<void> {
    if (!es.length) return;
    try {
      await this.retry('publish_batch', () => this.o.sink.publishBatch(es.map(e => e.wire!)));
      this.published += es.length;
    } catch (e) {
      const bad = e instanceof PayloadTooLargeError || (e instanceof FatalSinkError && !e.permission && this.o.keepGoing);
      if (!bad) throw e;
      if (es.length > 1) {
        const mid = es.length >> 1;
        await this.sendRecords(es.slice(0, mid));
        await this.sendRecords(es.slice(mid));
      } else {
        const w = es[0].wire!;
        this.log(`skipping ${w.writer}#${w.seq} (${w.kind}): rejected by the server (${(e as Error).message})`);
        this.skipped++;
      }
    }
  }

  private noteThumb(it: TailItem): void {
    const artifact = artifactOf(it.record);
    if (!artifact) return;
    const rkey = rkeyOf(it.record as any);
    if (this.thumbSeen.has(rkey)) return;
    this.thumbSeen.add(rkey);
    if (this.thumbCounter++ % this.caps.thumbEvery !== 0) return; // sampled out
    this.thumbQueue.push({ rkey, run_id: it.record.run_id as string, artifact, attempts: 0 });
  }

  private async sendThumbs(g: WireThumb[]): Promise<void> {
    try {
      await this.retry('publish_thumbs', () => this.o.sink.publishThumbs(g));
      this.thumbsUploaded += g.length;
    } catch (e) {
      const bad = e instanceof PayloadTooLargeError || (e instanceof FatalSinkError && !e.permission && this.o.keepGoing);
      if (!bad) throw e;
      if (g.length > 1) {
        await this.sendThumbs(g.slice(0, g.length >> 1));
        await this.sendThumbs(g.slice(g.length >> 1));
      } else this.log(`skipping thumbnail ${g[0].rkey}: rejected by the server (${(e as Error).message})`);
    }
  }

  /** Upload queued thumbnails lazily, after their records are published. Missing files are retried a few times. */
  async pumpThumbs(): Promise<void> {
    if (!this.thumbQueue.length) return;
    const todo = this.thumbQueue;
    this.thumbQueue = [];
    let group: WireThumb[] = [];
    let bytes = 0;
    const send = async () => {
      if (!group.length) return;
      const g = group; group = []; bytes = 0;
      await this.sendThumbs(g);
    };
    for (const p of todo) {
      const buf = await readImage(this.o.runDir, p.artifact, this.caps.maxThumbBytes);
      if (!buf) {
        if (++p.attempts < 20) this.thumbQueue.push(p); // file may not be written yet
        else this.log(`thumbnail ${p.artifact} unavailable; giving up`);
        continue;
      }
      if (group.length && bytes + buf.length > this.caps.thumbBatchBytes) await send();
      group.push({ rkey: p.rkey, run_id: p.run_id, mime: mimeOf(p.artifact), bytes: Array.from(buf) });
      bytes += buf.length;
    }
    await send();
  }

  /** The spectator frame `live/latest.jpg` is republished under one stable key whenever it changes. */
  async pumpLive(): Promise<void> {
    this.lastLiveCheck = this.now();
    const runId = this.runIdSeen ?? this.o.runId;
    if (!runId) return;
    const real = await resolveAllowed(this.o.runDir, 'live/latest.jpg', 'image');
    if (!real) return;
    let sig: string;
    try { const st = fs.statSync(real); sig = `${st.mtimeMs}:${st.size}`; } catch { return; }
    if (sig === this.liveSig) return;
    const buf = await readImage(this.o.runDir, 'live/latest.jpg', this.caps.maxLiveBytes);
    if (!buf) return; // missing, too big, or half-written: try again next time
    this.liveSig = sig;
    await this.sendThumbs([{ rkey: liveThumbKey(runId), run_id: runId, mime: 'image/jpeg', bytes: Array.from(buf) }]);
    this.liveFrames++;
  }

  private async retry(what: string, fn: () => Promise<void>): Promise<void> {
    let delay = this.o.backoffMs ?? 500;
    for (let attempt = 1; ; attempt++) {
      try {
        return await fn();
      } catch (e) {
        if (e instanceof PayloadTooLargeError) throw e;
        if (e instanceof FatalSinkError) {
          // Follow mode: a missing role is fixed by the owner while we wait; keep the mirror alive.
          if (!(this.o.keepGoing && e.permission)) throw e;
          delay = Math.max(delay, 5_000);
        } else if (this.o.maxAttempts && attempt >= this.o.maxAttempts) throw e;
        this.log(`${what} failed (${(e as Error).message}); retry in ${delay} ms`);
        await this.sleep(delay);
        delay = Math.min(delay * 2, e instanceof FatalSinkError ? 60_000 : 30_000);
      }
    }
  }

  pendingCount(): number { return this.queue.length; }
}
