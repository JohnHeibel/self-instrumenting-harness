import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { Run, TRec } from '../data/model'
import type { Spectator } from '../data/source'
import { Backdrop } from '../Backdrop'
import { HookDetail } from '../components/HookDetail'
import { Actions, Event, Goal, Header, Kits, Ledger, Now, Screen } from '../components/panels'
import { MetricCharts } from '../components/MetricCharts'
import { Column } from '../components/Column'
import { Timeline } from '../components/Timeline'
import { SPEEDS, useNow } from '../util'

/** A run, live or finished: follows the newest moment by default; scrub, play at timelapse speed, jump between
 *  key moments, open any hook. `t=live` in the URL means following. */
export function Story({ run, compare, spectator, imageUrl }: { run: Run; compare?: Run | null; spectator: Spectator | null; imageUrl: (r: TRec) => string | undefined }) {
  const now = useNow(1000)
  const live = run.isLive(now)
  const params = new URLSearchParams(location.hash.slice(1))
  const [at, setAt] = useState(() => params.get('t') === 'live' ? run.t1
    : Number(params.get('t')) ? run.t0 + Number(params.get('t')) * 1000 : run.isLive(Date.now()) ? run.t1 : run.t0)
  // Following: when you're at the newest moment and new records arrive, stay at the newest moment.
  const lastEnd = useRef(run.t1)
  useEffect(() => {
    if (run.t1 !== lastEnd.current) {
      setAt(a => (a >= lastEnd.current - 2000 ? run.t1 : a))
      lastEnd.current = run.t1
    }
  }, [run.t1])
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
      const raw = new URLSearchParams(location.hash.slice(1)).get('t')
      if (raw === 'live') setAt(run.t1)
      else if (Number.isFinite(Number(raw))) setAt(run.t0 + Number(raw) * 1000)
    }
    addEventListener('hashchange', on)
    return () => removeEventListener('hashchange', on)
  }, [run.t0, run.t1])

  // Keep the position in the URL so a moment can be linked or reloaded.
  useEffect(() => {
    const id = setTimeout(() => {
      const p = new URLSearchParams(location.hash.slice(1))
      p.set('t', live && at >= run.t1 - 2000 ? 'live' : String(Math.round((at - run.t0) / 1000)))
      history.replaceState(null, '', '#' + p.toString())
    }, 300)
    return () => clearTimeout(id)
  }, [at, run.t0, run.t1, live])
  const jumpLive = () => { setPlaying(false); setAt(run.t1) }
  const following = live && at >= run.t1 - 2000

  const hooks = run.hooks(at)
  const openHook = open ? hooks.find(h => h.name === open) : undefined
  return (
    <div className="stage-fit">
      <div className="canvas" style={{ transform: `scale(${scale})` }}>
        <Column side="left" height={1032} />
        <Column side="right" height={1032} />
        <Header run={run} at={at} now={now} onJump={jumpLive} />
        <div className="box hero">
          <Screen run={run} at={at} now={now} following={following} spectator={spectator} imageUrl={imageUrl} />
          <Event run={run} at={at} />
        </div>
        <MetricCharts run={run} at={at} compare={compare} />
        <Timeline run={run} at={at} playing={playing} speed={speed} live={live} onJump={jumpLive}
                  onSeek={t => setAt(Math.max(run.t0, Math.min(run.t1, t)))} onPlay={setPlaying} onSpeed={setSpeed} />
        <aside className="side">
          <Goal run={run} at={at} />
          <Now run={run} at={at} now={now} />
          <Ledger run={run} at={at} />
          <Kits run={run} at={at} onOpen={setOpen} />
          <div className="sidefoot">
            <Actions run={run} at={at} />
            <div className="box figure"><Backdrop /></div>
          </div>
        </aside>
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
