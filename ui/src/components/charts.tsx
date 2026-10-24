import type { Run } from '../data/model'
import { big, fitLine, span, trend } from '../util'

const W = 245, H = 64, PAD = { l: 4, r: 2, t: 6, b: 13 }

type Polarity = 'down' | 'up'  // which way is good

/** "▼ 41%" with the colour of good or not good. */
function Trend({ pct, good, unit, pts }: { pct: number | undefined; good: Polarity; unit?: string; pts?: boolean }) {
  if (pct === undefined || Math.abs(pct) < (pts ? 0.02 : 0.02)) return <span className="trend flat">{pct === undefined ? '' : '≈ flat'}</span>
  const better = good === 'down' ? pct < 0 : pct > 0
  return <span className={`trend ${better ? 'better' : 'worse'}`} title={`${unit ?? 'Change'} from the start to now`}>
    {pct < 0 ? '▼' : '▲'} {Math.round(Math.abs(pct) * 100)}{pts ? ' pts' : '%'}</span>
}

function Frame({ title, value, tr, note, children }: { title: string; value: string; tr: React.ReactNode; note: string; children: React.ReactNode }) {
  return (
    <section className="chart">
      <h3><span>{title}</span><b>{value}{tr}</b></h3>
      {children}
      <p title={note}>{note}</p>
    </section>
  )
}

/** One bar per milestone, left to right in the order reached, with a fitted trend line. Falling bars are good. */
function MilestoneBars({ run, at, title, note, values, current, fmt }: {
  run: Run; at: number; title: string; note: string
  values: { id: string; name: string; v: number }[]; current: number; fmt: (v: number) => string
}) {
  const n = Math.max(run.milestonesDefined.length, values.length + 1, 1)
  const all = [...values.map(b => b.v), current]
  const max = Math.max(1, ...all)
  const bw = (W - PAD.l - PAD.r) / n
  const y = (v: number) => PAD.t + (1 - v / max) * (H - PAD.t - PAD.b)
  const vs = values.map(b => b.v)
  const fit = fitLine(vs)
  const tr = trend(vs)
  const last = values[values.length - 1]
  const cx = (i: number) => PAD.l + i * bw + bw / 2
  const unfinished = run.milestones(at).length > values.length
  return (
    <Frame title={title} value={last ? fmt(last.v) : '–'} tr={<Trend pct={tr?.pct} good="down" unit={title} />} note={note}>
      <svg width={W} height={H} role="img" aria-label={`${title}, one bar per milestone`}>
        <line x1={PAD.l} x2={W - PAD.r} y1={H - PAD.b} y2={H - PAD.b} stroke="var(--rule)" />
        {values.map((b, i) => (
          <g key={b.id}>
            <title>{i + 1}. {b.name}: {fmt(b.v)}</title>
            <rect x={cx(i) - Math.max(2, bw / 2 - 1.5)} y={y(b.v)} width={Math.max(3, bw - 3)} height={Math.max(1, H - PAD.b - y(b.v))} fill="var(--stone-hi)" />
          </g>
        ))}
        {unfinished && current > 0 && (
          <g><title>In progress: {fmt(current)} so far</title>
            <rect x={cx(values.length) - Math.max(2, bw / 2 - 1.5)} y={y(current)} width={Math.max(3, bw - 3)} height={Math.max(1, H - PAD.b - y(current))}
                  fill="none" stroke="var(--stone-hi)" strokeDasharray="3 2" /></g>
        )}
        {fit && <line x1={cx(0)} x2={cx(values.length - 1)} y1={y(Math.max(0, fit[0]))} y2={y(Math.max(0, fit[1]))}
                      stroke={tr && tr.pct < 0 ? 'var(--verde)' : 'var(--ochre)'} strokeWidth={1.8} strokeDasharray="5 3" />}
        <text x={PAD.l} y={H - 1}>1</text>
        <text x={W - PAD.r} y={H - 1} textAnchor="end">{n}</text>
        <text x={W - PAD.r} y={PAD.t + 6} textAnchor="end">{fmt(max)}</text>
      </svg>
    </Frame>
  )
}

/** Tokens spent reaching each milestone: the number to watch. It should fall as the agent's own tools take over. */
export function TokensChart({ run, at }: { run: Run; at: number }) {
  const steps = run.steps(at)
  return <MilestoneBars run={run} at={at} title="Tokens per milestone" fmt={big}
    note="Falling is good: the tools it built are paying off."
    values={steps.map(s => ({ id: s.id, name: s.name, v: s.tokens }))} current={run.inProgress(at).tokens} />
}

