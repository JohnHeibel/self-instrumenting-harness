import { useEffect, useState } from 'react'
import { Run, type Rec } from './data/model'
import { source, type Spectator } from './data/source'
import { Story } from './views/Story'

/** Route: #run=<name>&t=<seconds | live>. With no run, list the runs the data source knows. */
export default function App() {
  const [runName, setRunName] = useState(() => new URLSearchParams(location.hash.slice(1)).get('run'))
  const [run, setRun] = useState<Run | null>(null)
  const [spectator, setSpectator] = useState<Spectator | null>(null)
  const [, setAssets] = useState(0)
  const [runs, setRuns] = useState<{ name: string }[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const on = () => setRunName(new URLSearchParams(location.hash.slice(1)).get('run'))
    addEventListener('hashchange', on)
    return () => removeEventListener('hashchange', on)
  }, [])

  useEffect(() => {
    setRun(null); setError(null); setSpectator(null)
    if (!runName) { source.listRuns().then(setRuns, e => setError(String(e))); return }
    // Merge what the source delivers by writer and seq; rebuild the model at most every 250 ms.
    const seen = new Map<string, Rec>()
    let timer = 0, last = 0, dirty = false, imgTimer = 0
    const flush = () => { timer = 0; last = performance.now(); if (dirty) { dirty = false; setRun(new Run([...seen.values()])) } }
    const stop = source.watch(runName, {
      records: recs => {
        for (const r of recs) { const k = `${r.writer}:${r.seq}`; if (!seen.has(k)) { seen.set(k, r); dirty = true } }
        if (!dirty) return
        if (!timer) timer = window.setTimeout(flush, Math.max(0, 250 - (performance.now() - last)))
      },
      spectator: setSpectator,
      assets: () => { if (!imgTimer) imgTimer = window.setTimeout(() => { imgTimer = 0; setAssets(n => n + 1) }, 150) },
      error: e => setError(String(e)),
    })
    return () => { stop(); clearTimeout(timer); clearTimeout(imgTimer) }
  }, [runName])

  if (error) return <div className="picker">Couldn't load: {error}</div>
  if (runName && !run) return <div className="picker">Loading {runName}…</div>
  if (run) return <Story key={runName} run={run} spectator={spectator} imageUrl={rec => source.imageUrl(runName!, rec)} />
  return (
    <div className="picker">
      <h2 style={{ fontWeight: 500 }}>Runs</h2>
      {runs?.length ? runs.map(r => <a key={r.name} href={`#run=${encodeURIComponent(r.name)}`}>{r.name}</a>)
        : <p style={{ color: 'var(--muted)' }}>No runs found in {source.label}. Generate one with tools/fakerun/generate.py.</p>}
      <p className="src">Reading {source.label}</p>
    </div>
  )
}
