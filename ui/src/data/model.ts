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
  extension: string; sourceRef: string
}
export type HookView = {
  name: string; versions: HookVersion[]; status: 'ok' | 'broken' | 'repaired'; statusDetail?: string
  calls: number; checks: number; lastCall?: number; isNew: boolean; extension: string
}
export type Tone = 'good' | 'bad' | 'warn' | 'info' | 'muted'
export type FeedItem = {
  t: number; kind: 'hook' | 'screenshot' | 'input' | 'docs' | 'extension' | 'meta' | 'say' | 'milestone' | 'marker' | 'goal' | 'fail' | 'status'
    | 'host' | 'grader' | 'fair' | 'check' | 'turn'
  text: string; count: number; tone?: Tone
}
export type Milestone = { id: string; name: string; t?: number; evidence?: string }
export type MarkerKind = 'milestone' | 'hook' | 'break' | 'repair' | 'compaction' | 'message'
  | 'restart' | 'interrupt' | 'prompt' | 'violation' | 'grew' | 'note'
export type Marker = { t: number; kind: MarkerKind; label: string; detail?: string }
/** Marker kinds that "previous / next moment" stops at. */
export const KEY_KINDS: MarkerKind[] = ['milestone', 'hook', 'break', 'repair', 'message', 'interrupt', 'violation', 'grew']

export type MilestoneStep = { id: string; name: string; t: number; tokens: number; ms: number }
export type SignalState = { signal: string; active: boolean; everActive: boolean; since: number; detail: string; evidence: unknown; flips: number }
export type Integrity = {
  status: 'fair' | 'broken' | 'unverified' | 'unmonitored'
  signals: SignalState[]
  grader?: { ok: boolean; since: number; detail: string }
}
export type UsageInfo = { requests: number; input: number; cached: number; output: number; reasoning: number; contextWindow?: number; t: number }

const AGENT_STATES: Record<string, string> = {
  thinking: 'Thinking', acting: 'Acting', waiting: 'Waiting on the host', compacting: 'Compacting memory',
  host_down: 'Host is down', input_blocked: 'Input is blocked', backing_off: 'Sleeping after a failed turn', paused: 'Paused', ended: 'Run ended',
}

/** Run markers the runner writes (`marker.kind`), as the timeline and feed show them. */
const MARKER_KINDS: Record<string, { kind: MarkerKind; text: string; tone: Tone }> = {
  planted_break: { kind: 'break', text: 'Planted break', tone: 'bad' },
  runner_restart: { kind: 'restart', text: 'Runner restarted', tone: 'muted' },
  toolserver_restart: { kind: 'restart', text: 'Tool server restarted', tone: 'muted' },
  container_recreated: { kind: 'restart', text: 'Container recreated', tone: 'muted' },
  interrupt: { kind: 'interrupt', text: 'Interrupted', tone: 'warn' },
  prompt_goal: { kind: 'prompt', text: 'Goal prompt sent', tone: 'info' },
  prompt_continue: { kind: 'prompt', text: 'Continue prompt sent', tone: 'info' },
}

const byAgent = (r: Rec) => r.via !== 'health_check'  // a health check is the harness testing a hook, not the agent looking

export class Run {
  readonly recs: TRec[]
  readonly t0: number
  readonly t1: number
  readonly started?: Rec
  readonly markers: Marker[]
  /** The newest announced milestone list (milestones can grow mid-run), plus any reached id it lacks. */
  readonly milestonesDefined: { id: string; name: string }[]
  /** Each announcement after the first that added milestones. */
  readonly growth: { t: number; from: number; to: number; names: string[] }[] = []
  readonly hasGrader: boolean
  private byType = new Map<string, TRec[]>()
  private tokenSeries: { t: number; cum: number }[] = []
  private toolSeries: { t: number; n: number }[] = []
  private steps_: MilestoneStep[] = []

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
    this.hasGrader = this.recs.some(r => r.writer === 'grader')

    // Milestones: the newest list is the full one; remember each time it grew.
    let list: { id: string; name: string }[] = []
    for (const d of this.of('milestones_defined')) {
      const next: { id: string; name: string }[] = d.milestones ?? []
      const known = new Set(list.map(m => m.id))
      const added = next.filter(m => !known.has(m.id))
      if (list.length && added.length) this.growth.push({ t: d.t, from: list.length, to: list.length + added.length, names: added.map(m => m.name) })
      list = next.length ? [...next, ...list.filter(m => !next.some(n => n.id === m.id))] : list
    }
    const have = new Set(list.map(m => m.id))
    for (const m of this.of('milestone')) if (!have.has(m.id)) { list.push({ id: m.id, name: m.id }); have.add(m.id) }
    this.milestonesDefined = list

