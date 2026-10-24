import { useEffect, useRef, useState } from 'react'
import type { FeedItem, HookView, Milestone, Run, TRec } from '../data/model'
import { big, hms, span } from '../util'

/** Wall-clock hold times, so nothing flashes by at timelapse speed. */
const NOTICE_MS = 10_000
const READ_HOLD_MS = 3_000
/** An event counts as "just happened" for this long in run time. */
const RECENT = 10 * 60_000

export function Header({ run, at }: { run: Run; at: number }) {
  const c = run.counts(at)
  return (
    <header className="head">
      <div className="brand">Sisyphus<em lang="grc">Σίσυφος</em><span>{run.title}</span></div>
      {run.live && <span className="pill live" title="New records appear as the run writes them">
        <i className="livedot" />{at >= run.t1 - 2000 ? 'Live' : 'Live run · behind'}</span>}
      <dl className="stats">
        <div><dt>Tool calls</dt><dd>{c.calls.toLocaleString()}</dd></div>
        <div className="fail"><dt>Failed</dt><dd>{c.failed}</dd></div>
        <div><dt>Tokens</dt><dd>{big(run.tokens(at))}</dd></div>
        <div><dt>Hooks built</dt><dd>{run.hooks(at).length}</dd></div>
      </dl>
      <div className="clock" aria-label="Run time">{hms(at - run.t0)}</div>
    </header>
  )
}

export function Milestones({ items }: { items: Milestone[] }) {
  const current = items.findIndex(m => m.t === undefined)
  return (
    <ol className="strip" aria-label="Milestones">
      {items.map((m, i) => (
        <li key={m.id} className={m.t !== undefined ? 'done' : i === current ? 'current' : ''} title={m.evidence ?? 'not reached yet'}>
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
      <h1>{opening ? run.goal : g?.text ?? run.goal}</h1>
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
    </section>
  )
}

type Notice = { key: string; t: number; kind: 'new' | 'rewrite' | 'break' | 'repair'; title: string; body: string }

function recentNotices(run: Run, at: number): Notice[] {
  const out: Notice[] = []
  for (const r of run.upTo('hook_loaded', at)) if (at - r.t < RECENT)
    out.push({ key: `L${r.t}${r.hook}`, t: r.t, kind: r.version > 1 ? 'rewrite' : 'new',
      title: `The agent ${r.version > 1 ? 'rewrote' : 'wrote'} ${r.hook} v${r.version}`, body: r.reason ?? '' })
  for (const r of run.upTo('hook_status', at)) if (r.status !== 'ok' && at - r.t < RECENT)
    out.push({ key: `S${r.t}${r.hook}`, t: r.t, kind: r.status === 'broken' ? 'break' : 'repair',
      title: `${r.hook} ${r.status === 'broken' ? 'broke' : 'was repaired'}`, body: r.detail ?? '' })
  return out.sort((a, b) => a.t - b.t)
}

const NOTICE_LABEL = { new: 'New hook', rewrite: 'Rewrite', break: 'Break', repair: 'Repair' }

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
        <div className="etext"><b>{a.title}</b>{a.body && <span> {a.kind === 'break' || a.kind === 'repair' ? a.body : `“${a.body}”`}</span>}</div>
      </>}
    </section>
  )
}

/** The newest screenshot with its age. Before the first one, an engraved column holds the place. */
export function Screen({ run, at, artifactUrl }: { run: Run; at: number; artifactUrl: (p: string) => string }) {
  const shot = run.screenshot(at)
  return (
    <figure className="screen">
      {shot ? <img src={artifactUrl(shot.artifact)} alt="What the agent last saw" /> : (
        <div className="empty">
          <svg viewBox="0 0 120 200" width="150" aria-hidden="true">
            <g fill="none" stroke="currentColor" strokeWidth="1" strokeLinecap="round">
              <rect x="22" y="14" width="76" height="9" /><path d="M30 23 Q60 34 90 23" /><rect x="34" y="34" width="52" height="4" />
              <path d="M38 38 L42 166 M86 38 L82 166" />
              {[48, 56, 64, 72].map(x => <path key={x} d={`M${x - 2} 40 L${x + (x - 60) * 0.05} 164`} />)}
              <rect x="30" y="166" width="60" height="7" /><rect x="22" y="173" width="76" height="9" /><rect x="14" y="182" width="92" height="9" />
            </g>
          </svg>
          <span>Waiting for the agent to look at the host</span>
        </div>
      )}
      {shot && <figcaption>Last screenshot it looked at · {span(at - shot.t)} ago</figcaption>}
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
              <button onClick={() => onOpen(h.name)} title={`Open ${h.name}: source and version history`}>
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
}

/** Plain-language feed. Failures are marked in the line, and the count and latest one are pinned above it. */
export function Feed({ run, at, items }: { run: Run; at: number; items: FeedItem[] }) {
  const t0 = run.t0
  const calls = run.upTo('tool_call', at)
  let failed = 0, last: TRec | undefined
  for (const c of calls) if (!c.ok) { failed++; last = c }
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
              <span className="tag" style={{ color }}>{label}</span>
              <span className="msg">{it.text}{it.count > 1 && <em> ×{it.count}</em>}</span>
            </div>
          )
        })}
      </div>
    </section>
  )
}
