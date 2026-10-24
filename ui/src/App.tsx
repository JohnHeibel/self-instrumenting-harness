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
    fetch(`/api/runs/${encodeURIComponent(runName)}/records`)
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`${r.status} loading ${runName}`)))
      .then((recs: Rec[]) => setRun(new Run(recs)), e => setError(String(e)))
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