    let cum = 0, prev = 0
    for (const u of this.of('usage')) {
      const total = Number(u.total_tokens) || 0
      cum += total >= prev ? total - prev : total  // a smaller total means the thread counter restarted
      prev = total
      this.tokenSeries.push({ t: u.t, cum })
    }
    const seen = new Set<string>()
    for (const h of this.of('hook_loaded')) if (!seen.has(h.hook)) { seen.add(h.hook); this.toolSeries.push({ t: h.t, n: seen.size }) }

    // Cost of each milestone: tokens and wall time since the one before it.
    const names = new Map(list.map(m => [m.id, m.name]))
    let pt = this.t0, ptok = 0
    const done = new Set<string>()
    for (const m of this.of('milestone')) {
      if (done.has(m.id)) continue
      done.add(m.id)
      const tok = this.tokens(m.t)
      this.steps_.push({ id: m.id, name: names.get(m.id) ?? m.id, t: m.t, tokens: Math.max(0, tok - ptok), ms: Math.max(0, m.t - pt) })
      pt = m.t; ptok = tok
    }
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

  get ended(): boolean { return this.of('run_ended').length > 0 }
  /** Still running: no run_ended yet and a record in the last two minutes. */
  isLive(now: number): boolean { return !this.ended && now - this.t1 < 120_000 }

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

  /** The milestone list as announced by `at`, with what has been reached. Milestones that were added later are not in it yet. */
  milestones(at: number): Milestone[] {
    const reached = new Map(this.upTo('milestone', at).map(m => [m.id, m]))
    const defs = this.of('milestones_defined')
    const first = defs[0]
    const asOf = this.latest('milestones_defined', at) ?? first
    const ids = new Set<string>(asOf ? (asOf.milestones ?? []).map((m: { id: string }) => m.id) : [])
    const list = this.milestonesDefined.filter(m => ids.has(m.id) || reached.has(m.id) || !asOf)
    return list.map(m => ({ ...m, t: reached.get(m.id)?.t, evidence: reached.get(m.id)?.evidence }))
  }

  /** Tokens used so far: the sum of per-request growth of the thread total, so a counter reset doesn't undo progress. */
  tokens(at: number): number {
    const i = upper(this.tokenSeries, at)
    return i ? this.tokenSeries[i - 1].cum : 0
  }

  /** The newest request's usage, and the totals over the run so far. */
  usage(at: number): UsageInfo | undefined {
    const us = this.upTo('usage', at)
    const u = us[us.length - 1]
    if (!u) return undefined
    return { requests: us.length, input: u.input_tokens ?? 0, cached: u.cached_input_tokens ?? 0, output: u.output_tokens ?? 0,
      reasoning: u.reasoning_output_tokens ?? 0, contextWindow: u.context_window || undefined, t: u.t }
  }

  counts(at: number) {
    const calls = this.upTo('tool_call', at)
    let hooks = 0, shots = 0, inputs = 0, failed = 0, checks = 0
    for (const c of calls) {
      if (!byAgent(c)) { checks++; continue }
      if (c.channel === 'hook') hooks++
      else if (c.channel === 'screenshot') shots++
      else if (c.channel === 'input') inputs++
      if (!c.ok) failed++
    }
    return { calls: calls.length, hooks, shots, inputs, failed, checks }
  }

  hooks(at: number): HookView[] {
    const out = new Map<string, HookView>()
    for (const r of this.upTo('hook_loaded', at)) {
      const v: HookVersion = { name: r.hook, version: r.version, t: r.t, reason: r.reason ?? '', language: r.language ?? '',
        kind: r.kind ?? '', dependsOn: r.depends_on ?? [], files: r.files ?? [], extension: r.extension ?? '', sourceRef: r.source_ref ?? '' }
      const h: HookView = out.get(r.hook) ?? { name: r.hook, versions: [], status: 'ok', calls: 0, checks: 0, isNew: false, extension: '' }
      h.versions.push(v)
      h.extension = v.extension || h.extension
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
      if (!h) continue
      if (c.hook?.extension && !h.extension) h.extension = c.hook.extension
      if (byAgent(c)) { h.calls++; h.lastCall = c.t } else h.checks++
    }
    return [...out.values()]
  }

