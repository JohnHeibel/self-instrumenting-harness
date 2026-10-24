import { createElement } from 'react'
// The one place that names the backdrop. To swap in another version, change this import and the element below;
// nothing else refers to it. The module registers <sisyphus-backdrop> (an SVG drawn with the figure, the stone and
// the ramp in the manner of black-figure painting) and respects prefers-reduced-motion by holding a still pose.
import './backdrop/sisyphus.js'

/** Sits behind the dashboard: very faint, ink and terracotta tuned to the night-stone palette, never intercepting input. */
export function Backdrop() {
  return createElement('sisyphus-backdrop', { class: 'backdrop', ground: '#b5683f', ink: '#050607', opacity: '0.13', 'aria-hidden': 'true' })
}
