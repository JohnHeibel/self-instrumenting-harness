import { useEffect, useRef, useState } from 'react'
import type { FeedItem, HookView, Milestone, Run, Tone, TRec } from '../data/model'
import type { Spectator } from '../data/source'
import { big, hms, span } from '../util'

/** Wall-clock hold times, so nothing flashes by at timelapse speed. */
const NOTICE_MS = 10_000
const READ_HOLD_MS = 3_000
/** An event counts as "just happened" for this long in run time. */
const RECENT = 10 * 60_000

export function Header({ run, at, now, onJump }: { run: Run; at: number; now: number; onJump: () => void }) {
  const c = run.counts(at)
  return (
    <header className="head">
      <div className="brand">Sisyphus<em lang="grc">Σίσυφος</em><span>{run.title}</span></div>
      <div className="badges">
        <LiveBadge run={run} at={at} now={now} onJump={onJump} />
        <FairPlay run={run} at={at} />
      </div>
      <dl className="stats">
        <div><dt>Tool calls</dt><dd>{c.calls.toLocaleString()}</dd></div>
        <div className="fail"><dt>Failed</dt><dd>{c.failed}</dd></div>
        <div><dt>Tokens</dt><dd>{big(run.tokens(at))}</dd></div>
        <div><dt>Hooks built</dt><dd>{run.toolsBuilt(at)}</dd></div>
      </dl>
      <div className="clock" aria-label="Run time">{hms(at - run.t0)}</div>
    </header>
  )
}

/** Where the view is relative to the run: following it live, behind it, idle, or replaying a finished run. */
export function LiveBadge({ run, at, now, onJump }: { run: Run; at: number; now: number; onJump: () => void }) {
  const behind = run.t1 - at
  if (run.ended) return <span className="pill" title="This run has ended; you are replaying it"><i className="dot" />Run ended · replay</span>
  const live = run.isLive(now)
  if (!live) return (
    <span className="pill idle" title="No run_ended record, but nothing has been written for two minutes">
      <i className="dot" />Idle · last record {span(now - run.t1)} ago
      {behind > 2000 && <button className="jump" onClick={onJump}>Jump to latest</button>}
    </span>
  )
  if (behind <= 2000) return <span className="pill live" title="Following the newest records as they are written"><i className="livedot" />Live</span>
  return (
    <span className="pill behind" title="Replaying earlier in a run that is still going">
      <i className="dot" />Behind by {span(behind)}
      <button className="jump" onClick={onJump}>Jump to live</button>
    </span>
  )
}

const signalName = (s: string) => s.replace(/_/g, ' ')
const show = (v: unknown) => (v == null || v === '' ? '' : typeof v === 'string' ? v : JSON.stringify(v))