  /** Distinct hooks written by `at`. */
  toolsBuilt(at: number): number {
    const i = upper(this.toolSeries, at)
    return i ? this.toolSeries[i - 1].n : 0
  }
  /** Step series of distinct hooks written, over the whole run. */
  toolsOverTime(): { t: number; n: number }[] { return this.toolSeries }

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
    for (let i = upper(shots, at) - 1; i >= 0; i--) if (shots[i].artifact && shots[i].channel === 'screenshot') return shots[i]
    return undefined
  }

  /** The newest hook observation by the agent, for the "what it just read" card. */
  lastHookCall(at: number): TRec | undefined {
    const calls = this.of('tool_call')
    for (let i = upper(calls, at) - 1, k = 0; i >= 0 && k < 400; i--, k++) if (calls[i].channel === 'hook' && byAgent(calls[i])) return calls[i]
    return undefined
  }

  /** Share of observations made through hooks, per bucket, for buckets that have started by `at`. */
  coverage(at: number, buckets = 48): { t: number; share: number; n: number }[] {
    const span = Math.max(1, this.t1 - this.t0), w = span / buckets
    const out = Array.from({ length: buckets }, (_, i) => ({ t: this.t0 + (i + 0.5) * w, hook: 0, shot: 0 }))
    for (const c of this.upTo('tool_call', at)) {
      if (c.channel !== 'hook' && c.channel !== 'screenshot') continue
      if (!byAgent(c)) continue
      const b = out[Math.min(buckets - 1, Math.floor((c.t - this.t0) / w))]
      if (c.channel === 'hook') b.hook++; else b.shot++
    }
    return out.filter(b => b.hook + b.shot > 0).map(b => ({ t: b.t, share: b.hook / (b.hook + b.shot), n: b.hook + b.shot }))
  }

  /** Coverage over the hour before `at` (or since the start if the run is younger), so the headline doesn't swing with one bucket. */
  coverageRecent(at: number, windowMs = 3_600_000): number | undefined {
    const calls = this.of('tool_call')
    let hook = 0, shot = 0
    for (let i = upper(calls, at) - 1; i >= 0 && calls[i].t > at - windowMs; i--) {
      const c = calls[i]
      if (!byAgent(c)) continue
      if (c.channel === 'hook') hook++; else if (c.channel === 'screenshot') shot++
    }
    return hook + shot ? hook / (hook + shot) : undefined
  }

  /** Cost of each milestone reached by `at` (tokens and wall time since the previous one). */
  steps(at: number): MilestoneStep[] { return this.steps_.slice(0, upper(this.steps_, at)) }

  /** What the milestone in progress has cost so far. */
  inProgress(at: number): { tokens: number; ms: number } {
    const done = this.steps(at), last = done[done.length - 1]
    return { tokens: Math.max(0, this.tokens(at) - this.tokens(last?.t ?? this.t0 - 1)), ms: Math.max(0, at - (last?.t ?? this.t0)) }
  }

  /** Fair-play state from the grader's integrity signals and its reachability. */
  integrity(at: number): Integrity {
    const sig = new Map<string, SignalState>()
    for (const r of this.upTo('integrity', at)) {
      const s = sig.get(r.signal) ?? { signal: r.signal, active: false, everActive: false, since: r.t, detail: '', evidence: undefined, flips: 0 }
      if (s.active !== !!r.active) { s.since = r.t; s.flips++ }
      s.active = !!r.active; s.everActive ||= s.active
      s.detail = r.detail ?? s.detail; s.evidence = r.evidence ?? s.evidence
      sig.set(r.signal, s)
    }
    const gs = this.latest('grader_status', at)
    const signals = [...sig.values()]
    const broken = signals.some(s => s.everActive && s.signal !== 'grader_unavailable')
    const unverified = signals.some(s => s.signal === 'grader_unavailable' && s.active) || (gs ? gs.ok === false : false)
    const monitored = this.hasGrader || signals.length > 0
    return {
      status: broken ? 'broken' : unverified ? 'unverified' : monitored ? 'fair' : 'unmonitored', signals,
      grader: gs && { ok: gs.ok !== false, since: gs.t, detail: gs.detail ?? '' },
    }
  }

  private buildMarkers(): Marker[] {
    const names = new Map(this.milestonesDefined.map(m => [m.id, m.name]))
    const ms: Marker[] = [
      ...this.of('milestone').map(r => ({ t: r.t, kind: 'milestone' as const, label: names.get(r.id) ?? r.id })),
      ...this.of('hook_loaded').map(r => ({ t: r.t, kind: 'hook' as const, label: `${r.hook} v${r.version}` })),
      ...this.of('hook_status').filter(r => r.status !== 'ok').map(r => ({
        t: r.t, kind: r.status === 'broken' ? 'break' as const : 'repair' as const,
        label: `${r.hook} ${r.status}`, detail: r.detail })),
      ...this.of('marker').map(r => {
        const k = MARKER_KINDS[r.kind] ?? { kind: 'note' as const, text: '', tone: 'muted' as Tone }
        return { t: r.t, kind: k.kind, label: r.label ?? k.text, detail: r.reason }
      }),
      ...this.of('turn_completed').filter(r => r.status && r.status !== 'completed').map(r => ({
        t: r.t, kind: r.status === 'cut_off' ? 'restart' as const : 'interrupt' as const,
        label: r.status === 'cut_off' ? 'Turn cut off' : r.status === 'failed' ? 'Turn failed' : 'Turn interrupted', detail: r.error })),
      ...this.of('integrity').filter(r => r.active && r.signal !== 'grader_unavailable').map(r => ({
        t: r.t, kind: 'violation' as const, label: r.signal.replace(/_/g, ' '), detail: r.detail })),
      ...this.growth.map(g => ({ t: g.t, kind: 'grew' as const, label: `+${g.to - g.from} milestones`, detail: g.names.join(', ') })),
      ...this.of('agent_state').filter(r => r.state === 'compacting').map(r => ({ t: r.t, kind: 'compaction' as const, label: 'Compacting memory' })),
      ...this.of('agent_message').map(r => ({ t: r.t, kind: 'message' as const, label: r.text })),
    ]
    return ms.sort((a, b) => a.t - b.t)
  }
}

