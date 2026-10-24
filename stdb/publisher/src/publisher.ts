import { FatalSinkError, type Sink } from './sink.ts';
import type { TailItem, Tailer } from './tailer.ts';
import { readImage } from './safefs.ts';
import { artifactOf, mimeOf, rkeyOf, toWire, type WireRecord, type WireThumb } from './wire.ts';

export interface Caps {
  maxRecords: number; // records per reducer call
  maxBytes: number; // payload bytes per reducer call (the HTTP body limit is likely 2 MB)
  flushMs: number; // flush a non-full batch after this long
  thumbEvery: number; // upload every Nth thumbnail (free-tier egress); 1 = all
  maxThumbBytes: number; // skip thumbnails larger than this
  thumbBatchBytes: number; // thumbnail bytes per reducer call
}

export const DEFAULT_CAPS: Caps = {
  maxRecords: 200, // SCHEMA.md allows ~500
  maxBytes: 512 * 1024, // SCHEMA.md allows ~768 KB
  flushMs: 1000,
  thumbEvery: 1,
  maxThumbBytes: 128 * 1024,
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
}

interface PendingThumb { rkey: string; run_id: string; artifact: string; attempts: number }

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
  private queue: TailItem[] = [];
  private queuedBytes = 0;
  private lastFlush: number;
  private thumbQueue: PendingThumb[] = [];
  private thumbSeen = new Set<string>(); // rkeys already uploaded or given up on
  private thumbCounter = 0;
  private sleep: (ms: number) => Promise<void>;
  private now: () => number;
  private log: (m: string) => void;
  published = 0;
  thumbsUploaded = 0;

  constructor(private o: PublisherOptions) {
    this.caps = { ...DEFAULT_CAPS, ...o.caps };
    this.sleep = o.sleep ?? (ms => new Promise(r => setTimeout(r, ms)));
    this.now = o.now ?? Date.now;
    this.log = o.log ?? (() => {});
    this.lastFlush = this.now();
  }

  /** One poll: read new lines, flush if a cap or the interval says so. Returns lines read. */
  async step(): Promise<number> {
    const items = await this.o.tailer.poll();
    for (const it of items) {
      this.queue.push(it);
      this.queuedBytes += it.raw.length;
    }
    const due = this.now() - this.lastFlush >= this.caps.flushMs;
    if (this.queue.length >= this.caps.maxRecords || this.queuedBytes >= this.caps.maxBytes || (due && this.queue.length)) {
      await this.flush();
    }
    await this.pumpThumbs();
    return items.length;
  }

  /** Read until caught up, then flush everything (replay / tests / --once). */
  async drain(): Promise<void> {
    while ((await this.o.tailer.poll().then(items => this.enqueue(items))) > 0) {
      if (this.queue.length >= this.caps.maxRecords) await this.flush();
    }
    await this.flush();
    for (let i = 0; i < 2 && this.thumbQueue.length; i++) await this.pumpThumbs();
  }

  private enqueue(items: TailItem[]): number {
    for (const it of items) { this.queue.push(it); this.queuedBytes += it.raw.length; }
    return items.length;
  }

  async flush(): Promise<void> {
    this.lastFlush = this.now();
    while (this.queue.length) {
      const sized = this.queue.map(it => ({ it, size: it.raw.length + 200 }));
      const [first] = splitBatches(sized, this.caps.maxRecords, this.caps.maxBytes);
      const batch = first.map(s => s.it);
      const wire: WireRecord[] = batch.map(toWire);
      await this.retry('publish_batch', () => this.o.sink.publishBatch(wire));
      this.queue.splice(0, batch.length);
      this.queuedBytes = this.queue.reduce((n, it) => n + it.raw.length, 0);
      this.published += batch.length;
      this.o.tailer.commit(batch);
      for (const it of batch) this.noteThumb(it);
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
      await this.retry('publish_thumbs', () => this.o.sink.publishThumbs(g));
      this.thumbsUploaded += g.length;
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

  private async retry(what: string, fn: () => Promise<void>): Promise<void> {
    let delay = this.o.backoffMs ?? 500;
    for (let attempt = 1; ; attempt++) {
      try {
        return await fn();
      } catch (e) {
        if (e instanceof FatalSinkError) throw e;
        if (this.o.maxAttempts && attempt >= this.o.maxAttempts) throw e;
        this.log(`${what} failed (${(e as Error).message}); retry in ${delay} ms`);
        await this.sleep(delay);
        delay = Math.min(delay * 2, 30_000);
      }
    }
  }

  pendingCount(): number { return this.queue.length; }
}
