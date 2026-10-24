import { useState } from 'react'
import type { Leverage as LeverageData, LeverageBin, LeveragePre, Run } from '../data/model'
import { big, span, trend } from '../util'

/** Leverage: what the program got done per model step, and what each action cost in tokens. Two single-series charts in
 *  30-minute bins (no shared axis), with the trailing-30-minute value large above each. Reusable: it only needs a Run and a
 *  time, so the Stage overlay can mount it as is. Estimated bins (made afterwards from host commands) are dashed and hollow. */

const W = 214, H = 66, PAD = { l: 2, r: 2, t: 5, b: 14 }, PRE_W = 38  // PRE_W: room for the "before tracking" block
const HEAD_W = 120  // the large value sits left of each chart
const SURFACE = '#17191d'

const fmtAps = (v: number) => (v < 10 ? v.toFixed(1) : v < 100 ? v.toFixed(0) : Math.round(v).toLocaleString('en-US'))
const clock = (t: number) => new Date(t).toISOString().slice(11, 16) + 'Z'

type Geo = { x: (t: number) => number; bw: number; x0: number; x1: number; preX: number; e0: number; e1: number }

function geometry(run: Run, lev: LeverageData): Geo {
  const pre = lev.pre
  const e0 = pre ? run.t0 + Math.floor((pre.t1 - run.t0) / lev.binMs) * lev.binMs : run.t0
  const e1 = Math.max(run.t1, e0 + 4 * lev.binMs)
  const x0 = PAD.l + (pre ? PRE_W : 0), x1 = W - PAD.r
  const x = (t: number) => x0 + (t - e0) / (e1 - e0) * (x1 - x0)
  return { x, bw: lev.binMs / (e1 - e0) * (x1 - x0), x0, x1, preX: PAD.l, e0, e1 }
}

function Headline({ label, value, unit, tr, est }: { label: string; value: string; unit: string; tr: React.ReactNode; est: boolean }) {
  return (
    <h3>
      <span>{label}{est && <em className="est" title="Estimated afterwards from the commands the agent sent: actions it asked for, not actions done"> estimated</em>}</span>
      <b>{est && '≈'}{value}<small>{unit}</small>{tr}</b>
    </h3>
  )
}

function Trend({ values, good }: { values: number[]; good: 'up' | 'down' }) {
  const t = trend(values)
  if (!t || Math.abs(t.pct) < 0.05) return <span className="trend flat">{t ? '≈ flat' : ''}</span>
  const better = good === 'up' ? t.pct > 0 : t.pct < 0
  const text = t.pct >= 2 ? `${Math.round(1 + t.pct)}×` : `${Math.round(Math.abs(t.pct) * 100)}%`
  return <span className={`trend ${better ? 'better' : 'worse'}`} title="Change from the first bins to the latest">{t.pct < 0 ? '▼' : '▲'} {text}</span>
}

function Tip({ bin, pre, geo, kinds }: { bin?: LeverageBin; pre?: LeveragePre; geo: Geo; kinds: string[] }) {
  const d = bin ?? pre
  if (!d) return null
  const cx = bin ? geo.x((bin.t0 + bin.t1) / 2) : geo.preX + PRE_W / 2
  const entries = kinds.filter(k => (d.kinds[k] ?? 0) > 0).map(k => [k, d.kinds[k]] as const)
  const sum = entries.reduce((s, [, n]) => s + n, 0) || 1
  return (
    <div className="levtip" style={{ left: Math.min(Math.max(cx + HEAD_W, 110), W + HEAD_W - 70) }} role="tooltip">
      <div className="lt-h">{bin ? `${clock(bin.t0)} to ${clock(bin.t1)}` : `Before tracking (to ${clock(pre!.t1)})`}</div>
      {pre && !bin && <div className="lt-n">Counted as one lump when tracking began. Shown as an average.</div>}
      {bin?.estimated && <div className="lt-n est">Estimated from host commands</div>}
      <div className="lt-r"><span>Actions per step</span><b>{d.aps !== undefined ? fmtAps(d.aps) : '–'}</b></div>
      <div className="lt-r"><span>Tokens per action</span><b>{d.tpa !== undefined ? big(d.tpa) : '–'}</b></div>
      <div className="lt-r dim"><span>{d.actions.toLocaleString('en-US')} actions · {d.steps} steps</span><b /></div>
      {entries.map(([k, n]) => (
        <div className="lt-k" key={k}>
          <span>{k}</span><i style={{ width: `${Math.max(2, n / sum * 60)}px` }} /><b>{n.toLocaleString('en-US')}</b>
        </div>
      ))}
    </div>
  )
}

