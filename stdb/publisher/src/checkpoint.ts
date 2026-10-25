import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

export interface FileCheckpoint {
  offset: number; // bytes fully published (always at a line boundary)
  ino: string;
  headLen: number; // how many leading bytes `head` covers, to detect replacement
  head: string; // hex sha1 of those bytes
}

export interface CheckpointData {
  run_id: string;
  files: Record<string, FileCheckpoint>;
  updated: string;
}

export function defaultStateDir(): string {
  return process.env.PUBLISHER_STATE_DIR || path.join(os.homedir(), '.sisyphus-publisher');
}

export class CheckpointStore {
  readonly file: string;
  /** Called with a message when a save fails; a failed save never stops publishing. */
  onWarn: (msg: string) => void = () => {};
  constructor(stateDir: string, readonly runId: string) {
    this.file = path.join(stateDir, `${runId.replace(/[^A-Za-z0-9._-]/g, '_')}.json`);
  }

  load(): CheckpointData | null {
    try {
      const d = JSON.parse(fs.readFileSync(this.file, 'utf8')) as CheckpointData;
      return d && typeof d.files === 'object' ? d : null;
    } catch {
      return null;
    }
  }

  /** Atomic write (tmp + rename). Windows can refuse the rename (EPERM/EBUSY: antivirus, indexer), so retry,
   *  then fall back to writing in place. Returns false (after warning) if nothing worked. */
  save(files: Record<string, FileCheckpoint>): boolean {
    const data: CheckpointData = { run_id: this.runId, files, updated: new Date().toISOString() };
    const json = JSON.stringify(data, null, 1);
    const tmp = `${this.file}.${process.pid}.tmp`;
    try {
      fs.mkdirSync(path.dirname(this.file), { recursive: true });
      fs.writeFileSync(tmp, json);
      let lastErr: unknown;
      for (let i = 0; i < 6; i++) {
        try { fs.renameSync(tmp, this.file); return true; } catch (e) {
          lastErr = e;
          Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 20 * (i + 1));
        }
      }
      fs.writeFileSync(this.file, json); // in place: not atomic, but better than losing progress
      try { fs.rmSync(tmp, { force: true }); } catch {}
      this.onWarn(`checkpoint rename failed (${(lastErr as Error).message}); wrote it in place`);
      return true;
    } catch (e) {
      this.onWarn(`checkpoint not saved (${(e as Error).message}); a restart may re-publish recent records, which is harmless`);
      try { fs.rmSync(tmp, { force: true }); } catch {}
      return false;
    }
  }
}
