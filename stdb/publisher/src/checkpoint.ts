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

  save(files: Record<string, FileCheckpoint>): void {
    fs.mkdirSync(path.dirname(this.file), { recursive: true });
    const data: CheckpointData = { run_id: this.runId, files, updated: new Date().toISOString() };
    const tmp = `${this.file}.${process.pid}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(data, null, 1));
    fs.renameSync(tmp, this.file);
  }
}
