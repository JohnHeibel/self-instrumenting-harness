/** Run model: everything the views show, derived from event-log records at any time t.
 *  Host-agnostic: it only knows the generic record types in docs/interfaces/event-log.md. */

export type Rec = {
  v: number; ts: string; run_id: string; writer: string; seq: number; type: string
  [k: string]: any  // eslint-disable-line @typescript-eslint/no-explicit-any
}
export type TRec = Rec & { t: number }  // t: ms since epoch

export type HookVersion = {
  name: string; version: number; t: number; reason: string; language: string; kind: string
  dependsOn: string[]; files: { path: string; content: string }[]
}
export type HookView = {
  name: string; versions: HookVersion[]; status: 'ok' | 'broken' | 'repaired'; statusDetail?: string
  calls: number; lastCall?: number; isNew: boolean
}
export type FeedItem = {
  t: number; kind: 'hook' | 'screenshot' | 'input' | 'docs' | 'extension' | 'meta' | 'say' | 'milestone' | 'marker' | 'goal' | 'fail' | 'status'
  text: string; count: number
}
export type Milestone = { id: string; name: string; t?: number; evidence?: string }
export type Marker = { t: number; kind: 'milestone' | 'hook' | 'break' | 'repair' | 'compaction' | 'message'; label: string }

const AGENT_STATES: Record<string, string> = {
  thinking: 'Thinking', acting: 'Acting', waiting: 'Waiting on the host', compacting: 'Compacting memory',
  host_down: 'Host is down', backing_off: 'Sleeping after a failed turn', paused: 'Paused', ended: 'Run ended',
}

export class Run {
  readonly recs: TRec[]
  readonly t0: number
  readonly t1: number
  readonly started?: Rec
  readonly markers: Marker[]
  readonly milestonesDefined: { id: string; name: string }[]
  private byType = new Map<string, TRec[]>()

  constructor(records: Rec[]) {
    this.recs = records.map(r => ({ ...r, t: Date.parse(r.ts) }))
      .sort((a, b) => a.t - b.t || a.writer.localeCompare(b.writer) || a.seq - b.seq)
    for (const r of this.recs) {
      if (!this.byType.has(r.type)) this.byType.set(r.type, [])
      this.byType.get(r.type)!.push(r)
    }
    this.t0 = this.recs[0]?.t ?? 0
    this.t1 = this.recs[this.recs.length - 1]?.t ?? 0
    this.started = this.of('run_started')[0]
    this.milestonesDefined = this.of('milestones_defined')[0]?.milestones ?? []
    this.markers = this.buildMarkers()
  }

  of(type: string): TRec[] { return this.byType.get(type) ?? [] }

  /** Records of a type with t <= at. */
  upTo(type: string, at: number): TRec[] { const a = this.of(type); return a.slice(0, upper(a, at)) }
  latest(type: string, at: number): TRec | undefined { const a = this.of(type); const i = upper(a, at); return i ? a[i - 1] : undefined }

  get title(): string {
    const s = this.started
    return s ? `${s.agent ?? 'agent'} · ${s.host ?? 'host'}` : (this.recs[0]?.run_id ?? 'run')
  }
  get goal(): string { return this.started?.goal ?? '' }

  /** Still running: no run_ended yet and a record in the last two minutes. */
  get live(): boolean {
    return !this.of('run_ended').length && Date.now() - this.t1 < 120_000
  }

  agentState(at: number): { label: string; state: string; since: number } {
    const s = this.latest('agent_state', at)
    if (!s) return { label: 'Starting', state: 'thinking', since: this.t0 }
    return { label: s.text && s.state !== 'thinking' ? s.text : AGENT_STATES[s.state] ?? s.state, state: s.state, since: s.t }
  }

  subgoal(at: number): { text: string; serves: string } | undefined {
    const g = this.latest('goal', at)
    return g && { text: g.text, serves: g.serves ?? '' }
  }

  reasoning(at: number): TRec | undefined { return this.latest('reasoning', at) }

  milestones(at: number): Milestone[] {
    const reached = new Map(this.upTo('milestone', at).map(m => [m.id, m]))
    return this.milestonesDefined.map(m => ({ ...m, t: reached.get(m.id)?.t, evidence: reached.get(m.id)?.evidence }))
  }

  tokens(at: number): number { return this.latest('usage', at)?.total_tokens ?? 0 }

  counts(at: number) {
    const calls = this.upTo('tool_call', at)
    let hooks = 0, shots = 0, inputs = 0, failed = 0
    for (const c of calls) {
      if (c.channel === 'hook') hooks++
      else if (c.channel === 'screenshot') shots++
      else if (c.channel === 'input') inputs++
      if (!c.ok) failed++
    }
    return { calls: calls.length, hooks, shots, inputs, failed }
  }

  hooks(at: number): HookView[] {
    const out = new Map<string, HookView>()
    for (const r of this.upTo('hook_loaded', at)) {
      const v: HookVersion = { name: r.hook, version: r.version, t: r.t, reason: r.reason ?? '', language: r.language ?? '',
        kind: r.kind ?? '', dependsOn: r.depends_on ?? [], files: r.files ?? [] }
      const h: HookView = out.get(r.hook) ?? { name: r.hook, versions: [], status: 'ok', calls: 0, isNew: false }
      h.versions.push(v)
      h.isNew = at - r.t < 20 * 60_000
      out.set(r.hook, h)
    }
    for (const s of this.upTo('hook_status', at)) {
      const h = out.get(s.hook)
      if (h) { h.status = s.status; h.statusDetail = s.detail }
    }
    for (const c of this.upTo('tool_call', at)) {
      if (c.channel !== 'hook') continue
      const h = out.get(c.hook?.name ?? c.tool)
      if (h) { h.calls++; h.lastCall = c.t }
    }
    return [...out.values()]
  }

