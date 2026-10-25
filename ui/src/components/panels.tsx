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

/** Where the view is relative to the run (position, not the agent's state: that is in Now). */
export function LiveBadge({ run, at, now, onJump }: { run: Run; at: number; now: number; onJump: () => void }) {
  const behind = run.t1 - at
  const live = run.isLive(now)
  if (live && behind <= 2000) return <span className="pill live"><i className="livedot" />live</span>
  if (behind > 2000) return (
    <span className="pill behind">replay −{span(behind)}
      <button className="jump" onClick={onJump}>{live ? 'live' : 'latest'}</button>
    </span>
  )
  return run.ended ? <span className="pill">replay</span> : null
}

/** The current subgoal (`goal` record from set_goal), what it serves and how long ago it was set, so a stale goal shows;
 *  the run's own goal line until one is set. Then milestones reached of those announced, one cell each, the next named. */
export function Goal({ run, at }: { run: Run; at: number }) {
  const g = run.latest('goal', at)
  const ms = run.milestones(at)
  const done = ms.filter(m => m.t !== undefined).length
  const next = ms.find(m => m.t === undefined)
  return (
    <section className="box goal" aria-label="Current goal">
      <div className="gtop">
        <span className="gl">{g ? 'goal' : 'run goal'}</span>
        {g && <span className="gage">set {span(at - g.t)} ago</span>}
      </div>
      <h1>{g?.text ?? (run.goalTitle || DASH)}</h1>
      {g?.serves && <p className="serves">for {g.serves}</p>}
      <div className="progress">
        <div className="pn"><b>{ms.length ? done : DASH}</b>{ms.length > 0 && <span>/ {ms.length}</span>}<em>milestones</em></div>
        <ol className="cells">
          {ms.map(m => <li key={m.id} className={m.t !== undefined ? (at - m.t < RECENT ? 'on recent' : 'on') : m === next ? 'next' : ''} title={m.t !== undefined ? `${m.name} · ${hms(m.t - run.t0)}` : m.name} />)}
        </ol>
        <p className="next">{next ? <>next <span>{next.name}</span></> : ms.length ? 'all reached' : ''}</p>
      </div>
    </section>
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
    ['cw tokens', num(m?.cw), 'diag', 'Σ usage input + output + 0.1 × cached input'],
    ['raw tok / hour', num(m?.tokPerHour), 'diag', 'diagnostic: Σ usage total_tokens growth ÷ wall hours (time_use.py)'],
    ['raw tok / step', num(m?.tokPerStep), 'diag', 'diagnostic: Σ usage total_tokens growth ÷ usage records'],
    ['context / req', num(m?.context), 'diag', 'diagnostic: newest usage input_tokens + cached_input_tokens'],
  ]
  return (
    <dl className="box ledger" aria-label="Counts">
      {rows.map(([k, v, tone, how]) => <div key={k} title={how} className={tone === 'diag' ? 'diag' : undefined}><dt>{k}</dt><dd className={tone === 'diag' ? undefined : tone}>{v}</dd></div>)}
    </dl>
  )
}

const STATE: Record<string, string> = {
  thinking: 'thinking', acting: 'acting', waiting: 'waiting', compacting: 'compacting', host_down: 'host down',
  input_blocked: 'input blocked', backing_off: 'backing off', paused: 'paused', ended: 'ended',
}

/** What the agent is doing now, as one state: the run ended, the run stopped writing (when following the newest moment of a
 *  run with no records for two minutes, its last agent_state is not trusted), or the agent's state. Then the background task
 *  running, and the agent's newest words. */
