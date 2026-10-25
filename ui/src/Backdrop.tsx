import { createElement, useEffect, useRef } from 'react'
// The one place that names the backdrop. To swap in another version, change these imports and the element below;
// nothing else refers to it. The modules register <sisyphus-backdrop-v3>: Sisyphus pushing his stone up a 38° slope,
// drawn in the manner of a red-figure vase. Its `phase` attribute (in strides; 1 = one 7 s stride) sets the pose, which
// is how its playback speed is driven from here.
import './backdrop/sisyphus-parts.js'
import './backdrop/sisyphus-v3.js'

import { pushStart, pushStep } from './pushRate'

const CYCLE_S = 7  // the asset's stride, in seconds of animation at full speed

/** Sisyphus pushes while `active` (the agent's hands are busy) and eases to a standstill when not; see pushRate.ts for the
 *  easing. Under reduced motion he holds still. */
export function Backdrop({ active }: { active: boolean }) {
  const el = useRef<HTMLElement | null>(null)
  const want = useRef(active)
  useEffect(() => { want.current = active }, [active])

  useEffect(() => {
    const calm = matchMedia('(prefers-reduced-motion: reduce)')
    let raf = 0, last = performance.now(), s = pushStart(), clock = 0.3 * CYCLE_S
    const tick = (now: number) => {
      const dt = Math.min(0.1, (now - last) / 1000)
      last = now
      s = pushStep(s, dt, want.current, calm.matches)
      if (s.rate > 0) { clock += s.rate * dt; el.current?.setAttribute('phase', (clock / CYCLE_S).toFixed(5)) }
      raf = requestAnimationFrame(tick)
    }
    el.current?.setAttribute('phase', (clock / CYCLE_S).toFixed(5))
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [])

  return createElement('sisyphus-backdrop-v3', { ref: el, class: 'backdrop', palette: 'marble', bands: 'none', opacity: '1', 'aria-hidden': 'true' })
}
