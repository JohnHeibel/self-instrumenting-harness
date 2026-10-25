import { useEffect, useMemo, useRef, useState } from 'react'
import type { Kit, Run, TRec } from '../data/model'
import { source, type Spectator, type VideoInfo } from '../data/source'
import { Event, Screen } from './panels'
import { MetricCharts } from './MetricCharts'
import { Calls } from './stories'
import { SPEEDS, diffLines, hms, span } from '../util'

const DASH = '—'

/** Header: title, run switcher, model · agent, replay speed, run clock, and the "?" drawer. */
export function DemoHeader({ run, at, speed, onSpeed, onHelp }: { run: Run; at: number; speed: number; onSpeed: (s: number) => void; onHelp: () => void }) {
  const [runs, setRuns] = useState<string[]>([])
  const current = run.recs[0]?.run_id ?? ''
  useEffect(() => { source.listRuns().then(r => setRuns(r.map(x => x.name)), () => setRuns([])) }, [])
  const h = run.harness
  const go = (name: string) => {
    const p = new URLSearchParams(location.hash.slice(1))
    p.set('run', name); p.delete('t')
    location.hash = p.toString()
  }
  return (
    <header className="dh">
      <span className="dtitle">Sisyphus</span>
      <select className="runs" value={current} onChange={e => go(e.target.value)} aria-label="Run">
        {(runs.includes(current) ? runs : [current, ...runs]).map(r => <option key={r} value={r}>{r}</option>)}
      </select>
      <span className="rig">{[h.model, h.agent].filter(Boolean).join(' · ') || DASH}</span>
      <span className="speeds" role="group" aria-label="Replay speed">
        {SPEEDS.map(s => <button key={s} className={s === speed ? 'on' : ''} onClick={() => onSpeed(s)}>{s}×</button>)}
      </span>
      <span className="dclock">{hms(at - run.t0)}</span>
      <button className="help" onClick={onHelp} aria-label="How it works">?</button>
    </header>
  )
}

/** The recorded video, on the event log's clock: `at` is the master time. At speeds a video can play (≤16×) it plays at
 *  that rate and is nudged back when it drifts more than a second; faster, or paused, it is seeked to `at` (at most five
 *  times a second). Without a video, the run's saved frames. */
export function VideoHero({ run, runName, at, now, playing, speed, following, spectator, imageUrl }: {
  run: Run; runName: string; at: number; now: number; playing: boolean; speed: number; following: boolean
  spectator: Spectator | null; imageUrl: (r: TRec) => string | undefined
}) {
  const [video, setVideo] = useState<VideoInfo | null>(null)
  const el = useRef<HTMLVideoElement>(null)
  const lastSeek = useRef(0)
  const [ready, setReady] = useState(0)  // bumps when the video can seek, so a seek asked for before then is redone
  useEffect(() => { let dead = false; setVideo(null); source.video?.(runName).then(v => { if (!dead) setVideo(v) }, () => {}); return () => { dead = true } }, [runName])
  useEffect(() => {
    const v = el.current
    if (!v || !video || v.readyState < 1) return
    const target = (at - video.start) / 1000
    if (target < 0 || (v.duration && target > v.duration)) { v.pause(); return }
    const native = playing && speed <= 16
    if (native) {
      if (v.playbackRate !== speed) v.playbackRate = speed
      if (v.paused) v.play().catch(() => {})
      if (Math.abs(v.currentTime - target) > 1) v.currentTime = target
    } else {
      if (!v.paused) v.pause()
      const t = performance.now()
      if (Math.abs(v.currentTime - target) > 0.2 && t - lastSeek.current > 200) { lastSeek.current = t; v.currentTime = target }
    }
  }, [at, playing, speed, video, ready])
  const inRange = video && at >= video.start
  return (
    <div className="hero">
      {video && <video ref={el} src={video.url} muted playsInline preload="auto" onLoadedMetadata={() => { lastSeek.current = 0; setReady(n => n + 1) }}
        style={{ visibility: inRange ? 'visible' : 'hidden' }} />}
      {(!video || !inRange) && <Screen run={run} at={at} now={now} following={following} spectator={spectator} imageUrl={imageUrl} />}
      <Event run={run} at={at} />
    </div>
  )
}

type Chapter = { t: number; kind: 'milestone' | 'tool' | 'death'; label: string }

