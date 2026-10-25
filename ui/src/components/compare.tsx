import { useRef } from 'react'
import type { Run, TRec } from '../data/model'
import { MetricCharts } from './MetricCharts'
import { VideoHero } from './demo'
import { hms } from '../util'

const DASH = '—'

/** The run's milestone set with its count: reached ✓-style filled squares, the rest hollow (as the main view). */
function MilestoneBlock({ run, at }: { run: Run; at: number }) {
  const ms = run.milestones(at)
  const done = ms.filter(m => m.t !== undefined).length
  return (
    <section className="goalbox cmp-ms">
      <div className="gl1"><span className="cmp-name">{run.recs[0]?.run_id}</span><span className="gcount">{ms.length ? `${done} / ${ms.length}` : DASH}</span></div>
      <ul className="mlist">
        {ms.map(m => <li key={m.id} className={m.t !== undefined ? 'on' : ''} title={m.t !== undefined ? `${m.name} · ${hms(m.t - run.t0)}` : m.name}>
          <i aria-hidden="true" /><span>{m.name}</span></li>)}
      </ul>
    </section>
  )
}

/** One shared scrubber over both runs, each from its own start (t = 0): milestone marks of the harness run above the
 *  track's middle, of the baseline below. */
function SharedScrubber({ a, b, rel, dur, playing, onPlay, onSeek }: {
  a: Run; b: Run; rel: number; dur: number; playing: boolean; onPlay: (p: boolean) => void; onSeek: (rel: number) => void
}) {
  const track = useRef<HTMLDivElement>(null)
  const x = (r: number) => `${(Math.max(0, Math.min(dur, r)) / dur) * 100}%`
  const seekAt = (cx: number) => { const r = track.current!.getBoundingClientRect(); onSeek(Math.max(0, Math.min(1, (cx - r.left) / r.width)) * dur) }
  const marks = (run: Run) => { const seen = new Set<string>(); return run.of('milestone').filter(m => !seen.has(m.id) && seen.add(m.id)).map(m => m.t - run.t0) }
  return (
    <div className="scrub cmp-scrub">
      <button className="play" onClick={() => onPlay(!playing)} aria-label={playing ? 'Pause' : 'Play'}>
        <svg viewBox="0 0 16 16" width="18" height="18">{playing ? <path d="M4 2h3v12H4zM9 2h3v12H9z" /> : <path d="M4 2l10 6-10 6z" />}</svg>
      </button>
      <div className="track" ref={track} onMouseDown={e => { seekAt(e.clientX); const mv = (ev: MouseEvent) => seekAt(ev.clientX); const up = () => { removeEventListener('mousemove', mv); removeEventListener('mouseup', up) }; addEventListener('mousemove', mv); addEventListener('mouseup', up) }}>
        <div className="past" style={{ width: x(rel) }} />
        {marks(a).map(t => <button key={`a${t}`} className="ch milestone up" style={{ left: x(t) }} title={`${a.recs[0]?.run_id} ${hms(t)}`} onMouseDown={e => { e.stopPropagation(); onSeek(t + 1000) }} />)}
        {marks(b).map(t => <button key={`b${t}`} className="ch milestone down" style={{ left: x(t) }} title={`${b.recs[0]?.run_id} ${hms(t)}`} onMouseDown={e => { e.stopPropagation(); onSeek(t + 1000) }} />)}
        <div className="head" style={{ left: x(rel) }} />
      </div>
      <span className="dur">{hms(dur)}</span>
    </div>
  )
}

/** The comparison screen: the harness run and its raw baseline side by side on one clock (each from its own start), one
 *  scrubber, and under each recording its own milestones and its own four headline numbers. Nothing is compared. */
export function CompareView({ a, b, rel, setRel, playing, setPlaying, speed, imageUrlFor }: {
  a: Run; b: Run | null; rel: number; setRel: (r: number) => void; playing: boolean; setPlaying: (p: boolean) => void; speed: number
  imageUrlFor: (run: string) => (r: TRec) => string | undefined
}) {
  if (!b) return <div className="cmp-loading">{DASH}</div>
  const dur = Math.max(a.t1 - a.t0, b.t1 - b.t0, 1)
  const side = (run: Run) => {
    const at = run.t0 + Math.min(rel, run.t1 - run.t0)
    const name = run.recs[0]?.run_id ?? ''
    return (
      <div className="cmp-side" key={name}>
        <VideoHero run={run} runName={name} at={at} now={Date.now()} playing={playing} speed={speed} following={false} spectator={null} imageUrl={imageUrlFor(name)} />
        <MilestoneBlock run={run} at={at} />
        <MetricCharts run={run} at={at} widths={[222, 222, 222, 222]} />
      </div>
    )
  }
  return (
    <>
      <div className="cmp-pair">{side(a)}{side(b)}</div>
      <SharedScrubber a={a} b={b} rel={rel} dur={dur} playing={playing} onPlay={setPlaying} onSeek={r => setRel(Math.max(0, Math.min(dur, r)))} />
    </>
  )
}