/** Evidence of fair play: calm while no signal has fired, porphyry once one has. Details on hover or focus. */
export function FairPlay({ run, at }: { run: Run; at: number }) {
  const f = run.integrity(at)
  const label = { fair: 'Played fair', broken: 'Fair play broken', unverified: 'Fair play unverified', unmonitored: 'Fair play not monitored' }[f.status]
  const t0 = run.t0
  return (
    <span className={`pill fair ${f.status}`} tabIndex={0} aria-label={label}>
      <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
        {f.status === 'broken'
          ? <path d="M3 3 L13 13 M13 3 L3 13" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
          : <path d="M3 8.5 L6.5 12 L13 4.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />}
      </svg>
      {label}
      <span className="pop" role="tooltip">
        <b>Fair-play checks</b>
        {f.signals.length === 0 && <span className="row dim">{f.status === 'unmonitored' ? 'The run has no grader, so nothing is checked.' : 'No signal has fired. The grader watches the host for outside help (console commands, cheat modes) and found none.'}</span>}
        {f.signals.map(s => (
          <span key={s.signal} className={`row ${s.active ? (s.signal === 'grader_unavailable' ? 'warn' : 'bad') : 'ok'}`}>
            <i />
            <span><em>{signalName(s.signal)}</em> · {s.active ? `on since ${hms(s.since - t0)}` : s.everActive ? `was on, off since ${hms(s.since - t0)}` : 'off'}
              {show(s.detail) && <small>{show(s.detail)}</small>}
              {show(s.evidence) && <small className="ev">{show(s.evidence)}</small>}</span>
          </span>
        ))}
        {f.grader && <span className={`row ${f.grader.ok ? 'ok' : 'warn'}`}><i /><span>
          <em>grader</em> · {f.grader.ok ? 'can reach the host' : `cannot reach the host since ${hms(f.grader.since - t0)}`}
          {f.grader.detail && <small>{f.grader.detail}</small>}</span></span>}
      </span>
    </span>
  )
}

export function Milestones({ items }: { items: Milestone[] }) {
  const current = items.findIndex(m => m.t === undefined)
  return (
    <ol className={`strip ${items.length > 18 ? 'xs' : items.length > 11 ? 'sm' : ''}`} aria-label="Milestones">
      {items.map((m, i) => (
        <li key={m.id} className={m.t !== undefined ? 'done' : i === current ? 'current' : ''} title={`${m.name}: ${m.evidence ?? 'not reached yet'}`}>
          <span>{m.name}</span>
        </li>
      ))}
    </ol>
  )
}

export function Goal({ run, at, opening }: { run: Run; at: number; opening: boolean }) {
  const g = run.subgoal(at)
  return (
    <section className="goal">
      <div className="kicker">{opening ? 'The mission' : 'Current goal'}</div>
      <h1>{(opening ? run.goal : g?.text ?? run.goal).replace(/^#+\s*/, '')}</h1>
      <p className="serves">
        {opening
          ? `One agent, ${span(run.t1 - run.t0)}, no human help. It builds its own tools inside the host as it goes.`
          : g?.serves ? (g.serves === 'the goal' ? 'This is the end goal.' : `Why: ${g.serves}`) : ''}
      </p>
    </section>
  )
}

const STATE_HUE: Record<string, string> = {
  thinking: 'var(--lapis)', acting: 'var(--verde)', waiting: 'var(--ochre)', compacting: 'var(--ochre)',
  host_down: 'var(--porphyry)', backing_off: 'var(--porphyry)', paused: 'var(--muted)', ended: 'var(--muted)',
}

/** State plus the newest reasoning. Typed out only when it arrives during playback; after a seek it is shown whole. */
export function Mind({ run, at, playing, opening }: { run: Run; at: number; playing: boolean; opening: boolean }) {
  const st = run.agentState(at)
  const r = run.reasoning(at)
  const key = r ? r.t : 0
  const live = useRef({ playing, at })
  const prev = useRef({ key, at })
  const [typing, setTyping] = useState<{ key: number; start: number } | null>(null)
  const [, tick] = useState(0)
  useEffect(() => { live.current = { playing, at } })
  useEffect(() => {
    const p = prev.current
    prev.current = { key, at: live.current.at }
    const arrived = live.current.playing && key > p.key && live.current.at - p.at < 120_000
    setTyping(arrived ? { key, start: performance.now() } : null)
  }, [key])
  const active = typing && typing.key === key && r ? typing : null
  useEffect(() => {
    if (!active || !r) return
    const id = setInterval(() => {
      tick(n => n + 1)
      if ((performance.now() - active.start) * 0.07 >= r.text.length) { clearInterval(id); setTyping(null) }
    }, 40)
    return () => clearInterval(id)
  }, [active, r])
  const n = active && r ? Math.min(r.text.length, Math.floor((performance.now() - active.start) * 0.07)) : r?.text.length ?? 0
  return (
    <section className="mind">
      <div className="kicker">
        <span>The agent's reasoning</span>
        <span className="state"><i style={{ background: STATE_HUE[st.state] ?? 'var(--muted)' }} />{st.label} · {span(at - st.since)}</span>
      </div>
      {r && !opening ? <>
        <div className="mtitle">{r.title}</div>
        <p className="thought">{r.text.slice(0, n)}{n < r.text.length && <i className="caret" />}</p>
      </> : <p className="thought dim">{opening ? 'Waiting for the agent’s first thought.' : 'No reasoning summary yet.'}</p>}
      <Usage run={run} at={at} />
    </section>
  )
}

/** The newest model request: what it read and wrote, and how full its context was. */
function Usage({ run, at }: { run: Run; at: number }) {
  const u = run.usage(at)
  if (!u) return null
  const cached = u.input ? Math.round((u.cached / u.input) * 100) : 0
  const fill = u.contextWindow ? Math.min(100, Math.round((u.input / u.contextWindow) * 100)) : undefined
  return (
    <div className="usage" title="From the runner's usage record after each model request">
      <span>request {u.requests.toLocaleString()}</span>
      <span>{big(u.input)} in{u.input ? ` · ${cached}% cached` : ''}</span>
      <span>{big(u.output + u.reasoning)} out</span>
      {fill !== undefined && <span className="ctx">context {fill}%<i><b style={{ width: `${fill}%` }} /></i></span>}
    </div>
  )
}

type Notice = { key: string; t: number; kind: 'new' | 'rewrite' | 'break' | 'repair' | 'restart' | 'interrupt' | 'violation'; title: string; body: string }

function recentNotices(run: Run, at: number): Notice[] {
  const out: Notice[] = []
  for (const r of run.upTo('hook_loaded', at)) if (at - r.t < RECENT)
    out.push({ key: `L${r.t}${r.hook}`, t: r.t, kind: r.version > 1 ? 'rewrite' : 'new',
      title: `The agent ${r.version > 1 ? 'rewrote' : 'wrote'} ${r.hook} v${r.version}`, body: r.reason ?? '' })
  for (const r of run.upTo('hook_status', at)) if (r.status !== 'ok' && at - r.t < RECENT)
    out.push({ key: `S${r.t}${r.hook}`, t: r.t, kind: r.status === 'broken' ? 'break' : 'repair',
      title: `${r.hook} ${r.status === 'broken' ? 'broke' : 'was repaired'}`, body: r.detail ?? '' })
  for (const m of run.markers) if (m.t <= at && at - m.t < RECENT && (m.kind === 'restart' || m.kind === 'interrupt' || m.kind === 'violation'))
    out.push({ key: `M${m.t}${m.label}`, t: m.t, kind: m.kind, title: m.kind === 'violation' ? `Fair play: ${m.label}` : m.label, body: m.detail ?? '' })
  return out.sort((a, b) => a.t - b.t)
}

const NOTICE_LABEL = { new: 'New hook', rewrite: 'Rewrite', break: 'Break', repair: 'Repair', restart: 'Restart', interrupt: 'Interrupted', violation: 'Fair play' }
const PLAIN_BODY = ['break', 'repair', 'restart', 'interrupt', 'violation']

/** Hook events, one after another, each held for a fixed wall-clock time. A break never hides the rewrite that follows it. */
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
  return (
    <section className={`event ${a ? a.kind : 'idle'}`} aria-live="polite">
      {a && <>
        <div className="etag">{NOTICE_LABEL[a.kind]}{st.queue.length > 0 && <small> · {st.queue.length} more</small>}</div>
        <div className="etext"><b>{a.title}</b>{a.body && <span> {PLAIN_BODY.includes(a.kind) ? a.body : `“${a.body}”`}</span>}</div>
      </>}
    </section>
  )
}

/** The last image shown stays up while the next one is still loading. */
function useSticky(url: string | undefined): string | undefined {
  const last = useRef<string>(undefined)
  if (url) last.current = url
  return url ?? last.current
}

/** Spectator frames older than this stand in for a stale agent screenshot only up to the point they are themselves stale. */
const STALE_SHOT_MS = 30_000

/** What the agent last saw, with its age. When it hasn't looked for a while and the view is live, the spectator
 *  frame (the host window, which the agent never gets) shows the host as it is now, with the agent's last look inset.
 *  Before the first screenshot, an engraved column holds the place. */
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
      {spec ? <img src={specUrl} alt="The host window as a spectator sees it" /> : shot && shotUrl ? <img src={shotUrl} alt="What the agent last saw" /> : (
        <div className="empty">
          <svg viewBox="0 0 120 200" width="150" aria-hidden="true">
            <g fill="none" stroke="currentColor" strokeWidth="1" strokeLinecap="round">
              <rect x="22" y="14" width="76" height="9" /><path d="M30 23 Q60 34 90 23" /><rect x="34" y="34" width="52" height="4" />
              <path d="M38 38 L42 166 M86 38 L82 166" />
              {[48, 56, 64, 72].map(x => <path key={x} d={`M${x - 2} 40 L${x + (x - 60) * 0.05} 164`} />)}
              <rect x="30" y="166" width="60" height="7" /><rect x="22" y="173" width="76" height="9" /><rect x="14" y="182" width="92" height="9" />
            </g>
          </svg>
          <span>{shot ? 'Loading the screenshot…' : 'Waiting for the agent to look at the host'}</span>
        </div>
      )}
      {spec
        ? <figcaption className="spectator">Spectator view · the host window, {span(now - spec.t)} old · the agent can't see this</figcaption>
        : shot && <figcaption>Last screenshot it looked at · {span(age)} ago</figcaption>}
      {spec && shotUrl && (
        <div className="inset">
          <img src={shotUrl} alt="What the agent last saw" />
          <span>What the agent last saw · {span(age)} ago</span>
        </div>
      )}
    </figure>
  )
}

