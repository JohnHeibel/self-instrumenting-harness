import crypto from 'node:crypto';
import { CheckpointStore, type FileCheckpoint } from './checkpoint.ts';
import { listLogFiles, openAllowed } from './safefs.ts';

export interface TailItem {
  file: string;
  epoch: number; // bumped when the file is truncated or replaced; stale items are not committed
  endOffset: number; // byte offset just after this line
  raw: string; // the line, verbatim (no newline)
  record: Record<string, unknown>;
}

export interface TailerOptions {
  fromStart?: boolean; // ignore any checkpoint and replay every file from byte 0
  liveOnly?: boolean; // with no checkpoint, skip what is already in the files and publish only new lines
  chunkBytes?: number;
  log?: (msg: string) => void;
}

interface State {
  committed: number;
  read: number;
  ino: string;
  headLen: number;
  head: string;
  epoch: number;
}

const HEAD_BYTES = 64;
const MAX_CHUNK = 64 * 1024 * 1024;
const sha = (b: Buffer) => crypto.createHash('sha1').update(b).digest('hex');

export class Tailer {
  private states = new Map<string, State>();
  private initialized = false;
  private hadCheckpoint = false;
  private log: (m: string) => void;
  private chunk: number;

  constructor(
    private runDir: string,
    private store: CheckpointStore | null,
    private opts: TailerOptions = {}
  ) {
    this.log = opts.log ?? (() => {});
    if (store) store.onWarn = m => this.log(m);
    this.chunk = opts.chunkBytes ?? 4 * 1024 * 1024;
    const cp = opts.fromStart ? null : store?.load() ?? null;
    this.hadCheckpoint = !!cp;
    if (cp) {
      for (const [name, f] of Object.entries(cp.files)) {
        this.states.set(name, {
          committed: f.offset, read: f.offset, ino: f.ino, headLen: f.headLen, head: f.head, epoch: 0,
        });
      }
    }
  }

  /** Complete new lines from every log file, in per-file order. Partial trailing lines are left for later. */
  async poll(): Promise<TailItem[]> {
    const names = await listLogFiles(this.runDir);
    const out: TailItem[] = [];
    for (const name of names) {
      try {
        await this.pollFile(name, out);
      } catch (e) {
        this.log(`tailer: ${name}: ${(e as Error).message}`);
      }
    }
    this.initialized = true;
    return out;
  }

  private async pollFile(name: string, out: TailItem[]): Promise<void> {
    const fh = await openAllowed(this.runDir, name, 'jsonl');
    if (!fh) return;
    try {
      const st = await fh.stat();
      const ino = String(st.ino);
      let s = this.states.get(name);
      if (!s) {
        // Default: no checkpoint means the whole history from byte 0. Only with the explicit liveOnly
        // option are files that already exist at startup skipped. Files that appear later, or that a
        // checkpoint doesn't know, always start at 0.
        const live = !!this.opts.liveOnly && !this.initialized && !this.opts.fromStart && !this.hadCheckpoint;
        const start = live ? st.size : 0;
        s = { committed: start, read: start, ino, headLen: 0, head: '', epoch: 0 };
        this.states.set(name, s);
        if (live) this.persist();
      }
      if (await this.replaced(fh, s, st.size, ino)) {
        this.log(`tailer: ${name} was truncated or replaced; re-reading from the start`);
        s.committed = 0; s.read = 0; s.headLen = 0; s.head = ''; s.epoch++;
      }
      s.ino = ino;
      await this.refreshHead(fh, s);
      let chunk = this.chunk;
      while (s.read < st.size) {
        const want = Math.min(chunk, st.size - s.read);
        const buf = Buffer.alloc(want);
        const { bytesRead } = await fh.read(buf, 0, want, s.read);
        if (bytesRead === 0) break;
        const data = buf.subarray(0, bytesRead);
        let lineStart = 0;
        let produced = false;
        for (;;) {
          const nl = data.indexOf(0x0a, lineStart);
          if (nl < 0) break;
          let line = data.subarray(lineStart, nl).toString('utf8');
          if (line.endsWith('\r')) line = line.slice(0, -1);
          lineStart = nl + 1;
          produced = true;
          const endOffset = s.read + lineStart;
          if (line.trim() === '') continue;
          let rec: unknown;
          try { rec = JSON.parse(line); } catch { rec = null; }
          if (!rec || typeof rec !== 'object' || Array.isArray(rec) || !validEnvelope(rec as Record<string, unknown>)) {
            this.log(`tailer: ${name}: skipping a line that is not a valid record (offset ${endOffset - Buffer.byteLength(line) - 1})`);
            continue;
          }
          out.push({ file: name, epoch: s.epoch, endOffset, raw: line, record: rec as Record<string, unknown> });
        }
        if (lineStart === 0 && !produced) {
          if (bytesRead < want || chunk >= MAX_CHUNK) break; // partial trailing line: wait for the rest
          chunk *= 2; // a single line bigger than the chunk: grow and retry
          continue;
        }
        s.read += lineStart;
        chunk = this.chunk;
        if (bytesRead < want) break;
      }
    } finally {
      await fh.close();
    }
  }

