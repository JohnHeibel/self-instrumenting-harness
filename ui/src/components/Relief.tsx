/** A low-contrast relief behind the upper area: Sisyphus pushing his stone up the ridge, in the manner of
 *  Attic black-figure painting (solid silhouette, incised detail, meander band). Hand drawn; decorative only. */
export function Relief() {
  const limb = (d: string, w: number) => <path d={d} strokeWidth={w} />
  return (
    <svg className="relief" viewBox="0 0 800 640" preserveAspectRatio="xMaxYMid meet" aria-hidden="true">
      <defs>
        <pattern id="key" width="16" height="12" patternUnits="userSpaceOnUse">
          <path d="M0 11 H3 V1 H13 V8 H7 V5 H10 M13 8 V11 H16" fill="none" stroke="currentColor" strokeWidth="1.4" />
        </pattern>
        <mask id="incise" maskUnits="userSpaceOnUse" x="0" y="0" width="800" height="640">
          <rect width="800" height="640" fill="#fff" />
          <g fill="none" stroke="#000" strokeWidth="2.2" strokeLinecap="round">
            <path d="M384 262 C396 272 412 282 420 286" /><path d="M345 326 C358 330 366 326 372 318" />
            <path d="M352 352 C360 358 368 356 374 350" /><path d="M370 440 C380 446 388 444 394 438" />
            <path d="M388 232 C394 228 402 228 408 232" /><path d="M392 246 C398 248 404 247 408 244" />
            <path d="M296 466 C304 470 310 468 314 462" /><path d="M404 316 C412 312 420 312 426 316" />
          </g>
        </mask>
      </defs>
      <g color="#cfd2d6" stroke="currentColor" fill="none" strokeLinecap="round" strokeLinejoin="round">
        {/* ridge with strata */}
        <path d="M0 590 L200 545 L380 470 L560 360 L800 215" strokeWidth="2.5" />
        {[1, 2, 3, 4, 5].map(i => <path key={i} d={`M0 ${590 + i * 22} L200 ${545 + i * 22} L380 ${470 + i * 22} L560 ${360 + i * 22} L800 ${215 + i * 22}`} strokeWidth="1" opacity={1 - i * 0.15} />)}
        {/* the stone, with engraved arcs */}
        <circle cx="528" cy="251" r="110" strokeWidth="2.5" />
        {[0.82, 0.64, 0.46].map(k => <circle key={k} cx="528" cy="251" r={110 * k} strokeWidth="1" strokeDasharray="2 7" />)}
        <path d="M470 205 C500 190 540 192 566 208 M452 260 C470 285 500 296 530 294" strokeWidth="1.2" />
        {/* the figure: black-figure silhouette, incised lines cut through a mask */}
        <g mask="url(#incise)" fill="currentColor" stroke="currentColor">
          <ellipse cx="396" cy="246" rx="16" ry="20" stroke="none" />
          <path d="M404 258 L424 262 L406 270 Z" stroke="none" />
          <path d="M392 256 L380 276" strokeWidth="12" />
          {limb('M338 378 L372 288', 32)}
          {limb('M372 290 L404 318 L436 306', 12)}
          {limb('M380 282 L408 296 L436 300', 13)}
          {limb('M332 382 L372 436 L350 480', 19)}
          {limb('M326 384 L296 452 L268 518', 18)}
          <path d="M318 372 L352 372 L358 410 L322 408 Z" stroke="none" />
        </g>
      </g>
    </svg>
  )
}
