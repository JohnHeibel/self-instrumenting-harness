import { useEffect, useMemo, useRef, useState } from 'react'
import { KEY_KINDS, type Marker, type Run } from '../data/model'
import { SPEEDS, span } from '../util'

// Canvas pixels. One chart: the slope is real progress (milestones reached) over time.
const W = 1840, X0 = 24, X1 = 1816, BASE = 156, PEAK = 50, R = 14
const LEVEL = BASE - PEAK

type Placed<T> = { item: T; x: number; row: number; anchor: 'start' | 'end' | 'middle'; w: number }

/** Greedy label rows: each label goes on the first row where it clears the previous one. */
function place<T extends { x: number }>(items: T[], widthOf: (i: T) => number, rows: number, centered = false): Placed<T>[] {
  const ends = Array<number>(rows).fill(-1e9)
  return items.map(item => {
    const w = widthOf(item)
    const anchor = centered ? 'middle' as const : item.x + w > W - 8 ? 'end' as const : 'start' as const
    const [a, b] = anchor === 'middle' ? [item.x - w / 2, item.x + w / 2 + 10] : anchor === 'start' ? [item.x - 4, item.x + w + 14] : [item.x - w - 14, item.x + 4]
    let row = ends.findIndex(e => e <= a)
    if (row < 0) row = ends.indexOf(Math.min(...ends))
    ends[row] = b
    return { item, x: item.x, row, anchor, w }
  })
}

/** Milestone labels sit above the slope; each takes the lowest tier where its box clears the ones already placed. */
function placeMilestones<T extends { x: number; y: number; label: string }>(items: T[]): Placed<T>[] {
  const boxes: { x0: number; x1: number; y: number }[] = []
  return items.map(item => {
    const w = item.label.length * 9.2
    const x0 = item.x - w / 2 - 6, x1 = item.x + w / 2 + 6
    let row = 0
    const yOfRow = (r: number) => item.y - 16 - r * 22
    while (row < 6 && boxes.some(b => b.x1 > x0 && b.x0 < x1 && Math.abs(b.y - yOfRow(row)) < 20)) row++
    boxes.push({ x0, x1, y: yOfRow(row) })
    return { item, x: Math.min(W - 8 - w / 2, Math.max(8 + w / 2, item.x)), row, anchor: 'middle' as const, w }
  })
}

type Pt = { x: number; y: number }
type Seg = { a: Pt; b: Pt; c1: Pt; c2: Pt }
const K = 0.42  // how long each rise eases in and out

/** Cubic segments through the progress points. Monotone, so the slope never overshoots the data. */
function segs(pts: Pt[]): Seg[] {
  return pts.slice(1).map((b, i) => {
    const a = pts[i], dx = (b.x - a.x) * K
    return { a, b, c1: { x: a.x + dx, y: a.y }, c2: { x: b.x - dx, y: b.y } }
  })
}
const bez = (s: Seg, u: number): Pt => {
  const m = 1 - u
  const f = (a: number, b: number, c: number, d: number) => m * m * m * a + 3 * m * m * u * b + 3 * m * u * u * c + u * u * u * d
  return { x: f(s.a.x, s.c1.x, s.c2.x, s.b.x), y: f(s.a.y, s.c1.y, s.c2.y, s.b.y) }
}
function slopeAt(ss: Seg[], x: number): Pt & { ang: number } {
  const s = ss.find(q => x <= q.b.x) ?? ss[ss.length - 1]
  let lo = 0, hi = 1
  for (let i = 0; i < 24; i++) { const mid = (lo + hi) / 2; if (bez(s, mid).x < x) lo = mid; else hi = mid }
  const p = bez(s, hi), q = bez(s, Math.min(1, hi + 0.01)), o = bez(s, Math.max(0, hi - 0.01))
  return { ...p, ang: Math.atan2(q.y - o.y, q.x - o.x) }
}