/** Actions per step: one bar per bin. Estimated bins are dashed outlines. */
function ActionsChart({ lev, geo, hover, setHover }: { lev: LeverageData; geo: Geo; hover: number; setHover: (i: number) => void }) {
  const vals = [...lev.bins.map(b => b.aps ?? 0), lev.pre?.aps ?? 0]
  const max = Math.max(1, ...vals) * 1.08
  const y = (v: number) => PAD.t + (1 - v / max) * (H - PAD.t - PAD.b)
  const base = H - PAD.b
  const bw = Math.max(3, geo.bw - 2)
  return (
    <>
      <defs><pattern id="lev-hatch" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="1.7" height="4" fill="var(--verde)" /></pattern></defs>
      <line x1={PAD.l} x2={W - PAD.r} y1={base} y2={base} stroke="var(--rule)" />
      {lev.pre?.aps !== undefined && (
        <rect x={geo.preX + 4} y={y(lev.pre.aps)} width={PRE_W - 12} height={Math.max(1, base - y(lev.pre.aps))}
              fill="none" stroke="var(--stone-hi)" strokeDasharray="3 3" strokeWidth={1.4} />
      )}
      {lev.bins.map((b, i) => {
        const h = Math.max(1.5, base - y(b.aps ?? 0)), x = geo.x(b.t0) + 1
        return b.estimated
          ? <rect key={i} x={x + .5} y={base - h + .5} width={bw - 1} height={h - .5} fill="url(#lev-hatch)" stroke="var(--verde)" strokeWidth={1} />
          : <rect key={i} x={x} y={base - h} width={bw} height={h} fill="var(--verde)" opacity={hover === i ? 1 : .88} />
      })}
      <text x={geo.x1} y={PAD.t + 7} textAnchor="end">{fmtAps(max / 1.08)}</text>
      {hover >= 0 && <rect x={geo.x(lev.bins[hover].t0)} y={PAD.t} width={geo.bw} height={base - PAD.t} fill="none" stroke="var(--text)" strokeWidth={1} opacity={.5} />}
      {hits(lev, geo, setHover)}
    </>
  )
}

function hits(lev: LeverageData, geo: Geo, setHover: (i: number) => void) {
  return (
    <>
      {lev.pre && <rect x={geo.preX} y={0} width={PRE_W} height={H - PAD.b} fill="transparent" onMouseEnter={() => setHover(-2)} />}
      {lev.bins.map((b, i) => <rect key={i} x={geo.x(b.t0)} y={0} width={geo.bw} height={H - PAD.b} fill="transparent" onMouseEnter={() => setHover(i)} />)}
    </>
  )
}

function tpaRange(lev: LeverageData) {
  const vals = [...lev.bins.filter(b => b.tpa).map(b => b.tpa!), ...(lev.pre?.tpa ? [lev.pre.tpa] : [])]
  const lo = Math.min(...vals), hi = Math.max(...vals)
  return { lo, hi, log: vals.length > 1 && hi / lo > 6 }
}

/** Tokens per action: a line through the bins, on a log scale when the range spans more than about 6×. Dashed and hollow where estimated. */
function TokensPerActionChart({ lev, geo, hover, setHover }: { lev: LeverageData; geo: Geo; hover: number; setHover: (i: number) => void }) {
  const pts = lev.bins.filter(b => b.tpa !== undefined && b.tpa > 0)
  const { lo, hi, log } = tpaRange(lev)
  const dlo = log ? Math.pow(10, Math.floor(Math.log10(lo))) : 0, dhi = log ? Math.pow(10, Math.ceil(Math.log10(hi))) : (hi || 1) * 1.08
  const f = (v: number) => log ? (Math.log10(v) - Math.log10(dlo)) / (Math.log10(dhi) - Math.log10(dlo)) : v / dhi
  const y = (v: number) => PAD.t + 3 + (1 - f(v)) * (H - PAD.t - PAD.b - 6)
  const base = H - PAD.b
  const cx = (b: LeverageBin) => geo.x((b.t0 + b.t1) / 2)
  const last = pts[pts.length - 1]
  return (
    <>
      {[dhi, ...(log ? [dlo] : [])].map(v => <line key={v} x1={PAD.l} x2={W - PAD.r} y1={y(v)} y2={y(v)} stroke="var(--rule)" strokeDasharray={v === dlo ? undefined : '1 4'} />)}
      <line x1={PAD.l} x2={W - PAD.r} y1={base} y2={base} stroke="var(--rule)" />
      {lev.pre?.tpa && (
        <g>
          <line x1={geo.preX + 4} x2={geo.preX + PRE_W - 8} y1={y(lev.pre.tpa)} y2={y(lev.pre.tpa)} stroke="var(--stone-hi)" strokeDasharray="3 3" strokeWidth={1.8} />
          <circle cx={geo.preX + PRE_W - 8} cy={y(lev.pre.tpa)} r={3.5} fill={SURFACE} stroke="var(--stone-hi)" strokeWidth={1.6} />
        </g>
      )}
      {pts.slice(1).map((b, i) => {
        const a = pts[i]
        return <line key={i} x1={cx(a)} y1={y(a.tpa!)} x2={cx(b)} y2={y(b.tpa!)} stroke="var(--verde)" strokeWidth={2.4} strokeLinecap="round"
                     strokeDasharray={a.estimated || b.estimated ? '4 3' : undefined} />
      })}
      {pts.map((b, i) => b !== last && (
        <circle key={i} cx={cx(b)} cy={y(b.tpa!)} r={hover === lev.bins.indexOf(b) ? 4.5 : 3}
                fill={b.estimated ? SURFACE : 'var(--verde)'} stroke="var(--verde)" strokeWidth={b.estimated ? 1.6 : 0} />
      ))}
      {last && <circle cx={cx(last)} cy={y(last.tpa!)} r={5} fill={last.estimated ? SURFACE : 'var(--verde)'} stroke={last.estimated ? 'var(--verde)' : SURFACE} strokeWidth={2} />}
      <text x={geo.x1} y={y(dhi) + 11} textAnchor="end">{big(dhi)}</text>
      {log && <text x={geo.x1} y={y(dlo) - 3} textAnchor="end">{big(dlo)}</text>}
      {hover >= 0 && <line x1={cx(lev.bins[hover])} x2={cx(lev.bins[hover])} y1={PAD.t} y2={base} stroke="var(--text)" opacity={.5} />}
      {hits(lev, geo, setHover)}
    </>
  )
}

