/** A Doric column in thin line work, framing an edge of the stage: abacus and echinus, a fluted shaft with a slight
 *  taper, and a stepped base. Pure ornament: hidden from assistive technology and from input. */
export function Column({ side, height }: { side: 'left' | 'right'; height: number }) {
  const W = 40, cx = W / 2, top = 0, cap = 26, base = 22
  const s0 = top + cap, s1 = height - base
  const flutes = [-9, -4.5, 0, 4.5, 9]
  return (
    <svg className={`column ${side}`} width={W} height={height} viewBox={`0 0 ${W} ${height}`} aria-hidden="true">
      <rect x={1} y={top + 0.5} width={W - 2} height={7} />
      <path d={`M5 ${top + 8} Q${cx} ${top + 24} ${W - 5} ${top + 8}`} />
      <line x1={8} x2={W - 8} y1={s0 - 1} y2={s0 - 1} />
      <path d={`M8 ${s0} L9.5 ${s1} M${W - 8} ${s0} L${W - 9.5} ${s1}`} />
      {flutes.map(f => <line key={f} x1={cx + f * 0.9} x2={cx + f * 0.78} y1={s0 + 4} y2={s1 - 4} className="flute" />)}
      <line x1={7} x2={W - 7} y1={s1 + 1} y2={s1 + 1} />
      <rect x={4} y={s1 + 5} width={W - 8} height={6} />
      <rect x={1} y={s1 + 13} width={W - 2} height={8} />
    </svg>
  )
}
