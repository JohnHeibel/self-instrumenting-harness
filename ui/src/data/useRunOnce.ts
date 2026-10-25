import { useEffect, useState } from 'react'
import { Run, type Rec } from './model'
import { source } from './source'

/** Load a run once (the comparison is a replay of two recorded runs). */
export function useRunOnce(name: string | null): Run | null {
  const [run, setRun] = useState<Run | null>(null)
  useEffect(() => {
    setRun(null)
    if (!name) return
    let dead = false, stop: (() => void) | null = null
    const seen = new Map<string, Rec>()
    stop = source.watch(name, {
      records: recs => {
        for (const r of recs) seen.set(`${r.writer}:${r.seq}`, r)
        if (!dead) setRun(new Run([...seen.values()]))
      },
      spectator: () => {}, assets: () => {}, error: () => {},
    })
    return () => { dead = true; stop?.() }
  }, [name])
  return run
}