function AxisLabels({ run, geo, pre }: { run: Run; geo: Geo; pre?: LeveragePre }) {
  return (
    <>
      {pre && <text x={geo.preX + 2} y={H - 2}>pre</text>}
      <text x={geo.x0} y={H - 2}>{pre ? span(geo.e0 - run.t0) : '0h'}</text>
      <text x={geo.x1} y={H - 2} textAnchor="end">{span(geo.e1 - run.t0)}</text>
    </>
  )
}

export function Leverage({ run, at }: { run: Run; at: number }) {
  const [src, setSrc] = useState(0)  // which chart the pointer is over: only that one shows the tooltip
  const [hover, setHover] = useState(-1)  // bin index; -2: the "before tracking" block; -1: none
  if (!run.of('program_actions').length) return null
  const lev = run.leverage(at)
  if (!lev.bins.length && !lev.pre) {
    return <section className="lev"><h2>Leverage</h2><p className="lev-empty">Not counted yet: the program has not reported any actions by this moment.</p></section>
  }
  const geo = geometry(run, lev)
  const bin = hover >= 0 ? lev.bins[hover] : undefined
  const tip = bin || (hover === -2 && lev.pre) ? <Tip bin={bin} pre={hover === -2 ? lev.pre : undefined} geo={geo} kinds={lev.kinds} /> : null
  const apsVals = lev.bins.map(b => b.aps!), tpaVals = lev.bins.map(b => b.tpa!)
  const now = lev.now ?? (lev.bins.length ? { aps: lev.bins[lev.bins.length - 1].aps!, tpa: lev.bins[lev.bins.length - 1].tpa!, estimated: lev.bins[lev.bins.length - 1].estimated } : undefined)
  const clear = () => setHover(-1)
  return (
    <section className="lev" aria-label="Leverage">
      <h2>
        <span>Leverage</span>
        <span className="lev-note">
          <span>30-minute bins</span>
          {lev.anyEstimated && <span><i className="sw est" />estimated (asked for, not done)</span>}
          {lev.pre && <span><i className="sw pre" />before tracking: {lev.pre.actions.toLocaleString('en-US')} actions, as an average</span>}
          {tpaRange(lev).log && <span>tokens per action on a log scale</span>}
        </span>
      </h2>
      <div className="lev-grid">
        <div className="lev-chart" onMouseLeave={clear}>
          <div className="lev-row"><Headline label="Actions per step" value={now ? fmtAps(now.aps) : '–'} unit="/step" tr={<Trend values={apsVals} good="up" />} est={!!now?.estimated} />
          <svg width={W} height={H} role="img" aria-label="Actions per model step, in 30-minute bins">
            <ActionsChart lev={lev} geo={geo} hover={hover} setHover={i => { setSrc(0); setHover(i) }} />
            <AxisLabels run={run} geo={geo} pre={lev.pre} />
          </svg></div>
          {src === 0 && tip}
        </div>
        <div className="lev-chart" onMouseLeave={clear}>
          <div className="lev-row"><Headline label="Tokens per action" value={now ? big(now.tpa) : '–'} unit="/action" tr={<Trend values={tpaVals} good="down" />} est={!!now?.estimated} />
          <svg width={W} height={H} role="img" aria-label="Tokens per action, in 30-minute bins, log scale when the range is wide">
            <TokensPerActionChart lev={lev} geo={geo} hover={hover} setHover={i => { setSrc(1); setHover(i) }} />
            <AxisLabels run={run} geo={geo} pre={lev.pre} />
          </svg></div>
          {src === 1 && tip}
        </div>
      </div>
      <table className="sr">
        <caption>Leverage by 30-minute bin</caption>
        <thead><tr><th>From</th><th>Actions per step</th><th>Tokens per action</th><th>Estimated</th><th>Kinds</th></tr></thead>
        <tbody>{lev.bins.map((b, i) => (
          <tr key={i}><td>{clock(b.t0)}</td><td>{fmtAps(b.aps!)}</td><td>{Math.round(b.tpa!)}</td><td>{b.estimated ? 'yes' : 'no'}</td>
            <td>{Object.entries(b.kinds).map(([k, n]) => `${k} ${n}`).join(', ')}</td></tr>))}</tbody>
      </table>
    </section>
  )
}