/** The latest hook observation, held at least a few wall-clock seconds before it is replaced. */
export function ReadLine({ run, at }: { run: Run; at: number }) {
  const latest = run.lastHookCall(at)
  const [shown, setShown] = useState<TRec | undefined>(latest)
  const lastSwap = useRef(0)
  const want = useRef(latest)
  useEffect(() => { want.current = latest })
  useEffect(() => {
    const wait = READ_HOLD_MS - (performance.now() - lastSwap.current)
    const swap = () => { lastSwap.current = performance.now(); setShown(want.current) }
    if (wait <= 0) { swap(); return }
    const id = setTimeout(swap, wait)
    return () => clearTimeout(id)
  }, [latest])
  return (
    <div className="read">
      <div className="kicker">Latest read through a hook</div>
      {shown ? <>
        <div className="rbody">{shown.summary ?? 'ok'}</div>
        <div className="rmeta">{shown.tool}</div>
      </> : <div className="rbody dim">None yet. Hooks start empty.</div>}
    </div>
  )
}

/** Hooks as a tree: a hook built on others sits under the first one it depends on. */
export function HookLibrary({ hooks, onOpen }: { hooks: HookView[]; onOpen: (name: string) => void }) {
  const byName = new Map(hooks.map(h => [h.name, h]))
  const parentOf = (h: HookView) => h.versions[h.versions.length - 1].dependsOn.find(d => byName.has(d))
  const children = new Map<string, HookView[]>()
  const roots: HookView[] = []
  for (const h of hooks) {
    const p = parentOf(h)
    if (p) children.set(p, [...(children.get(p) ?? []), h]); else roots.push(h)
  }
  const rows: { h: HookView; depth: number }[] = []
  const walk = (h: HookView, depth: number) => { rows.push({ h, depth }); for (const c of children.get(h.name) ?? []) walk(c, depth + 1) }
  roots.forEach(h => walk(h, 0))
  return (
    <section className="hooks">
      <h2>Hooks the agent built <span>{hooks.length}</span></h2>
      <ul>
        {rows.map(({ h, depth }) => {
          const v = h.versions[h.versions.length - 1]
          return (
            <li key={h.name} style={{ paddingLeft: depth * 16 }}>
              <button onClick={() => onOpen(h.name)} title={`Open ${h.name}: source and version history${h.extension ? ` · from extension ${h.extension}` : ''}`}>
                <span className="name">{depth > 0 ? '└ ' : ''}{h.name}</span>
                {h.status === 'broken' && <span className="st broken">broken</span>}
                {h.status === 'repaired' && <span className="st repaired">repaired</span>}
                {h.isNew && h.status === 'ok' && <span className="st new">new</span>}
                <span className="meta">v{v.version} · {h.calls}</span>
              </button>
            </li>
          )
        })}
        {!rows.length && <li className="none">None yet.</li>}
      </ul>
    </section>
  )
}

