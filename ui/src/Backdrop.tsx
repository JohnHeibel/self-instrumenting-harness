import { createElement } from 'react'
// The one place that names the backdrop. To swap in another version, change these imports and the element below;
// nothing else refers to it. The modules register <sisyphus-backdrop-v3>: Sisyphus pushing his stone up a 38° slope,
// drawn in the manner of a red-figure vase, held still under prefers-reduced-motion.
import './backdrop/sisyphus-parts.js'
import './backdrop/sisyphus-v3.js'

/** In its own panel at the foot of the side column, at full opacity. styles.css (.backdrop) recolours it to one light
 *  stone tone on the page ground, so the figure is crisp; it never intercepts input. */
export function Backdrop() {
  return createElement('sisyphus-backdrop-v3', { class: 'backdrop', palette: 'marble', bands: 'none', opacity: '1', 'aria-hidden': 'true' })
}