export function Now({ run, at, now }: { run: Run; at: number; now: number }) {
  const atEnd = at >= run.t1 - 2000
  const stale = atEnd && !run.ended && !run.isLive(now)
  const st = run.agentState(at)
  const [label, cls, age] = atEnd && run.ended ? ['ended', 'ended', at - run.t1]
    : stale ? ['no records', 'stale', now - run.t1]
    : [STATE[st.state] ?? st.state, st.state, at - st.since]
  const tasks = stale ? [] : run.openTasks(at)
  const t = tasks[0]
  const msg = run.message(at)
  return (
    <section className="box now" aria-label="Now">
      <div className="nowhead">
        <span className={`state s-${cls}`}><i />{label}<em>{span(age)}</em></span>
        {t && <span className="task" title={`${t.task} started ${hms(t.t - run.t0)}${t.hands ? ' · holds the hands' : ''}`}>
          <b>{t.task}</b><span className="tn">{t.name}</span>{t.hands && <span className="hands">hands</span>}<em>{span(at - t.t)}</em>
          {tasks.length > 1 && <span className="more">+{tasks.length - 1}</span>}
        </span>}
      </div>
      {msg ? <p className="msg"><span>{msg.text}</span></p> : <p className="msg none">{DASH}</p>}
      {msg && <div className="msgt">said {span(at - msg.t)} ago</div>}
    </section>
  )
}

/** The agent's own recent calls, newest first, repeats folded. */
export function Actions({ run, at }: { run: Run; at: number }) {
  const lines = run.actions(at, 9)
  return (
    <section className="box actions" aria-label="Recent actions">
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

/** Extensions and kept scripts: the current version and when it changed, the versions before it with their run times,
 *  calls and the last call. A version written in the last ten minutes of run time is marked. A hook opens its source. */
export function Kits({ run, at, onOpen }: { run: Run; at: number; onOpen: (hook: string) => void }) {
  const kits = run.kits(at)
  const ext = kits.filter(k => k.kind === 'extension'), scripts = kits.filter(k => k.kind === 'script')
  const hooks = ext.reduce((n, k) => n + k.hooks.length, 0)
  return (
    <section className="box kits" aria-label="Tools the agent built">
      <h2>tools <span>{hooks} hooks · {ext.length} ext · {scripts.length} scripts</span></h2>
      {!kits.length && <p className="none">{DASH}</p>}
      {[...ext, ...scripts.slice(0, 3)].map(k => <KitRow key={k.kind + k.name} kit={k} run={run} at={at} onOpen={onOpen} />)}
      {scripts.length > 3 && <p className="kmore">+{scripts.length - 3} scripts</p>}
    </section>
  )
}

function KitRow({ kit, run, at, onOpen }: { kit: Kit; run: Run; at: number; onOpen: (hook: string) => void }) {
  const last = kit.versions[kit.versions.length - 1]
  const fresh = last && at - last.t < RECENT && kit.versions.length > 1
  const solo = kit.hooks.length === 1 ? kit.hooks[0] : undefined
  const earlier = kit.versions.slice(-4, -1).reverse()
  return (
    <div className={`kit ${kit.kind} ${fresh ? 'fresh' : ''}`}>
      <div className="kh">
        {solo ? <button className="kn" onClick={() => onOpen(solo.name)}>{solo.name}</button> : <span className="kn">{kit.name}</span>}
        <span className="kv">{last ? <><b>v{last.version}</b> {span(at - last.t)} ago{fresh && <i>new</i>}</> : <span className="dim">{DASH}</span>}</span>
        <span className="kc" title="calls">{kit.calls.toLocaleString('en-US')}</span>
        <span className="kl" title="last call">{kit.lastCall ? span(at - kit.lastCall) : DASH}</span>
      </div>
      {earlier.length > 0 && (
        <div className="khist" title={kit.versions.map(v => `v${v.version} ${hms(v.t - run.t0)}`).join(' → ')}>
          {earlier.map(v => <span key={v.version}>v{v.version} <em>{hms(v.t - run.t0)}</em></span>)}
          {kit.versions.length > 4 && <span className="dim">… v{kit.versions[0].version} <em>{hms(kit.versions[0].t - run.t0)}</em></span>}
        </div>
      )}
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
      {a.body && (a.kind === 'break' || a.kind === 'repair') && <span className="ebody">{a.body}</span>}
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
