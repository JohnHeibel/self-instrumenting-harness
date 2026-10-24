import { useEffect, useRef, useState } from 'react'
import type { Kit, Run, TRec } from '../data/model'
import type { Spectator } from '../data/source'
import { big, hms, span } from '../util'

/** Wall-clock hold time for a notice on the game view, so nothing flashes by at timelapse speed. */
const NOTICE_MS = 10_000
/** A tool version counts as "just changed" for this long in run time. */
const RECENT = 10 * 60_000
const DASH = '—'

const num = (n: number | undefined, f: (n: number) => string = big) => (n === undefined || !Number.isFinite(n) ? DASH : f(n))
const pct = (p: number | undefined) => num(p, x => `${Math.round(x * 100)}%`)

export function Header({ run, at, now, onJump }: { run: Run; at: number; now: number; onJump: () => void }) {
  const h = run.harness
  return (
    <header className="head">
      <div className="brand">
        <span className="mark">Sisyphus</span>
        <span className="run">{run.recs[0]?.run_id}</span>
      </div>
      <dl className="rig">
        <div><dt>agent</dt><dd>{h.agent ?? DASH}{h.version ? ` ${h.version}` : ''}</dd></div>
        <div><dt>model</dt><dd>{h.model ?? DASH}</dd></div>
        <div><dt>effort</dt><dd className={h.effort ? '' : 'none'}>{h.effort ?? DASH}</dd></div>
        <div><dt>host</dt><dd>{run.started?.host ?? DASH}</dd></div>
      </dl>
      <LiveBadge run={run} at={at} now={now} onJump={onJump} />
      <div className="clock" aria-label="Run time">{hms(at - run.t0)}</div>
    </header>
  )
}

/** Where the view is relative to the run: following it live, behind it, idle, or replaying a finished run. */
export function LiveBadge({ run, at, now, onJump }: { run: Run; at: number; now: number; onJump: () => void }) {
  const behind = run.t1 - at
  if (run.ended) return <span className="pill" title="run_ended recorded"><i className="dot" />Ended</span>
  if (!run.isLive(now)) return (
    <span className="pill idle" title="No run_ended record, nothing written for two minutes">
      <i className="dot" />Idle {span(now - run.t1)}
      {behind > 2000 && <button className="jump" onClick={onJump}>Latest</button>}
    </span>
  )
  if (behind <= 2000) return <span className="pill live"><i className="livedot" />Live</span>
  return (
    <span className="pill behind"><i className="dot" />−{span(behind)}
      <button className="jump" onClick={onJump}>Live</button>
    </span>
  )
}

/** The current subgoal (`goal` record from set_goal) and what it serves; the run's own goal line until one is set. */
export function Goal({ run, at }: { run: Run; at: number }) {
  const g = run.subgoal(at)
  return (
    <section className="goal" aria-label="Current goal">
      <span className="gl">{g ? 'goal' : 'run goal'}</span>
      <h1>{g?.text ?? (run.goalTitle || DASH)}</h1>
      {g?.serves && <><span className="gl">for</span><p className="serves">{g.serves}</p></>}
    </section>
  )
}

/** Milestones reached of those announced, one cell each; the next one named. */
export function Progress({ run, at }: { run: Run; at: number }) {
  const ms = run.milestones(at)
  const done = ms.filter(m => m.t !== undefined).length
  const next = ms.find(m => m.t === undefined)
  if (!ms.length) return <section className="progress"><div className="pn"><b>{DASH}</b></div><p className="next">no milestones defined</p></section>
  return (
    <section className="progress" aria-label="Milestones">
      <div className="pn"><b>{done}</b><span>/ {ms.length}</span><em>milestones</em></div>
      <ol className="cells">
        {ms.map(m => <li key={m.id} className={m.t !== undefined ? (at - m.t < RECENT ? 'on recent' : 'on') : m === next ? 'next' : ''} title={m.t !== undefined ? `${m.name} · ${hms(m.t - run.t0)}` : m.name} />)}
      </ol>
      <p className="next">{next ? <>next <span>{next.name}</span></> : 'all reached'}</p>
    </section>
  )
}

