import { useState } from 'react'
import type { Run, TimeUse as TimeUseData } from '../data/model'

/** Time use: how the agent's wall-clock time split between hands busy (acting on the program), tools only (tools or reading
 *  tasks running, hands free) and idle (nothing running: the model thinking). Totals as one proportional bar with h:mm and %,
 *  and the split per 30-minute bin. Time when the agent was not running is left out of the split and shown as a gap.
 *  Takes only a Run and a time, like Leverage. Derived from tool_call, task_* and agent_state records (event-log.md). */

const W = 190, H = 34, PAD = { l: 2, r: 2, t: 1, b: 1 }
const KEYS = [
  { k: 'hands', label: 'Hands busy', hint: 'acting on the program', color: 'var(--ochre)' },
  { k: 'tools', label: 'Tools only', hint: 'tools running, hands free', color: 'var(--stone-hi)' },
  { k: 'idle', label: 'Idle', hint: 'nothing running: the model thinking', color: 'var(--rule)' },
] as const

const clock = (t: number) => new Date(t).toISOString().slice(11, 16) + 'Z'
/** h:mm */
const hm = (ms: number) => { const m = Math.round(ms / 60_000); return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')}` }
const pct = (part: number, whole: number) => whole > 0 ? `${Math.round(part / whole * 100)}%` : '–'

function Timeline({ run, tu, hover, setHover }: { run: Run; tu: TimeUseData; hover: number; setHover: (i: number) => void }) {
  const e1 = Math.max(run.t0 + 4 * tu.binMs, tu.bins[tu.bins.length - 1].t1)
  const x = (t: number) => PAD.l + (t - run.t0) / (e1 - run.t0) * (W - PAD.l - PAD.r)
  const bw = tu.binMs / (e1 - run.t0) * (W - PAD.l - PAD.r)
  const base = H - PAD.b, top = PAD.t, full = base - top
  return (
    <svg width={W} height={H} role="img" aria-label="Time use in 30-minute bins: share of time hands busy, tools only, idle">
      {tu.bins.map((b, i) => {
        const active = b.hands + b.tools + b.idle
        const x0 = x(b.t0) + 1, w = Math.max(3, bw - 2)
        if (active <= 0) return <rect key={i} x={x0} y={top} width={w} height={full} fill="none" stroke="var(--rule)" strokeDasharray="2 3" />
        // Partial bins (the newest) are as tall as the rest: the bar shows shares, not amounts.
        let y = base
        return (
          <g key={i} opacity={hover < 0 || hover === i ? 1 : .7}>
            {KEYS.map(({ k, color }) => {
              const h = b[k] / active * full
              y -= h
              return h > 0 ? <rect key={k} x={x0} y={y} width={w} height={h} fill={color} /> : null
            })}
          </g>
        )
      })}
      <line x1={PAD.l} x2={W - PAD.r} y1={base} y2={base} stroke="var(--rule)" />
      {hover >= 0 && <rect x={x(tu.bins[hover].t0)} y={top} width={bw} height={full} fill="none" stroke="var(--text)" opacity={.5} />}
      {tu.bins.map((b, i) => <rect key={i} x={x(b.t0)} y={0} width={bw} height={base} fill="transparent" onMouseEnter={() => setHover(i)} />)}
    </svg>
  )
}

export function TimeUse({ run, at }: { run: Run; at: number }) {
  const [hover, setHover] = useState(-1)
  const tu = run.timeUse(at)
  // Older runs and sources without tool calls: nothing to split, so say so quietly.
  if (!tu) return <section className="lev tu" aria-label="Time use"><h2><span>Time use</span></h2><p className="lev-empty">Not recorded for this run.</p></section>
  const t = tu.total, active = t.hands + t.tools + t.idle
  if (active <= 0) return <section className="lev tu" aria-label="Time use"><h2><span>Time use</span></h2><p className="lev-empty">Not enough time has passed to split yet.</p></section>
  const bin = hover >= 0 ? tu.bins[hover] : undefined
  const ba = bin ? bin.hands + bin.tools + bin.idle : 0
  return (
    <section className="lev tu" aria-label="Time use">
      <div className="tu-row">
        <h2 title={`Split of the ${hm(active)} the agent was running${t.off > 0 ? `; ${hm(t.off)} when it was off is left out` : ''}`}><span>Time use</span>{t.off > 0 && <small>{hm(t.off)} agent off</small>}</h2>
        <div className="tu-totals">
          <div className="tu-bar" role="img" aria-label={KEYS.map(({ k, label }) => `${label} ${pct(t[k], active)}`).join(', ')}>
            {KEYS.map(({ k, label, color }) => <i key={k} title={label} style={{ flex: t[k], background: color }} />)}
          </div>
          <dl className="tu-nums">{KEYS.map(({ k, label, hint, color }) => (
            <div key={k} title={hint}>
              <dt><i style={{ background: color }} />{label}</dt><dd>{pct(t[k], active)}<small>{hm(t[k])}</small></dd>
            </div>))}</dl>
        </div>
        <div className="lev-chart" onMouseLeave={() => setHover(-1)}>
          <Timeline run={run} tu={tu} hover={hover} setHover={setHover} />
          {bin && (
            <div className="levtip" style={{ left: 110, top: -6 }} role="tooltip">
              <div className="lt-h">{clock(bin.t0)} to {clock(bin.t1)}</div>
              {ba > 0 ? KEYS.map(({ k, label }) => (
                <div className="lt-r" key={k}><span>{label}</span><b>{pct(bin[k], ba)} · {hm(bin[k])}</b></div>
              )) : <div className="lt-n">The agent was not running.</div>}
              {bin.off > 0 && ba > 0 && <div className="lt-r dim"><span>agent off</span><b>{hm(bin.off)}</b></div>}
            </div>
          )}
        </div>
      </div>
      <table className="sr">
        <caption>Time use by 30-minute bin</caption>
        <thead><tr><th>From</th><th>Hands busy</th><th>Tools only</th><th>Idle</th><th>Agent off</th></tr></thead>
        <tbody>{tu.bins.map((b, i) => (
          <tr key={i}><td>{clock(b.t0)}</td><td>{hm(b.hands)}</td><td>{hm(b.tools)}</td><td>{hm(b.idle)}</td><td>{hm(b.off)}</td></tr>))}</tbody>
      </table>
    </section>
  )
}
