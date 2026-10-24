import { useMemo, useState } from 'react'
import type { MetricPoint, Metrics, Run, Segment } from '../data/model'
import { big, hms } from '../util'

type Key = 'cwPerAction' | 'actPerStep' | 'handsPct'
type Def = { label: string; how: string; w: number; key?: Key; log?: boolean; pct?: boolean; dec?: boolean; value: (m?: Metrics) => number | undefined }
// docs/BRIEF.md "Metrics", in its order. Widths in canvas px sum with three 16px gutters to the game's 1240.
const DEFS: Def[] = [
  { label: 'cw tokens / milestone', how: 'cache-weighted tokens so far ÷ milestones reached; bars: cw spent in each segment between milestones', w: 400, value: m => m?.cwPerMilestone },
  { label: 'cw tokens / action', how: 'cache-weighted tokens ÷ Σ program_actions.count; bars: 15-minute bins', w: 300, key: 'cwPerAction', log: true, value: m => m?.cwPerAction },
  { label: 'actions / step', how: 'Σ program_actions.count ÷ usage records; bars: 15-minute bins', w: 260, key: 'actPerStep', dec: true, value: m => m?.actPerStep },
  { label: 'hands busy', how: 'hands-busy time ÷ (wall − off), costs.py rules; bars: 15-minute bins', w: 232, key: 'handsPct', pct: true, value: m => m?.handsPct },
]
const H = 170, PL = 10, PR = 10, PT = 62, PB = 16
const fmt = (v: number | undefined, d: Pick<Def, 'pct' | 'dec'> = {}) => v === undefined || !Number.isFinite(v) ? '—'
  : d.pct ? `${Math.round(v * 100)}%` : d.dec ? v.toFixed(1) : big(v)

/** The four headline numbers, each over a chart across the run, milestones as ticks on the time axis, and the previous
 *  comparable run as a thin reference line when one was loaded. */
export function MetricCharts({ run, at, compare }: { run: Run; at: number; compare?: Run | null }) {
  const m = run.metrics(at)
  const series = run.metricSeries(at)
  const segs = run.segments(at)
  const prev = useMemo(() => compare ? { s: compare.metricSeries(compare.t1), g: compare.segments(compare.t1), start: compare.runnerStart } : undefined, [compare])
  const milestones = useMemo(() => {
    const seen = new Set<string>()
    return run.of('milestone').filter(x => !seen.has(x.id) && seen.add(x.id)).map(x => x.t)
  }, [run])
  const start = series?.start ?? run.runnerStart
  const common = { run, at, start, milestones, prevName: compare?.recs[0]?.run_id, prevShift: prev ? start - prev.start : 0 }
  return (
    <section className="mcharts" aria-label="Headline metrics over the run">
      <SegmentChart def={DEFS[0]} value={DEFS[0].value(m)} segs={segs} prevSegs={prev?.g} {...common} />
      {DEFS.slice(1).map(d => <BinChart key={d.label} def={d} value={d.value(m)} bins={series?.bins ?? []} cum={series?.cum ?? []} prev={prev?.s?.cum} {...common} />)}
    </section>
  )
}

type Common = { run: Run; at: number; start: number; milestones: number[]; prevName?: string; prevShift: number }

function useAxis(run: Run, start: number, w: number) {
  const W = w - 2, span = Math.max(1, run.t1 - start)
  return { W, span, x: (t: number) => PL + ((t - start) / span) * (W - PL - PR) }
}

function Frame({ def, value, W, scaleHi, scaleLo, hover, legend, children, onMove, onLeave }: {
  def: Def; value?: number; W: number; scaleHi?: string; scaleLo?: string; hover?: string; legend?: string
  children: React.ReactNode; onMove: (e: React.MouseEvent<HTMLElement>) => void; onLeave: () => void
}) {
  return (
    <figure className="box mchart" title={def.how} onMouseMove={onMove} onMouseLeave={onLeave}>
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} aria-hidden="true">
        <line x1={PL} x2={W - PR} y1={H - PB} y2={H - PB} className="ax" />
        {children}
        {scaleHi && <text x={W - PR} y={PT - 4} className="sc">{scaleHi}</text>}
        {scaleLo && <text x={W - PR} y={H - PB - 3} className="sc">{scaleLo}</text>}
      </svg>
      <figcaption style={{ right: 12 }}>
        <span className="ml">{def.label}{def.log && <em>log</em>}</span>
        <b>{fmt(value, def)}</b>
        {hover ? <span className="mh">{hover}</span> : legend && <span className="mh">— {legend}</span>}
      </figcaption>
    </figure>
  )
}

function Ticks({ milestones, at, x }: { milestones: number[]; at: number; x: (t: number) => number }) {
  return <>{milestones.map(t => <line key={t} x1={x(t)} x2={x(t)} y1={H - PB} y2={H - PB + 5} className={t <= at ? 'mt on' : 'mt'} />)}</>
}

/** Bars: cache-weighted tokens spent in each segment between milestones, as wide as the segment lasted; the segment in
 *  progress is an outline. The big number is the run's cw per milestone. */