/** The four efficiency numbers, as tools/time_use.py's "all" line, set as a ruled table. */
export function Metrics({ run, at }: { run: Run; at: number }) {
  const m = run.metrics(at)
  const rows: [string, string, string][] = [
    ['tokens / hour', num(m?.tokPerHour), 'Σ usage tokens ÷ wall hours, first runner/tool-server record → now'],
    ['tokens / step', num(m?.tokPerStep), 'Σ usage tokens ÷ model requests (usage records)'],
    ['tokens / action', num(m?.tokPerAction), 'Σ usage tokens ÷ Σ program_actions.count'],
    ['hands busy', pct(m?.handsPct), 'hands-busy time ÷ time the agent ran (wall − off)'],
  ]
  return (
    <dl className="metrics" aria-label="Efficiency">
      {rows.map(([label, value, how]) => <div key={label} title={how}><dt>{label}</dt><dd>{value}</dd></div>)}
    </dl>
  )
}

/** Counts straight from the records; a dash where the run has no record of that kind. */
export function Ledger({ run, at }: { run: Run; at: number }) {
  const l = run.ledger(at), m = run.metrics(at)
  const rows: [string, string, string?, string?][] = [
    ['steps', num(m?.steps, n => n.toLocaleString('en-US')), undefined, 'usage records (model requests)'],
    ['actions', run.of('program_actions').length ? num(m?.actions, n => n.toLocaleString('en-US')) : DASH, undefined, 'Σ program_actions.count (counted by the program)'],
    ['failed calls', l.failed.toLocaleString('en-US'), undefined, 'tool_call ok=false, health checks excluded'],
    ['scripts run', l.scripts.toLocaleString('en-US'), undefined, 'run_script calls by the agent'],
    ['tokens in', big(l.input), undefined, 'Σ usage.input_tokens'],
    ['cached in', big(l.cached), undefined, 'Σ usage.cached_input_tokens'],
    ['tokens out', big(l.output), undefined, 'Σ usage.output_tokens'],
    ['tasks', l.tasks.toLocaleString('en-US'), undefined, 'task_started records'],
    ['deaths', run.hasGrader ? String(l.deaths) : DASH, l.deaths ? 'bad' : undefined, l.lastDeath ?? 'marker kind agent_died'],
    ['stalls', String(l.stalls), undefined, 'task_stalled records'],
    ['handoffs', String(l.handoffs), undefined, 'marker kind context_handoff_done'],
    ['nudges', String(l.nudges), undefined, 'marker kinds idle_nudge, supervisor_nudge'],
  ]
  return (
    <dl className="ledger" aria-label="Counts">
      {rows.map(([k, v, tone, how]) => <div key={k} title={how}><dt>{k}</dt><dd className={tone}>{v}</dd></div>)}
    </dl>
  )
}

const STATE: Record<string, string> = {
  thinking: 'thinking', acting: 'acting', waiting: 'waiting', compacting: 'compacting', host_down: 'host down',
  input_blocked: 'input blocked', backing_off: 'backing off', paused: 'paused', ended: 'ended',
}

/** What the agent is doing now: its state, the background task running (if any), and its newest words. */
export function Now({ run, at }: { run: Run; at: number }) {
  const st = run.agentState(at)
  const tasks = run.openTasks(at)
  const t = tasks[0]
  const msg = run.message(at)
  return (
    <section className="now" aria-label="Now">
      <div className="nowhead">
        <span className={`state s-${st.state}`}><i />{STATE[st.state] ?? st.state}<em>{span(at - st.since)}</em></span>
        {t && <span className="task" title={`${t.task} started ${hms(t.t - run.t0)}${t.hands ? ' · holds the hands' : ''}`}>
          <b>{t.task}</b><span className="tn">{t.name}</span>{t.hands && <span className="hands">hands</span>}<em>{span(at - t.t)}</em>
          {tasks.length > 1 && <span className="more">+{tasks.length - 1}</span>}
        </span>}
      </div>
      {msg ? <p className="msg"><span>{msg.text}</span></p> : <p className="msg none">{DASH}</p>}
      {msg && <div className="msgt">{span(at - msg.t)} ago</div>}
    </section>
  )
}