/** Play/pause and a scrubber with chapter marks for milestones, tool changes and deaths; click a mark or the track to jump. */
export function Scrubber({ run, at, playing, onPlay, onSeek }: { run: Run; at: number; playing: boolean; onPlay: (p: boolean) => void; onSeek: (t: number) => void }) {
  const track = useRef<HTMLDivElement>(null)
  const dur = Math.max(1, run.t1 - run.t0)
  const chapters = useMemo<Chapter[]>(() => {
    const names = new Map(run.milestonesDefined.map(m => [m.id, m.name]))
    const seen = new Set<string>(), out: Chapter[] = []
    for (const m of run.of('milestone')) if (!seen.has(m.id)) { seen.add(m.id); out.push({ t: m.t, kind: 'milestone', label: names.get(m.id) ?? m.id }) }
    const ext = new Set<string>()
    for (const h of run.of('hook_loaded')) { const k = `${h.extension || h.hook}v${h.version}`; if (!ext.has(k)) { ext.add(k); out.push({ t: h.t, kind: 'tool', label: `${h.extension || h.hook} v${h.version}` }) } }
    for (const m of run.of('marker')) if (m.kind === 'agent_died') out.push({ t: m.t, kind: 'death', label: String(m.label ?? 'died') })
    return out.sort((a, b) => a.t - b.t)
  }, [run])
  const x = (t: number) => `${((t - run.t0) / dur) * 100}%`
  const seekAt = (cx: number) => { const r = track.current!.getBoundingClientRect(); onSeek(run.t0 + Math.max(0, Math.min(1, (cx - r.left) / r.width)) * dur) }
  return (
    <div className="scrub">
      <button className="play" onClick={() => onPlay(!playing)} aria-label={playing ? 'Pause' : 'Play'}>
        <svg viewBox="0 0 16 16" width="18" height="18">{playing ? <path d="M4 2h3v12H4zM9 2h3v12H9z" /> : <path d="M4 2l10 6-10 6z" />}</svg>
      </button>
      <div className="track" ref={track} onMouseDown={e => { seekAt(e.clientX); const mv = (ev: MouseEvent) => seekAt(ev.clientX); const up = () => { removeEventListener('mousemove', mv); removeEventListener('mouseup', up) }; addEventListener('mousemove', mv); addEventListener('mouseup', up) }}>
        <div className="past" style={{ width: x(at) }} />
        {chapters.map(c => <button key={c.t + c.label} className={`ch ${c.kind}`} style={{ left: x(c.t) }} title={`${hms(c.t - run.t0)} ${c.label}`}
          onMouseDown={e => { e.stopPropagation(); onSeek(c.t + 1000) }} />)}
        <div className="head" style={{ left: x(at) }} />
      </div>
      <span className="dur">{hms(run.t1 - run.t0)}</span>
    </div>
  )
}

/** The current goal (set_goal, else the run's goal line) with milestones reached of announced, and every milestone. */
export function GoalBox({ run, at }: { run: Run; at: number }) {
  const g = run.latest('goal', at)
  const ms = run.milestones(at)
  const done = ms.filter(m => m.t !== undefined).length
  return (
    <section className="goalbox">
      <div className="gl1"><h1>{g?.text ?? (run.goalTitle || DASH)}</h1><span className="gcount">{ms.length ? `${done} / ${ms.length}` : DASH}</span></div>
      <ul className="mlist">
        {ms.map(m => <li key={m.id} className={m.t !== undefined ? 'on' : ''} title={m.t !== undefined ? `${m.name} · ${hms(m.t - run.t0)}` : m.name}>
          <i aria-hidden="true" /><span>{m.name}</span></li>)}
      </ul>
    </section>
  )
}

/** The newest reasoning at the replay position: step and text, two lines at most. */
export function ReasonStrip({ run, at }: { run: Run; at: number }) {
  const t = [...run.thoughts(at, 40)].reverse().find(x => x.kind === 'say')
  return (
    <section className="rstrip" title={t?.full}>
      {t ? <><b>{t.step}</b><p>{t.text}</p></> : <p>{DASH}</p>}
    </section>
  )
}

/** The headline: number first over a faint chart of the same number across the run. */
export function DemoMetrics({ run, at, compare }: { run: Run; at: number; compare?: Run | null }) {
  return <MetricCharts run={run} at={at} compare={compare} widths={[334, 334, 334, 334]} />
}

export function TaskLog({ run, at }: { run: Run; at: number }) { return <Calls run={run} at={at} /> }

const kitKey = (k: Kit) => `${k.kind}:${k.name}`
const lastChange = (k: Kit) => k.versions[k.versions.length - 1]?.t ?? -Infinity

/** Square tiles, newest change first, the newest highlighted: extension or kept script, version, time since the change. */
export function ToolTiles({ run, at, open, onOpen }: { run: Run; at: number; open: string | null; onOpen: (key: string) => void }) {
  const kits = run.kits(at).sort((a, b) => lastChange(b) - lastChange(a) || (b.lastCall ?? 0) - (a.lastCall ?? 0))
  const newest = kits.find(k => k.versions.length)
  return (
    <section className="tiles">
      <h2>tools it built</h2>
      <div className="tgrid">
        {[...kits.map(k => {
          const v = k.versions[k.versions.length - 1]
          return (
            <button key={kitKey(k)} className={`tile ${k === newest ? 'newest' : ''} ${open === kitKey(k) ? 'open' : ''}`} onClick={() => onOpen(kitKey(k))}>
              <span className="tn">{k.hooks.length === 1 ? k.hooks[0].name : k.name}</span>
              <span className="tv">{v ? `v${v.version}` : DASH}</span>
              <span className="tt">{v ? span(at - v.t) : DASH}</span>
            </button>
          )
        }), ...Array.from({ length: Math.max(0, 8 - kits.length) }, (_, i) => <span key={`empty${i}`} className="tile empty" aria-hidden="true" />)]}
      </div>
    </section>
  )
}

