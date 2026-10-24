import { useEffect, useState } from 'react'
import { Run, type Rec } from './data/model'
import { Story } from './views/Story'

/** Route: #run=<name>&t=<seconds>. With no run, list the local runs. */
export default function App() {
  const [runName, setRunName] = useState(() => new URLSearchParams(location.hash.slice(1)).get('run'))
  const [run, setRun] = useState<Run | null>(null)
  const [runs, setRuns] = useState<{ name: string }[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const on = () => setRunName(new URLSearchParams(location.hash.slice(1)).get('run'))
    addEventListener('hashchange', on)
    return () => removeEventListener('hashchange', on)
  }, [])

  useEffect(() => {
    setRun(null); setError(null)
    if (!runName) { fetch('/api/runs').then(r => r.json()).then(setRuns, e => setError(String(e))); return }
    // Load the run, then keep polling for new records so a running run updates live.
    const url = `/api/runs/${encodeURIComponent(runName)}/records`
    const seen = new Map<string, Rec>()
    let stopped = false, timer = 0
    const merge = (recs: Rec[]) => {
      let added = 0
      for (const r of recs) {
        const key = `${r.writer}:${r.seq}`
        if (!seen.has(key)) { seen.set(key, r); added++ }
      }
      return added
    }
    const poll = async () => {
      if (stopped) return
      let latest = ''
      for (const r of seen.values()) if (r.ts > latest) latest = r.ts
      // Re-ask for the last 30 s too: writers flush independently, so a record can land slightly late.
      const since = latest ? new Date(Date.parse(latest) - 30_000).toISOString() : ''
      try {
        const r = await fetch(since ? `${url}?since=${encodeURIComponent(since)}` : url, { cache: 'no-store' })
        if (!r.ok) throw new Error(`${r.status} loading ${runName}`)
        const added = merge(await r.json())
        if (added || !since) setRun(new Run([...seen.values()]))
      } catch (e) {
        if (!seen.size) { setError(String(e)); return }
      }
      const ended = [...seen.values()].some(x => x.type === 'run_ended')
      timer = window.setTimeout(poll, ended ? 30_000 : 2_000)
    }
    poll()
    return () => { stopped = true; clearTimeout(timer) }
  }, [runName])

  if (error) return <div className="picker">Couldn't load: {error}</div>
  if (runName && !run) return <div className="picker">Loading {runName}…</div>
  if (run) return <Story run={run} artifactUrl={p => `/runs/${encodeURIComponent(runName!)}/${p}`} />
  return (
    <div className="picker">
      <h2 style={{ fontWeight: 500 }}>Runs</h2>
      {runs?.length ? runs.map(r => <a key={r.name} href={`#run=${encodeURIComponent(r.name)}`}>{r.name}</a>)
        : <p style={{ color: 'var(--text-muted)' }}>No runs in runs/. Generate one with tools/fakerun/generate.py.</p>}
    </div>
  )
}