  private async replaced(fh: import('node:fs/promises').FileHandle, s: State, size: number, ino: string): Promise<boolean> {
    if (size < s.read) return true;
    if (s.ino && s.ino !== '0' && ino !== '0' && s.ino !== ino && s.read > 0) return true;
    if (s.headLen > 0 && size >= s.headLen) {
      const b = Buffer.alloc(s.headLen);
      const { bytesRead } = await fh.read(b, 0, s.headLen, 0);
      if (bytesRead < s.headLen || sha(b) !== s.head) return true;
    }
    return false;
  }

  private async refreshHead(fh: import('node:fs/promises').FileHandle, s: State): Promise<void> {
    const n = Math.min(s.committed, HEAD_BYTES);
    if (n <= s.headLen) return;
    const b = Buffer.alloc(n);
    const { bytesRead } = await fh.read(b, 0, n, 0);
    if (bytesRead === n) { s.headLen = n; s.head = sha(b); }
  }

  /**
   * Records from the last `maxBytes` before each file's committed offset (already published). Used on
   * resume to re-queue recent thumbnails, which may not have been uploaded before the last stop.
   */
  async recentItems(maxBytes = 512 * 1024): Promise<TailItem[]> {
    const out: TailItem[] = [];
    for (const [name, s] of this.states) {
      if (s.committed <= 0) continue;
      const fh = await openAllowed(this.runDir, name, 'jsonl');
      if (!fh) continue;
      try {
        const start = Math.max(0, s.committed - maxBytes);
        const buf = Buffer.alloc(s.committed - start);
        const { bytesRead } = await fh.read(buf, 0, buf.length, start);
        let text = buf.subarray(0, bytesRead);
        if (start > 0) { const nl = text.indexOf(0x0a); text = nl < 0 ? Buffer.alloc(0) : text.subarray(nl + 1); }
        for (const l of text.toString('utf8').split('\n')) {
          const line = l.endsWith('\r') ? l.slice(0, -1) : l;
          if (!line.trim()) continue;
          try {
            const rec = JSON.parse(line);
            if (rec && typeof rec === 'object' && validEnvelope(rec)) out.push({ file: name, epoch: s.epoch, endOffset: 0, raw: line, record: rec });
          } catch {}
        }
      } finally { await fh.close(); }
    }
    return out;
  }

  /** Mark everything up to `endOffset` of `file` as safely published, and persist the checkpoint. */
  commit(items: TailItem[]): void {
    for (const it of items) {
      const s = this.states.get(it.file);
      if (!s || s.epoch !== it.epoch) continue;
      if (it.endOffset > s.committed) s.committed = it.endOffset;
    }
    this.persist();
  }

  private persist(): void {
    if (!this.store) return;
    const files: Record<string, FileCheckpoint> = {};
    for (const [name, s] of this.states) {
      files[name] = { offset: s.committed, ino: s.ino, headLen: s.headLen, head: s.head };
    }
    this.store.save(files);
  }

  /** Test/inspection helper. */
  offsets(): Record<string, number> {
    return Object.fromEntries([...this.states].map(([n, s]) => [n, s.committed]));
  }
}

function validEnvelope(r: Record<string, unknown>): boolean {
  return (
    typeof r.run_id === 'string' && typeof r.writer === 'string' && typeof r.type === 'string' &&
    typeof r.ts === 'string' && /Z$/.test(r.ts) && Number.isInteger(r.seq) && (r.seq as number) >= 0 &&
    !Number.isNaN(Date.parse(r.ts))
  );
}