/** A tool's window over the right half: name, version, calls, why it was built, versions on the run clock, the selected
 *  version's code with the change from the previous one marked, and beside it which version added it and why. */
export function ToolWindow({ run, at, kitId, onClose }: { run: Run; at: number; kitId: string; onClose: () => void }) {
  const kit = run.kits(at).find(k => kitKey(k) === kitId)
  const [vi, setVi] = useState<number | null>(null)
  const code = useRef<HTMLPreElement>(null)
  useEffect(() => { const el = code.current; const add = el?.querySelector('.add') as HTMLElement | null; if (el) el.scrollTop = add ? Math.max(0, add.offsetTop - 40) : 0 }, [vi, kitId])
  if (!kit) return null
  const n = kit.versions.length
  const i = vi === null ? n - 1 : Math.min(vi, n - 1)
  const v = kit.versions[i], prev = i > 0 ? kit.versions[i - 1] : undefined
  const dur = Math.max(1, run.t1 - run.t0)
  const files = v?.files ?? []
  return (
    <div className="twin" role="dialog" aria-label={kit.name}>
      <div className="twh">
        <b>{kit.hooks.length === 1 ? kit.hooks[0].name : kit.name}</b>
        <span>{v ? `v${v.version}` : DASH}</span>
        <span>{kit.calls.toLocaleString('en-US')} calls</span>
        <span className="nav">
          <button disabled={i <= 0} onClick={() => setVi(i - 1)} aria-label="Previous version">◂</button>
          <button disabled={i >= n - 1} onClick={() => setVi(i + 1)} aria-label="Next version">▸</button>
          <button onClick={onClose} aria-label="Close">✕</button>
        </span>
      </div>
      {kit.versions[0]?.reason && <p className="why">{kit.versions[0].reason}</p>}
      <div className="vline">
        {kit.versions.map((x, j) => <button key={x.version} className={j === i ? 'on' : ''} style={{ left: `${((x.t - run.t0) / dur) * 100}%` }}
          onClick={() => setVi(j)} title={`v${x.version} ${hms(x.t - run.t0)}`} />)}
      </div>
      {kit.hooks.length > 1 && <div className="subs">{kit.hooks.map(h => <span key={h.name}>{h.name} <em>{h.calls}</em></span>)}</div>}
      <div className="tcode">
        <pre ref={code}>{files.length ? files.map(f => {
          const before = prev?.files?.find(p => p.path === f.path)?.content
          const lines = before !== undefined ? diffLines(before, f.content) : f.content.split('\n').map(text => ({ op: (prev ? '+' : ' ') as ' ' | '+', text }))
          return { f, lines, changed: lines.some(l => l.op === '+') }
        }).sort((a, b) => Number(b.changed) - Number(a.changed)).map(({ f, lines }) => (
          <div key={f.path}><div className="fpath">{f.path}</div>{lines.filter(l => l.op !== '-').map((l, k) => <div key={k} className={l.op === '+' && prev ? 'add' : ''}>{l.text || ' '}</div>)}</div>
        )) : DASH}</pre>
        {v && prev && <aside className="added"><b>added in v{v.version} · {hms(v.t - run.t0)}</b>{v.reason && <p>{v.reason}</p>}</aside>}
      </div>
    </div>
  )
}

const BOXES: [string, string][] = [
  ['agent', 'runner/src/runner/run.py'], ['tool server', 'toolserver/src/toolserver/server.py'], ['hooks', 'toolserver/src/toolserver/extensions.py'],
  ['grader', 'graders'], ['event log', 'docs/interfaces/event-log.md'], ['SpacetimeDB', 'stdb/SCHEMA.md'], ['this page', 'ui/src/views/Story.tsx'],
]

/** "How it works": the parts, each with its source path in the repo (plain text: the booth runs offline). */
export function HowDrawer({ onClose }: { onClose: () => void }) {
  return (
    <div className="how" role="dialog" aria-label="How it works">
      <button className="x" onClick={onClose} aria-label="Close">✕</button>
      <div className="flow">
        {BOXES.map(([name, path], i) => <span key={name} className="hb"><span className="box"><b>{name}</b><code>{path}</code></span>{i < BOXES.length - 1 && <i aria-hidden="true">→</i>}</span>)}
      </div>
    </div>
  )
}
