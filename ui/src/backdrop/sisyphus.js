/*
 * <sisyphus-backdrop> — Sisyphus pushing his stone up a gentle ramp, drawn as an archaic
 * Attic black-figure panel: flat orange ground, solid black figures, details incised as
 * thin orange lines through the black. Meander band above, tongue band below, thick frame.
 *
 * Cut-out rig (torso, tunic skirt, head, upper/lower arms, hands, thighs, shins, sandalled
 * feet) on two-bone IK. Treadmill loop: the hero and the stone hold their place while the
 * ramp and its cracks slide down-left; the stone turns with the ground.
 *
 * Attributes (all optional):
 *   opacity   0..1        default var(--sisyphus-opacity, .12)
 *   ground    CSS color   the clay ground / incision colour (default #E8861C)
 *   ink       CSS color   the black glaze (default #0d0a08)
 *   speed     number      time multiplier (default 1)
 *   paused    boolean
 *   time      seconds     freeze at this time (stills / screenshots)
 * CSS custom properties: --sisyphus-opacity, --sisyphus-ground, --sisyphus-ink.
 */
(() => {
  const DEG = Math.PI / 180;
  const W = 1920, H_ = 1080;
  const SLOPE = 6 * DEG;                 // the ramp: shallow and constant
  const T = 7;                           // seconds per full stride (two steps)
  const STANCE = 0.64;
  const L_THIGH = 150, L_SHIN = 146, L_UPPER = 104, L_FORE = 92;
  const TOE = [52, 25];                  // ball of the foot in foot coordinates
  const S_PLANT = 104, STRIDE = 286;
  const HIP_Y = -262;
  const LEAN = 54;
  const R = 232;                         // the stone, in hero units (larger than the man)
  const K = 1.36;                        // hero scale on the 1920x1080 panel
  const ORIGIN_X = 600;                  // where the hero stands
  const RAMP_Y0 = 905;                   // ramp height at the left edge of the panel
  const rampY = (x) => RAMP_Y0 - Math.tan(SLOPE) * x;
  const ORIGIN = [ORIGIN_X, rampY(ORIGIN_X)];

  // ---------------------------------------------------------------- helpers
  const rot = (v, a) => [v[0] * Math.cos(a) - v[1] * Math.sin(a), v[0] * Math.sin(a) + v[1] * Math.cos(a)];
  const add = (a, b) => [a[0] + b[0], a[1] + b[1]];
  const sub = (a, b) => [a[0] - b[0], a[1] - b[1]];
  const mul = (a, k) => [a[0] * k, a[1] * k];
  const len = (v) => Math.hypot(v[0], v[1]);
  const lerp = (a, b, t) => a + (b - a) * t;
  const clamp01 = (t) => Math.min(1, Math.max(0, t));
  const smooth = (t) => { t = clamp01(t); return t * t * (3 - 2 * t); };
  const easeInOut = (t) => 0.5 - 0.5 * Math.cos(Math.PI * clamp01(t));
  const ground = (s) => [s * Math.cos(SLOPE), -s * Math.sin(SLOPE)];
  const NORMAL = [-Math.sin(SLOPE), -Math.cos(SLOPE)];
  const hangAngle = (v) => Math.atan2(-v[0], v[1]) / DEG;
  const f2 = (n) => n.toFixed(2);
  const tf = (p, ang, sx = 1, sy = 1) =>
    `translate(${f2(p[0])} ${f2(p[1])}) rotate(${f2(ang)})${sx !== 1 || sy !== 1 ? ` scale(${f2(sx)} ${f2(sy)})` : ''}`;
  function rng(seed) {          // mulberry32
    return () => {
      seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  // a wavy strand from p0 to p1
  function wavy(p0, p1, amp, waves, phase = 0) {
    const n = Math.max(8, Math.round(waves * 8));
    const d = sub(p1, p0), L = len(d), nx = -d[1] / L, ny = d[0] / L;
    let s = '';
    for (let k = 0; k <= n; k++) {
      const u = k / n, w = Math.sin(u * waves * 2 * Math.PI + phase) * amp * Math.sin(Math.PI * Math.min(1, u * 1.6));
      const x = p0[0] + d[0] * u + nx * w, y = p0[1] + d[1] * u + ny * w;
      s += (k ? ' L' : 'M') + x.toFixed(1) + ',' + y.toFixed(1);
    }
    return s + ' ';
  }
  // parallel bands across a limb drawn along +y
  const bands = (ys, half) => ys.map((y) => { const [a, b] = half(y); return `M${a.toFixed(1)},${y} L${b.toFixed(1)},${y}`; }).join(' ');
  function ik(root, target, a, b, bendSign) {
    const d0 = sub(target, root);
    const L0 = len(d0) || 1;
    const d = Math.min(Math.max(L0, Math.abs(a - b) + 1), a + b - 0.6);
    const base = Math.atan2(d0[1], d0[0]);
    const A = Math.acos(Math.min(1, Math.max(-1, (a * a + d * d - b * b) / (2 * a * d))));
    const ang = base + bendSign * A;
    return { joint: add(root, [a * Math.cos(ang), a * Math.sin(ang)]), end: add(root, mul(d0, d / L0)) };
  }

  // ---------------------------------------------------------------- the figure
  // Parts are drawn upright facing right, joint at (0,0), limbs hanging along +y.
  // o: black silhouette. c: incised separation contour. i: incised detail (ground colour).
  const foreHalf = (y) => [-14 + y * 0.03, 19 - y * 0.09];

  const PARTS = {
    torso: {
      o: `M-14,-170 C-27,-163 -41,-152 -45,-134 C-51,-110 -49,-84 -43,-64 C-39,-48 -41,-30 -45,-14
          C-48,2 -42,14 -30,18 L24,18 C36,4 38,-14 38,-30 C38,-56 44,-80 51,-98 C58,-116 60,-136 53,-150
          C46,-162 32,-168 20,-172 C16,-174 14,-176 12,-178 Z`,
      c: `M-14,-170 C-27,-163 -41,-152 -45,-134 C-51,-110 -49,-84 -43,-64 C-39,-48 -41,-30 -45,-14
          M38,-30 C38,-56 44,-80 51,-98 C58,-116 60,-136 53,-150 C46,-162 32,-168 20,-172`,
      i: `M-10,-166 C0,-158 10,-158 18,-170
          M-38,-140 C-30,-120 -12,-112 6,-118
          M-46,-96 L50,-96 M-46,-90 L48,-90
          M-44,-60 L42,-60 M-44,-54 L40,-54 M-43,-48 L39,-48
          M-46,-20 L37,-20 M-46,-12 L36,-12
          M-40,-126 l0,6 M-26,-122 l0,6 M-12,-120 l0,6
          M30,-132 C40,-120 48,-108 50,-100`
    },
    skirt: {
      o: `M-46,-22 L38,-22 C45,6 52,34 58,60 L58,66 C30,74 -22,74 -54,64 C-52,30 -50,4 -46,-22 Z`,
      c: `M38,-22 C45,6 52,34 58,60 L58,66 C30,74 -22,74 -54,64 C-52,30 -50,4 -46,-22`,
      i: `M-48,6 L46,6 M-49,12 L47,12 M-51,36 L52,36 M-51,42 L53,42 M-53,56 L57,56
          ${Array.from({ length: 11 }, (_, k) => `M${-48 + k * 10},57 l0,10`).join(' ')}
`
    },
    head: {
      o: `M-16,8 C-12,-12 -10,-30 -13,-44 L-10,-92 C0,-110 24,-113 33,-100 C38,-93 40,-87 41,-83
          L51,-69 C51,-65 48,-63 44,-63 L45,-58 C43,-56 41,-55 39,-56 L42,-52 C41,-49 38,-47 36,-47
          L44,-40 C46,-32 44,-24 40,-18 L16,-26 C15,-16 16,-4 22,8 Z`,
      i: `M16,-89 C22,-92.5 31,-92.5 38,-88.5
          M18,-82 C22,-86.5 28,-86.5 33,-82.5 C28,-79 22,-79 18,-82 Z
          M10,-72 C20,-66 30,-60 36,-56 M35,-61 C39,-59 43,-58 46,-59
          M14,-60 C20,-50 28,-38 38,-26 M22,-58 C28,-48 34,-38 42,-28 M12,-48 C18,-40 24,-32 32,-24
          M7,-75 C-1,-76 -3,-63 2,-58 C6,-55 10,-59 8,-63 C6,-66 4,-63 5,-61
          M-6,-30 C0,-18 6,-8 12,4`,
      pupil: [26.2, -82.6]
    },
    hair: {
      o: `M36,-100 C30,-114 8,-121 -12,-115 C-30,-109 -42,-92 -42,-70 C-42,-48 -46,-26 -50,-4
          C-54,16 -54,34 -48,50 C-42,44 -38,48 -34,54 C-31,44 -27,46 -22,50 C-19,42 -16,44 -12,46
          C-14,30 -12,14 -10,0 C-8,-20 -6,-40 -2,-56 C2,-70 8,-84 18,-90 C26,-94 31,-97 36,-100 Z`,
      c: `M36,-100 C30,-114 8,-121 -12,-115 C-30,-109 -42,-92 -42,-70 C-42,-48 -46,-26 -50,-4
          C-54,16 -54,34 -48,50 M-12,46 C-14,30 -12,14 -10,0 C-8,-20 -6,-40 -2,-56 C2,-70 8,-84 18,-90 C26,-94 31,-97 36,-100`,
      i: wavy([-30, -100], [-46, 44], 2.2, 5) + wavy([-22, -98], [-36, 48], 2.2, 5, 1) + wavy([-14, -94], [-26, 44], 2, 5, 2) +
         wavy([-6, -84], [-16, 40], 2, 4.5, 0.5) + wavy([-36, -86], [-50, 30], 2, 4.5, 2.5) +
         `M34,-104 C18,-112 -4,-112 -34,-96 M33,-98 C18,-106 -4,-106 -36,-88
          M-34,-92 c-8,4 -14,12 -16,22 M-32,-90 c-3,8 -4,16 -2,24
          M28,-106 q-3,-4 -6,0 M20,-109 q-3,-4 -6,0 M12,-111 q-3,-4 -6,0 M4,-111 q-3,-4 -6,0 M-4,-110 q-3,-4 -6,0`
    },
    upper: {
      o: `M-24,-14 C-16,-28 14,-30 27,-12 C33,2 32,18 27,32 C32,46 30,68 22,90 C18,101 13,108 6,113
          C-2,117 -12,115 -15,108 C-22,90 -27,64 -27,40 C-29,22 -28,4 -24,-14 Z`,
      i: `M27,28 C17,38 9,47 2,58 M-26,22 C-17,37 -8,48 2,58 M4,64 C2,80 4,94 7,105 M-12,-4 C-2,-10 10,-10 18,-3`
    },
    fore: {
      o: `M13,-6 C24,8 24,28 19,48 C15,64 12,78 10,96 L-8,96 C-10,72 -14,46 -16,26 C-18,10 -16,0 -12,-8
          C-4,-12 6,-11 13,-6 Z`,
      c: `M16,0 C24,10 24,28 19,48 C15,64 12,78 10,92 M-8,92 C-10,72 -14,46 -16,26 C-17,14 -16,6 -14,0`,
      i: bands([34, 39, 44, 66, 71, 80, 84, 88], foreHalf) + ` M14,6 C8,18 6,26 6,30`
    },
    hand: {
      o: `M-9,-6 C-11,10 -11,24 -10,34 C-10,44 -9,52 -6,58 C-3,62 2,61 3,56 C5,48 7,40 9,33
          C14,29 18,24 20,18 C21,13 17,11 14,14 C12,16 11,18 10,20 C11,11 10,3 9,-6 Z`,
      c: `M-9,0 C-11,10 -11,24 -10,34 C-10,44 -9,52 -6,58 C-3,62 2,61 3,56 C5,48 7,40 9,33
          C14,29 18,24 20,18 C21,13 17,11 14,14 C12,16 11,18 10,20 C11,11 10,3 9,0`,
      i: `M-5,36 C-5,44 -4,50 -3,57 M-1,36 C-1,44 0,50 1,56 M3,35 C4,42 4,46 5,50 M10,20 C8,26 7,30 6,34`
    },
    thigh: {
      o: `M30,-26 C42,-8 48,20 46,46 C44,68 38,86 34,104 C30,118 26,130 24,142 C24,152 16,158 4,158
          C-8,158 -15,152 -17,144 C-20,128 -24,110 -30,88 C-37,62 -40,34 -40,8 C-40,-6 -38,-18 -36,-26 Z`,
      c: `M36,-12 C44,4 48,22 46,46 C44,68 38,86 34,104 C30,118 26,130 24,142 C24,152 16,158 4,158
          C-8,158 -15,152 -17,144 C-20,128 -24,110 -30,88 C-37,62 -40,34 -40,8 C-40,0 -39,-8 -38,-14`,
      i: bands([62, 70, 78, 86, 94, 102, 110, 118], (y) => [-38 + y * 0.17, 45 - y * 0.12]) +
         ` M8,128 C18,126 24,136 20,148 M6,136 C12,138 14,146 10,150`
    },
    shin: {
      o: `M18,-14 C21,8 20,44 16,78 C14,102 11,122 11,146 L-10,146 C-10,128 -11,116 -14,102
          C-19,84 -31,66 -35,42 C-38,20 -32,-2 -20,-12 C-9,-18 6,-18 18,-14 Z`,
      c: `M19,-2 C21,12 20,44 16,78 C14,102 11,122 11,140 M-10,140 C-10,128 -11,116 -14,102
          C-19,84 -31,66 -35,42 C-37,24 -34,8 -27,-4`,
      i: `M-30,8 C-16,4 4,4 18,6 M-14,104 C-6,108 4,108 14,104 ` +
         bands([18, 26, 34, 42, 50, 58, 66, 74, 82, 90], (y) => [-31 + Math.max(0, y - 50) * 0.33 - Math.max(0, 30 - y) * 0.1, 19 - y * 0.03]) +
         ` M-2,124 c-5,3 -5,10 0,12 c4,-2 5,-9 0,-12`
    },
    foot: {
      o: `M-11,-8 C-5,-12 7,-12 12,-7 C26,2 42,8 58,11 C68,13 77,15 79,20 C80,24 76,26 70,26 L8,26
          C-5,26 -17,26 -19,18 C-21,10 -17,-2 -11,-8 Z`,
      c: `M12,-4 C26,2 42,8 58,11 C68,13 77,15 79,20 C80,24 76,26 70,26 L8,26 C-5,26 -17,26 -19,18
          C-21,10 -18,0 -13,-5`,
      i: `M-16,21 L74,21 M-14,4 C-4,8 6,8 13,2 M18,4 C22,10 24,16 24,21 M34,8 C38,13 40,17 40,21
          M44,10 C30,14 18,16 6,12 M66,14 L68,21`
    }
  };

  const partSVG = (key, cls = '') => {
    const p = PARTS[key];
    let s = `<g class="${cls}">`;
    if (p.o) s += `<path class="o" d="${p.o}"/>`;
    s += `<path class="c" d="${p.c || p.o}"/>`;
    if (p.i) s += `<path class="i" d="${p.i}"/>`;
    if (p.pupil) s += `<circle class="o" cx="${p.pupil[0]}" cy="${p.pupil[1]}" r="2.4"/>`;
    return s + '</g>';
  };

  // ---------------------------------------------------------------- the stone
  function stoneTexture() {
    const r = rng(7), out = [];
    let tries = 0;
    while (out.length < 120 && tries++ < 4000) {
      const a = r() * Math.PI * 2, rr = Math.sqrt(r()) * (R - 20);
      const x = Math.cos(a) * rr, y = Math.sin(a) * rr;
      if (out.some((p) => Math.hypot(p[0] - x, p[1] - y) < 34)) continue;
      out.push([x, y]);
    }
    return out.map(([x, y]) => {
      const L = 10 + r() * 14, ang = r() * Math.PI * 2, bend = (r() < 0.5 ? -1 : 1) * (3 + r() * 4);
      const dx = Math.cos(ang) * L / 2, dy = Math.sin(ang) * L / 2;
      const cx = x - Math.sin(ang) * bend, cy = y + Math.cos(ang) * bend;
      return `M${(x - dx).toFixed(1)},${(y - dy).toFixed(1)} Q${cx.toFixed(1)},${cy.toFixed(1)} ${(x + dx).toFixed(1)},${(y + dy).toFixed(1)}`;
    }).join(' ');
  }

  // ---------------------------------------------------------------- the frame
  const BORDER = 26, TOP_BAND = [44, 124], BOT_BAND = 948;
  function meander() {
    const y0 = TOP_BAND[0] + 12, h = TOP_BAND[1] - TOP_BAND[0] - 24, u = h, s = h / 6;
    let d = '';
    for (let x = BORDER - u; x < W; x += u) {
      d += `M${x},${y0 + h} V${y0} H${x + u - s} V${y0 + h - s * 2} H${x + s * 2} V${y0 + s * 2} H${x + u - s * 3} V${y0 + h - s * 4} `;
    }
    return `<path class="key" d="M0,${y0 + h} H${W} ${d}"/>`;
  }
  function tongues() {
    let d = '';
    const base = BOT_BAND + 64, tip = BOT_BAND + 18, w = 36, gap = 54;
    for (let x = BORDER + 8; x < W - BORDER; x += gap) {
      d += `M${x},${base} C${x},${base - 28} ${x + w * 0.2},${tip + 10} ${x + w / 2},${tip} C${x + w * 0.8},${tip + 10} ${x + w},${base - 28} ${x + w},${base} Z `;
    }
    return `<path class="g" d="${d}"/>`;
  }
  function rays() {
    const r = rng(3);
    let d = '';
    for (let k = 0; k < 9; k++) {
      const x = 120 + k * 230 + r() * 80, y = 150 + r() * 160, L = 260 + r() * 260;
      d += `M${x.toFixed(0)},${y.toFixed(0)} l${(-L * 0.82).toFixed(0)},${(L * 0.57).toFixed(0)} `;
    }
    return `<path class="ray" d="${d}"/>`;
  }
  // cracks in the mountain: a repeating tile of jagged zig-zags, in ramp coordinates
  const CRACK_TILE = 640;
  function cracks() {
    const r = rng(11);
    let tile = '';
    for (let k = 0; k < 7; k++) {
      let x = r() * CRACK_TILE, y = 30 + r() * 140;
      tile += `M${x.toFixed(0)},${y.toFixed(0)}`;
      const n = 4 + Math.floor(r() * 4), dir = r() < 0.5 ? -1 : 1;
      for (let j = 0; j < n; j++) {
        x += dir * (10 + r() * 22); y += (j % 2 ? -1 : 1) * (8 + r() * 16) + 6;
        tile += ` L${x.toFixed(0)},${y.toFixed(0)}`;
      }
      tile += ' ';
    }
    let d = '';
    for (let o = -CRACK_TILE * 2; o < W + CRACK_TILE * 2; o += CRACK_TILE) d += `<path class="crack" transform="translate(${o} 0)" d="${tile}"/>`;
    return d;
  }

  // ---------------------------------------------------------------- styles
  const STYLE = `
    :host { position:absolute; inset:0; display:block; pointer-events:none; overflow:hidden;
      --g: var(--sisyphus-ground, #E8861C); --k: var(--sisyphus-ink, #0d0a08);
      opacity: var(--sisyphus-opacity, .12); contain: strict; }
    svg { width:100%; height:100%; display:block; }
    .bg { fill: var(--g); }
    .o, .k { fill: var(--k); }
    .g { fill: var(--g); }
    .c { fill: none; stroke: var(--g); stroke-width: 1.5; stroke-linejoin: round; stroke-linecap: round; }
    .i { fill: none; stroke: var(--g); stroke-width: 1.7; stroke-linejoin: round; stroke-linecap: round; }
    .key { fill: none; stroke: var(--k); stroke-width: 7; stroke-linejoin: miter; stroke-linecap: square; }
    .ray { fill: none; stroke: var(--k); stroke-width: 1.4; stroke-dasharray: 2 9; opacity: .32; stroke-linecap: round; }
    .crack { fill: none; stroke: var(--g); stroke-width: 2; stroke-linejoin: miter; }
    .tex { fill: none; stroke: var(--g); stroke-width: 2.4; stroke-linecap: round; }
    .rule { fill: none; stroke: var(--g); stroke-width: 2.2; }
  `;

  class SisyphusBackdrop extends HTMLElement {
    static get observedAttributes() { return ['opacity', 'ground', 'color', 'ink', 'paused', 'time', 'speed']; }

    constructor() {
      super();
      this.root = this.attachShadow({ mode: 'open' });
      const rampPoly = `M-200,${rampY(-200)} L${W + 200},${rampY(W + 200)} L${W + 200},${H_ + 50} L-200,${H_ + 50} Z`;
      this.root.innerHTML = `<style>${STYLE}</style>
        <svg viewBox="0 0 ${W} ${H_}" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
          <defs><clipPath id="ramp"><path d="${rampPoly}"/></clipPath></defs>
          <rect class="bg" x="-400" y="-400" width="${W + 800}" height="${H_ + 800}"/>
          ${rays()}
          <path class="k" d="${rampPoly}"/>
          <g clip-path="url(#ramp)"><g transform="translate(0 ${RAMP_Y0}) rotate(${-SLOPE / DEG})"><g class="cracks">${cracks()}</g></g></g>
          <g class="hero" transform="translate(${f2(ORIGIN[0])} ${f2(ORIGIN[1])}) scale(${K})">
            <g class="stonePos"><circle class="o" r="${R}"/><g class="stoneSpin"><path class="tex" d="${stoneTexture()}"/></g></g>
            ${partSVG('hand', 'farHand')}${partSVG('fore', 'farFore')}${partSVG('upper', 'farUpper')}
            ${partSVG('foot', 'farFoot')}${partSVG('shin', 'farShin')}${partSVG('thigh', 'farThigh')}
            ${partSVG('foot', 'nearFoot')}${partSVG('shin', 'nearShin')}${partSVG('thigh', 'nearThigh')}
            <g class="headG">${partSVG('hair')}${partSVG('head')}</g>
            ${partSVG('torso', 'torso')}${partSVG('skirt', 'skirt')}
            ${partSVG('hand', 'nearHand')}${partSVG('fore', 'nearFore')}${partSVG('upper', 'nearUpper')}
          </g>
          <rect class="k" x="0" y="0" width="${W}" height="${TOP_BAND[0]}"/>
          <rect class="k" x="0" y="${TOP_BAND[1]}" width="${W}" height="10"/>
          <rect class="g" x="0" y="${TOP_BAND[0]}" width="${W}" height="${TOP_BAND[1] - TOP_BAND[0]}"/>
          ${meander()}
          <rect class="k" x="0" y="${BOT_BAND}" width="${W}" height="${H_ - BOT_BAND}"/>
          <path class="rule" d="M0,${BOT_BAND + 8} H${W} M0,${BOT_BAND + 74} H${W}"/>
          ${tongues()}
          <rect class="k" x="0" y="0" width="${BORDER}" height="${H_}"/>
          <rect class="k" x="${W - BORDER}" y="0" width="${BORDER}" height="${H_}"/>
        </svg>`;
      const q = (s) => this.root.querySelector(s);
      this.el = { cracks: q('.cracks'), stonePos: q('.stonePos'), spin: q('.stoneSpin'), torso: q('.torso'), skirt: q('.skirt'), head: q('.headG') };
      for (const side of ['near', 'far'])
        for (const p of ['Upper', 'Fore', 'Hand', 'Thigh', 'Shin', 'Foot']) this.el[side + p] = q('.' + side + p);
      this._solveStone();
      this._t = 0; this._last = null; this._raf = null;
      this._tick = this._tick.bind(this);
      this._mq = window.matchMedia('(prefers-reduced-motion: reduce)');
      this._onMq = () => this._schedule();
      this._onVis = () => this._schedule();
    }

    // Rest the stone on the ramp so it just meets his bowed head; find where the palms land.
    _solveStone() {
      const pose = this._pose(0);
      const headPts = [[51, -69], [34, -100], [44, -24], [0, -114]].map((p) => add(pose.neck, rot(p, pose.beta * DEG)));
      const center = (s) => add(ground(s), mul(NORMAL, R));
      const gap = (s) => Math.min(...headPts.map((p) => len(sub(center(s), p)))) - R;
      let lo = 0, hi = 1400;
      for (let k = 0; k < 50; k++) { const m = (lo + hi) / 2; gap(m) > 2 ? (hi = m) : (lo = m); }
      this.C = center(hi);
      const C = this.C;
      const faceAng = Math.atan2(headPts[0][1] - C[1], headPts[0][0] - C[0]) / DEG + 360;
      const reach = (sh, frac, start) => {
        for (let a = start; a >= 140; a -= 0.5) {
          const w = add(C, mul([Math.cos(a * DEG), Math.sin(a * DEG)], R + 8));
          if (len(sub(w, sh)) >= (L_UPPER + L_FORE) * frac) return a;
        }
        return 190;
      };
      this.nearContact = reach(pose.shNear, 0.66, faceAng - 6);
      this.farContact = reach(pose.shFar, 0.7, faceAng + 2);
    }

    connectedCallback() {
      this._applyVars();
      this._mq.addEventListener?.('change', this._onMq);
      document.addEventListener('visibilitychange', this._onVis);
      this._render(this._fixedTime() ?? (this._mq.matches ? T * 0.12 : 0));
      this._schedule();
    }
    disconnectedCallback() {
      cancelAnimationFrame(this._raf); this._raf = null;
      this._mq.removeEventListener?.('change', this._onMq);
      document.removeEventListener('visibilitychange', this._onVis);
    }
    attributeChangedCallback() {
      if (!this.isConnected) return;
      this._applyVars();
      const ft = this._fixedTime();
      if (ft != null) this._render(ft);
      this._schedule();
    }
    _fixedTime() { const v = this.getAttribute('time'); return v == null || v === '' ? null : parseFloat(v); }
    _applyVars() {
      const s = this.style;
      const set = (v, val) => (val != null && val !== '' ? s.setProperty(v, val) : s.removeProperty(v));
      set('--sisyphus-opacity', this.getAttribute('opacity'));
      set('--sisyphus-ground', this.getAttribute('ground') ?? this.getAttribute('color'));
      set('--sisyphus-ink', this.getAttribute('ink'));
    }
    _schedule() {
      const still = this.hasAttribute('paused') || this._fixedTime() != null || this._mq.matches || document.hidden;
      if (still) {
        cancelAnimationFrame(this._raf); this._raf = null; this._last = null;
        if (this._mq.matches && this._fixedTime() == null) this._render(T * 0.12);
      } else if (!this._raf) {
        this._raf = requestAnimationFrame(this._tick);
      }
    }
    _tick(now) {
      const speed = parseFloat(this.getAttribute('speed') || '1');
      if (this._last != null) this._t += Math.min(0.1, (now - this._last) / 1000) * speed;
      this._last = now;
      this._render(this._t);
      this._raf = requestAnimationFrame(this._tick);
    }

    // ------------------------------------------------------------- pose
    // Gait time τ slows right after each foot lands (the re-brace), then surges (the shove).
    _gait(t) { return t - 0.5 * T / (4 * Math.PI) * Math.sin(4 * Math.PI * t / T); }
    _pose(t) {
      const tau = this._gait(t);
      const p = ((tau / T) % 1 + 1) % 1;
      const sg = -Math.cos(4 * Math.PI * t / T);               // +1 shove, -1 re-brace
      const V0 = STRIDE / (STANCE * T);
      const D = V0 * tau;
      const Hp = [3 + 6 * sg, HIP_Y + 5 * sg + 2.5 * Math.cos(4 * Math.PI * p)];
      const alpha = LEAN + 2.2 * sg;
      const legs = {};
      const leg = (phase, tauPlant, side) => {
        let toeS, lift = 0, heel;
        if (phase < STANCE) {
          toeS = S_PLANT - (D - V0 * tauPlant);
          heel = 6 + 48 * smooth((phase - 0.2) / (STANCE - 0.2));
        } else {
          const u = (phase - STANCE) / (1 - STANCE);
          toeS = lerp(S_PLANT - STRIDE, S_PLANT, easeInOut(Math.pow(u, 1.25)));
          lift = Math.sin(Math.PI * Math.pow(u, 1.3)) * 34;
          heel = lerp(54, 2, smooth(u * 1.2)) + 4 * Math.sin(Math.PI * u);
        }
        legs[side] = { toe: add(ground(toeS), mul(NORMAL, lift)), footRot: -SLOPE / DEG + heel };
      };
      const tc = Math.floor(tau / T) * T;
      leg(p, tc, 'near');
      leg((p + 0.5) % 1, p >= 0.5 ? tc + T * 0.5 : tc - T * 0.5, 'far');
      // ride up over the planted leg at mid-stance so he never sinks into a crouch
      let lift = 0;
      for (const [side, ph] of [['near', p], ['far', (p + 0.5) % 1]]) {
        if (ph >= STANCE) continue;
        const L = legs[side];
        const ank = sub(L.toe, rot(TOE, L.footRot * DEG));
        const dx = ank[0] - Hp[0], reach = 0.93 * (L_THIGH + L_SHIN);
        if (Math.abs(dx) < reach) lift = Math.min(lift, ank[1] - Math.sqrt(reach * reach - dx * dx) - Hp[1]);
      }
      Hp[1] += 0.9 * lift;
      const a = alpha * DEG;
      return {
        D, sg, H: Hp, alpha, legs,
        beta: 58 + 3 * sg,
        neck: add(Hp, rot([8, -168], a)),
        shNear: add(Hp, rot([8, -152], a)),
        shFar: add(Hp, rot([2, -156], a))
      };
    }

    _render(time) {
      const e = this.el;
      const P = this._pose(time);

      // ramp cracks slide down-left with the ground; the stone turns with it
      const off = -(P.D * K) % CRACK_TILE;
      e.cracks.setAttribute('transform', `translate(${f2(off)} 0)`);
      e.stonePos.setAttribute('transform', `translate(${f2(this.C[0])} ${f2(this.C[1])})`);
      e.spin.setAttribute('transform', `rotate(${f2((P.D / R) / DEG)})`);

      const H = P.H, a = P.alpha * DEG;
      e.torso.setAttribute('transform', tf(H, P.alpha));
      e.skirt.setAttribute('transform', tf(add(H, rot([0, -4], a)), P.alpha * 0.42));
      e.head.setAttribute('transform', tf(P.neck, P.beta));

      for (const side of ['near', 'far']) {
        const L = P.legs[side];
        const ankle = sub(L.toe, rot(TOE, L.footRot * DEG));
        const hip = side === 'far' ? add(H, [-3, -2]) : H;
        const { joint: knee, end } = ik(hip, ankle, L_THIGH, L_SHIN, -1);
        e[side + 'Thigh'].setAttribute('transform', tf(hip, hangAngle(sub(knee, hip)), 1, L_THIGH / 144));
        e[side + 'Shin'].setAttribute('transform', tf(knee, hangAngle(sub(end, knee)), 1, L_SHIN / 140));
        e[side + 'Foot'].setAttribute('transform', tf(end, L.footRot));
      }

      const C = this.C;
      const arm = (side, contactDeg, sh) => {
        const ca = contactDeg * DEG;
        const wrist = add(C, mul([Math.cos(ca), Math.sin(ca)], R + 8));
        const tip = add(C, mul([Math.cos(ca + 13 * DEG), Math.sin(ca + 13 * DEG)], R + 2));
        const { joint: elbow, end } = ik(sh, wrist, L_UPPER, L_FORE, 1);
        e[side + 'Upper'].setAttribute('transform', tf(sh, hangAngle(sub(elbow, sh)), 1, L_UPPER / 106));
        e[side + 'Fore'].setAttribute('transform', tf(elbow, hangAngle(sub(end, elbow)), 1, L_FORE / 92));
        e[side + 'Hand'].setAttribute('transform', tf(end, hangAngle(sub(tip, end))));
      };
      arm('far', this.farContact, P.shFar);
      arm('near', this.nearContact, P.shNear);
    }
  }

  if (!customElements.get('sisyphus-backdrop')) customElements.define('sisyphus-backdrop', SisyphusBackdrop);
})();
