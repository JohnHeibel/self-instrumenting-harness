import { useEffect, useMemo, useRef } from 'react'
import type { RoomItem, Run } from '../data/model'
import { big, hms, span } from '../util'

const DASH = '—'
const fmt = (v: number | undefined, f: (n: number) => string = big) => (v === undefined || !Number.isFinite(v) ? DASH : f(v))
const pct = (v?: number) => fmt(v, x => `${Math.round(x * 100)}%`)
const dec = (v?: number) => fmt(v, x => x.toFixed(1))

const FROM_LABEL: Record<string, string> = {
  player: 'player', supervisor: 'supervisor', harness: 'harness', 'tool review': 'tool review', runner: 'runner', grader: 'grader',
}

/** The agent's state as one line: ended, no records for two minutes (its last agent_state is then not trusted), or its
 *  agent_state; then the background task holding the hands, if any. */
export function StateLine({ run, at, now }: { run: Run; at: number; now: number }) {
  const atEnd = at >= run.t1 - 2000
  const stale = atEnd && !run.ended && !run.isLive(now)
  const st = run.agentState(at)
  const [label, age] = atEnd && run.ended ? ['ended', at - run.t1] : stale ? ['no records', now - run.t1] : [st.state.replace(/_/g, ' '), at - st.since]
  const t = stale ? undefined : run.openTasks(at)[0]
  return (
    <div className="stateline">
      <b>{label}</b> <span className="age">{span(age)}</span>
      {t && <span className="task"> · {t.task} {t.name.replace(/^scripts\//, '')}{t.hands ? ', hands' : ''} <span className="age">{span(at - t.t)}</span></span>}
    </div>
  )
}

/** The room: the player's words and goals, what its watchers told it, and the grader's verdicts, newest at the bottom,
 *  cut at the replay position. Each line is a record's own text, cut at a sentence end. */
export function Room({ run, at, now, variant }: { run: Run; at: number; now: number; variant: 'letters' | 'chat' | 'inscriptions' | 'app' }) {
  const items = run.room(at, variant === 'chat' ? 60 : 40)
  const box = useRef<HTMLDivElement>(null)
  const last = items[items.length - 1]
  useEffect(() => { const el = box.current; if (el) el.scrollTop = el.scrollHeight }, [last?.t, items.length])
  return (
    <section className={`room room-${variant}`} aria-label="The room">
      <div className="roomhead"><StateLine run={run} at={at} now={now} /></div>
      <div className="roombody" ref={box}>
        {items.map(i => variant === 'app' ? <Msg key={`${i.t}${i.kind}${i.text.slice(0, 12)}`} item={i} run={run} /> : <Line key={`${i.t}${i.kind}${i.text.slice(0, 12)}`} item={i} run={run} />)}
        {!items.length && <p className="quiet">{DASH}</p>}
      </div>
    </section>
  )
}

const INITIALS: Record<string, string> = { player: 'P', supervisor: 'S', harness: 'H', 'tool review': 'TR', runner: 'R' }

/** A message-app row: avatar initials, sender and time, then the bubble. The grader's verdicts are system rows. */
function Msg({ item: i, run }: { item: RoomItem; run: Run }) {
  if (i.from === 'grader') return <div className={`sys ${i.kind}`}><span>{i.kind === 'milestone' ? `milestone · ${i.text}` : i.text}</span><time>{hms(i.t - run.t0)}</time></div>
  const own = i.from === 'player'
  return (
    <div className={`msg ${own ? 'own' : 'watch'} from-${i.from.replace(/\s+/g, '-')} k-${i.kind}`} title={i.full}>
      <span className="av" aria-hidden="true">{INITIALS[i.from] ?? i.from.slice(0, 1).toUpperCase()}</span>
      <div className="mb">
        <div className="mhd"><b>{FROM_LABEL[i.from] ?? i.from}</b><time>{hms(i.t - run.t0)}</time></div>
        <p>{i.kind === 'goal' && <em>goal </em>}{i.text}</p>
      </div>
    </div>
  )
}

function Line({ item: i, run }: { item: RoomItem; run: Run }) {
  if (i.from === 'grader') return (
    <div className={`rl verdict ${i.kind}`}><span>{i.kind === 'milestone' ? `milestone: ${i.text}` : i.text}</span><time>{hms(i.t - run.t0)}</time></div>
  )
  const own = i.from === 'player'
  return (
    <div className={`rl ${own ? 'own' : 'watch'} from-${i.from.replace(' ', '-')} k-${i.kind}`} title={i.full}>
      <div className="who"><span>{FROM_LABEL[i.from] ?? i.from}</span><time>{hms(i.t - run.t0)}</time></div>
      <p>{i.kind === 'goal' ? <><em>goal</em> {i.text}</> : i.text}</p>
    </div>
  )
}

/** The agent's own words, one entry per step that had any, newest at the bottom, cut at the replay position. */
export function Reasoning({ run, at, now }: { run: Run; at: number; now: number }) {
  const items = run.thoughts(at)
  const box = useRef<HTMLDivElement>(null)
  const newest = items[items.length - 1]?.t
  useEffect(() => { const el = box.current; if (el) el.scrollTop = el.scrollHeight }, [items.length, newest])
  return (
    <section className="paper reason" aria-label="Reasoning">
      <div className="ph"><h2>Reasoning</h2><StateLine run={run} at={at} now={now} /></div>
      <div className="pbody" ref={box}>
        {items.map(i => (
          <div key={i.t + i.kind} className={`th k-${i.kind}`} title={i.full}>
            <span className="st">{i.step}</span><time>{hms(i.t - run.t0)}</time>
            <p>{i.kind === 'goal' && <b>goal </b>}{i.text}</p>
          </div>
        ))}
        {!items.length && <p className="quiet">{DASH}</p>}
      </div>
    </section>
  )
}

/** Every tool call the agent made itself, in order: step, time, tool (kept script name for run_script), short arguments,
 *  failed or ok, duration. Identical consecutive calls are folded. */
export function Calls({ run, at }: { run: Run; at: number }) {
  const items = run.calls(at, 90)
  const box = useRef<HTMLDivElement>(null)
  const newest = items[items.length - 1]?.t
  useEffect(() => { const el = box.current; if (el) el.scrollTop = el.scrollHeight }, [items.length, newest])
  return (
    <section className="paper calls" aria-label="Tool calls">
      <div className="ph"><h2>Tool calls</h2><span className="pc">{run.stepAt(at)} steps</span></div>
      <div className="pbody" ref={box}>
        <table>
          <tbody>
            {items.map(c => (
              <tr key={c.t + c.tool} className={c.ok ? '' : 'fail'} title={c.error || undefined}>
                <td className="st">{c.step}</td><td className="tm">{hms(c.t - run.t0)}</td>
                <td className="tn">{c.tool}{c.count > 1 && <span className="ct"> ×{c.count}</span>}</td>
                <td className="ta">{c.args}</td>
                <td className="ok">{c.ok ? '' : 'failed'}</td>
                <td className="du">{c.ms >= 1000 ? `${(c.ms / 1000).toFixed(c.ms < 10000 ? 1 : 0)}s` : `${Math.round(c.ms)}ms`}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!items.length && <p className="quiet">{DASH}</p>}
      </div>
    </section>
  )
}

/** Milestones reached of those announced; opens to the whole set by name, reached ones with their run time. */
export function MilestoneList({ run, at }: { run: Run; at: number }) {
  const ms = run.milestones(at)
  const done = ms.filter(m => m.t !== undefined).length
  return (
    <details className="mslist">
      <summary><b>{ms.length ? done : DASH}</b>{ms.length > 0 && <span> / {ms.length}</span>} <em>milestones</em></summary>
      <ol>
        {ms.map(m => <li key={m.id} className={m.t !== undefined ? 'on' : ''}><span>{m.name}</span>{m.t !== undefined && <time>{hms(m.t - run.t0)}</time>}</li>)}
      </ol>
    </details>
  )
}

/** The climb: milestones reached over run time as a stepped slope, each stretch labelled with the cache-weighted tokens it
 *  cost, deaths marked under the base, the stone at the moment shown. Click or drag to seek. */
export function Climb({ run, at, w, h, onSeek, labels = true }: { run: Run; at: number; w: number; h: number; onSeek: (t: number) => void; labels?: boolean }) {
  const ref = useRef<SVGSVGElement>(null)
  const X0 = 16, X1 = w - 16, BASE = h - 30, TOP = 34
  const start = run.runnerStart, dur = Math.max(1, run.t1 - start)
  const total = Math.max(1, run.milestonesDefined.length)
  const fx = (t: number) => X0 + ((t - start) / dur) * (X1 - X0)
  const fy = (n: number) => BASE - (n / total) * (BASE - TOP)
  const segs = run.segments(run.t1)
  const g = useMemo(() => {
    let n = 0, d = `M${X0},${BASE}`
    const steps = segs.filter(s => s.id).map(s => { d += `H${fx(s.t1)}`; const y0 = fy(n); n++; d += `V${fy(n)}`; return { ...s, y0, y1: fy(n), x0: fx(s.t0), x1: fx(s.t1) } })
    d += `H${X1}`
    return { d, steps, deaths: run.of('marker').filter(m => m.kind === 'agent_died').map(m => ({ t: m.t, x: fx(m.t), label: String(m.label ?? 'died') })) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run, w, h])
  const reached = run.segments(at).filter(s => s.id).length
  const px = fx(Math.min(at, run.t1))
  const seek = (cx: number) => { const r = ref.current!.getBoundingClientRect(); const x = (cx - r.left) * (w / r.width); onSeek(start + Math.max(0, Math.min(1, (x - X0) / (X1 - X0))) * dur) }
  const hours = Array.from({ length: Math.floor(dur / 3_600_000) + 1 }, (_, i) => i)
  // Stretch labels: the costliest stretches get a label (the cost), others show it on hover.
  const ranked = [...g.steps].filter(s => s.cw > 0).sort((a, b) => b.cw - a.cw).slice(0, 6).map(s => s.t1)
  return (
    <svg ref={ref} className="climb" width={w} height={h} viewBox={`0 0 ${w} ${h}`} role="slider" aria-label="Milestones over the run"
      aria-valuemin={0} aria-valuemax={Math.round(dur / 1000)} aria-valuenow={Math.round((at - start) / 1000)}
      onMouseDown={e => { seek(e.clientX); const mv = (ev: MouseEvent) => seek(ev.clientX); const up = () => { removeEventListener('mousemove', mv); removeEventListener('mouseup', up) }; addEventListener('mousemove', mv); addEventListener('mouseup', up) }}>
      <defs><clipPath id={`past${w}`}><rect x={0} y={0} width={px} height={h} /></clipPath></defs>
      {[0.25, 0.5, 0.75, 1].map(f => <line key={f} x1={X0} x2={X1} y1={fy(total * f)} y2={fy(total * f)} className="grid" />)}
      <path d={`${g.d}V${BASE}H${X0}Z`} className="hill ghost" />
      <g clipPath={`url(#past${w})`}><path d={`${g.d}V${BASE}H${X0}Z`} className="hill" /></g>
      <path d={g.d} className="ridge" />
      <line x1={X0} x2={X1} y1={BASE} y2={BASE} className="base" />
      {hours.map(i => <g key={i}><line x1={fx(start + i * 3_600_000)} x2={fx(start + i * 3_600_000)} y1={BASE} y2={BASE + 5} className="base" /><text x={fx(start + i * 3_600_000)} y={BASE + 18} className="lab mid">{i}h</text></g>)}
      {g.steps.map(s => (
        <g key={s.t1} className={s.t1 <= at ? 'step on' : 'step'}>
          <title>{s.name} · {hms(s.t1 - run.t0)} · {big(s.cw)} cw tokens since the previous milestone</title>
          <rect x={s.x1 - 3} y={s.y1 - 3} width={6} height={6} transform={`rotate(45 ${s.x1} ${s.y1})`} className="ms" />
          {labels && ranked.includes(s.t1) && <text x={(s.x0 + s.x1) / 2} y={s.y0 - 6} className="cost mid">{big(s.cw)}</text>}
        </g>
      ))}
      {g.deaths.map(d => <path key={d.t} d={`M${d.x - 4},${BASE + 3} L${d.x + 4},${BASE + 11} M${d.x + 4},${BASE + 3} L${d.x - 4},${BASE + 11}`} className="death"><title>{hms(d.t - run.t0)} · {d.label}</title></path>)}
      <line x1={px} x2={px} y1={TOP} y2={BASE} className="now" />
      <circle cx={px} cy={fy(reached) - 7} r={7} className="stone" />
    </svg>
  )
}

/** The headline as a table of figures: the run so far, the last 15-minute bin, and the first one, for comparison. */
export function Figures({ run, at }: { run: Run; at: number }) {
  const m = run.metrics(at), s = run.metricSeries(at)
  const segs = run.segments(at).filter(x => x.id)
  const bins = s?.bins ?? []
  const firstWith = (k: 'cwPerAction' | 'actPerStep' | 'handsPct') => bins.find(b => b[k] !== undefined)?.[k]
  const lastBin = bins[bins.length - 1]
  const rows: [string, string, string, string, string][] = [
    ['cw tokens / milestone', fmt(m?.cwPerMilestone), segs.length ? big(segs[segs.length - 1].cw) : DASH, segs.length ? big(segs[0].cw) : DASH, 'run: cw ÷ milestones; then the latest and the first stretch'],
    ['cw tokens / action', fmt(m?.cwPerAction), fmt(lastBin?.cwPerAction), fmt(firstWith('cwPerAction')), 'cw ÷ program_actions; 15-minute bins'],
    ['actions / step', dec(m?.actPerStep), dec(lastBin?.actPerStep), dec(firstWith('actPerStep')), 'program_actions ÷ usage records; 15-minute bins'],
    ['hands busy', pct(m?.handsPct), pct(lastBin?.handsPct), pct(firstWith('handsPct')), 'costs.py rules; 15-minute bins'],
  ]
  return (
    <section className="figures" aria-label="Headline figures">
      <table>
        <thead><tr><th /><th>run</th><th>latest</th><th>first</th></tr></thead>
        <tbody>{rows.map(([k, a, b, c, how]) => <tr key={k} title={how}><th>{k}</th><td className="big">{a}</td><td>{b}</td><td>{c}</td></tr>)}</tbody>
      </table>
    </section>
  )
}
