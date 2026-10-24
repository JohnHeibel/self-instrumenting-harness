import { useMemo, useRef } from 'react'
import type { Marker, Run } from '../data/model'
import { big, span } from '../util'

export const SPEEDS = [60, 300, 600, 1800]

// Geometry in canvas pixels. The plot is one chart: every lane shares the same time axis.
const W = 1800, H = 480, X0 = 190, X1 = 1790
const Y = { ms: 76, hook: 164, brk: 252, fail: 282, cov: [304, 372] as const, tok: [394, 458] as const, axis: 474 }

type Placed<T> = { item: T; x: number; row: number; anchor: 'start' | 'end'; w: number }

/** Greedy label rows: each label goes on the first row where it clears the previous one. */
function place<T extends { x: number }>(items: T[], widthOf: (i: T) => number, rows: number): Placed<T>[] {
  const ends = Array<number>(rows).fill(-1e9)
  return items.map(item => {
    const w = widthOf(item)
    const anchor = item.x + w > W - 8 ? 'end' as const : 'start' as const
    const [a, b] = anchor === 'start' ? [item.x - 4, item.x + w + 14] : [item.x - w - 14, item.x + 4]
    let row = ends.findIndex(e => e <= a)
    if (row < 0) row = ends.indexOf(Math.min(...ends))
    ends[row] = b
    return { item, x: item.x, row, anchor, w }
  })
}

const HUE = {
  milestone: 'var(--c-milestone)', hook: 'var(--c-hook)', break: 'var(--c-break)', repair: 'var(--c-repair)', fail: 'var(--c-fail)',
}