const TAG: Record<FeedItem['kind'], [string, string]> = {
  hook: ['hook', 'var(--lapis)'], screenshot: ['look', 'var(--muted-hi)'], input: ['input', 'var(--muted-hi)'],
  docs: ['docs', 'var(--muted-hi)'], extension: ['build', 'var(--lapis)'], meta: ['meta', 'var(--muted)'],
  say: ['says', 'var(--text)'], milestone: ['done', 'var(--ochre)'], marker: ['event', 'var(--porphyry)'],
  goal: ['goal', 'var(--muted-hi)'], fail: ['failed', 'var(--porphyry)'], status: ['hook', 'var(--porphyry)'],
  host: ['cmd', 'var(--ochre)'], grader: ['grader', 'var(--muted-hi)'], fair: ['fair', 'var(--porphyry)'],
  check: ['check', 'var(--muted)'], turn: ['turn', 'var(--ochre)'],
}
const TONE: Record<Tone, string> = { good: 'var(--verde)', bad: 'var(--porphyry)', warn: 'var(--ochre)', info: 'var(--lapis)', muted: 'var(--muted)' }

/** Plain-language feed. Failures are marked in the line, and the count and latest one are pinned above it. */
export function Feed({ run, at, items }: { run: Run; at: number; items: FeedItem[] }) {
  const t0 = run.t0
  const calls = run.upTo('tool_call', at)
  let failed = 0, last: TRec | undefined
  for (const c of calls) if (!c.ok && c.via !== 'health_check') { failed++; last = c }
  return (
    <section className="feed">
      <h2>What it is doing</h2>
      <div className={`pin ${failed ? 'has' : ''}`}>
        <span className="n">{failed}</span>
        <span className="pl">failed tool call{failed === 1 ? '' : 's'}</span>
        {last && <span className="pm" title={`${last.tool}: ${last.error}`}>latest: {last.tool}: {last.error}</span>}
      </div>
      <div className="lines">
        {items.map((it, i) => {
          const [label, color] = TAG[it.kind]
          return (
            <div key={i} className={`line ${it.kind}`} title={it.text}>
              <span className="ts">{hms(it.t - t0)}</span>
              <span className="tag" style={{ color: it.tone ? TONE[it.tone] : color }}>{label}</span>
              <span className="msg">{it.text}{it.count > 1 && <em> ×{it.count}</em>}</span>
            </div>
          )
        })}
      </div>
    </section>
  )
}
