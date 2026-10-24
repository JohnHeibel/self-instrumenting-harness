import { useRef, useState } from 'react'
import type { Marker, Run } from '../data/model'
import { span } from '../util'

const LANES: { kind: Marker['kind'][]; label: string; top: number; color: (m: Marker) => string; flag?: boolean }[] = [
  { kind: ['milestone'], label: 'milestones', top: 10, color: () => 'var(--good)', flag: true },
  { kind: ['hook'], label: 'hooks written', top: 34, color: () => 'var(--ch-hook)' },
  { kind: ['break', 'repair'], label: 'breaks and repairs', top: 56, color: m => m.kind === 'break' ? 'var(--bad)' : 'var(--good)', flag: true },
  { kind: ['compaction', 'message'], label: 'agent', top: 78, color: m => m.kind === 'compaction' ? 'var(--text-muted)' : 'var(--text-secondary)' },
]

export const SPEEDS = [60, 300, 600, 1800]

export function Timeline({ run, at, playing, speed, onSeek, onPlay, onSpeed }: {
  run: Run; at: number; playing: boolean; speed: number
  onSeek: (t: number) => void; onPlay: (p: boolean) => void; onSpeed: (s: number) => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [hover, setHover] = useState<{ x: number; text: string } | null>(null)
  const dur = Math.max(1, run.t1 - run.t0)
  const frac = (t: number) => (t - run.t0) / dur
  const tAt = (clientX: number) => {
    const r = ref.current!.getBoundingClientRect()
    const scale = r.width / ref.current!.offsetWidth  // the canvas is CSS-scaled to fit the window
    const x = (clientX - r.left) / scale - 12
    return run.t0 + Math.max(0, Math.min(1, x / (ref.current!.offsetWidth - 24))) * dur
  }
  const hours = Array.from({ length: Math.floor(dur / 3_600_000) + 1 }, (_, h) => h).filter(h => h % 2 === 0)
  const step = (dir: 1 | -1) => {
    const ms = run.markers.filter(m => m.kind !== 'compaction')
    const next = dir > 0 ? ms.find(m => m.t > at + 1000) : [...ms].reverse().find(m => m.t < at - 60_000)
    if (next) onSeek(next.t + 2000)
  }
  return (
    <div className="timeline">
      <div className="controls">
        <div className="row">
          <button className="btn" onClick={() => step(-1)} title="Previous moment">◀◀</button>
          <button className="btn on" style={{ flex: 1 }} onClick={() => onPlay(!playing)}>{playing ? '❚❚ Pause' : '▶ Play'}</button>
          <button className="btn" onClick={() => step(1)} title="Next moment">▶▶</button>
        </div>
        <div className="row">
          {SPEEDS.map(s => <button key={s} className={`btn ${s === speed ? 'on' : ''}`} style={{ flex: 1, padding: '4px 0' }}
                                   onClick={() => onSpeed(s)}>{s}×</button>)}
        </div>
      </div>
      <div className="track" ref={ref}
           onMouseDown={e => { onSeek(tAt(e.clientX)); const move = (ev: MouseEvent) => onSeek(tAt(ev.clientX))
             const up = () => { removeEventListener('mousemove', move); removeEventListener('mouseup', up) }
             addEventListener('mousemove', move); addEventListener('mouseup', up) }}
           onMouseLeave={() => setHover(null)}>
        <div className="lane" style={{ top: 0, bottom: 0 }}>
          <div className="progress" style={{ width: `${frac(at) * 100}%` }} />
          {LANES.flatMap(l => run.markers.filter(m => l.kind.includes(m.kind)).map((m, i) => (
            <div key={l.label + i} className={`mk ${l.flag ? 'flag' : 'tick'}`}
                 style={{ left: `${frac(m.t) * 100}%`, top: l.top, background: l.color(m), opacity: m.t <= at ? 1 : 0.35 }}
                 onMouseEnter={() => setHover({ x: frac(m.t), text: `${span(m.t - run.t0)} · ${m.label}` })}
                 onMouseDown={e => { e.stopPropagation(); onSeek(m.t + 2000) }} />
          )))}
          {hours.map(h => <span key={h} className="axis" style={{ left: `${(h * 3_600_000 / dur) * 100}%` }}>{h}h</span>)}
        </div>
        {hover && <div className="tip" style={{ left: `calc(${hover.x * 100}% + 12px)`, top: -30, transform: 'translateX(-50%)', maxWidth: 520, whiteSpace: 'normal' }}>{hover.text}</div>}
      </div>
    </div>
  )
}
