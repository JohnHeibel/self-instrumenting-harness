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