  /** Newest feed lines up to `at`, with repeats of one tool folded into a count. */
  feed(at: number, n = 16): FeedItem[] {
    const lo = Math.max(0, upper(this.recs, at) - 600)
    const items: FeedItem[] = []
    for (const r of this.recs.slice(lo, upper(this.recs, at))) {
      const item = toFeed(r, this)
      if (!item) continue
      const last = items[items.length - 1]
      if (last && last.kind === item.kind && last.text === item.text && item.kind !== 'say') { last.count++; last.t = item.t; continue }
      items.push(item)
    }
    return items.slice(-n)
  }

  /** The newest screenshot with a saved thumbnail. */
  screenshot(at: number): TRec | undefined {
    const shots = this.of('tool_call')
    for (let i = upper(shots, at) - 1; i >= 0; i--) if (shots[i].artifact) return shots[i]
    return undefined
  }

  /** The newest hook observation, for the "what it just read" card. */
  lastHookCall(at: number): TRec | undefined {
    const calls = this.of('tool_call')
    for (let i = upper(calls, at) - 1, k = 0; i >= 0 && k < 400; i--, k++) if (calls[i].channel === 'hook') return calls[i]
    return undefined
  }

  /** Share of observations made through hooks, per bucket, for buckets that have started by `at`. */
  coverage(at: number, buckets = 48): { t: number; share: number; n: number }[] {
    const span = Math.max(1, this.t1 - this.t0), w = span / buckets
    const out = Array.from({ length: buckets }, (_, i) => ({ t: this.t0 + (i + 0.5) * w, hook: 0, shot: 0 }))
    for (const c of this.upTo('tool_call', at)) {
      if (c.channel !== 'hook' && c.channel !== 'screenshot') continue
      const b = out[Math.min(buckets - 1, Math.floor((c.t - this.t0) / w))]
      if (c.channel === 'hook') b.hook++; else b.shot++
    }
    return out.filter(b => b.hook + b.shot > 0).map(b => ({ t: b.t, share: b.hook / (b.hook + b.shot), n: b.hook + b.shot }))
  }

  /** Tokens spent reaching each milestone; the last entry is the one in progress, if any. */
  costPerMilestone(at: number): { id: string; name: string; tokens: number; done: boolean }[] {
    const ms = this.milestones(at)
    const out: { id: string; name: string; tokens: number; done: boolean }[] = []
    let prev = 0
    for (const m of ms) {
      if (m.t === undefined) {
        out.push({ id: m.id, name: m.name, tokens: this.tokens(at) - prev, done: false })
        break
      }
      const at_m = this.tokens(m.t)
      out.push({ id: m.id, name: m.name, tokens: at_m - prev, done: true })
      prev = at_m
    }
    return out
  }

  private buildMarkers(): Marker[] {
    const names = new Map(this.milestonesDefined.map(m => [m.id, m.name]))
    const ms: Marker[] = [
      ...this.of('milestone').map(r => ({ t: r.t, kind: 'milestone' as const, label: names.get(r.id) ?? r.id })),
      ...this.of('hook_loaded').map(r => ({ t: r.t, kind: 'hook' as const, label: `${r.hook} v${r.version}` })),
      ...this.of('hook_status').filter(r => r.status !== 'ok').map(r => ({
        t: r.t, kind: r.status === 'broken' ? 'break' as const : 'repair' as const,
        label: `${r.hook} ${r.status}` })),
      ...this.of('marker').map(r => ({ t: r.t, kind: 'break' as const, label: r.label })),
      ...this.of('agent_state').filter(r => r.state === 'compacting').map(r => ({ t: r.t, kind: 'compaction' as const, label: 'Compacting memory' })),
      ...this.of('agent_message').map(r => ({ t: r.t, kind: 'message' as const, label: r.text })),
    ]
    return ms.sort((a, b) => a.t - b.t)
  }
}

function toFeed(r: TRec, run: Run): FeedItem | null {
  switch (r.type) {
    case 'tool_call': {
      if (r.channel === 'extension') return null  // shown by hook_loaded
      const kind = !r.ok ? 'fail' : r.channel
      const text = !r.ok ? `${r.tool}: ${r.error}` : r.channel === 'hook' ? `${r.tool} → ${r.summary ?? 'ok'}` : r.summary ?? r.tool
      return { t: r.t, kind, text, count: 1 }
    }
    case 'hook_loaded':
      return { t: r.t, kind: 'extension', text: `${r.version > 1 ? 'updated' : 'wrote'} ${r.hook} v${r.version} (${r.language})`, count: 1 }
    case 'hook_status':
      return r.status === 'ok' ? null : { t: r.t, kind: 'status', text: `${r.hook} ${r.status}: ${r.detail ?? ''}`, count: 1 }
    case 'milestone': {
      const name = run.milestonesDefined.find(m => m.id === r.id)?.name ?? r.id
      return { t: r.t, kind: 'milestone', text: `milestone: ${name}`, count: 1 }
    }
    case 'agent_message': return { t: r.t, kind: 'say', text: r.text, count: 1 }
    case 'marker': return { t: r.t, kind: 'marker', text: r.label, count: 1 }
    case 'goal': return { t: r.t, kind: 'goal', text: `goal: ${r.text}`, count: 1 }
    default: return null
  }
}

/** Index of the first element with t > at. */
function upper(a: { t: number }[], at: number): number {
  let lo = 0, hi = a.length
  while (lo < hi) { const mid = (lo + hi) >> 1; if (a[mid].t <= at) lo = mid + 1; else hi = mid }
  return lo
}
