import { useEffect, useRef, useState } from 'react'
import type { FeedItem, HookView, Milestone, Run } from '../data/model'
import { big, hms, span } from '../util'

export function Topbar({ run, at }: { run: Run; at: number }) {
  const c = run.counts(at)
  return (
    <div className="topbar">
      <div className="brand">Self-instrumenting harness<small>{run.title}</small></div>
      <div className="spacer" />
      <span className="pill"><b>{c.calls.toLocaleString()}</b> tool calls</span>
      <span className="pill"><b>{c.failed}</b> failed</span>
      <span className="pill"><b>{big(run.tokens(at))}</b> tokens</span>
      <span className="clock">{hms(at - run.t0)}</span>
    </div>
  )
}

export function Milestones({ items }: { items: Milestone[] }) {
  const current = items.findIndex(m => m.t === undefined)
  return (
    <div className="milestones">
      {items.map((m, i) => (
        <div key={m.id} className={`milestone ${m.t !== undefined ? 'done' : ''} ${i === current ? 'current' : ''}`}
             title={m.evidence ?? 'not reached yet'}>
          {m.t !== undefined ? '✓ ' : i === current ? '▸ ' : ''}{m.name}
        </div>
      ))}
    </div>
  )
}

export function Screen({ run, at, artifactUrl }: { run: Run; at: number; artifactUrl: (p: string) => string }) {
  const shot = run.screenshot(at)
  const read = run.lastHookCall(at)
  const hooks = run.hooks(at)
  const fresh = hooks.flatMap(h => h.versions).filter(v => at - v.t < 8 * 60_000).sort((a, b) => b.t - a.t)[0]
  const broken = hooks.find(h => h.status === 'broken')
  return (
    <div className="panel screen">
      {shot ? <img src={artifactUrl(shot.artifact)} alt="What the agent saw" /> : <div className="empty">No screenshot yet</div>}
      {read && at - read.t < 3 * 60_000 && (
        <div className="readcard">
          <div className="head"><span className="tag" style={{ color: 'var(--ch-hook)' }}>hook</span>{read.tool} · {span(at - read.t)} ago</div>
          <div className="body">{read.summary ?? 'ok'}</div>
        </div>
      )}
      {broken ? (
        <div className="toast bad">
          <div className="head">⚠ {broken.name} broke</div>
          <div className="body">{broken.statusDetail}</div>
        </div>
      ) : fresh && (
        <div className="toast">
          <div className="head">＋ The agent {fresh.version > 1 ? 'rewrote' : 'wrote'} <b>{fresh.name}</b> v{fresh.version} ({fresh.language})</div>
          <div className="body">“{fresh.reason}”</div>
        </div>
      )}
    </div>
  )
}

export function Goal({ run, at }: { run: Run; at: number }) {
  const g = run.subgoal(at)
  return (
    <div className="panel goal">
      <div className="label">Goal</div>
      <div className="text">{g?.text ?? run.goal}</div>
      {g?.serves && <div className="serves">for {g.serves}</div>}
    </div>
  )
}

/** State plus the newest reasoning summary, typed out at reading pace the first time it appears. */
export function Mind({ run, at }: { run: Run; at: number }) {
  const st = run.agentState(at)
  const r = run.reasoning(at)
  const [shown, setShown] = useState(0)
  const key = r ? r.t : 0
  const startedRef = useRef({ key: -1, start: 0 })
  useEffect(() => {
    if (!r) return
    if (startedRef.current.key !== key) startedRef.current = { key, start: performance.now() }
    const id = setInterval(() => {
      const n = Math.floor((performance.now() - startedRef.current.start) * 0.07)
      setShown(Math.min(r.text.length, n))
    }, 40)
    return () => clearInterval(id)
  }, [key, r])
  const text = r ? r.text.slice(0, shown) : ''
  return (
    <div className="panel mind">
      <div className="state"><span className={`dot ${st.state}`} />{st.label}<span className="since">{span(at - st.since)}</span></div>
      {r ? <>
        <div className="title">{r.title}</div>
        <div className="thought">{text}{shown < r.text.length && <i className="caret" />}</div>
      </> : <div className="thought" style={{ color: 'var(--text-muted)' }}>No reasoning summary yet</div>}
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
    <div className="panel hooks">
      <h3>Hook library <span className="count">· {hooks.length} built by the agent</span></h3>
      <ul>
        {rows.map(({ h, depth }) => {
          const v = h.versions[h.versions.length - 1]
          return (
            <li key={h.name} className={`hookrow ${h.isNew ? 'new' : ''}`} style={{ paddingLeft: 8 + depth * 22 }} onClick={() => onOpen(h.name)}>
              {depth > 0 && <span style={{ color: 'var(--text-muted)' }}>└</span>}
              <span className="name">{h.name}</span>
              <span className="lang">{v.language}{v.kind === 'in_host' ? ' · in game' : ''}</span>
              {h.status === 'broken' && <span className="badge broken">⚠ broken</span>}
              {h.status === 'repaired' && <span className="badge repaired">✓ repaired</span>}
              {h.isNew && h.status === 'ok' && <span className="badge new">new</span>}
              <span className="meta">v{v.version} · {h.calls} calls</span>
            </li>
          )
        })}
        {!rows.length && <li style={{ color: 'var(--text-muted)', fontSize: 15 }}>No hooks yet. The agent is working from screenshots.</li>}
      </ul>
    </div>
  )
}

const TAG: Record<FeedItem['kind'], [string, string]> = {
  hook: ['hook', 'var(--ch-hook)'], screenshot: ['look', 'var(--ch-screenshot)'], input: ['input', 'var(--ch-input)'],
  docs: ['docs', 'var(--ch-docs)'], extension: ['build', 'var(--ch-hook)'], meta: ['meta', 'var(--text-muted)'],
  say: ['says', 'var(--text-primary)'], milestone: ['✓', 'var(--good)'], marker: ['event', 'var(--warn)'],
  goal: ['goal', 'var(--text-secondary)'], fail: ['fail', 'var(--bad)'], status: ['⚠', 'var(--bad)'],
}

export function Feed({ items, t0 }: { items: FeedItem[]; t0: number }) {
  return (
    <div className="panel feed">
      <h3>Activity</h3>
      {items.map((it, i) => {
        const [label, color] = TAG[it.kind]
        return (
          <div key={i} className={`line ${it.kind}`}>
            <span className="ts">{hms(it.t - t0)}</span>
            <span className="tag" style={{ color, borderColor: color }}>{label}</span>
            <span className="msg">{it.text}</span>
            {it.count > 1 && <span className="x">×{it.count}</span>}
          </div>
        )
      })}
    </div>
  )
}
