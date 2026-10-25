// The only module that opens files from a run directory. Run dirs also hold secrets
// (codex-home/auth.json, host/rcon.json), so everything goes through an explicit allowlist:
//   - a regular file named *.jsonl directly in the run dir (the event log), or
//   - an image directly under shots/ (a thumbnail), or
//   - exactly live/latest.jpg (the spectator frame; nothing else under live/).
// Images must also start with a known image signature, and files with extra hard links are refused.
// A symlink is followed only if its real target is itself allowed and inside the real run dir.
import fs from 'node:fs';
import path from 'node:path';

const JSONL_RE = /^[A-Za-z0-9._-]+\.jsonl$/;
const IMAGE_RE = /^[A-Za-z0-9._-]+\.(jpe?g|png|webp|gif)$/i;

export type FileKind = 'jsonl' | 'image';

/** Classify a path relative to the run dir (forward slashes). null = not allowed. */
export function classify(rel: string): FileKind | null {
  const parts = rel.split(/[\\/]/);
  if (parts.length === 1 && JSONL_RE.test(parts[0]) && parts[0] !== '.jsonl') return 'jsonl';
  if (parts.length === 2 && parts[0] === 'live' && parts[1] === 'latest.jpg') return 'image';
  if (parts.length === 2 && parts[0] === 'shots' && IMAGE_RE.test(parts[1]) && !parts[1].startsWith('..')) {
    return 'image';
  }
  return null;
}

/** Real path of `rel` if it may be opened (allowlisted, regular file, no escape), else null. */
export async function resolveAllowed(runDir: string, rel: string, want?: FileKind): Promise<string | null> {
  const kind = classify(rel);
  if (!kind || (want && kind !== want)) return null;
  try {
    const realRoot = await fs.promises.realpath(runDir);
    const real = await fs.promises.realpath(path.join(runDir, rel));
    const back = path.relative(realRoot, real);
    if (back.startsWith('..') || path.isAbsolute(back)) return null;
    if (classify(back) !== kind) return null; // symlink to a file that is not itself allowed
    const st = await fs.promises.stat(real);
    return st.isFile() && st.nlink === 1 ? real : null;
  } catch {
    return null;
  }
}

/** Names of candidate event-log files at the top level of the run dir (names only, nothing opened). */
export async function listLogFiles(runDir: string): Promise<string[]> {
  let names: string[];
  try {
    names = await fs.promises.readdir(runDir);
  } catch {
    return [];
  }
  return names.filter(n => classify(n) === 'jsonl').sort();
}

export async function openAllowed(runDir: string, rel: string, want?: FileKind) {
  const real = await resolveAllowed(runDir, rel, want);
  if (!real) return null;
  try {
    return await fs.promises.open(real, 'r');
  } catch {
    return null;
  }
}

export async function readImage(runDir: string, rel: string, maxBytes: number): Promise<Buffer | null> {
  const fh = await openAllowed(runDir, rel, 'image');
  if (!fh) return null;
  try {
    const st = await fh.stat();
    if (st.size > maxBytes) return null;
    const buf = Buffer.alloc(st.size);
    const { bytesRead } = await fh.read(buf, 0, st.size, 0);
    const img = buf.subarray(0, bytesRead);
    return looksLikeImage(img) ? img : null;
  } finally {
    await fh.close();
  }
}

/** JPEG, PNG, GIF or WebP signature. */
export function looksLikeImage(b: Buffer): boolean {
  if (b.length < 12) return false;
  if (b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) return true;
  if (b.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))) return true;
  const head = b.subarray(0, 6).toString('latin1');
  if (head === 'GIF87a' || head === 'GIF89a') return true;
  return b.subarray(0, 4).toString('latin1') === 'RIFF' && b.subarray(8, 12).toString('latin1') === 'WEBP';
}
