import fs from 'node:fs';
import { rkeyOf, type WireRecord, type WireThumb } from './wire.ts';

/** Thrown for errors that retrying cannot fix (bad token, permission denied, bad request). */
export class FatalSinkError extends Error {}

export interface Sink {
  publishBatch(records: WireRecord[]): Promise<void>;
  publishThumbs(thumbs: WireThumb[]): Promise<void>;
  close?(): Promise<void>;
}

/** Keeps everything in memory, deduplicated by key like the module's upsert. For tests. */
export class MemorySink implements Sink {
  records = new Map<string, WireRecord>();
  thumbs = new Map<string, WireThumb>();
  batchSizes: number[] = [];
  thumbCalls = 0;
  async publishBatch(records: WireRecord[]) {
    this.batchSizes.push(records.length);
    for (const r of records) this.records.set(rkeyOf(r), r);
  }
  async publishThumbs(thumbs: WireThumb[]) {
    this.thumbCalls++;
    for (const t of thumbs) this.thumbs.set(t.rkey, t);
  }
}

/** Appends wire records as JSON lines to a file (dry runs; `--sink file:<path>`). */
export class FileSink implements Sink {
  constructor(private path: string) {}
  async publishBatch(records: WireRecord[]) {
    fs.appendFileSync(this.path, records.map(r => JSON.stringify(r) + '\n').join(''));
  }
  async publishThumbs(thumbs: WireThumb[]) {
    fs.appendFileSync(
      this.path,
      thumbs.map(t => JSON.stringify({ thumb: t.rkey, mime: t.mime, bytes: t.bytes.length }) + '\n').join('')
    );
  }
}

export interface SpacetimeOptions {
  server: string; // e.g. http://localhost:3000
  module: string; // database name or identity
  token: string;
  fetchImpl?: typeof fetch;
}

/**
 * Calls the module's reducers (stdb/SCHEMA.md) over the HTTP API:
 * POST /v1/database/<db>/call/<reducer> with the argument list as a JSON array.
 */
export class SpacetimeSink implements Sink {
  private f: typeof fetch;
  constructor(private o: SpacetimeOptions) {
    this.f = o.fetchImpl ?? fetch;
  }
  private async call(reducer: string, args: unknown[]): Promise<void> {
    const url = `${this.o.server.replace(/\/+$/, '')}/v1/database/${encodeURIComponent(this.o.module)}/call/${reducer}`;
    const res = await this.f(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: `Bearer ${this.o.token}` },
      body: JSON.stringify(args),
    });
    if (res.ok) return;
    const text = (await res.text().catch(() => '')).slice(0, 300);
    const msg = `${reducer}: HTTP ${res.status} ${text}`;
    // SCHEMA.md: a reducer that fails (e.g. "not permitted") is HTTP 530; other 4xx are malformed
    // requests. Neither heals by retrying. Other 5xx, 408, 429 and network errors are retried.
    if (res.status === 530 || (res.status >= 400 && res.status < 500 && res.status !== 408 && res.status !== 429)) {
      throw new FatalSinkError(msg);
    }
    throw new Error(msg);
  }
  publishBatch(records: WireRecord[]) {
    return this.call('publish_batch', [records]);
  }
  publishThumbs(thumbs: WireThumb[]) {
    return this.call('publish_thumbs', [thumbs]);
  }
}

/** Mint an anonymous identity (token) from the server. */
export async function mintIdentity(server: string, f: typeof fetch = fetch): Promise<{ identity: string; token: string }> {
  const res = await f(`${server.replace(/\/+$/, '')}/v1/identity`, { method: 'POST' });
  if (!res.ok) throw new Error(`identity: HTTP ${res.status}`);
  return (await res.json()) as { identity: string; token: string };
}
