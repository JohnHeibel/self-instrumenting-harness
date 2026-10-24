import { useMemo, useRef } from 'react'
import { KEY_KINDS, type Run } from '../data/model'
import { SPEEDS, hms } from '../util'

// Canvas pixels: the climb is milestones reached (grader `milestone` records) over run time, as a stepped slope.
const W = 940, H = 48, X0 = 6, X1 = W - 10, BASE = 40, TOP = 6, R = 6

/** Scrubber and playback. The slope rises one step per milestone; the stone is the moment shown. Deaths are marked
 *  under the base line. Click or drag to seek. */
export function Timeline({ run, at, playing, speed, live, onJump, onSeek, onPlay, onSpeed }: {
  run: Run; at: number; playing: boolean; speed: number; live: boolean; onJump: () => void
  onSeek: (t: number) => void; onPlay: (p: boolean) => void; onSpeed: (s: number) => void
}) {
  const ref = useRef<SVGSVGElement>(null)
  const dur = Math.max(1, run.t1 - run.t0)
  const fx = (t: number) => X0 + ((t - run.t0) / dur) * (X1 - X0)
  const total = Math.max(1, run.milestonesDefined.length)
  const fy = (n: number) => BASE - (n / total) * (BASE - TOP)
  const tAt = (clientX: number) => {
    const r = ref.current!.getBoundingClientRect()
    const x = (clientX - r.left) * (W / r.width)  // the canvas is CSS-scaled to fit the window
    return run.t0 + Math.max(0, Math.min(1, (x - X0) / (X1 - X0))) * dur
  }

  const g = useMemo(() => {
    const seen = new Set<string>()
    const ms = run.of('milestone').filter(m => !seen.has(m.id) && seen.add(m.id))
    const names = new Map(run.milestonesDefined.map(m => [m.id, m.name]))
    let d = `M${X0},${BASE}`, n = 0
    for (const m of ms) { d += `H${fx(m.t)}V${fy(++n)}` }
    d += `H${X1}`
    return {
      d, area: `${d}V${BASE}H${X0}Z`,
      ms: ms.map((m, i) => ({ t: m.t, x: fx(m.t), y: fy(i + 1), name: names.get(m.id) ?? m.id })),
      deaths: run.of('marker').filter(m => m.kind === 'agent_died').map(m => ({ t: m.t, x: fx(m.t), label: String(m.label ?? 'died') })),
      hours: Array.from({ length: Math.floor(dur / 3_600_000) + 1 }, (_, h) => h),
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run])

  const px = fx(at)
  const reached = g.ms.filter(m => m.t <= at).length
  const step = (dir: 1 | -1) => {
    const ms = run.markers.filter(m => KEY_KINDS.includes(m.kind))
    const next = dir > 0 ? ms.find(m => m.t > at + 1000) : [...ms].reverse().find(m => m.t < at - 60_000)
    if (next) onSeek(next.t + 2000)
  }
  return (
    <section className="box timeline" aria-label="Run timeline">
      <div className="controls">
        <button onClick={() => step(-1)} aria-label="Previous key moment" title="Previous key moment">
          <svg viewBox="0 0 16 16" width="14" height="14"><path d="M11 3 L5 8 L11 13" /></svg>
        </button>
        <button className="play" onClick={() => onPlay(!playing)} aria-label={playing ? 'Pause' : 'Play'}>
          <svg viewBox="0 0 16 16" width="14" height="14">{playing ? <path d="M5 3 V13 M11 3 V13" /> : <path d="M5 3 L13 8 L5 13 Z" className="fill" />}</svg>
        </button>
        <button onClick={() => step(1)} aria-label="Next key moment" title="Next key moment">
          <svg viewBox="0 0 16 16" width="14" height="14"><path d="M5 3 L11 8 L5 13" /></svg>
        </button>
        <span className="speeds" role="group" aria-label="Playback speed">
          {SPEEDS.map(s => <button key={s} className={s === speed ? 'on' : ''} onClick={() => onSpeed(s)}>{s}×</button>)}
        </span>
        {live && at < run.t1 - 2000 && <button className="golive" onClick={onJump}>live</button>}
      </div>
      <svg ref={ref} className="plot" width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="slider" aria-label="Position in run"
           aria-valuemin={0} aria-valuemax={Math.round(dur / 1000)} aria-valuenow={Math.round((at - run.t0) / 1000)} aria-valuetext={hms(at - run.t0)}
           onMouseDown={e => {
             onSeek(tAt(e.clientX))
             const move = (ev: MouseEvent) => onSeek(tAt(ev.clientX))
             const up = () => { removeEventListener('mousemove', move); removeEventListener('mouseup', up) }
             addEventListener('mousemove', move); addEventListener('mouseup', up)
           }}>
        <defs><clipPath id="past"><rect x={0} y={0} width={Math.max(0, px)} height={H} /></clipPath></defs>
        <path d={g.area} className="rock ghost" />
        <g clipPath="url(#past)"><path d={g.area} className="rock" /><path d={g.d} className="ridge" /></g>
        <line x1={X0} x2={X1} y1={BASE} y2={BASE} className="base" />
        {g.hours.map(h => <line key={h} x1={fx(run.t0 + h * 3_600_000)} x2={fx(run.t0 + h * 3_600_000)} y1={BASE} y2={BASE + 4} className="tick" />)}
        {g.ms.map(m => <rect key={m.t} x={m.x - 3} y={m.y - 3} width={6} height={6} transform={`rotate(45 ${m.x} ${m.y})`} className={m.t <= at ? 'ms on' : 'ms'}><title>{hms(m.t - run.t0)} · {m.name}</title></rect>)}
        {g.deaths.map(d => <path key={d.t} d={`M${d.x - 3},${BASE + 2} L${d.x + 3},${BASE + 8} M${d.x + 3},${BASE + 2} L${d.x - 3},${BASE + 8}`} className="death"><title>{hms(d.t - run.t0)} · {d.label}</title></path>)}
        <circle cx={px} cy={Math.min(BASE, fy(reached)) - R} r={R} className="stone" />
      </svg>
    </section>
  )
}