/** The agent's own recent calls, newest first, repeats folded. */
export function Actions({ run, at }: { run: Run; at: number }) {
  const lines = run.actions(at, 9)
  return (
    <section className="actions" aria-label="Recent actions">
      <ol>
        {lines.map(l => (
          <li key={l.t + l.label} className={l.ok ? '' : 'fail'}>
            <span className="ts">{hms(l.t - run.t0)}</span>
            <span className="al">{l.label}</span>
            {l.count > 1 && <span className="ct">×{l.count}</span>}
          </li>
        ))}
        {!lines.length && <li className="none">{DASH}</li>}
      </ol>
    </section>
  )
}

/** Extensions and kept scripts: each version on the run's time axis, calls, and the last call. A version written in
 *  the last ten minutes of run time is marked. Clicking a hook opens its source and diffs. */
export function Kits({ run, at, onOpen }: { run: Run; at: number; onOpen: (hook: string) => void }) {
  const kits = run.kits(at)
  const dur = Math.max(1, at - run.t0)
  const ext = kits.filter(k => k.kind === 'extension'), scripts = kits.filter(k => k.kind === 'script')
  const hooks = ext.reduce((n, k) => n + k.hooks.length, 0)
  return (
    <section className="kits" aria-label="Tools the agent built">
      <h2>tools <span>{hooks} hooks · {ext.length} ext · {scripts.length} scripts</span></h2>
      {!kits.length && <p className="none">{DASH}</p>}
      {kits.map(k => <KitRow key={k.kind + k.name} kit={k} run={run} at={at} dur={dur} onOpen={onOpen} />)}
    </section>
  )
}