/** Wall-clock time to reach each milestone, from the previous one. */
export function TimeChart({ run, at }: { run: Run; at: number }) {
  const steps = run.steps(at)
  return <MilestoneBars run={run} at={at} title="Time per milestone" fmt={span}
    note="Falling is good: less time for each step up."
    values={steps.map(s => ({ id: s.id, name: s.name, v: s.ms }))} current={run.inProgress(at).ms} />
}

/** Share of observations made through the agent's own hooks, over the whole run. */
export function CoverageChart({ run, at }: { run: Run; at: number }) {
  const pts = run.coverage(at)
  const x = (t: number) => PAD.l + (t - run.t0) / Math.max(1, run.t1 - run.t0) * (W - PAD.l - PAD.r)
  const y = (s: number) => PAD.t + (1 - s) * (H - PAD.t - PAD.b)
  const now = pts[pts.length - 1]
  const path = pts.map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(1)},${y(p.share).toFixed(1)}`).join('')
  // Coverage is already a share: show the change in percentage points, not a ratio of ratios.
  const k = Math.min(3, Math.floor(pts.length / 2))
  const avg = (a: typeof pts) => a.reduce((s, p) => s + p.share, 0) / Math.max(1, a.length)
  const delta = k ? avg(pts.slice(-k)) - avg(pts.slice(0, k)) : undefined
  return (
    <Frame title="Coverage" value={`${Math.round((run.coverageRecent(at) ?? now?.share ?? 0) * 100)}%`} tr={<Trend pct={delta} good="up" unit="Coverage" pts />}
           note="Share of looks made through its own hooks, last hour.">
      <svg width={W} height={H} role="img" aria-label="Share of observations made through hooks over time">
        {[0, 0.5, 1].map(s => <line key={s} x1={PAD.l} x2={W - PAD.r} y1={y(s)} y2={y(s)} stroke="var(--rule)" />)}
        <path d={path} fill="none" stroke="var(--lapis)" strokeWidth={2.2} strokeLinejoin="round" strokeLinecap="round" />
        {now && <circle cx={x(now.t)} cy={y(now.share)} r={3.5} fill="var(--lapis)" />}
        <text x={W - PAD.r} y={PAD.t + 6} textAnchor="end">100%</text>
        <text x={PAD.l} y={H - 1}>0h</text>
        <text x={W - PAD.r} y={H - 1} textAnchor="end">{span(run.t1 - run.t0)}</text>
      </svg>
    </Frame>
  )
}

/** Distinct hooks written so far, as a staircase over the run. */
export function ToolsChart({ run, at }: { run: Run; at: number }) {
  const series = run.toolsOverTime()
  const total = Math.max(4, series[series.length - 1]?.n ?? 0)
  const x = (t: number) => PAD.l + (t - run.t0) / Math.max(1, run.t1 - run.t0) * (W - PAD.l - PAD.r)
  const y = (n: number) => PAD.t + (1 - n / total) * (H - PAD.t - PAD.b)
  const n = run.toolsBuilt(at)
  let d = `M${x(run.t0)},${y(0)}`, prev = 0
  for (const p of series) { if (p.t > at) break; d += `L${x(p.t)},${y(prev)}L${x(p.t)},${y(p.n)}`; prev = p.n }
  d += `L${x(Math.min(at, run.t1))},${y(prev)}`
  return (
    <Frame title="Hooks built" value={String(n)} tr={null} note="Distinct tools it wrote for itself, by name.">
      <svg width={W} height={H} role="img" aria-label="Distinct hooks built over time">
        {[0, 0.5, 1].map(s => <line key={s} x1={PAD.l} x2={W - PAD.r} y1={y(total * s)} y2={y(total * s)} stroke="var(--rule)" />)}
        <path d={d} fill="none" stroke="var(--ochre)" strokeWidth={2.2} strokeLinejoin="round" />
        <circle cx={x(Math.min(at, run.t1))} cy={y(prev)} r={3.5} fill="var(--ochre)" />
        <text x={W - PAD.r} y={PAD.t + 6} textAnchor="end">{total}</text>
        <text x={PAD.l} y={H - 1}>0h</text>
        <text x={W - PAD.r} y={H - 1} textAnchor="end">{span(run.t1 - run.t0)}</text>
      </svg>
    </Frame>
  )
}
