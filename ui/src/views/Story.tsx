import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { CallItem, Run, TRec } from '../data/model'
import type { Spectator } from '../data/source'
import { Backdrop } from '../Backdrop'
import { HookDetail } from '../components/HookDetail'
import { Event, Header, Kits, Ledger, Screen } from '../components/panels'
import { Calls, Climb, Figures, MilestoneList, Reasoning, Room } from '../components/stories'
import { CallWindow, DemoHeader, DemoMetrics, GoalBox, ReasonStrip, Scrubber, TaskLog, ToolTiles, ToolWindow, VideoHero, HowDrawer } from '../components/demo'
import { MetricCharts } from '../components/MetricCharts'
import { span } from '../util'
import { Timeline } from '../components/Timeline'
import { SPEEDS, useNow } from '../util'

/** Three directions, chosen with #v=a|b|c: a broadsheet front page, the room first, the climb. */
type V = 'demo' | 'a' | 'b' | 'c' | 'a1' | 'a2' | 'a3' | 's1' | 's2' | 's3' | 'm1' | 'm2' | 'm3'

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
  const [speed, setSpeed] = useState(60)
  const [tool, setTool] = useState<string | null>(() => params.get('tool'))
  const [help, setHelp] = useState(false)
  const [call, setCall] = useState<CallItem | null>(null)
  // #call=last opens the newest task-log row (for linking a moment with its detail open).
  useEffect(() => { if (params.get('call') === 'last') setCall(run.calls(at).slice(-1)[0] ?? null) }, [])  // eslint-disable-line react-hooks/exhaustive-deps
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
      if (e.key === 'Escape') { setOpen(null); setTool(null); setHelp(false); setCall(null) }
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
  const v = (['demo', 'a', 'b', 'c', 'a1', 'a2', 'a3', 's1', 's2', 's3', 'm1', 'm2', 'm3'].includes(params.get('v') ?? '') ? params.get('v') : 'demo') as V
  const seek = (t: number) => setAt(Math.max(run.t0, Math.min(run.t1, t)))
  const active = !(following && !live) && run.handsActive(at)
  const g = run.latest('goal', at)
  const game = (
    <div className="box game">
      <Screen run={run} at={at} now={now} following={following} spectator={spectator} imageUrl={imageUrl} />
      <Event run={run} at={at} />
    </div>
  )
  const timeline = <Timeline run={run} at={at} playing={playing} speed={speed} live={live} onJump={jumpLive} onSeek={seek} onPlay={setPlaying} onSpeed={setSpeed} />
  const headline = (
    <section className="headline">
      <h1>{g?.text ?? (run.goalTitle || '—')}</h1>
      {g && <p className="deck">{g.serves && <>for {g.serves} · </>}set {span(at - g.t)} ago</p>}
    </section>
  )
  if (v === 'demo') return (
    <div className="stage-fit demo-stage">
      <div className="canvas v-demo" style={{ transform: `scale(${scale})` }}>
        <DemoHeader run={run} at={at} speed={speed} onSpeed={setSpeed} onHelp={() => setHelp(h => !h)} />
        <VideoHero run={run} runName={run.recs[0]?.run_id ?? ''} at={at} now={now} playing={playing} speed={speed} following={following} spectator={spectator} imageUrl={imageUrl} />
        <div className="strip">
          <Scrubber run={run} at={at} playing={playing} onPlay={setPlaying} onSeek={seek} />
          <GoalBox run={run} at={at} />
          <ReasonStrip run={run} at={at} />
        </div>
        <div className="right">
          <DemoMetrics run={run} at={at} compare={compare} />
          <TaskLog run={run} at={at} open={call?.t} onOpen={c => { setTool(null); setCall(o => (o?.t === c.t ? null : c)) }} />
          <ToolTiles run={run} at={at} open={tool} onOpen={k => { setCall(null); setTool(t => (t === k ? null : k)) }} />
        </div>
        {call && <CallWindow run={run} call={call} onClose={() => setCall(null)} />}
        {tool && <ToolWindow key={tool} run={run} at={at} kitId={tool} onClose={() => setTool(null)} />}
        {help && <HowDrawer onClose={() => setHelp(false)} />}
      </div>
    </div>
  )
  return (
    <div className="stage-fit">
      <div className={`canvas v-${v}${v[0] === 'a' && v.length === 2 ? ' v-at' : ''}${v[0] === 's' || v[0] === 'm' ? ' v-st' : ''}${v[0] === 'm' ? ' v-mt' : ''}`} style={{ transform: `scale(${scale})` }}>
        {v.length === 2 && <Backdrop active={active} />}
        {v[0] === 'm' && <Slope />}
        <Header run={run} at={at} now={now} onJump={jumpLive} />
        {(v[0] === 's' || v[0] === 'm') && <>
          {headline}
          <aside className="stats">
            <MilestoneList run={run} at={at} />
            <Figures run={run} at={at} />
            <section className="climbbox"><Climb run={run} at={at} w={374} h={170} onSeek={seek} /></section>
            <Ledger run={run} at={at} />
            <Kits run={run} at={at} onOpen={setOpen} />
          </aside>
          <div className="lead">{game}</div>
          <Reasoning run={run} at={at} now={now} />
          <Calls run={run} at={at} />
          <div className="tl paper">{timeline}</div>
        </>}
        {v[0] === 'a' && v.length === 2 && <>
          {headline}
          <aside className="stats">
            <MilestoneList run={run} at={at} />
            {v === 'a2'
              ? <><section className="climbbox"><Climb run={run} at={at} w={374} h={300} onSeek={seek} /></section><Figures run={run} at={at} /></>
              : <><Figures run={run} at={at} /><section className="climbbox"><Climb run={run} at={at} w={374} h={200} onSeek={seek} /></section></>}
            <Ledger run={run} at={at} />
          </aside>
          <div className="lead">{game}</div>
          <div className="tl"><Kits run={run} at={at} onOpen={setOpen} />{timeline}</div>
          <Room run={run} at={at} now={now} variant="app" />
        </>}
        {v === 'a' && <>
          {headline}
          <div className="lead">{game}</div>
          <div className="tl"><Kits run={run} at={at} onOpen={setOpen} />{timeline}</div>
          <aside className="stats">
            <Backdrop active={active} />
            <Figures run={run} at={at} />
            <section className="climbbox"><Climb run={run} at={at} w={388} h={190} onSeek={seek} labels={false} /></section>
            <Ledger run={run} at={at} />
          </aside>
          <Room run={run} at={at} now={now} variant="letters" />
        </>}
        {v === 'b' && <>
          <div className="left">
            {game}
            <MetricCharts run={run} at={at} compare={compare} widths={[272, 272, 272, 272]} />
          </div>
          <Room run={run} at={at} now={now} variant="chat" />
          <aside className="stats">
            <Backdrop active={active} />
            {headline}
            <section className="climbbox"><Climb run={run} at={at} w={454} h={230} onSeek={seek} /></section>
            <Ledger run={run} at={at} />
            <Kits run={run} at={at} onOpen={setOpen} />
          </aside>
          <div className="tl">{timeline}</div>
        </>}
        {v === 'c' && <>
          <section className="mountain">
            <Backdrop active={active} />
            {headline}
            <Climb run={run} at={at} w={1250} h={540} onSeek={seek} />
          </section>
          {game}
          <Room run={run} at={at} now={now} variant="inscriptions" />
          <section className="tablet"><Figures run={run} at={at} /><Kits run={run} at={at} onOpen={setOpen} /><Ledger run={run} at={at} /></section>
          <div className="tl">{timeline}</div>
        </>}
        {openHook && <HookDetail hook={openHook} run={run} onClose={() => setOpen(null)} />}
      </div>
    </div>
  )
}

/** The slope Sisyphus climbs, as one line from the bottom edge of the screen to the top edge, continuing the surface of the
 *  asset's slope under his feet (the figure is placed so it stands on it; see .v-mt > .backdrop in styles.css). */
function Slope() {
  return <svg className="slope" width={1920} height={1080} viewBox="0 0 1920 1080" aria-hidden="true"><line x1={152.5} y1={1080} x2={1538.6} y2={0} /></svg>
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
