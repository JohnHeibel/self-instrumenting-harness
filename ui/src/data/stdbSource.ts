import type { Rec, TRec } from './model'
import type { DataSource, Watch } from './source'

/** SpacetimeDB source, through the SQL-over-HTTP API (stdb/SCHEMA.md, "Query shapes for the UI").
 *  Replay loads the run in time windows (`ts_us` ranges); live polls for records newer than the last one seen.
 *  Thumbnails are fetched one at a time by key when a screenshot is first shown, the spectator frame is the
 *  fixed key `<run>|live|latest`. The TypeScript SDK would need bindings generated from the module, and
 *  SCHEMA.md documents the HTTP API only, so the UI uses that. */
export type StdbConfig = { base: string; db: string }

// SpacetimeDB is the default source (a local server on port 3000); VITE_SPACETIMEDB_HOST points elsewhere, and `off`
// reads the run files only. When the server can't be reached the UI falls back to the files (source.ts).
const envHost = import.meta.env.VITE_SPACETIMEDB_HOST as string | undefined
const host = envHost === 'off' ? undefined : envHost || 'ws://127.0.0.1:3000'
export const stdbConfig: StdbConfig | null = host
  ? { base: host.replace(/^ws/, 'http').replace(/\/+$/, ''), db: (import.meta.env.VITE_SPACETIMEDB_DB_NAME as string | undefined) || 'harness-events' }
  : null

const LIVE_POLL_MS = 2_000, ENDED_POLL_MS = 30_000, SPECTATOR_POLL_MS = 5_000
const WINDOWS = 24, CONCURRENCY = 4
const q = (s: string) => `'${s.replace(/'/g, "''")}'`

type Row = Record<string, unknown>

export function stdbSource({ base, db }: StdbConfig): DataSource {
  async function sql(query: string): Promise<Row[]> {
    const r = await fetch(`${base}/v1/database/${encodeURIComponent(db)}/sql`, { method: 'POST', body: query })
    if (!r.ok) throw new Error(`SpacetimeDB ${r.status}: ${(await r.text()).slice(0, 200)}`)
    const [res] = await r.json() as { schema: { elements: { name: { some?: string } }[] }; rows: unknown[][] }[]
    const names = res.schema.elements.map(e => e.name.some ?? '')
    return res.rows.map(row => Object.fromEntries(row.map((v, i) => [names[i], v])))
  }

  const parse = (rows: Row[]): Rec[] => rows.flatMap(r => {
    try { return [JSON.parse(String(r.payload)) as Rec] } catch { return [] }
  })

  const blobs = new Map<string, string | null>()     // rkey -> object URL (null: not available)
  const asked = new Map<string, number>()
  const hexBlob = (hex: string, mime: string) => {
    const bytes = new Uint8Array(hex.length / 2)
    for (let i = 0; i < bytes.length; i++) bytes[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16)
    return new Blob([bytes], { type: mime })
  }
  /** `bytes` arrives as hex text in the SQL API; some versions return a number array. */
  const toBlob = (v: unknown, mime: string) => typeof v === 'string' ? hexBlob(v, mime) : new Blob([new Uint8Array(v as number[])], { type: mime })

  let notify: (() => void) | undefined

  return {
    kind: 'spacetimedb',
    label: `SpacetimeDB ${db} at ${base}`,

    async listRuns() {
      const rows = await sql('SELECT * FROM run')
      return rows.sort((a, b) => Number(b.last_ts_us) - Number(a.last_ts_us)).map(r => ({ name: String(r.run_id) }))
    },

    imageUrl(run, rec: TRec) {
      if (!rec.artifact) return undefined
      const key = `${run}|${rec.writer}|${rec.seq}`
      const have = blobs.get(key)
      if (have !== undefined) {
        if (have === null && Date.now() - (asked.get(key) ?? 0) > 60_000) blobs.delete(key)  // maybe uploaded since
        else return have ?? undefined
      }
      if (!asked.has(key) || Date.now() - asked.get(key)! > 60_000) {
        asked.set(key, Date.now())
        sql(`SELECT mime, bytes FROM thumb WHERE rkey = ${q(key)}`).then(rows => {
          blobs.set(key, rows[0] ? URL.createObjectURL(toBlob(rows[0].bytes, String(rows[0].mime))) : null)
          notify?.()
        }, () => { blobs.set(key, null) })
      }
      return undefined
    },

    watch(run: string, cb: Watch) {
      let stopped = false, timer = 0, stimer = 0
      notify = () => cb.assets()

      const live = async (from: number) => {
        let last = from, ended = false, first = true
        while (!stopped) {
          try {
            const recs = parse(await sql(`SELECT payload FROM record WHERE run_id = ${q(run)} AND ts_us >= ${Math.max(0, last - 30_000_000)}`))
            for (const r of recs) { last = Math.max(last, Date.parse(r.ts) * 1000); if (r.type === 'run_ended') ended = true }
            if (recs.length || first) cb.records(recs)
            first = false
          } catch { /* the server is restarting; try again */ }
          await new Promise(res => { timer = window.setTimeout(res, ended ? ENDED_POLL_MS : LIVE_POLL_MS) })
        }
      }

      const load = async () => {
        const [info] = await sql(`SELECT * FROM run WHERE run_id = ${q(run)}`)
        if (!info) throw new Error(`no run named ${run} in ${db}`)
        const a = Number(info.first_ts_us), b = Number(info.last_ts_us) + 1
        const w = Math.max(15 * 60_000_000, Math.ceil((b - a) / WINDOWS))
        const ranges: [number, number][] = []
        for (let t = a; t < b; t += w) ranges.push([t, Math.min(b, t + w)])
        const out: Rec[] = []
        let next = 0
        await Promise.all(Array.from({ length: CONCURRENCY }, async () => {
          while (next < ranges.length && !stopped) {
            const [lo, hi] = ranges[next++]
            out.push(...parse(await sql(`SELECT payload FROM record WHERE run_id = ${q(run)} AND ts_us >= ${lo} AND ts_us < ${hi}`)))
          }
        }))
        if (stopped) return
        cb.records(out)
        live(Math.max(a, ...out.map(r => Date.parse(r.ts) * 1000)))
      }
      load().catch(e => cb.error(e as Error))

      // Spectator frame: a fixed thumb key, overwritten by the publisher every few seconds.
      let sig = ''
      const spectate = async () => {
        if (stopped) return
        try {
          const [row] = await sql(`SELECT mime, bytes FROM thumb WHERE rkey = ${q(`${run}|live|latest`)}`)
          if (row) {
            const bytes = row.bytes
            const s = typeof bytes === 'string' ? `${bytes.length}:${bytes.slice(-64)}` : String((bytes as number[]).length)
            if (s !== sig) { sig = s; cb.spectator({ url: URL.createObjectURL(toBlob(bytes, String(row.mime))), t: Date.now() }) }
          }
        } catch { /* ignore */ }
        stimer = window.setTimeout(spectate, SPECTATOR_POLL_MS)
      }
      spectate()
      return () => { stopped = true; clearTimeout(timer); clearTimeout(stimer); notify = undefined }
    },
  }
}
