import type { Run } from '../data/model'
import { big, span } from '../util'

const W = 500, H = 64, PAD = { l: 30, r: 4, t: 6, b: 14 }

/** Share of observations made through the agent's own hooks, over the whole run. */
export function CoverageChart({ run, at }: { run: Run; at: number }) {
  const pts = run.coverage(at)
  const x = (t: number) => PAD.l + (t - run.t0) / Math.max(1, run.t1 - run.t0) * (W - PAD.l - PAD.r)
  const y = (s: number) => PAD.t + (1 - s) * (H - PAD.t - PAD.b)
  const now = pts[pts.length - 1]
  const path = pts.map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(1)},${y(p.share).toFixed(1)}`).join('')
  return (
    <section className="chart">
      <h3>Coverage <b>{now ? Math.round(now.share * 100) : 0}%</b></h3>
      <svg width={W} height={H} role="img" aria-label="Share of observations made through hooks over time">
        {[0, 0.5, 1].map(s => <g key={s}>
          <line x1={PAD.l} x2={W - PAD.r} y1={y(s)} y2={y(s)} stroke="var(--rule)" />
          <text x={PAD.l - 5} y={y(s) + 4} textAnchor="end">{s * 100}%</text>
        </g>)}
        <path d={path} fill="none" stroke="var(--lapis)" strokeWidth={2.5} strokeLinejoin="round" strokeLinecap="round" />
        {now && <circle cx={x(now.t)} cy={y(now.share)} r={4} fill="var(--lapis)" />}
        <text x={PAD.l} y={H - 1}>0h</text>
        <text x={W - PAD.r} y={H - 1} textAnchor="end">{span(run.t1 - run.t0)}</text>
      </svg>
      <p>Share of observations made through its own hooks, not screenshots.</p>
    </section>
  )
}

/** Tokens spent reaching each milestone; the unfinished one is hollow. */
export function CostChart({ run, at }: { run: Run; at: number }) {
  const bars = run.costPerMilestone(at)
  const n = Math.max(1, run.milestonesDefined.length)
  const max = Math.max(1, ...bars.map(b => b.tokens))
  const bw = (W - PAD.l - PAD.r) / n
  const y = (v: number) => PAD.t + (1 - v / max) * (H - PAD.t - PAD.b)
  const last = [...bars].reverse().find(b => b.done)
  return (
    <section className="chart">
      <h3>Tokens per milestone <b>{last ? big(last.tokens) : '–'}</b></h3>
      <svg width={W} height={H} role="img" aria-label="Tokens spent per milestone">
        <line x1={PAD.l} x2={W - PAD.r} y1={H - PAD.b} y2={H - PAD.b} stroke="var(--rule)" />
        {bars.map((b, i) => {
          const x0 = PAD.l + i * bw + 4
          return <g key={b.id}>
            <title>{b.name}: {big(b.tokens)} tokens{b.done ? '' : ' so far'}</title>
            <rect x={x0} y={y(b.tokens)} width={bw - 8} height={H - PAD.b - y(b.tokens)}
                  fill={b.done ? 'var(--stone-hi)' : 'none'} stroke="var(--stone-hi)" strokeDasharray={b.done ? undefined : '4 3'} />
            <text x={x0 + (bw - 8) / 2} y={H - 2} textAnchor="middle">{i + 1}</text>
          </g>
        })}
        <text x={PAD.l - 5} y={PAD.t + 8} textAnchor="end">{big(max)}</text>
      </svg>
      <p>Tokens spent to reach each milestone; falls as hooks accumulate.</p>
    </section>
  )
}
