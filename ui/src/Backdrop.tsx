import { createElement, useEffect, useRef } from 'react'
// The one place that names the Sisyphus animation. The modules register <sisyphus-backdrop-v3>: the red-figure Sisyphus
// and his boulder; its `flat` attribute re-poses him on level ground, and its `phase` attribute (in strides) sets the pose.
import './backdrop/sisyphus-parts.js'
import './backdrop/sisyphus-v3.js'

// The asset's own walk: a stride cycle (two steps) is 7 s of asset time.
const CYCLE_S = 7, DUTY = 0.6, SURGE = 0.35, STRIDE = 215
const W4 = (4 * Math.PI) / CYCLE_S, PH = -0.6
const V = STRIDE / (DUTY * CYCLE_S + (SURGE / W4) * (Math.sin(W4 * DUTY * CYCLE_S + PH) - Math.sin(PH)))
/** Ground travelled by asset time t (art px), the asset's `_ground`: moving him by it, a planted foot stays put on screen. */
const groundAt = (t: number) => V * (t + (SURGE / W4) * (Math.sin(W4 * t + PH) - Math.sin(PH)))

// Placement: the element draws a 1920 × 1080 scene; figure and boulder span x 639–1354, y 412–912 of it. Scaled so the
// figure is 52 px tall it fits the 62 px header.
const S = 52 / 500
const EL_W = 1920 * S, EL_H = 1080 * S
const FIG_X = 639 * S, FIG_W = 715 * S, FIG_TOP = 412 * S
const PX_PER_ART = 0.95 * (1080 / 1024) * S        // art px → screen px (world scale × scene scale × element scale)
const HEADER_W = 1920
/** At 1×: one step every 0.5 s, so one stride cycle (two steps) per second; he then covers about 35 px a second. */
const CYCLES_PER_S = 1
/** Fastest effective speed: a crossing (~56 s at 1×) never takes less than about 1.5 s. */
export const WALK_CAP = 36
const GAP_S = 3.5

/** Sisyphus pushing his boulder across the header, left to right, behind everything in it; off the right edge, a 3.5 s
 *  pause, then in again from the left. Walking speed and the pause scale with the replay speed (capped at WALK_CAP×).
 *  He walks on wall-clock time, always, whatever the agent or the replay is doing. */
export function HeaderWalk({ speed }: { speed: number }) {
  const el = useRef<HTMLElement | null>(null)
  const wrap = useRef<HTMLDivElement>(null)
  const sp = useRef(speed)
  useEffect(() => { sp.current = speed }, [speed])
  useEffect(() => {
    let raf = 0, last = performance.now(), cycles = 0, waiting = 0
    const x0 = -FIG_W - 2
    // #walkat=<seconds into a crossing at 1×> holds him there (for stills).
    const hold = Number(new URLSearchParams(location.hash.slice(1)).get('walkat'))
    const tick = (now: number) => {
      if (hold > 0) { cycles = hold * CYCLES_PER_S; sp.current = 0; waiting = 0 }
      const dt = Math.min(0.1, (now - last) / 1000)
      last = now
      const m = hold > 0 ? 0 : Math.max(0.25, Math.min(WALK_CAP, sp.current))
      if (waiting > 0) { waiting -= dt * m; if (waiting <= 0) cycles = 0 }
      else {
        cycles += dt * m * CYCLES_PER_S
        const x = x0 + groundAt(cycles * CYCLE_S) * PX_PER_ART
        const w = wrap.current
        if (x > HEADER_W + 2) { waiting = GAP_S; w?.style.setProperty('visibility', 'hidden') }
        else if (w) {
          w.style.visibility = 'visible'
          w.style.transform = `translate3d(${(x - FIG_X).toFixed(2)}px, ${(5 - FIG_TOP).toFixed(2)}px, 0)`
          el.current?.setAttribute('phase', cycles.toFixed(5))
        }
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [])
  return (
    <div className="walk" aria-hidden="true">
      <div ref={wrap} className="walker" style={{ width: EL_W, height: EL_H, visibility: 'hidden' }}>
        {createElement('sisyphus-backdrop-v3', { ref: el, flat: '', palette: 'marble', bands: 'none', opacity: '1', phase: '0' })}
      </div>
    </div>
  )
}
