import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { Run, TRec } from '../data/model'
import type { Spectator } from '../data/source'
import { Backdrop } from '../Backdrop'
import { Leverage } from '../components/Leverage'
import { HookDetail } from '../components/HookDetail'
import { CoverageChart, TimeChart, TokensChart, ToolsChart } from '../components/charts'
import { Event, Feed, Goal, Header, HookLibrary, Milestones, Mind, ReadLine, Screen } from '../components/panels'
import { Timeline } from '../components/Timeline'
import { SPEEDS, useNow } from '../util'

/** A run, live or finished: follows the newest moment by default; scrub, play at timelapse speed, jump between
 *  key moments, open any hook. `t=live` in the URL means following. */
export function Story({ run, spectator, imageUrl }: { run: Run; spectator: Spectator | null; imageUrl: (r: TRec) => string | undefined }) {
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
  const opening = at - run.t0 < 90_000
  return (
    <div className="stage-fit">
      <div className="canvas" style={{ transform: `scale(${scale})` }}>
        <Backdrop />
        <Header run={run} at={at} now={now} onJump={jumpLive} />
        <Milestones items={run.milestones(at)} />
        <div className="main">
          <div className="left">
            <div className="screenwrap">
              <Screen run={run} at={at} now={now} following={following} spectator={spectator} imageUrl={imageUrl} />
              <div className="over top"><ReadLine run={run} at={at} /></div>
              <div className="over bottom"><Event run={run} at={at} /></div>
            </div>
            <div className="charts">
              <TokensChart run={run} at={at} />
              <TimeChart run={run} at={at} />
              <CoverageChart run={run} at={at} />
              <ToolsChart run={run} at={at} />
            </div>
          </div>
          <div className="right">
            <Goal run={run} at={at} opening={opening} />
            <Mind run={run} at={at} playing={playing || following} opening={opening} />
            <HookLibrary hooks={hooks} onOpen={setOpen} />
            <Leverage run={run} at={at} />
            <Feed run={run} at={at} items={run.feed(at, 2)} />
          </div>
        </div>
        <Timeline run={run} at={at} playing={playing} speed={speed} live={live} onJump={jumpLive}
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