export function Timeline({ run, at, playing, speed, onSeek, onPlay, onSpeed }: {
  run: Run; at: number; playing: boolean; speed: number
  onSeek: (t: number) => void; onPlay: (p: boolean) => void; onSpeed: (s: number) => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const dur = Math.max(1, run.t1 - run.t0)
  const fx = (t: number) => X0 + (t - run.t0) / dur * (X1 - X0)
  const tAt = (clientX: number) => {
    const r = ref.current!.getBoundingClientRect()
    const x = (clientX - r.left) / (r.width / ref.current!.offsetWidth)  // the canvas is CSS-scaled to fit the window
    return run.t0 + Math.max(0, Math.min(1, (x - X0) / (X1 - X0))) * dur
  }
  const dim = (t: number) => (t <= at ? 1 : 0.4)
  const seekTo = (t: number) => (e: React.MouseEvent) => { e.stopPropagation(); onSeek(t + 2000) }

  const lanes = useMemo(() => {
    const ms = run.markers.filter(m => m.kind === 'milestone').map(m => ({ ...m, x: fx(m.t) }))
    const hooks = run.markers.filter(m => m.kind === 'hook').map(m => ({ ...m, x: fx(m.t) }))
    const brk = run.markers.filter(m => m.kind === 'break' || m.kind === 'repair').map(m => ({ ...m, x: fx(m.t) }))
    const fails = run.of('tool_call').filter(c => !c.ok).map(c => c.t)
    return {
      ms: place(ms, m => m.label.length * 10.4, 2),
      hooks: place(hooks, m => m.label.length * 9.2, 3),
      brk: place(brk, m => m.label.length * 8.8, 3),
      fails,
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run])
  const finalCost = useMemo(() => run.costPerMilestone(run.t1), [run])
  const cost = run.costPerMilestone(at)
  const maxTok = Math.max(1, ...finalCost.map(b => b.tokens))
  const reached = run.markers.filter(m => m.kind === 'milestone')
  const barSpan = (i: number) => {
    const a = i === 0 ? run.t0 : reached[i - 1]?.t ?? run.t0
    const b = reached[i]?.t ?? at
    return [fx(a), fx(Math.max(a, b))] as const
  }
  const barY = (v: number) => Y.tok[1] - (v / maxTok) * (Y.tok[1] - Y.tok[0] - 16)

  const pts = run.coverage(at)
  const covY = (s: number) => Y.cov[1] - s * (Y.cov[1] - Y.cov[0])
  const covPath = pts.map((p, i) => `${i ? 'L' : 'M'}${fx(p.t).toFixed(1)},${covY(p.share).toFixed(1)}`).join('')
  const covNow = pts[pts.length - 1]
  const lastCost = [...cost].reverse().find(b => b.done)

  const hours = Array.from({ length: Math.floor(dur / 3_600_000) + 1 }, (_, h) => h).filter(h => h % 2 === 0)
  const step = (dir: 1 | -1) => {
    const ms = run.markers.filter(m => m.kind !== 'compaction')
    const next = dir > 0 ? ms.find(m => m.t > at + 1000) : [...ms].reverse().find(m => m.t < at - 60_000)
    if (next) onSeek(next.t + 2000)
  }
  const px = fx(at)
  const stem = (p: Placed<Marker & { x: number }>, base: number, rule: number) => base + p.row * 18 + 3 > rule ? rule : base + p.row * 18 + 3

  return (
    <section className="timeline">
      <div className="thead">
        <div>
          <h2>The whole run, start to finish <span>{span(dur)}</span></h2>
          <p>Each mark is something that happened, placed at the moment it happened. Click or drag anywhere to jump there.</p>
        </div>
        <div className="controls">
          <button onClick={() => step(-1)} title="Previous key moment">◀ Prev moment</button>
          <button className="play" onClick={() => onPlay(!playing)}>{playing ? 'Pause' : 'Play'}</button>
          <button onClick={() => step(1)} title="Next key moment">Next moment ▶</button>
          <span className="speeds" role="group" aria-label="Playback speed">
            {SPEEDS.map(s => <button key={s} className={s === speed ? 'on' : ''} onClick={() => onSpeed(s)}>{s}×</button>)}
          </span>
        </div>
      </div>
      <div className="plot" ref={ref}
           onMouseDown={e => {
             onSeek(tAt(e.clientX))
             const move = (ev: MouseEvent) => onSeek(tAt(ev.clientX))
             const up = () => { removeEventListener('mousemove', move); removeEventListener('mouseup', up) }
             addEventListener('mousemove', move); addEventListener('mouseup', up)
           }}>
        <div className="gut" style={{ top: 18 }}><b><i style={{ background: HUE.milestone }} />Milestones</b><span>Steps toward the goal</span></div>
        <div className="gut" style={{ top: 100 }}><b><i style={{ background: HUE.hook }} />Hooks written</b><span>Tools the agent built for itself</span></div>
        <div className="gut" style={{ top: 188 }}><b><i style={{ background: HUE.break }} /><i style={{ background: HUE.repair }} />Breaks and repairs</b><span>A hook stops working, then is fixed</span></div>
        <div className="gut" style={{ top: 270 }}><b><i style={{ background: HUE.fail }} />Failed calls</b></div>
        <div className="gut" style={{ top: 304 }}>
          <b><i style={{ background: HUE.hook }} />Coverage, latest</b>
          <em>{covNow ? Math.round(covNow.share * 100) : 0}%</em>
          <span>of observations via own hooks, not screenshots</span>
        </div>
        <div className="gut" style={{ top: 396 }}>
          <b><i style={{ background: 'var(--muted-hi)' }} />Tokens per milestone</b>
          <em>{lastCost ? big(lastCost.tokens) : '–'}</em>
          <span>for the last milestone</span>
        </div>
        <svg width={W} height={H} role="img" aria-label="Annotated timeline of the run">
          {hours.map(h => <line key={h} x1={fx(run.t0 + h * 3_600_000)} x2={fx(run.t0 + h * 3_600_000)} y1={0} y2={Y.axis - 10} stroke="var(--rule-soft)" />)}

          {/* playhead */}
          <line x1={px} x2={px} y1={4} y2={Y.axis - 10} stroke="var(--text)" strokeWidth={2} />
          <path d={`M${px - 7},0 H${px + 7} L${px},9 Z`} fill="var(--text)" />

          {/* milestones */}
          <line x1={X0} x2={X1} y1={Y.ms} y2={Y.ms} stroke="var(--rule)" />
          {lanes.ms.map(p => (
            <g key={p.item.t} opacity={dim(p.item.t)} onMouseDown={seekTo(p.item.t)} className="hit">
              <title>{span(p.item.t - run.t0)} · {p.item.label}</title>
              <line x1={p.x} x2={p.x} y1={22 + p.row * 26 + 5} y2={Y.ms} stroke={HUE.milestone} strokeWidth={1.5} />
              <rect x={p.x - 6} y={Y.ms - 6} width={12} height={12} transform={`rotate(45 ${p.x} ${Y.ms})`} fill={HUE.milestone} />
              <text x={p.anchor === 'start' ? p.x - 3 : p.x + 3} y={22 + p.row * 26} textAnchor={p.anchor} className="t-ms">{p.item.label}</text>
            </g>
          ))}

          {/* hooks */}
          <line x1={X0} x2={X1} y1={Y.hook} y2={Y.hook} stroke="var(--rule)" />
          {lanes.hooks.map(p => (
            <g key={p.item.t + p.item.label} opacity={dim(p.item.t)} onMouseDown={seekTo(p.item.t)} className="hit">
              <title>{span(p.item.t - run.t0)} · {p.item.label}</title>
              <line x1={p.x} x2={p.x} y1={110 + p.row * 18 + 3} y2={Y.hook} stroke={HUE.hook} strokeWidth={1.5} />
              <rect x={p.x - 4} y={Y.hook - 4} width={8} height={8} fill={HUE.hook} />
              <text x={p.anchor === 'start' ? p.x - 3 : p.x + 3} y={110 + p.row * 18} textAnchor={p.anchor} className="t-hook">{p.item.label}</text>
            </g>
          ))}

          {/* breaks and repairs */}
          <line x1={X0} x2={X1} y1={Y.brk} y2={Y.brk} stroke="var(--rule)" />
          {lanes.brk.map(p => {
            const hue = p.item.kind === 'repair' ? HUE.repair : HUE.break
            const base = 192
            return (
              <g key={p.item.t + p.item.label} opacity={dim(p.item.t)} onMouseDown={seekTo(p.item.t)} className="hit">
                <title>{span(p.item.t - run.t0)} · {p.item.label}</title>
                <line x1={p.x} x2={p.x} y1={stem(p, base, Y.brk)} y2={Y.brk} stroke={hue} strokeWidth={2} />
                <circle cx={p.x} cy={Y.brk} r={6} fill={hue} />
                <text x={p.anchor === 'start' ? p.x - 3 : p.x + 3} y={base + p.row * 18} textAnchor={p.anchor} className="t-brk" fill={hue}>{p.item.label}</text>
              </g>
            )
          })}

          {/* failed calls */}
          <line x1={X0} x2={X1} y1={Y.fail} y2={Y.fail} stroke="var(--rule)" />
          {lanes.fails.map(t => <line key={t} x1={fx(t)} x2={fx(t)} y1={Y.fail - 16} y2={Y.fail} stroke={HUE.fail} strokeWidth={2} opacity={dim(t)} />)}

          {/* coverage */}
          {[0, 0.5, 1].map(s => <g key={s}>
            <line x1={X0} x2={X1} y1={covY(s)} y2={covY(s)} stroke={s === 0 ? 'var(--rule)' : 'var(--rule-soft)'} />
            <text x={X0 - 8} y={covY(s) + 4} textAnchor="end" className="t-axis">{s * 100}%</text>
          </g>)}
          <path d={covPath} fill="none" stroke={HUE.hook} strokeWidth={3} strokeLinejoin="round" strokeLinecap="round" />
          {covNow && <circle cx={fx(covNow.t)} cy={covY(covNow.share)} r={5} fill={HUE.hook} />}

          {/* tokens per milestone, each bar spans the time it took */}
          <line x1={X0} x2={X1} y1={Y.tok[1]} y2={Y.tok[1]} stroke="var(--rule)" />
          {finalCost.map((b, i) => {
            const [a, z] = barSpan(i)
            const [fa, fz] = [fx(i === 0 ? run.t0 : reached[i - 1]?.t ?? run.t0), fx(reached[i]?.t ?? run.t1)]
            const now = cost[i]
            return <g key={b.id}>
              <rect x={fa + 2} y={barY(b.tokens)} width={Math.max(0, fz - fa - 4)} height={Y.tok[1] - barY(b.tokens)} fill="var(--muted-hi)" opacity={0.14} />
              {now && <rect x={a + 2} y={barY(now.tokens)} width={Math.max(0, z - a - 4)} height={Y.tok[1] - barY(now.tokens)}
                            fill="var(--muted-hi)" opacity={now.done ? 0.85 : 0.5} />}
              {now?.done && z - a > 56 && <text x={(a + z) / 2} y={barY(now.tokens) - 5} textAnchor="middle" className="t-axis hi">{big(now.tokens)}</text>}
            </g>
          })}

          {/* time axis */}
          {hours.map(h => <text key={h} x={fx(run.t0 + h * 3_600_000)} y={Y.axis + 6} textAnchor="middle" className="t-axis">{h}h</text>)}

        </svg>
      </div>
    </section>
  )
}