function SegmentChart({ def, value, segs, prevSegs, run, at, start, milestones, prevName, prevShift }: Common & { def: Def; value?: number; segs: Segment[]; prevSegs?: Segment[] }) {
  const [hover, setHover] = useState<number | null>(null)
  const { W, span, x } = useAxis(run, start, def.w)
  const hi = Math.max(1, ...segs.map(s => s.cw), ...(prevSegs ?? []).map(s => s.cw))
  const y = (v: number) => H - PB - Math.min(1, v / hi) * (H - PT - PB)
  const hs = hover !== null ? segs[hover] : undefined
  const prevPath = prevSegs?.filter(s => s.id).map(s => `M${x(s.t0 + prevShift)},${y(s.cw)}H${x(s.t1 + prevShift)}`).join('')
  return (
    <Frame def={def} value={value} W={W} scaleHi={segs.length ? fmt(hi) : undefined} scaleLo={segs.length ? '0' : undefined}
      hover={hs && `${hs.name ?? 'in progress'} · ${fmt(hs.cw)}`} legend={prevSegs?.length ? prevName : undefined}
      onMove={e => { const r = e.currentTarget.getBoundingClientRect(); const t = start + (((e.clientX - r.left) * (W / r.width) - PL) / (W - PL - PR)) * span; const i = segs.findIndex(s => t >= s.t0 && t < s.t1); setHover(i >= 0 ? i : null) }}
      onLeave={() => setHover(null)}>
      {segs.map((s, i) => (
        <rect key={s.t0} x={x(s.t0) + 1} width={Math.max(1, x(s.t1) - x(s.t0) - 2)} y={y(s.cw)} height={H - PB - y(s.cw)}
          className={`${s.id ? 'bar' : 'bar open'}${i === hover ? ' on' : ''}`} />
      ))}
      {prevPath && <path d={prevPath} className="prev" />}
      <Ticks milestones={milestones} at={at} x={x} />
    </Frame>
  )
}

/** Bars: the metric per 15-minute bin; line: the run so far (its end is the big number). */
function BinChart({ def, value, bins, cum, prev, run, at, start, milestones, prevName, prevShift }: Common & { def: Def; value?: number; bins: MetricPoint[]; cum: MetricPoint[]; prev?: MetricPoint[] }) {
  const [hover, setHover] = useState<number | null>(null)
  const k = def.key!
  const { W, span, x } = useAxis(run, start, def.w)
  const vals = [...bins, ...cum, ...(prev ?? [])].map(p => p[k]).filter((v): v is number => v !== undefined && Number.isFinite(v) && (!def.log || v > 0))
  const hi = def.pct ? 1 : Math.max(def.dec ? 0.1 : 1, ...vals)
  const lo = def.log ? Math.max(1, Math.min(...vals)) : 0
  const y = (v: number) => {
    const f = def.log ? (Math.log10(Math.max(v, lo)) - Math.log10(lo)) / Math.max(1e-9, Math.log10(hi) - Math.log10(lo)) : v / hi
    return H - PB - Math.max(0, Math.min(1, f)) * (H - PT - PB)
  }
  const line = (pts: MetricPoint[], dx = 0) => pts.filter(p => p[k] !== undefined).map((p, i) => `${i ? 'L' : 'M'}${x(p.t1 + dx).toFixed(1)},${y(p[k]!).toFixed(1)}`).join('')
  const now = cum[cum.length - 1]
  const hb = hover !== null ? bins[hover] : undefined, hc = hover !== null ? cum[hover] : undefined
  return (
    <Frame def={def} value={value} W={W} scaleHi={vals.length ? fmt(hi, def) : undefined} scaleLo={vals.length ? (def.log ? fmt(lo) : def.pct ? '0%' : '0') : undefined}
      hover={hb && `${hms(hb.t0 - run.t0)} · 15 min ${fmt(hb[k], def)} · run ${fmt(hc?.[k], def)}`} legend={prev?.length && def.log ? prevName : undefined}
      onMove={e => { const r = e.currentTarget.getBoundingClientRect(); const t = start + (((e.clientX - r.left) * (W / r.width) - PL) / (W - PL - PR)) * span; const i = bins.findIndex(b => t >= b.t0 && t < b.t1); setHover(i >= 0 ? i : null) }}
      onLeave={() => setHover(null)}>
      {bins.map((b, i) => b[k] !== undefined && (!def.log || b[k]! > 0) && (
        <rect key={b.t0} x={x(b.t0) + 1} width={Math.max(1, x(b.t1) - x(b.t0) - 2)} y={y(b[k]!)} height={H - PB - y(b[k]!)} className={i === hover ? 'bar on' : 'bar'} />
      ))}
      {prev && <path d={line(prev, prevShift)} className="prev" />}
      <path d={line(cum)} className="cum" />
      {now?.[k] !== undefined && <circle cx={x(now.t1)} cy={y(now[k]!)} r={2.5} className="end" />}
      <Ticks milestones={milestones} at={at} x={x} />
    </Frame>
  )
}
