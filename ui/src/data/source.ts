/** Where the Story view gets a run from. The default reads the run's JSONL files over HTTP (the dev server in
 *  vite.config.ts); with VITE_SPACETIMEDB_HOST set it reads a SpacetimeDB database instead (stdb/SCHEMA.md).
 *  Both feed the same Rec records, so nothing above this file knows which one is in use. */
import type { Rec, TRec } from './model'
import { httpSource } from './httpSource'
import { stdbSource, stdbConfig } from './stdbSource'

/** The spectator frame: the host window as a person sees it, written every few seconds, never shown to the agent. */
export type Spectator = { url: string; t: number }

export type Watch = {
  /** New records (any order, may repeat; the caller dedupes by writer and seq). */
  records(recs: Rec[]): void
  spectator(s: Spectator | null): void
  /** An image the source was loading has arrived: render again. */
  assets(): void
  error(e: Error): void
}

export interface DataSource {
  kind: 'jsonl' | 'spacetimedb'
  /** Short description for the footer of the run picker. */
  label: string
  listRuns(): Promise<{ name: string }[]>
  /** Load a run, then keep following it. Returns a function that stops. */
  watch(run: string, cb: Watch): () => void
  /** The saved image for a record's `artifact`, if it is available (yet). */
  imageUrl(run: string, rec: TRec): string | undefined
}

export const source: DataSource = stdbConfig ? stdbSource(stdbConfig) : httpSource