export function Timeline({ run, at, playing, speed, live, onJump, onSeek, onPlay, onSpeed }: {
  run: Run; at: number; playing: boolean; speed: number; live: boolean; onJump: () => void
  onSeek: (t: number) => void; onPlay: (p: boolean) => void; onSpeed: (s: number) => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const dur = Math.max(1, run.t1 - run.t0)
  const fx = (t: number) => X0 + (t - run.t0) / dur * (X1 - X0)
  const tAt = (clientX: number) => {
    const r = ref.current!.getBoundingClientRect()
    const x = (clientX - r.left) / (r.width / ref.current!.offsetWidth)  // the canvas is CSS-scaled to fit the window
    return run.t0 + Math.max(0, Math.min(1, (x - X0) / (X1 - X0))) * dur
  }
  const dim = (t: number) => (t <= at ? 1 : 0.45)
  const seekTo = (t: number) => (e: React.MouseEvent) => { e.stopPropagation(); onSeek(t + 2000) }
  const yOf = (n: number) => BASE - (n / Math.max(1, run.milestonesDefined.length)) * LEVEL

  const g = useMemo(() => {
    const ms = run.markers.filter(m => m.kind === 'milestone')
    const pts: Pt[] = [{ x: X0, y: BASE }, ...ms.map((m, i) => ({ x: fx(m.t), y: yOf(i + 1) })), { x: X1, y: yOf(ms.length) }]
    const ss = segs(pts)
    const d = ss.map((s, i) => `${i ? '' : `M${s.a.x},${s.a.y}`}C${s.c1.x},${s.c1.y} ${s.c2.x},${s.c2.y} ${s.b.x},${s.b.y}`).join('')
    const msP = ms.map((m, i) => ({ ...m, x: fx(m.t), y: yOf(i + 1) }))
    const onSlope = (m: Marker) => ({ ...m, x: fx(m.t), y: slopeAt(ss, fx(m.t)).y })
    const hooks = run.markers.filter(m => m.kind === 'hook').map(m => ({ ...m, x: fx(m.t) }))
    const brk = run.markers.filter(m => m.kind === 'break' || m.kind === 'repair').map(onSlope)
    const events = run.markers.filter(m => m.kind === 'restart' || m.kind === 'interrupt' || m.kind === 'prompt' || m.kind === 'violation').map(m => ({ ...m, x: fx(m.t) }))
    // The summit is where the newest milestone list ends. Where an announcement raised it, a line shows the old summit.
    const total = Math.max(1, run.milestonesDefined.length)
    const grew = run.growth.map(gr => ({ ...gr, x: fx(gr.t), y: BASE - (gr.from / total) * LEVEL }))
    return {
      ss, d, area: `${d}L${X1},${BASE}L${X0},${BASE}Z`,
      ms: placeMilestones(msP), hooks: place(hooks, m => m.label.length * 8.4, 3),
      brk: place(brk, m => Math.min(34, m.label.length) * 8.4, 3), events, grew,
      fails: run.of('tool_call').filter(c => !c.ok).map(c => c.t),
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run])

  // The myth, in the data: the boulder slips back while a hook is broken, and eases forward once it is repaired.
  const lastBR = [...run.markers].reverse().find(m => (m.kind === 'break' || m.kind === 'repair') && m.t <= at)
  const target = lastBR?.kind === 'break' ? 1 : 0
  const [slip, setSlip] = useState(0)
  const slipRef = useRef(0)
  useEffect(() => {
    const calm = matchMedia('(prefers-reduced-motion: reduce)').matches
    let raf = 0, last = performance.now()
    const step = (now: number) => {
      const dt = Math.min(0.1, (now - last) / 1000); last = now
      const s = slipRef.current, n = calm ? target : s + (target - s) * (1 - Math.exp(-dt * (target ? 7 : 2.4)))
      slipRef.current = Math.abs(n - target) < 0.003 ? target : n
      setSlip(slipRef.current)
      if (slipRef.current !== target) raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [target])

  const px = fx(at)
  const bx = Math.max(X0, px - slip * 34)
  const bp = slopeAt(g.ss, bx)
  const bc = { x: bp.x - Math.sin(bp.ang) * R, y: bp.y - Math.cos(bp.ang) * R }
  const spin = (bx / R) * (180 / Math.PI)  // it rolls by exactly the distance it travels
  const sh = { x: bp.x - Math.cos(bp.ang) * 16, y: bp.y - Math.sin(bp.ang) * 16 + 1 }

  const hours = Array.from({ length: Math.floor(dur / 3_600_000) + 1 }, (_, h) => h).filter(h => h % 2 === 0)
  const step = (dir: 1 | -1) => {
    const ms = run.markers.filter(m => KEY_KINDS.includes(m.kind))
    const next = dir > 0 ? ms.find(m => m.t > at + 1000) : [...ms].reverse().find(m => m.t < at - 60_000)
    if (next) onSeek(next.t + 2000)
  }
  const announced = run.milestones(at)
  const reached = announced.filter(m => m.t !== undefined).length

  return (
    <section className="timeline">
      <div className="thead">
        <div>
          <h2>The climb <span>{reached} of {announced.length} milestones · {span(dur)} run</span></h2>
          <p>The slope rises with each milestone the agent reaches. The boulder marks where the run is now. Click or drag to jump.</p>
        </div>
        <div className="controls">
          <button onClick={() => step(-1)} title="Previous key moment">◀ Prev moment</button>
          {live && at < run.t1 - 2000 && <button className="golive" onClick={onJump} title="Follow the newest moment again">Jump to live ▶|</button>}
          <button className="play" onClick={() => onPlay(!playing)}>{playing ? 'Pause' : 'Play'}</button>
          <button onClick={() => step(1)} title="Next key moment">Next moment ▶</button>
          <span className="speeds" role="group" aria-label="Playback speed">
            {SPEEDS.map(s => <button key={s} className={s === speed ? 'on' : ''} onClick={() => onSpeed(s)}>{s}×</button>)}
          </span>
        </div>
      </div>
      <div className={`plot ${playing ? 'playing' : ''}`} ref={ref}
           onMouseDown={e => {
             onSeek(tAt(e.clientX))
             const move = (ev: MouseEvent) => onSeek(tAt(ev.clientX))
             const up = () => { removeEventListener('mousemove', move); removeEventListener('mouseup', up) }
             addEventListener('mousemove', move); addEventListener('mouseup', up)
           }}>
        <svg width={W} height={224} role="img" aria-label="Progress of the run as a slope, with milestones, hooks, breaks and repairs">
          <defs>
            <linearGradient id="rock" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0" stopColor="#6a6e75" /><stop offset="1" stopColor="#2c2f34" />
            </linearGradient>
            <radialGradient id="boulder" cx="0.35" cy="0.3" r="0.8">
              <stop offset="0" stopColor="#e4e2dc" /><stop offset="0.55" stopColor="#a4a8ad" /><stop offset="1" stopColor="#5b5f65" />
            </radialGradient>
            <clipPath id="past"><rect x={0} y={0} width={Math.max(0, px)} height={224} /></clipPath>
          </defs>

          {/* the mountain: a ghost of the whole run, lit up to the playhead */}
          <path d={g.area} fill="url(#rock)" opacity={0.3} />
          <path d={g.d} fill="none" stroke="#8e939a" strokeWidth={1.5} opacity={0.55} />
          <g clipPath="url(#past)">
            <path d={g.area} fill="url(#rock)" />
            <path d={g.d} fill="none" stroke="#d9d8d3" strokeWidth={2} />
          </g>
          <line x1={X0} x2={X1} y1={BASE} y2={BASE} stroke="var(--stone-hi)" opacity={0.5} />

          {g.fails.map(t => <line key={t} x1={fx(t)} x2={fx(t)} y1={BASE - 11} y2={BASE - 1} stroke="var(--porphyry)" strokeWidth={2} opacity={dim(t)} />)}

          {/* milestones: small carved tablets on the slope */}
          {g.ms.map(p => (
            <g key={p.item.t} opacity={dim(p.item.t)} onMouseDown={seekTo(p.item.t)} className="hit">
              <title>{span(p.item.t - run.t0)} · {p.item.label}</title>
              <rect x={p.item.x - 6} y={p.item.y - 6} width={12} height={12} transform={`rotate(45 ${p.item.x} ${p.item.y})`}
                    fill={p.item.t <= at ? '#ece9e1' : '#2c2f34'} stroke="#ece9e1" strokeWidth={1.5} />
              <text x={p.x} y={p.item.y - 16 - p.row * 22} textAnchor="middle" className="t-ms">{p.item.label}</text>
            </g>
          ))}

          {/* breaks and repairs: notches cut into the slope, labelled inside the stone */}
          {g.brk.map(p => {
            const hue = p.item.kind === 'repair' ? 'var(--verde)' : 'var(--porphyry)'
            const label = p.item.label.length > 34 ? p.item.label.slice(0, 33) + '…' : p.item.label
            const ly = p.item.y + 28 + p.row * 18
            return (
              <g key={p.item.t + p.item.label} opacity={dim(p.item.t)} onMouseDown={seekTo(p.item.t)} className="hit">
                <title>{span(p.item.t - run.t0)} · {p.item.label}{p.item.detail ? ` · ${p.item.detail}` : ''}</title>
                <line x1={p.x} x2={p.x} y1={p.item.y} y2={ly - 12} stroke={hue} strokeWidth={1.5} />
                <path d={`M${p.x - 8},${p.item.y - 2} L${p.x + 8},${p.item.y - 2} L${p.x},${p.item.y + 12} Z`} fill={hue} />
                <text x={p.anchor === 'start' ? p.x + 4 : p.x - 4} y={ly} textAnchor={p.anchor === 'start' ? 'start' : 'end'} className="t-brk" style={{ fill: hue }}>{label}</text>
              </g>
            )
          })}

          {/* where the milestone list grew: the old summit stays as a dashed line */}
          {g.grew.map(gr => (
            <g key={gr.t} opacity={dim(gr.t)} onMouseDown={seekTo(gr.t)} className="hit">
              <title>{span(gr.t - run.t0)} · milestones grew from {gr.from} to {gr.to}: {gr.names.join(', ')}</title>
              <line x1={gr.x} x2={X1} y1={gr.y} y2={gr.y} stroke="var(--ochre)" strokeDasharray="3 5" opacity={0.55} />
              <path d={`M${gr.x},${gr.y - 9} L${gr.x},${gr.y + 9} M${gr.x - 5},${gr.y - 4} L${gr.x},${gr.y - 9} L${gr.x + 5},${gr.y - 4}`} fill="none" stroke="var(--ochre)" strokeWidth={1.6} />
              <text x={gr.x + 8} y={gr.y + 15} className="t-brk" style={{ fill: 'var(--ochre)' }}>+{gr.to - gr.from} milestones</text>
            </g>
          ))}

          {/* run events: restarts, interruptions and prompts, as short stems on the base line */}
          {g.events.map(e => {
            const hue = e.kind === 'interrupt' ? 'var(--ochre)' : e.kind === 'prompt' ? 'var(--stone-hi)' : e.kind === 'violation' ? 'var(--porphyry)' : 'var(--muted-hi)'
            return (
              <g key={e.t + e.label} opacity={dim(e.t)} onMouseDown={seekTo(e.t)} className="hit">
                <title>{span(e.t - run.t0)} · {e.kind === 'violation' ? 'Fair play: ' : ''}{e.label}{e.detail ? ` · ${e.detail}` : ''}</title>
                <line x1={e.x} x2={e.x} y1={BASE - 30} y2={BASE} stroke={hue} strokeWidth={1.2} strokeDasharray="2 2" />
                {e.kind === 'restart' && <circle cx={e.x} cy={BASE - 33} r={4} fill="none" stroke={hue} strokeWidth={1.6} />}
                {e.kind === 'interrupt' && <path d={`M${e.x - 5},${BASE - 28} L${e.x + 5},${BASE - 28} L${e.x},${BASE - 38} Z`} fill={hue} />}
                {e.kind === 'violation' && <path d={`M${e.x},${BASE - 43} L${e.x + 6},${BASE - 36} L${e.x},${BASE - 29} L${e.x - 6},${BASE - 36} Z`} fill={hue} />}
                {e.kind === 'prompt' && <rect x={e.x - 3.5} y={BASE - 37} width={7} height={7} fill="none" stroke={hue} strokeWidth={1.4} />}
              </g>
            )
          })}

          {/* hooks written: lapis ticks under the base, named */}
          {g.hooks.map(p => (
            <g key={p.item.t + p.item.label} opacity={dim(p.item.t)} onMouseDown={seekTo(p.item.t)} className="hit">
              <title>{span(p.item.t - run.t0)} · {p.item.label}</title>
              <line x1={p.x} x2={p.x} y1={BASE} y2={174 + p.row * 16 - 11} stroke="var(--lapis)" strokeWidth={1.5} />
              <rect x={p.x - 3} y={BASE} width={6} height={9} fill="var(--lapis)" />
              <text x={p.anchor === 'start' ? p.x - 3 : p.x + 3} y={174 + p.row * 16} textAnchor={p.anchor === 'start' ? 'start' : 'end'} className="t-hook">{p.item.label}</text>
            </g>
          ))}

          {hours.map(h => <text key={h} x={fx(run.t0 + h * 3_600_000)} y={218} textAnchor="middle" className="t-axis">{h}h</text>)}

          {/* the boulder, with a push shadow behind it */}
          <line x1={bx} x2={bx} y1={bc.y + R} y2={BASE} stroke="#d9d8d3" strokeDasharray="2 4" opacity={0.35} />
          <ellipse cx={sh.x} cy={sh.y} rx={20} ry={3.4} fill="#000" opacity={0.4} transform={`rotate(${(bp.ang * 180) / Math.PI} ${sh.x} ${sh.y})`} />
          <g transform={`translate(${bc.x} ${bc.y})`}>
            <circle r={R + 0.5} fill="url(#boulder)" stroke="#2a2c30" strokeWidth={1} />
            <g transform={`rotate(${spin})`} fill="none" stroke="#2f3236" strokeWidth={1.2} strokeLinecap="round" opacity={0.75}>
              <path d="M-8,-3 L-2,-1 L1,-8" /><path d="M2,2 L7,5 M-1,-1 L2,2" /><path d="M-6,6 L-3,3" />
            </g>
          </g>
        </svg>
        <div className="legend" aria-hidden="true">
          <span><i style={{ background: '#ece9e1', transform: 'rotate(45deg)' }} />Milestone</span>
          <span><i style={{ background: 'var(--lapis)' }} />Hook written</span>
          <span><i style={{ background: 'var(--porphyry)' }} />Break</span>
          <span><i style={{ background: 'var(--verde)' }} />Repair</span>
          <span><i style={{ background: 'var(--porphyry)', width: 3 }} />Failed call</span>
          <span><i style={{ border: '1.5px solid var(--muted-hi)', borderRadius: '50%', background: 'none' }} />Restart</span>
          <span><i style={{ background: 'var(--ochre)', clipPath: 'polygon(50% 0, 100% 100%, 0 100%)' }} />Interrupt</span>
          <span><i style={{ border: '1.5px solid var(--stone-hi)', background: 'none' }} />Prompt</span>
          <span><i style={{ background: 'var(--porphyry)', transform: 'rotate(45deg)' }} />Fair-play violation</span>
        </div>
        <div className="mist" aria-hidden="true"><i /><i /></div>
      </div>
    </section>
  )
}
