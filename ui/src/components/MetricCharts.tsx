import { useMemo, useState } from 'react'
import type { MetricPoint, Run } from '../data/model'
import { big, hms } from '../util'

type Key = 'tokPerHour' | 'tokPerStep' | 'tokPerAction' | 'handsPct'
// Widths in canvas px (they sum with three 16px gutters to the game's 1240): tokens / action, the number the agent's tools
// should drive down, gets the widest chart.
const DEFS: { key: Key; label: string; how: string; w: number; log?: boolean; pct?: boolean }[] = [
  { key: 'tokPerHour', label: 'tokens / hour', how: 'Σ usage tokens ÷ wall hours', w: 280 },
  { key: 'tokPerStep', label: 'tokens / step', how: 'Σ usage tokens ÷ usage records', w: 280 },
  { key: 'tokPerAction', label: 'tokens / action', how: 'Σ usage tokens ÷ Σ program_actions.count', w: 400, log: true },
  { key: 'handsPct', label: 'hands busy', how: 'hands-busy time ÷ (wall − off)', w: 232, pct: true },
]
const H = 170, PL = 10, PR = 10, PT = 62, PB = 16
const fmt = (v: number | undefined, pct?: boolean) => v === undefined || !Number.isFinite(v) ? '—' : pct ? `${Math.round(v * 100)}%` : big(v)

/** The four efficiency numbers, each over a chart of the same number across the run: 10-minute bins as bars, the run so far
 *  as a line (its end is the big number), milestones as ticks on the time axis, and the previous comparable run as a thin
 *  reference line when one was loaded. tokens / action is on a log scale. */
export function MetricCharts({ run, at, compare }: { run: Run; at: number; compare?: Run | null }) {
  const series = run.metricSeries(at)
  const prev = useMemo(() => compare?.metricSeries(compare.t1), [compare])
  const milestones = useMemo(() => {
    const seen = new Set<string>()
    return run.of('milestone').filter(m => !seen.has(m.id) && seen.add(m.id)).map(m => m.t)
  }, [run])
  return (
    <section className="mcharts" aria-label="Efficiency over the run">
      {DEFS.map(d => <MetricChart key={d.key} def={d} run={run} at={at} bins={series?.bins ?? []} cum={series?.cum ?? []}
        start={series?.start ?? run.t0} prev={prev?.cum} prevStart={prev?.start} prevName={compare?.recs[0]?.run_id} milestones={milestones} />)}
    </section>
  )
}

function MetricChart({ def, run, at, bins, cum, start, prev, prevStart, prevName, milestones }: {
  def: (typeof DEFS)[number]; run: Run; at: number; bins: MetricPoint[]; cum: MetricPoint[]; start: number
  prev?: MetricPoint[]; prevStart?: number; prevName?: string; milestones: number[]
}) {
  const [hover, setHover] = useState<number | null>(null)
  const k = def.key, W = def.w - 2
  const span = Math.max(1, run.t1 - start)
  const x = (t: number) => PL + ((t - start) / span) * (W - PL - PR)
  const vals = [...bins, ...cum, ...(prev ?? [])].map(p => p[k]).filter((v): v is number => v !== undefined && Number.isFinite(v) && (!def.log || v > 0))
  const hi = def.pct ? 1 : Math.max(1, ...vals)
  const lo = def.log ? Math.max(1, Math.min(...vals)) : 0
  const y = (v: number) => {
    const f = def.log ? (Math.log10(Math.max(v, lo)) - Math.log10(lo)) / Math.max(1e-9, Math.log10(hi) - Math.log10(lo)) : v / hi
    return H - PB - Math.max(0, Math.min(1, f)) * (H - PT - PB)
  }
  const line = (pts: MetricPoint[], dx = 0) => pts.filter(p => p[k] !== undefined).map((p, i) => `${i ? 'L' : 'M'}${x(p.t1 + dx).toFixed(1)},${y(p[k]!).toFixed(1)}`).join('')
  const now = cum[cum.length - 1]
  const hb = hover !== null ? bins[hover] : undefined, hc = hover !== null ? cum[hover] : undefined
  const pd = prev && prevStart !== undefined ? start - prevStart : 0  // align the previous run's start with this one's
  return (
    <figure className="box mchart" title={def.how}
      onMouseMove={e => {
        const r = e.currentTarget.getBoundingClientRect()
        const t = start + (((e.clientX - r.left) * (W / r.width) - PL) / (W - PL - PR)) * span
        const i = bins.findIndex(b => t >= b.t0 && t < b.t1)
        setHover(i >= 0 ? i : null)
      }} onMouseLeave={() => setHover(null)}>
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} aria-hidden="true">
        <line x1={PL} x2={W - PR} y1={H - PB} y2={H - PB} className="ax" />
        {bins.map((b, i) => b[k] !== undefined && (!def.log || b[k]! > 0) && (
          <rect key={b.t0} x={x(b.t0) + 1} width={Math.max(1, x(b.t1) - x(b.t0) - 2)} y={y(b[k]!)} height={H - PB - y(b[k]!)} className={i === hover ? 'bar on' : 'bar'} />
        ))}
        {prev && <path d={line(prev, pd)} className="prev" />}
        <path d={line(cum)} className="cum" />
        {now?.[k] !== undefined && <circle cx={x(now.t1)} cy={y(now[k]!)} r={2.5} className="end" />}
        {vals.length > 0 && <>
          <text x={W - PR} y={PT - 4} className="sc">{fmt(hi, def.pct)}</text>
          <text x={W - PR} y={H - PB - 3} className="sc">{def.log ? fmt(lo) : def.pct ? '0%' : '0'}</text>
        </>}
        {milestones.map(t => <line key={t} x1={x(t)} x2={x(t)} y1={H - PB} y2={H - PB + 5} className={t <= at ? 'mt on' : 'mt'} />)}
        {hover !== null && hb && <line x1={x((hb.t0 + hb.t1) / 2)} x2={x((hb.t0 + hb.t1) / 2)} y1={PT - 6} y2={H - PB} className="xh" />}
      </svg>
      <figcaption style={{ right: 12 }}>
        <span className="ml">{def.label}{def.log && <em>log</em>}</span>
        <b>{fmt(now?.[k], def.pct)}</b>
        {hb ? <span className="mh">{hms(hb.t0 - run.t0)} · 10 min {fmt(hb[k], def.pct)} · run {fmt(hc?.[k], def.pct)}</span>
          : def.log && prev && prevName && <span className="mh">— {prevName}</span>}
      </figcaption>
    </figure>
  )
}
