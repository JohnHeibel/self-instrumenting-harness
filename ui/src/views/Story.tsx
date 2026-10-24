import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { Run } from '../data/model'
import { HookDetail } from '../components/HookDetail'
import { CostChart, CoverageChart } from '../components/charts'
import { Relief } from '../components/Relief'
import { Event, Feed, Goal, Header, HookLibrary, Milestones, Mind, ReadLine, Screen } from '../components/panels'
import { SPEEDS, Timeline } from '../components/Timeline'

/** Replay of a whole run: scrub, play at timelapse speed, jump between key moments, open any hook. */
export function Story({ run, artifactUrl }: { run: Run; artifactUrl: (p: string) => string }) {
  const params = new URLSearchParams(location.hash.slice(1))
  const [at, setAt] = useState(() => Number(params.get('t')) ? run.t0 + Number(params.get('t')) * 1000 : run.t0)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(600)
  const [open, setOpen] = useState<string | null>(() => params.get('hook'))
  const scale = useFit()

  useEffect(() => {
    if (!playing) return
    let last = performance.now(), raf = 0
    const tick = (now: number) => {
      const dt = (now - last) * speed  // measured before `last` moves; the updater below runs later
      last = now
      setAt(a => {
        const next = a + dt
        if (next >= run.t1) { setPlaying(false); return run.t1 }
        return next
      })
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [playing, speed, run.t1])

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      // Space toggles playback, except when a button or field has focus (there it presses the button or types).
      const el = e.target instanceof Element ? e.target.closest('button, input, textarea, select, a, [contenteditable]') : null
      if (e.key === ' ' && !el) { e.preventDefault(); setPlaying(p => !p) }
      if (e.key === 'Escape') setOpen(null)
      const i = SPEEDS.indexOf(speed)
      if (e.key === ']' && i < SPEEDS.length - 1) setSpeed(SPEEDS[i + 1])
      if (e.key === '[' && i > 0) setSpeed(SPEEDS[i - 1])
    }
    addEventListener('keydown', key)
    return () => removeEventListener('keydown', key)
  }, [speed])

  // Follow links that change #t= while the page is open.
  useEffect(() => {
    const on = () => {
      const t = Number(new URLSearchParams(location.hash.slice(1)).get('t'))
      if (Number.isFinite(t)) setAt(run.t0 + t * 1000)
    }
    addEventListener('hashchange', on)
    return () => removeEventListener('hashchange', on)
  }, [run.t0])

  // Keep the position in the URL so a moment can be linked or reloaded.
  useEffect(() => {
    const id = setTimeout(() => {
      const p = new URLSearchParams(location.hash.slice(1)); p.set('t', String(Math.round((at - run.t0) / 1000)))
      history.replaceState(null, '', '#' + p.toString())
    }, 300)
    return () => clearTimeout(id)
  }, [at, run.t0])

  const hooks = run.hooks(at)
  const openHook = open ? hooks.find(h => h.name === open) : undefined
  const opening = at - run.t0 < 90_000
  return (
    <div className="stage-fit">
      <div className="canvas" style={{ transform: `scale(${scale})` }}>
        <Relief />
        <Header run={run} at={at} />
        <Milestones items={run.milestones(at)} />
        <div className="main">
          <div className="left">
            <div className="screenwrap">
              <Screen run={run} at={at} artifactUrl={artifactUrl} />
              <div className="over top"><ReadLine run={run} at={at} /></div>
              <div className="over bottom"><Event run={run} at={at} /></div>
            </div>
            <div className="charts">
              <CoverageChart run={run} at={at} />
              <CostChart run={run} at={at} />
            </div>
          </div>
          <div className="right">
            <Goal run={run} at={at} opening={opening} />
            <Mind run={run} at={at} playing={playing} opening={opening} />
            <HookLibrary hooks={hooks} onOpen={setOpen} />
            <Feed run={run} at={at} items={run.feed(at, 4)} />
          </div>
        </div>
        <Timeline run={run} at={at} playing={playing} speed={speed}
                  onSeek={t => setAt(Math.max(run.t0, Math.min(run.t1, t)))} onPlay={setPlaying} onSpeed={setSpeed} />
        {openHook && <HookDetail hook={openHook} run={run} onClose={() => setOpen(null)} />}
      </div>
    </div>
  )
}

function useFit(): number {
  const [s, setS] = useState(1)
  const raf = useRef(0)
  useLayoutEffect(() => {
    const fit = () => setS(Math.min(innerWidth / 1920, innerHeight / 1080))
    const on = () => { cancelAnimationFrame(raf.current); raf.current = requestAnimationFrame(fit) }
    fit(); addEventListener('resize', on)
    return () => removeEventListener('resize', on)
  }, [])
  return s
}
