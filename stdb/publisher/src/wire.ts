// Record envelope -> the module's RecordIn row. The full record travels verbatim in `payload`,
// so new record types and fields need no change here. Idempotency key: (run_id, writer, seq).
import type { TailItem } from './tailer.ts';

export interface WireRecord {
  run_id: string;
  writer: string;
  seq: number;
  ts: string;
  ts_us: number;
  kind: string;
  channel: string;
  has_thumb: boolean;
  payload: string;
}

export interface WireThumb {
  rkey: string;
  run_id: string;
  mime: string;
  bytes: number[];
}

export const rkeyOf = (r: { run_id: string; writer: string; seq: number }) => `${r.run_id}|${r.writer}|${r.seq}`;

const ARTIFACT_RE = /^shots\/[A-Za-z0-9._-]+\.(jpe?g|png|webp|gif)$/i;

/** The record's `artifact` path if it names a thumbnail we may upload, else null. */
export function artifactOf(rec: Record<string, unknown>): string | null {
  const a = rec.artifact;
  return typeof a === 'string' && ARTIFACT_RE.test(a) ? a : null;
}

/** Total `files[].content` bytes of a `hook_loaded` record above which contents are cut. */
export const HOOK_CONTENT_BUDGET = 1024 * 1024;

/** Key of the spectator frame in the thumb table; overwritten each time. */
export const liveThumbKey = (runId: string) => `${runId}|live|latest`;

/**
 * `hook_loaded` carries source files inline (up to 40 x 64 KB, once per tool). Above the budget, cut each
 * file's content to its share and mark it `truncated`; the full source stays in `source_ref`.
 */
export function truncateHookLoaded(rec: Record<string, any>, budget = HOOK_CONTENT_BUDGET): Record<string, any> | null {
  if (rec.type !== 'hook_loaded' || !Array.isArray(rec.files)) return null;
  const sizes = rec.files.map((f: any) => (typeof f?.content === 'string' ? Buffer.byteLength(f.content) : 0));
  if (sizes.reduce((a: number, b: number) => a + b, 0) <= budget) return null;
  const share = Math.floor(budget / rec.files.length);
  const files = rec.files.map((f: any, i: number) =>
    sizes[i] > share ? { ...f, content: Buffer.from(f.content).subarray(0, share).toString('utf8'), truncated: true } : f);
  return { ...rec, files, truncated: true };
}

export interface Prepared { wire: WireRecord; size: number }

/** Wire record plus its size in bytes after JSON encoding (what the request body will actually cost). */
export function prepare(item: TailItem): Prepared {
  const wire = toWire(item);
  const cut = truncateHookLoaded(item.record as Record<string, any>);
  if (cut) wire.payload = JSON.stringify(cut);
  return { wire, size: Buffer.byteLength(JSON.stringify(wire)) + 1 };
}

export function toWire(item: TailItem): WireRecord {
  const r = item.record as Record<string, any>;
  return {
    run_id: r.run_id,
    writer: r.writer,
    seq: r.seq,
    ts: r.ts,
    ts_us: Date.parse(r.ts) * 1000,
    kind: r.type,
    channel: typeof r.channel === 'string' ? r.channel : '',
    has_thumb: artifactOf(r) !== null,
    payload: item.raw,
  };
}

export function mimeOf(path: string): string {
  const ext = path.slice(path.lastIndexOf('.') + 1).toLowerCase();
  return ext === 'png' ? 'image/png' : ext === 'webp' ? 'image/webp' : ext === 'gif' ? 'image/gif' : 'image/jpeg';
}
