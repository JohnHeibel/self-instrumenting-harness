import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const FIXTURE = path.join(path.dirname(fileURLToPath(import.meta.url)), 'fixtures', 'fake-spreadsheet-slice');

export function tmp(prefix = 'pub-'): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

export function copyFixture(): string {
  const d = tmp('run-');
  fs.cpSync(FIXTURE, d, { recursive: true });
  return d;
}

export function rec(writer: string, seq: number, extra: Record<string, unknown> = {}) {
  return {
    v: 1, ts: new Date(Date.UTC(2026, 9, 25, 6, 0, 0) + seq * 1000).toISOString(),
    run_id: 'r1', writer, seq, type: 'marker', label: 'x', ...extra,
  };
}

export const JPEG = (tag = 'x') => Buffer.concat([Buffer.from([0xff, 0xd8, 0xff, 0xe0]), Buffer.from(`JFIF-${tag}-padding`)]);

export const line = (o: unknown) => JSON.stringify(o) + '\n';

export function lineCount(dir: string): number {
  return fs.readdirSync(dir).filter(f => f.endsWith('.jsonl'))
    .reduce((n, f) => n + fs.readFileSync(path.join(dir, f), 'utf8').split('\n').filter(Boolean).length, 0);
}