const short = (v: unknown, n = 90) => {
  const s = typeof v === 'string' ? v : v == null ? '' : JSON.stringify(v)
  return s.length > n ? s.slice(0, n - 1) + '…' : s
}

function toFeed(r: TRec, run: Run): FeedItem | null {
  switch (r.type) {
    case 'tool_call': {
      if (r.channel === 'extension') return null  // shown by hook_loaded
      if (r.via === 'health_check') return r.ok
        ? { t: r.t, kind: 'check', text: `${r.tool} passed its health check`, count: 1 }
        : { t: r.t, kind: 'fail', text: `${r.tool} health check: ${r.error}`, count: 1 }
      const kind = !r.ok ? 'fail' : r.channel
      const text = !r.ok ? `${r.tool}: ${r.error}` : r.channel === 'hook' ? `${r.tool} → ${r.summary ?? 'ok'}` : r.summary ?? r.tool
      return { t: r.t, kind, text, count: 1 }
    }
    case 'hook_loaded':
      return { t: r.t, kind: 'extension', text: `${r.version > 1 ? 'updated' : 'wrote'} ${r.hook} v${r.version} (${r.language})`, count: 1 }
    case 'hook_status':
      return r.status === 'ok' ? { t: r.t, kind: 'check', text: `${r.hook} is healthy${r.detail ? `: ${r.detail}` : ''}`, count: 1 }
        : { t: r.t, kind: 'status', text: `${r.hook} ${r.status}: ${r.detail ?? ''}`, count: 1 }
    case 'milestone': {
      const name = run.milestonesDefined.find(m => m.id === r.id)?.name ?? r.id
      return { t: r.t, kind: 'milestone', text: `milestone: ${name}`, count: 1 }
    }
    case 'milestones_defined': {
      const g = run.growth.find(x => x.t === r.t)
      return g ? { t: r.t, kind: 'milestone', text: `milestones grew to ${g.to}: ${g.names.join(', ')}`, count: 1 } : null
    }
    case 'agent_message': return { t: r.t, kind: 'say', text: r.text, count: 1 }
    case 'marker': {
      const k = MARKER_KINDS[r.kind]
      return { t: r.t, kind: 'marker', text: r.label ?? k?.text ?? r.kind, count: 1, tone: k?.tone ?? 'muted' }
    }
    case 'goal': return { t: r.t, kind: 'goal', text: `goal: ${r.text}`, count: 1 }
    case 'host_command':
      return { t: r.t, kind: 'host', count: 1, tone: r.custom ? 'info' : 'warn',
        text: `${r.custom ? 'helper command' : 'host command'}: ${short([r.command, r.parameters].filter(Boolean).join(' '))}${r.by ? ` (${r.by})` : ''}` }
    case 'integrity':
      return { t: r.t, kind: 'fair', count: 1, tone: r.active && r.signal !== 'grader_unavailable' ? 'bad' : r.active ? 'warn' : 'good',
        text: `${r.signal.replace(/_/g, ' ')} ${r.active ? 'on' : 'off'}${r.detail ? `: ${short(r.detail)}` : ''}` }
    case 'grader_status':
      return { t: r.t, kind: 'grader', count: 1, tone: r.ok === false ? 'warn' : 'good',
        text: r.ok === false ? `grader can't reach the host${r.detail ? `: ${short(r.detail)}` : ''}` : 'grader can reach the host again' }
    case 'turn_completed':
      return r.status && r.status !== 'completed'
        ? { t: r.t, kind: 'turn', count: 1, tone: r.status === 'failed' ? 'bad' : 'warn',
            text: r.status === 'cut_off' ? 'turn cut off when the runner stopped' : `turn ${r.status}${r.error ? `: ${short(r.error)}` : ''}` }
        : null
    default: return null
  }
}

/** Index of the first element with t > at. */
function upper(a: { t: number }[], at: number): number {
  let lo = 0, hi = a.length
  while (lo < hi) { const mid = (lo + hi) >> 1; if (a[mid].t <= at) lo = mid + 1; else hi = mid }
  return lo
}
