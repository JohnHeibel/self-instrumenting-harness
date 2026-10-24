import type { Rec } from './model'
import type { DataSource, Watch } from './source'

const enc = encodeURIComponent
const LIVE_POLL_MS = 2_000, ENDED_POLL_MS = 30_000, SPECTATOR_POLL_MS = 5_000

/** The dev server (vite.config.ts) serves a run directory:
 *  /api/runs, /api/runs/<run>/records[?since=<ts>], /runs/<run>/shots/<img>, /runs/<run>/live/latest.jpg */
export const httpSource: DataSource = {
  kind: 'jsonl',
  label: 'run files over HTTP',

  async listRuns() {
    const r = await fetch('/api/runs')
    if (!r.ok) throw new Error(`${r.status} listing runs`)
    return r.json()
  },

  imageUrl: (run, rec) => (rec.artifact ? `/runs/${enc(run)}/${rec.artifact}` : undefined),

  watch(run: string, cb: Watch) {
    const url = `/api/runs/${enc(run)}/records`
    const spectatorUrl = `/runs/${enc(run)}/live/latest.jpg`
    let stopped = false, timer = 0, stimer = 0, since = '', ended = false, loaded = false

    // Load everything, then keep asking for what is new. Writers flush independently, so re-ask for the last 30 s too.
    const poll = async () => {
      if (stopped) return
      try {
        const r = await fetch(since ? `${url}?since=${enc(since)}` : url, { cache: 'no-store' })
        if (!r.ok) throw new Error(`${r.status} loading ${run}`)
        const recs: Rec[] = await r.json()
        for (const x of recs) {
          if (x.ts > since) since = x.ts
          if (x.type === 'run_ended') ended = true
        }
        if (since) since = new Date(Date.parse(since) - 30_000).toISOString()
        loaded = true
        cb.records(recs)
      } catch (e) {
        if (!loaded) { cb.error(e as Error); return }
      }
      timer = window.setTimeout(poll, ended ? ENDED_POLL_MS : LIVE_POLL_MS)
    }

    // The spectator frame changes every few seconds; its Last-Modified says when.
    let lastMod = ''
    const spectate = async () => {
      if (stopped) return
      try {
        const r = await fetch(spectatorUrl, { method: 'HEAD', cache: 'no-store' })
        if (r.ok) {
          const mod = r.headers.get('last-modified') ?? ''
          if (mod !== lastMod) { lastMod = mod; cb.spectator({ url: `${spectatorUrl}?v=${enc(mod)}`, t: Date.parse(mod) || Date.now() }) }
        } else if (lastMod) { lastMod = ''; cb.spectator(null) }
      } catch { /* the server is restarting; try again */ }
      stimer = window.setTimeout(spectate, SPECTATOR_POLL_MS)
    }

    poll(); spectate()
    return () => { stopped = true; clearTimeout(timer); clearTimeout(stimer) }
  },
}