function KitRow({ kit, run, at, dur, onOpen }: { kit: Kit; run: Run; at: number; dur: number; onOpen: (hook: string) => void }) {
  const last = kit.versions[kit.versions.length - 1]
  const fresh = last && at - last.t < RECENT && kit.versions.length > 1
  const W = 110
  const solo = kit.hooks.length === 1 ? kit.hooks[0] : undefined
  return (
    <div className={`kit ${fresh ? 'fresh' : ''}`}>
      <div className="kh" title={kit.versions.map(v => `v${v.version} ${hms(v.t - run.t0)}`).join(' → ')}>
        {solo ? <button className="kn" onClick={() => onOpen(solo.name)}>{solo.name}</button> : <span className="kn">{kit.name}</span>}
        <span className="kv">{last ? `v${last.version}` : DASH}</span>
        <svg width={W} height={12} aria-hidden="true" className="vticks">
          <line x1={0} x2={W} y1={6} y2={6} />
          {kit.versions.map(v => <rect key={v.version} x={Math.min(W - 2, ((v.t - run.t0) / dur) * W) - 1} y={1} width={2} height={10} />)}
          {kit.restored.map(t => <circle key={t} cx={((t - run.t0) / dur) * W} cy={6} r={2.5} />)}
        </svg>
        <span className="kc" title="calls">{kit.calls.toLocaleString('en-US')}</span>
        <span className="kl" title="last call">{kit.lastCall ? span(at - kit.lastCall) : DASH}</span>
      </div>
      {kit.hooks.length > 1 && (
        <ul className="hk">
          {kit.hooks.map(h => (
            <li key={h.name}>
              <button onClick={() => onOpen(h.name)} title={`${h.name}: ${h.calls} calls${h.lastCall ? `, last ${hms(h.lastCall - run.t0)}` : ''}`}>
                <span>{h.name}</span><em>{h.calls.toLocaleString('en-US')}</em>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

type Notice = { key: string; t: number; kind: 'new' | 'rewrite' | 'break' | 'repair' | 'death' | 'milestone'; title: string; body: string }

function recentNotices(run: Run, at: number): Notice[] {
  const out: Notice[] = []
  const seenExt = new Set<string>()
  for (const r of run.upTo('hook_loaded', at)) if (at - r.t < RECENT) {
    const key = `${r.extension || r.hook}v${r.version}`
    if (seenExt.has(key)) continue  // one notice per extension version, not one per hook it provides
    seenExt.add(key)
    out.push({ key: `L${key}`, t: r.t, kind: r.version > 1 ? 'rewrite' : 'new', title: `${r.extension || r.hook} v${r.version}`, body: r.reason ?? '' })
  }
  for (const r of run.upTo('hook_status', at)) if (r.status !== 'ok' && at - r.t < RECENT)
    out.push({ key: `S${r.t}${r.hook}`, t: r.t, kind: r.status === 'broken' ? 'break' : 'repair', title: `${r.hook} ${r.status}`, body: r.detail ?? '' })
  const names = new Map(run.milestonesDefined.map(m => [m.id, m.name]))
  for (const r of run.upTo('milestone', at)) if (at - r.t < RECENT)
    out.push({ key: `M${r.id}`, t: r.t, kind: 'milestone', title: names.get(r.id) ?? r.id, body: '' })
  for (const r of run.upTo('marker', at)) if (r.kind === 'agent_died' && at - r.t < RECENT)
    out.push({ key: `D${r.t}`, t: r.t, kind: 'death', title: r.label ?? 'died', body: '' })
  return out.sort((a, b) => a.t - b.t)
}

const NOTICE_LABEL = { new: 'new tool', rewrite: 'tool improved', break: 'tool broken', repair: 'tool repaired', death: 'death', milestone: 'milestone' }

/** Tool versions, breaks and deaths, one after another, each held for a fixed wall-clock time. */
export function Event({ run, at }: { run: Run; at: number }) {
  const [st, setSt] = useState<{ queue: Notice[]; active: Notice | null }>({ queue: [], active: null })
  const seen = useRef(new Map<string, number>())
  const lastAt = useRef(at)
  useEffect(() => {
    const back = at < lastAt.current - 1000
    lastAt.current = at
    if (back) {
      for (const [k, t] of seen.current) if (t > at) seen.current.delete(k)
      setSt(s => ({ queue: s.queue.filter(n => n.t <= at), active: s.active && s.active.t <= at ? s.active : null }))
    }
    const fresh = recentNotices(run, at).filter(n => !seen.current.has(n.key))
    if (!fresh.length) return
    fresh.forEach(n => seen.current.set(n.key, n.t))
    setSt(s => ({ ...s, queue: [...s.queue, ...fresh] }))
  }, [run, at])
  useEffect(() => {
    if (!st.active && st.queue.length) setSt({ queue: st.queue.slice(1), active: st.queue[0] })
    if (!st.active) return
    const id = setTimeout(() => setSt(s => ({ ...s, active: null })), NOTICE_MS)
    return () => clearTimeout(id)
  }, [st.active, st.queue])
  const a = st.active
  if (!a) return <div className="event idle" aria-live="polite" />
  return (
    <div className={`event ${a.kind}`} aria-live="polite">
      <span className="etag">{NOTICE_LABEL[a.kind]}{st.queue.length > 0 && <small> +{st.queue.length}</small>}</span>
      <b>{a.title}</b>
      {a.body && <span className="ebody">{a.body}</span>}
    </div>
  )
}

/** The last image shown stays up while the next one is still loading. */
function useSticky(url: string | undefined): string | undefined {
  const last = useRef<string>(undefined)
  if (url) last.current = url
  return url ?? last.current
}

/** Spectator frames stand in for a stale agent screenshot only up to the point they are themselves stale. */
const STALE_SHOT_MS = 30_000

/** The game: what the agent last saw, or, live and when its last look is old, the spectator frame (the host window,
 *  which the agent never gets) with the agent's last look inset. */
export function Screen({ run, at, now, following, spectator, imageUrl }: {
  run: Run; at: number; now: number; following: boolean; spectator: Spectator | null; imageUrl: (r: TRec) => string | undefined
}) {
  const shot = run.screenshot(at)
  const shotUrl = useSticky(shot ? imageUrl(shot) : undefined)
  const age = shot ? (following ? now : at) - shot.t : Infinity
  const spec = following && spectator && age > STALE_SHOT_MS ? spectator : null
  const specUrl = useSticky(spectator?.url)
  return (
    <figure className="screen">
      {spec ? <img src={specUrl} alt="Host window, spectator view" />
        : shot && shotUrl ? <img src={shotUrl} alt="Agent's last screenshot" />
        : <div className="empty">{DASH}</div>}
      {spec ? <figcaption>spectator · {span(now - spec.t)}</figcaption>
        : shot && <figcaption>agent view · {span(age)}</figcaption>}
      {spec && shotUrl && (
        <div className="inset">
          <img src={shotUrl} alt="Agent's last screenshot" />
          <span>agent view · {span(age)}</span>
        </div>
      )}
    </figure>
  )
}
