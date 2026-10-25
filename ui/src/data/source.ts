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

/** A recorded video of the host for a run (OBS for games, a browser recording for Odoo): `runs/<run>/video.json` says which
 *  file and the UTC time its first frame shows, which puts it on the event log's clock. */
/** Pixels to crop off each edge of the picture (in the source's own pixels), e.g. a window title bar. Applied in CSS. */
export type Crop = { top: number; left: number; right: number; bottom: number }
export type VideoInfo = { url: string; start: number; crop?: Crop; frameCrop?: Crop }

export interface DataSource {
  kind: 'jsonl' | 'spacetimedb'
  /** Short description for the footer of the run picker. */
  label: string
  listRuns(): Promise<{ name: string }[]>
  /** Load a run, then keep following it. Returns a function that stops. */
  watch(run: string, cb: Watch): () => void
  /** The saved image for a record's `artifact`, if it is available (yet). */
  imageUrl(run: string, rec: TRec): string | undefined
  /** The run's recorded video, if it has one. */
  video?(run: string): Promise<VideoInfo | null>
  /** Harness run → its raw baseline run (runs/compare.json). */
  pairs?(): Promise<Record<string, string>>
}

/** SpacetimeDB first; when it can't be reached (listing or loading fails), the run files over HTTP. */
function withFallback(primary: DataSource, secondary: DataSource): DataSource {
  let use = primary
  return {
    get kind() { return use.kind },
    get label() { return use.label },
    async listRuns() {
      try { return await primary.listRuns() } catch { use = secondary; return secondary.listRuns() }
    },
    watch(run, cb) {
      let switched = false
      let stop = use.watch(run, { ...cb, error: e => {
        if (switched || use === secondary) { cb.error(e); return }
        switched = true; use = secondary; stop(); stop = secondary.watch(run, cb)
      } })
      return () => stop()
    },
    imageUrl: (run, rec) => use.imageUrl(run, rec),
    video: run => (secondary.video ? secondary.video(run) : Promise.resolve(null)),
    pairs: () => (secondary.pairs ? secondary.pairs() : Promise.resolve({})),
  }
}

export const source: DataSource = stdbConfig ? withFallback(stdbSource(stdbConfig), httpSource) : httpSource
