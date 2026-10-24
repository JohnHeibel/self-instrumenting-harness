import { useState } from 'react'
import type { Run } from '../data/model'
import { big, span } from '../util'

const W = 474, H = 100, PAD = { l: 34, r: 8, t: 8, b: 18 }

/** Share of observations made through the agent's own hooks, over the whole run's time axis. */
export function CoverageChart({ run, at }: { run: Run; at: number }) {
  const pts = run.coverage(at)
  const [hover, setHover] = useState<number | null>(null)
  const x = (t: number) => PAD.l + (t - run.t0) / Math.max(1, run.t1 - run.t0) * (W - PAD.l - PAD.r)
  const y = (s: number) => PAD.t + (1 - s) * (H - PAD.t - PAD.b)
  const now = pts[pts.length - 1]
  const path = pts.map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(1)},${y(p.share).toFixed(1)}`).join('')
  const h = hover !== null ? pts[hover] : null
  return (
    <div className="panel chart">
      <h3>Interface coverage <span className="count">· share of observations through the agent's hooks</span></h3>
      <div className="now">{now ? Math.round(now.share * 100) : 0}%</div>
      <svg width={W} height={H} role="img" aria-label="Share of observations made through hooks over time"
           onMouseMove={e => {
             if (!pts.length) return
             const mx = e.nativeEvent.offsetX
             let best = 0
             pts.forEach((p, i) => { if (Math.abs(x(p.t) - mx) < Math.abs(x(pts[best].t) - mx)) best = i })
             setHover(best)
           }} onMouseLeave={() => setHover(null)}>
        {[0, 0.5, 1].map(s => <g key={s}>
          <line x1={PAD.l} x2={W - PAD.r} y1={y(s)} y2={y(s)} stroke="var(--border)" strokeWidth={1} />
          <text x={PAD.l - 6} y={y(s) + 4} fontSize={11} fill="var(--text-muted)" textAnchor="end">{s * 100}%</text>
        </g>)}
        <path d={path} fill="none" stroke="var(--ch-hook)" strokeWidth={2.5} strokeLinejoin="round" strokeLinecap="round" />
        {now && <circle cx={x(now.t)} cy={y(now.share)} r={4.5} fill="var(--ch-hook)" stroke="var(--surface-1)" strokeWidth={2} />}
        {h && <line x1={x(h.t)} x2={x(h.t)} y1={PAD.t} y2={H - PAD.b} stroke="var(--text-muted)" strokeDasharray="3 3" />}
        <text x={PAD.l} y={H - 3} fontSize={11} fill="var(--text-muted)">0h</text>
        <text x={W - PAD.r} y={H - 3} fontSize={11} fill="var(--text-muted)" textAnchor="end">{span(run.t1 - run.t0)}</text>
      </svg>
      {h && <div className="tip" style={{ left: 16 + x(h.t), top: 70 }}>
        {span(h.t - run.t0)}: {Math.round(h.share * 100)}% via hooks ({h.n} observations)</div>}
    </div>
  )
}

/** Tokens spent between consecutive milestones; the unfinished one is drawn hollow. */
export function CostChart({ run, at }: { run: Run; at: number }) {
  const bars = run.costPerMilestone(at)
  const [hover, setHover] = useState<number | null>(null)
  const n = Math.max(1, run.milestonesDefined.length)
  const max = Math.max(1, ...bars.map(b => b.tokens))
  const bw = (W - PAD.l - PAD.r) / n
  const y = (v: number) => PAD.t + (1 - v / max) * (H - PAD.t - PAD.b)
  const last = [...bars].reverse().find(b => b.done)
  return (
    <div className="panel chart">
      <h3>Tokens per milestone <span className="count">· falls as the hook library grows</span></h3>
      <div className="now">{last ? big(last.tokens) : '–'}<span className="sub"> {last ? `for ${last.name}` : 'no milestone yet'}</span></div>
      <svg width={W} height={H} role="img" aria-label="Tokens spent per milestone" onMouseLeave={() => setHover(null)}>
        <line x1={PAD.l} x2={W - PAD.r} y1={H - PAD.b} y2={H - PAD.b} stroke="var(--border)" />
        {bars.map((b, i) => {
          const x0 = PAD.l + i * bw + 3, top = y(b.tokens), w = bw - 6
          return <g key={b.id} onMouseEnter={() => setHover(i)}>
            <rect x={PAD.l + i * bw} y={PAD.t} width={bw} height={H - PAD.t - PAD.b} fill="transparent" />
            <path d={`M${x0},${H - PAD.b} V${top + 4} q0,-4 4,-4 H${x0 + w - 4} q4,0 4,4 V${H - PAD.b} Z`}
                  fill={b.done ? 'var(--series-cost)' : 'none'} stroke="var(--series-cost)" strokeWidth={b.done ? 0 : 1.5}
                  strokeDasharray={b.done ? undefined : '4 3'} />
          </g>
        })}
        <text x={PAD.l - 6} y={PAD.t + 8} fontSize={11} fill="var(--text-muted)" textAnchor="end">{big(max)}</text>
      </svg>
      {hover !== null && bars[hover] && <div className="tip" style={{ left: 16 + PAD.l + hover * bw, top: 70 }}>
        {bars[hover].name}: {big(bars[hover].tokens)} tokens{bars[hover].done ? '' : ' so far'}</div>}
    </div>
  )
}
