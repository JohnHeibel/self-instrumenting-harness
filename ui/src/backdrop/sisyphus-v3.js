/* v3: <sisyphus-backdrop-v3 palette="original|muted|dark" opacity="0.12" paused phase="0.3">
 * v3: legs and greaves are new drawings generated per frame from an anatomical profile (no cut pieces);
 * torso, head, arms and chiton are the traced art, skinned as in v2.
 * v2: the traced figure is one continuous skinned body: every vertex is blended between bones
 * (pelvis, chest, head, hair, tunic, thighs, shins, forearm), so contours bend and squash instead of
 * rotating as paper pieces; tunic and hair follow with lag (overlapping action), thighs bulge under load.
 * Torso, arms and head keep the original pushing pose; the legs take real alternating steps up the
 * ramp under him (the rear foot swings past and plants higher while the other leg drives back), and
 * the ground and boulder advance in sync with the planted feet.
 * Attributes: palette, opacity (0..1, default 1), paused (boolean), phase (0..1 freezes the cycle).
 * `flat`: the same figure re-posed on level ground: no slope (the soles plant flat on a horizontal line at the soles'
 * height), the boulder set down on that line where his hands still meet it, and no ground, cracks, rays or bands drawn.
 * --sisy-ink-stroke / --sisy-ink-stroke-w stroke the silhouettes, for line-art styles.
 */
(function () {
  const NS = 'http://www.w3.org/2000/svg';
  const PALETTES = {
    original: { ground: '#eb8023', figure: '#090808', line: '#d8916c', mottle: '#dc7420', ray: '#3a1d08' },
    muted:    { ground: '#b8724a', figure: '#1c1613', line: '#c9967a', mottle: '#ad6941', ray: '#3b2416' },
    marble:   { ground: '#e8e6e1', figure: '#24272b', line: '#dedbd4', mottle: '#cfccc5', ray: '#8d939a' },
    dark:     { ground: '#14100e', figure: '#45332a', line: '#c97646', mottle: '#18130f', ray: '#8a5232' },
  };

  // ---- motion constants (art px = reference image px) ----
  const CYCLE = 7.0;          // s, one step with each foot
  const DUTY = 0.6;           // fraction of the cycle a foot is planted
  const SURGE = 0.35;         // ground speed swells after each plant (weighty push)
  const LIFT = 78;            // swing clearance
  const SLOPE_DEG = 38;        // one straight slope: under his feet, under the boulder, up and away
  const WORLD_SCALE = 0.95;    // figure+boulder+slope scale
  const WORLD_PIVOT = [600, 92];
  const STEP_AHEAD = 50;       // front plant, along the slope from the point under the hip
  const STEP_BACK = 165;       // lift-off point of the driving (rear) foot
  const ROT_FLAT = -SLOPE_DEG; // sole flat on the slope (deg)
  const ROT_TOE = ROT_FLAT + 40; // heel-up at toe-off
  // flat: level ground 212 art px below the hip (the legs' reach on the slope's lower side, so he strides rather than
  // crouches); the boulder (drawn centred at 597,347) sits on it with its centre as far from the hands as on the slope
  const FLAT_C = [637, 653];
  const HEAVE_ROT = 2.0;
  const HIP_SURGE = 5;          // hip pushes toward the stone with each heave
  const HIP_BOB = 8;            // hip drops into each plant, rises as the legs pass
  const CHEST_DRIVE = 2.6;      // deg, shoulders drive into the stone on the push
  const HAIR_FOLLOW = 1.0;      // hair lag gain
  const KNEE_SHRINK = 0.75;    // kneecap overhang shrinks as the leg straightens
  const KNEE_FOLLOW = 0.8;     // how far the kneecap turns with the shin as the knee opens
  const CHITON_FOLLOW = 0.2;  // how much of each thigh's swing the tunic takes      // deg, torso into the stone, twice per cycle
  const CRACK_PERIOD = 720;
  const FACE_ANGLE = 42;      // deg, slope of the mountain face behind the boulder
  const K = 1080 / 1024;      // scene scale (panel fills the height)
  const OX = (1920 - 992 * K) / 2;
  const D2R = Math.PI / 180;

  const easeIO = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
  const smooth = (t) => t * t * (3 - 2 * t);
  const clamp01 = (t) => Math.max(0, Math.min(1, t));

  function pathsGroup(spec, cls, scale) {
    const g = document.createElementNS(NS, 'g');
    if (cls) g.setAttribute('class', cls);
    g.setAttribute('transform', `scale(${scale})`);
    for (const [d, tx, ty] of spec) {
      const p = document.createElementNS(NS, 'path');
      p.setAttribute('d', d);
      p.setAttribute('transform', `translate(${tx},${ty})`);
      g.appendChild(p);
    }
    return g;
  }
  let _olId = 0;
  function partEl(name, data, halo, outline) {
    const g = document.createElementNS(NS, 'g');
    const P = data.parts[name];
    if (outline) {
      // incised contour on the inside edge of the silhouette (stroke clipped to the part itself)
      const id = 'ol' + (_olId++);
      const cp = document.createElementNS(NS, 'clipPath');
      cp.setAttribute('id', id);
      for (const [d, tx, ty] of P.black) {
        const cpp = document.createElementNS(NS, 'path');
        cpp.setAttribute('d', d);
        cpp.setAttribute('transform', `scale(${data.scale}) translate(${tx},${ty})`);
        cp.appendChild(cpp);
      }
      g.appendChild(cp);
      g.appendChild(pathsGroup(P.black, 'ink', data.scale));
      if (P.line.length) g.appendChild(pathsGroup(P.line, 'inc', data.scale));
      const wrap = document.createElementNS(NS, 'g');
      wrap.setAttribute('clip-path', `url(#${id})`);
      wrap.appendChild(pathsGroup(P.black, 'outl', data.scale));
      g.appendChild(wrap);
      return g;
    }
    g.appendChild(pathsGroup(P.black, 'ink', data.scale));
    if (P.line.length) g.appendChild(pathsGroup(P.line, 'inc', data.scale));
    return g;
  }


  // ---- skinned parts: traced paths re-built every frame from per-point weights ----
  function parsePaths(spec, scale) {
    const out = [];
    for (const [d, tx, ty] of spec) {
      const cmds = [], xy = [];
      const tok = d.match(/[MCZ]|-?\d*\.?\d+(?:e-?\d+)?/g);
      let i = 0, c = 'M';
      while (i < tok.length) {
        const t = tok[i];
        if (t === 'M' || t === 'C') { c = t; i++; continue; }
        if (t === 'Z') { cmds.push('Z'); i++; continue; }
        const n = c === 'M' ? 1 : 3;
        cmds.push(c);
        for (let k = 0; k < n; k++) { xy.push((parseFloat(tok[i]) + tx) * scale, (parseFloat(tok[i + 1]) + ty) * scale); i += 2; }
      }
      out.push({ cmds, xy: Float64Array.from(xy) });
    }
    return out;
  }
  class Skin {
    constructor(parent, part, scale, halo, weightFn, nw) {
      this.paths = [];
      const mkp = (cls) => { const e = document.createElementNS(NS, 'path'); e.setAttribute('class', cls); return e; };
      const layers = [['black', 'ink'], ['line', 'inc']];
      const haloG = halo ? halo.appendChild(document.createElementNS(NS, 'g')) : null;
      for (const [layer, cls] of layers) {
        for (const P of parsePaths(part[layer] || [], scale)) {
          const n = P.xy.length / 2, w = new Float32Array(n * nw);
          for (let j = 0; j < n; j++) { const ww = weightFn(P.xy[2 * j], P.xy[2 * j + 1]); for (let k = 0; k < nw; k++) w[j * nw + k] = ww[k]; }
          const el = parent.appendChild(mkp(cls));
          const h = halo && layer === 'black' ? haloG.appendChild(mkp('halo')) : null;
          this.paths.push({ ...P, w, el, h, out: new Float64Array(P.xy.length) });
        }
      }
      this.nw = nw;
    }
    update(fn) {
      const nw = this.nw, r = [0, 0];
      for (const P of this.paths) {
        const xy = P.xy, o = P.out, n = xy.length / 2;
        for (let j = 0; j < n; j++) { fn(xy[2 * j], xy[2 * j + 1], P.w, j * nw, r); o[2 * j] = r[0]; o[2 * j + 1] = r[1]; }
        let d = '', k = 0;
        for (const c of P.cmds) {
          if (c === 'Z') { d += 'Z'; continue; }
          const m = c === 'M' ? 1 : 3;
          d += c;
          for (let q = 0; q < m; q++) { d += o[k].toFixed(1) + ' ' + o[k + 1].toFixed(1) + ' '; k += 2; }
        }
        P.el.setAttribute('d', d);
        if (P.h) P.h.setAttribute('d', d);
      }
    }
  }

  // affine bone: rest frame (origin, angle) -> world frame (origin, angle), optional squash across the bone
  function boneMat(or, ar, ow, aw, sPerp = 1, sAlong = 1) {
    const cr = Math.cos(ar), sr = Math.sin(ar), cw = Math.cos(aw), sw = Math.sin(aw);
    const a = sAlong * cw * cr + sPerp * sw * sr, c = sAlong * cw * sr - sPerp * sw * cr;
    const b = sAlong * sw * cr - sPerp * cw * sr, d = sAlong * sw * sr + sPerp * cw * cr;
    return [a, b, c, d, ow[0] - (a * or[0] + c * or[1]), ow[1] - (b * or[0] + d * or[1])];
  }
  const apply = (m, p) => [m[0] * p[0] + m[2] * p[1] + m[4], m[1] * p[0] + m[3] * p[1] + m[5]];
  class Skin2 {
    // weightFn(x, y) -> array of nb weights (sum 1) plus optional extra channel at index nb
    constructor(parent, part, scale, haloLayer, nb, weightFn, pre) {
      this.nb = nb; this.paths = [];
      const mkp = (cls) => { const e = document.createElementNS(NS, 'path'); e.setAttribute('class', cls); return e; };
      const haloG = haloLayer ? haloLayer.appendChild(document.createElementNS(NS, 'g')) : null;
      const st = nb + 1;
      for (const [layer, cls] of [['black', 'ink'], ['line', 'inc']]) {
        for (const P of parsePaths(part[layer] || [], scale)) {
          if (pre) for (let j = 0; j < P.xy.length; j += 2) { const q = pre(P.xy[j], P.xy[j + 1]); P.xy[j] = q[0]; P.xy[j + 1] = q[1]; }
          const n = P.xy.length / 2, w = new Float32Array(n * st);
          for (let j = 0; j < n; j++) { const ww = weightFn(P.xy[2 * j], P.xy[2 * j + 1]); for (let k = 0; k < st; k++) w[j * st + k] = ww[k] || 0; }
          const el = parent.appendChild(mkp(cls));
          const h = haloG && layer === 'black' ? haloG.appendChild(mkp('halo')) : null;
          this.paths.push({ ...P, w, el, h, out: new Float64Array(P.xy.length) });
        }
      }
    }
    // mats: nb affine matrices; post(x, y, extraWeight, r) optional
    update(mats, post) {
      const nb = this.nb, st = nb + 1, r = [0, 0];
      for (const P of this.paths) {
        const xy = P.xy, o = P.out, n = xy.length / 2, w = P.w;
        for (let j = 0; j < n; j++) {
          const x = xy[2 * j], y = xy[2 * j + 1];
          let X = 0, Y = 0;
          for (let k = 0; k < nb; k++) {
            const wk = w[j * st + k]; if (!wk) continue;
            const m = mats[k];
            X += wk * (m[0] * x + m[2] * y + m[4]); Y += wk * (m[1] * x + m[3] * y + m[5]);
          }
          if (post) { r[0] = X; r[1] = Y; post(X, Y, w[j * st + nb], r); X = r[0]; Y = r[1]; }
          o[2 * j] = X; o[2 * j + 1] = Y;
        }
        let d = '', k = 0;
        for (const c of P.cmds) {
          if (c === 'Z') { d += 'Z'; continue; }
          const m = c === 'M' ? 1 : 3;
          d += c;
          for (let q = 0; q < m; q++) { d += o[k].toFixed(2) + ' ' + o[k + 1].toFixed(2) + ' '; k += 2; }
        }
        P.el.setAttribute('d', d);
        if (P.h) P.h.setAttribute('d', d);
      }
    }
  }

  // ---------- drawn legs ----------
  // closed Catmull-Rom curve through pts -> smooth cubic path
  function crPath(pts, closed = true, k = 1) {
    const n = pts.length; if (n < 2) return '';
    const P = (i) => closed ? pts[(i + n) % n] : pts[Math.max(0, Math.min(n - 1, i))];
    let d = `M${pts[0][0].toFixed(2)} ${pts[0][1].toFixed(2)}`;
    const last = closed ? n : n - 1;
    for (let i = 0; i < last; i++) {
      const p0 = P(i - 1), p1 = P(i), p2 = P(i + 1), p3 = P(i + 2);
      const c1 = [p1[0] + (p2[0] - p0[0]) * k / 6, p1[1] + (p2[1] - p0[1]) * k / 6];
      const c2 = [p2[0] - (p3[0] - p1[0]) * k / 6, p2[1] - (p3[1] - p1[1]) * k / 6];
      d += ` C${c1[0].toFixed(2)} ${c1[1].toFixed(2)} ${c2[0].toFixed(2)} ${c2[1].toFixed(2)} ${p2[0].toFixed(2)} ${p2[1].toFixed(2)}`;
    }
    return closed ? d + 'Z' : d;
  }
  const gauss = (x, m, w) => Math.exp(-((x - m) / w) * ((x - m) / w));
  // half-widths in art px (anterior = front of the leg, the side the knee points to)
  const THIGH_A = (s, load) => 29 + 6 * Math.sin(Math.PI * Math.min(1, Math.max(0, s))) * (1 + 0.5 * load) + 4 * gauss(s, 0.93, 0.07);
  const THIGH_P = (s, load) => 35 - 9 * s + 5 * Math.sin(Math.PI * Math.min(1, Math.max(0, s))) * (1 + 0.4 * load);
  const SHIN_A = (u) => 23 - 9 * u;
  const SHIN_P = (u, load) => 14 + (21 + 4 * load) * gauss(u, 0.28, 0.25) - 3 * u;
  class DrawnLeg {
    constructor(parent, scale, opts) {
      this.o = opts;
      const mk = (cls, tag = 'path') => { const e = document.createElementNS(NS, tag); e.setAttribute('class', cls); parent.appendChild(e); return e; };
      this.thigh = mk('ink'); this.shin = mk('ink');
      this.lines = mk('incs');
    }
    // H hip, K knee, A ankle (world), side = +-1 (anterior normal sign), load 0..1, w width scale
    draw(H, K, A, side, load, w) {
      const d1 = [K[0] - H[0], K[1] - H[1]], L1 = Math.hypot(...d1); d1[0] /= L1; d1[1] /= L1;
      const d2 = [A[0] - K[0], A[1] - K[1]], L2 = Math.hypot(...d2); d2[0] /= L2; d2[1] /= L2;
      const n1 = [-d1[1] * side, d1[0] * side], n2 = [-d2[1] * side, d2[0] * side];
      const at = (O, d, n, L, s, off) => [O[0] + d[0] * L * s + n[0] * off, O[1] + d[1] * L * s + n[1] * off];
      // thigh: front contour down to the kneecap, rounded end around the knee, back contour up
      const T = [];
      for (let i = 0; i <= 9; i++) { const s = -0.25 + i * 0.125; T.push(at(H, d1, n1, L1, s, THIGH_A(s, load) * w)); }
      const rk = (THIGH_A(1, load) + THIGH_P(1, load)) * 0.5 * w, kc = at(H, d1, n1, L1, 1, (THIGH_A(1, load) - THIGH_P(1, load)) * 0.5 * w);
      const a0 = Math.atan2(n1[1], n1[0]);
      for (let i = 1; i < 6; i++) { const t = a0 - side * Math.PI * i / 6; T.push([kc[0] + rk * Math.cos(t) + d1[0] * rk * 0.35 * Math.sin(Math.PI * i / 6), kc[1] + rk * Math.sin(t) + d1[1] * rk * 0.35 * Math.sin(Math.PI * i / 6)]); }
      for (let i = 9; i >= 0; i--) { const s = -0.25 + i * 0.125; T.push(at(H, d1, n1, L1, s, -THIGH_P(s, load) * w)); }
      this.thigh.setAttribute('d', crPath(T));
      // shin: rounded top at the knee, shin-bone front, calf behind, narrow ankle
      const S = [];
      const rt = (SHIN_A(0) + SHIN_P(0, load)) * 0.5 * w, tc = at(K, d2, n2, L2, 0.02, (SHIN_A(0) - SHIN_P(0, load)) * 0.5 * w);
      const b0 = Math.atan2(-n2[1], -n2[0]);
      for (let i = 1; i < 6; i++) { const t = b0 - side * Math.PI * i / 6; S.push([tc[0] + rt * Math.cos(t) - d2[0] * rt * 0.3 * Math.sin(Math.PI * i / 6), tc[1] + rt * Math.sin(t) - d2[1] * rt * 0.3 * Math.sin(Math.PI * i / 6)]); }
      for (let i = 1; i <= 10; i++) { const u = i * 0.105; S.push(at(K, d2, n2, L2, u, SHIN_A(Math.min(1, u)) * w)); }
      for (let i = 10; i >= 1; i--) { const u = i * 0.105; S.push(at(K, d2, n2, L2, u, -SHIN_P(Math.min(1, u), load) * w)); }
      this.shin.setAttribute('d', crPath(S));
      // incised lines: greave top band, wrapping greave stripes, kneecap, thigh muscle, knee crease
      let L = '';
      const strip = (u, bow) => {
        const pa = at(K, d2, n2, L2, u + 0.03, (SHIN_A(u) - 3.2) * w), pp = at(K, d2, n2, L2, u - 0.03, -(SHIN_P(u, load) - 3.2) * w);
        const m = [(pa[0] + pp[0]) / 2 + d2[0] * bow, (pa[1] + pp[1]) / 2 + d2[1] * bow];
        return `M${pa[0].toFixed(2)} ${pa[1].toFixed(2)} Q${m[0].toFixed(2)} ${m[1].toFixed(2)} ${pp[0].toFixed(2)} ${pp[1].toFixed(2)}`;
      };
      L += strip(0.17, 3);
      for (let u = 0.27; u <= 0.93; u += 0.073) L += ' ' + strip(u, 6);
      // kneecap: an arc inside the front of the knee
      const bend = Math.acos(Math.max(-1, Math.min(1, d1[0] * d2[0] + d1[1] * d2[1])));
      const kp = at(H, d1, n1, L1, 0.9, THIGH_A(0.9, load) * w * 0.32);
      const rr = 12 * w, ang0 = Math.atan2(n1[1], n1[0]);
      const arc = [];
      for (let i = 0; i <= 6; i++) { const t = ang0 - side * (-0.9 + 1.9 * i / 6); arc.push([kp[0] + rr * Math.cos(t) + d1[0] * 6 * Math.sin(Math.PI * i / 6), kp[1] + rr * Math.sin(t) + d1[1] * 6 * Math.sin(Math.PI * i / 6)]); }
      L += ' ' + crPath(arc, false);
      // thigh muscle line (vastus), sweeping toward the knee
      const mus = [at(H, d1, n1, L1, 0.18, THIGH_A(0.18, load) * w * 0.15), at(H, d1, n1, L1, 0.45, THIGH_A(0.45, load) * w * 0.32), at(H, d1, n1, L1, 0.7, THIGH_A(0.7, load) * w * 0.3)];
      L += ' ' + crPath(mus, false);
      const ham = [at(H, d1, n1, L1, 0.3, -THIGH_P(0.3, load) * w * 0.45), at(H, d1, n1, L1, 0.55, -THIGH_P(0.55, load) * w * 0.5), at(H, d1, n1, L1, 0.8, -THIGH_P(0.8, load) * w * 0.42)];
      L += ' ' + crPath(ham, false);
      // knee crease grows as the knee folds
      const fold = Math.max(0, Math.min(1, (bend - 0.9) / 1.2));
      if (fold > 0.05) {
        const kb = at(H, d1, n1, L1, 0.97, -THIGH_P(0.97, load) * w * 0.55);
        const c1 = [kb[0] - d1[0] * 10 * fold, kb[1] - d1[1] * 10 * fold], c2 = [kb[0] + d2[0] * 12 * fold, kb[1] + d2[1] * 12 * fold];
        L += ` M${c1[0].toFixed(2)} ${c1[1].toFixed(2)} Q${kb[0].toFixed(2)} ${kb[1].toFixed(2)} ${c2[0].toFixed(2)} ${c2[1].toFixed(2)}`;
      }
      this.lines.setAttribute('d', L);
      return { thigh: this.thigh.getAttribute('d'), shin: this.shin.getAttribute('d') };
    }
  }

  // ---------- drawn sandal: heel part follows the foot angle, toe part stays flat while planted ----------
  class DrawnFoot {
    constructor(parent, defs, id) {
      const mk = (el, cls, tag = 'path') => { const e = document.createElementNS(NS, tag); if (cls) e.setAttribute('class', cls); el.appendChild(e); return e; };
      this.ink = mk(parent, 'ink');
      const cp = document.createElementNS(NS, 'clipPath'); cp.setAttribute('id', id); cp.setAttribute('clipPathUnits', 'userSpaceOnUse'); defs.appendChild(cp);
      this.cp = mk(cp, null);
      const g = document.createElementNS(NS, 'g'); g.setAttribute('clip-path', `url(#${id})`); parent.appendChild(g);
      this.outl = mk(g, 'sep');
      this.lines = mk(parent, 'incs');
    }
    // A ankle (world), ankleRest, rot (deg) heel part, toeRot (deg) toe part
    draw(A, AR, rot, toeRot) {
      const r = rot * Math.PI / 180, c = Math.cos(r), s = Math.sin(r);
      const F = (x, y) => [A[0] + (x - AR[0]) * c - (y - AR[1]) * s, A[1] + (x - AR[0]) * s + (y - AR[1]) * c];
      const ball = F(474, 842);
      const t2 = toeRot * Math.PI / 180, c2 = Math.cos(t2), s2 = Math.sin(t2);
      const G = (x, y) => [ball[0] + (x - 474) * c2 - (y - 842) * s2, ball[1] + (x - 474) * s2 + (y - 842) * c2];
      const pts = [F(430, 796), F(452, 806), F(468, 824), G(486, 829), G(500, 834), G(506, 840), G(500, 846), G(484, 846), F(462, 846), F(430, 846), F(410, 844), F(403, 836), F(408, 822), F(418, 806)];
      const d = crPath(pts);
      this.ink.setAttribute('d', d); this.cp.setAttribute('d', d); this.outl.setAttribute('d', d);
      const seg = (p, q) => `M${p[0].toFixed(2)} ${p[1].toFixed(2)} L${q[0].toFixed(2)} ${q[1].toFixed(2)}`;
      let L = crPath([F(412, 839), F(440, 840.5), F(474, 840), G(490, 839.5), G(500, 838)], false);   // sole
      L += ' ' + crPath([F(444, 809), F(452, 820), F(457, 834)], false);                                // straps
      L += ' ' + crPath([F(458, 815), F(466, 826), F(470, 836)], false);
      L += ' ' + crPath([F(424, 806), F(428, 822), F(424, 836)], false);                                // heel strap
      this.lines.setAttribute('d', L);
    }
  }

  // ---------- drawn chiton: belt on the pelvis, hem and folds driven by the thighs with lag ----------
  class DrawnTunic {
    constructor(parent) {
      const mk = (cls, par = parent) => { const e = document.createElementNS(NS, 'path'); e.setAttribute('class', cls); par.appendChild(e); return e; };
      this.ink = mk('ink');
      const id = 'tunclip' + Math.random().toString(36).slice(2, 7);
      const defs = parent.ownerSVGElement.querySelector('defs');
      const cp = document.createElementNS(NS, 'clipPath'); cp.setAttribute('id', id); cp.setAttribute('clipPathUnits', 'userSpaceOnUse'); defs.appendChild(cp);
      this.cp = mk(null, cp);
      const g = document.createElementNS(NS, 'g'); g.setAttribute('clip-path', `url(#${id})`); parent.appendChild(g);
      this.lines = mk('incs', g);
    }
    // M: pelvis matrix (rest -> world); hip world pivot; fr/bk: front/back swing angles (rad); sway: hem flutter
    draw(M, hipW, fr, bk, sway) {
      const P = (x, y) => apply(M, [x, y]);
      const rot = (p, ang) => { const c = Math.cos(ang), s = Math.sin(ang); return [hipW[0] + (p[0] - hipW[0]) * c - (p[1] - hipW[1]) * s, hipW[1] + (p[0] - hipW[0]) * s + (p[1] - hipW[1]) * c]; };
      // weights: how much each contour point follows the front (near thigh) or back (far thigh) swing
      const pt = (x, y, wf, wb) => rot(P(x, y), wf * fr + wb * bk + sway * (wf + wb) * 0.5);
      const belt = [pt(392, 638, 0, 0), pt(340, 644, 0, 0), pt(292, 648, 0, 0)];
      const back = [pt(282, 670, 0, 0.25), pt(266, 696, 0, 0.55), pt(256, 722, 0, 0.8), pt(256, 744, 0.1, 1)];
      const hem = [pt(268, 756, 0.2, 0.8), pt(292, 762, 0.4, 0.6), pt(320, 758, 0.6, 0.4), pt(344, 746, 0.8, 0.2)];
      const front = [pt(364, 736, 1, 0), pt(384, 712, 0.9, 0), pt(396, 684, 0.6, 0), pt(398, 658, 0.2, 0)];
      const outline = [...belt, ...back, ...hem, ...front];
      const od = crPath(outline); this.ink.setAttribute('d', od); this.cp.setAttribute('d', od);
      // folds: curved bands across the skirt, like the original: each runs from the back edge to the
      // front edge at the same height fraction and sags toward the hem (the sag follows the swing)
      const lerpEdge = (arr, t) => { const f = t * (arr.length - 1), i = Math.min(arr.length - 2, Math.floor(f)), k = f - i; return [arr[i][0] + (arr[i + 1][0] - arr[i][0]) * k, arr[i][1] + (arr[i + 1][1] - arr[i][1]) * k]; };
      const frontDown = [belt[0], front[3], front[2], front[1], front[0], hem[3]];
      const backDown = [belt[2], ...back, hem[0], hem[1]];
      let L = crPath([pt(296, 652, 0, 0), pt(340, 649, 0, 0), pt(388, 642, 0, 0)], false);   // belt
      const JIT = [0.11, 0.24, 0.355, 0.5, 0.61, 0.745, 0.86];
      const SAGV = [1.15, 0.8, 1.05, 0.9, 1.2, 0.85, 1.0];
      for (let k = 1; k <= 7; k++) {
        const t = JIT[k - 1];
        const b0 = lerpEdge(backDown, Math.min(1, t * 1.02)), f0 = lerpEdge(frontDown, Math.min(1, t * 0.98));
        const ins = (p, q, a) => [p[0] + (q[0] - p[0]) * a, p[1] + (q[1] - p[1]) * a];
        const a1 = ins(b0, f0, 0.04), a2 = ins(f0, b0, 0.06);
        const mid = ins(a1, a2, 0.4);
        const vx = a2[0] - a1[0], vy = a2[1] - a1[1], vl = Math.hypot(vx, vy) || 1;
        let nx = -vy / vl, ny = vx / vl; if (ny < 0) { nx = -nx; ny = -ny; }   // toward the hem
        const sagAmt = SAGV[k - 1] * vl * (0.2 + 0.1 * t) + 30 * sway + 0.06 * vl * (fr - bk);
        const c = [mid[0] + nx * sagAmt, mid[1] + ny * sagAmt];
        // stop short of the front edge so the bands read as folds tucked into the front panel
        const e2 = ins(a1, a2, 0.88);
        L += ` M${a1[0].toFixed(2)} ${a1[1].toFixed(2)} Q${c[0].toFixed(2)} ${c[1].toFixed(2)} ${e2[0].toFixed(2)} ${e2[1].toFixed(2)}`;
      }
      // the gathered fold falling from the belt knot at the front
      const k0 = pt(372, 648, 0.1, 0), k1 = pt(362, 690, 0.6, 0), k2 = pt(350, 738, 0.85, 0.1);
      L += ' ' + crPath([k0, k1, k2], false);
      this.lines.setAttribute('d', L);
    }
  }
  const segDist = (px, py, a, b) => {
    const vx = b[0] - a[0], vy = b[1] - a[1];
    const t = Math.max(0, Math.min(1, ((px - a[0]) * vx + (py - a[1]) * vy) / (vx * vx + vy * vy)));
    return Math.hypot(px - a[0] - t * vx, py - a[1] - t * vy);
  };

  class SisyphusBackdrop extends HTMLElement {
    static get observedAttributes() { return ['palette', 'opacity', 'paused', 'phase']; }
    constructor() { super(); this.attachShadow({ mode: 'open' }); this._t0 = performance.now(); }
    connectedCallback() {
      if (!this._built) this._build();
      this._applyStyle();
      this._reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
      this._loop = this._loop.bind(this);
      cancelAnimationFrame(this._raf);
      this._raf = requestAnimationFrame(this._loop);
    }
    disconnectedCallback() { cancelAnimationFrame(this._raf); }
    attributeChangedCallback() { if (this._built) { this._applyStyle(); this._render(this._time()); } }

    _applyStyle() {
      const pal = PALETTES[this.getAttribute('palette')] || PALETTES.original;
      const op = parseFloat(this.getAttribute('opacity'));
      const s = this.style;
      s.setProperty('--sisy-ground', pal.ground);
      s.setProperty('--sisy-figure', pal.figure);
      s.setProperty('--sisy-line', pal.line);
      s.setProperty('--sisy-mottle', pal.mottle);
      s.setProperty('--sisy-ray', pal.ray);
      s.setProperty('--sisy-opacity', isNaN(op) ? 1 : op);
    }

    _build() {
      const data = window.SISY_PARTS;
      if (!data) { this.shadowRoot.textContent = 'parts.js not loaded'; return; }
      this._built = true;
      this._data = data;
      const J = (this._J = data.joints);
      this.shadowRoot.innerHTML = `<style>
        :host{display:block;position:relative;overflow:hidden;contain:strict} :host([bands="none"]) .bands{display:none}
        svg{position:absolute;inset:0;width:100%;height:100%;display:block;opacity:var(--sisy-opacity,1);transition:opacity .6s}
        .ground{fill:var(--sisy-ground)} .ink{fill:var(--sisy-figure);stroke:var(--sisy-ink-stroke,none);stroke-width:var(--sisy-ink-stroke-w,0);stroke-linejoin:round} .inc{fill:var(--sisy-line)}
        .band{fill:var(--sisy-figure)} .bandbg{fill:var(--sisy-ground)}
        .crack .inc{fill:var(--sisy-line)}
        .rayd{fill:var(--sisy-ray)} .rayl{stroke:var(--sisy-ray);stroke-width:1.7;stroke-dasharray:11 7;fill:none}
        .mottle{fill:var(--sisy-mottle)} .incs{fill:none;stroke:var(--sisy-line);stroke-width:2.7;stroke-linecap:round;stroke-linejoin:round} .sep{fill:none;stroke:var(--sisy-line);stroke-width:5.2;stroke-linejoin:round} .outl{fill:none;stroke:var(--sisy-line);stroke-width:21;stroke-linejoin:round} .halo{fill:none;stroke:var(--sisy-halo,var(--sisy-ground));stroke-width:11;stroke-linejoin:round} .cap{stroke:var(--sisy-figure);stroke-linecap:round;fill:none}
      </style>`;
      const svg = document.createElementNS(NS, 'svg');
      svg.setAttribute('viewBox', '0 0 1920 1080');
      svg.setAttribute('preserveAspectRatio', 'xMidYMid slice');
      svg.setAttribute('aria-hidden', 'true');
      svg.innerHTML = `<defs>
        <filter id="mot" x="0" y="0" width="100%" height="100%">
          <feTurbulence type="fractalNoise" baseFrequency="0.019" numOctaves="3" seed="7"/>
          <feColorMatrix type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 -2.4 1.38"/>
          <feComponentTransfer><feFuncA type="discrete" tableValues="0 0 0 0.75 0.9"/></feComponentTransfer>
          <feComposite in="SourceGraphic" operator="in"/>
        </filter></defs>`;
      this.shadowRoot.appendChild(svg);
      const add = (parent, tag, attrs) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); parent.appendChild(e); return e; };
      const defs = svg.querySelector('defs');

      add(svg, 'rect', { class: 'ground', x: 0, y: 0, width: 1920, height: 1080 });
      add(svg, 'rect', { class: 'mottle', x: 0, y: 80, width: 1920, height: 870, filter: 'url(#mot)', opacity: 0.55 });

      const bandsG = add(svg, 'g', { class: 'bands' });   // vase bands sit under the scene so feet can stand at the base
      let scene = add(svg, 'g', { transform: `translate(${OX},0) scale(${K})` });
      let xL = -OX / K - 2, xR = (1920 - OX) / K + 2;

      // sun rays: the traced dashes inside the panel, the same lines continued outside it
      const P0 = 9, P1 = 986;
      const cl = add(defs, 'clipPath', { id: 'outside', clipPathUnits: 'userSpaceOnUse' });
      add(cl, 'rect', { x: xL, y: 83, width: P0 - xL, height: 800 });
      add(cl, 'rect', { x: P1, y: 83, width: xR - P1, height: 800 });
      const rayExt = add(scene, 'g', { 'clip-path': 'url(#outside)', opacity: 0.8 });
      const seen = [];
      for (const [k, b] of data.parts.rays.fits) {
        if (seen.some(([k2, b2]) => Math.abs(k - k2) < 0.03 && Math.abs(b - b2) < 6)) continue;
        seen.push([k, b]);
        add(rayExt, 'line', { class: 'rayl', x1: xL, y1: k * xL + b, x2: xR, y2: k * xR + b });
      }
      scene.appendChild(pathsGroup(data.parts.rays.black, 'rayd', data.scale));

      // world group: figure, boulder and slope, scaled a little about a point under the top band
      // so the slope has room below his hip for the full driving stride
      const [PVX, PVY] = WORLD_PIVOT;
      scene = add(scene, 'g', { transform: `translate(${PVX},${PVY}) scale(${WORLD_SCALE}) translate(${-PVX},${-PVY})` });
      xL = PVX + (xL - PVX) / WORLD_SCALE; xR = PVX + (xR - PVX) / WORLD_SCALE;
      // the slope: one straight line at SLOPE_DEG, tangent to the boulder, from the base band up past the edge
      const M = data.parts.mountain;
      const flat = this.hasAttribute('flat');
      this._flat = flat;
      this._rotFlat = flat ? 0 : ROT_FLAT; this._rotToe = this._rotFlat + 40;
      const th = (flat ? 0 : SLOPE_DEG) * D2R;
      this._u = [Math.cos(th), -Math.sin(th)];           // up the slope
      this._n = [Math.sin(th), Math.cos(th)];            // into the ground
      const Cb = flat ? FLAT_C : [597, 347], Rb = 247;
      this._T = [Cb[0] + Rb * this._n[0], Cb[1] + Rb * this._n[1]]; // boulder contact
      const lineY = (x) => this._T[1] - (x - this._T[0]) * Math.tan(th);
      const yMax = PVY + (944.5 - PVY) / WORLD_SCALE;                                // stop above the tongue row of the base band
      const x0 = flat ? xL : this._T[0] + (this._T[1] - yMax) / Math.tan(th);
      const slopeD = `M${xL} ${yMax} L${x0} ${yMax} L${xR} ${lineY(xR)} L${xR} ${yMax} Z`;
      const slopeG = add(scene, 'g', { 'clip-path': 'url(#below-band)', style: flat ? 'display:none' : '' });
      const bb = add(defs, 'clipPath', { id: 'below-band', clipPathUnits: 'userSpaceOnUse' });
      add(bb, 'rect', { x: xL, y: 82.5, width: xR - xL, height: 900 });
      add(slopeG, 'path', { class: 'ink', d: slopeD });
      const sclip = add(defs, 'clipPath', { id: 'sclip', clipPathUnits: 'userSpaceOnUse' });
      add(sclip, 'path', { d: slopeD });
      // crack lines from the original mountain, repeated along the slope; they scroll downhill
      const crackTile = pathsGroup(M.line, 'inc', data.scale);
      const inner = add(slopeG, 'g', { 'clip-path': 'url(#sclip)', class: 'crack' });
      this._cracks = [];
      for (let i = -2; i <= 2; i++) {
        const g = add(inner, 'g', {});
        g.appendChild(crackTile.cloneNode(true));
        this._cracks.push([g, i]);
      }

      // boulder rolls about its centre
      const B = data.parts.boulder;
      this._boulder = add(add(scene, 'g', flat ? { transform: `translate(${FLAT_C[0] - 597},${FLAT_C[1] - 347})` } : {}), 'g', {});
      this._boulder.appendChild(partEl('boulder', data));

      const mk = (name, haloLayer, outline) => {
        const g = add(scene, 'g', {}); g.appendChild(partEl(name, data, false, outline));
        if (haloLayer) { g._halo = add(haloLayer, 'g', {}); g._halo.appendChild(pathsGroup(data.parts[name].black, 'halo', data.scale)); }
        return g;
      };
      const Jw = data.joints;
      const tauOf = (x, y, a, b) => { const vx = b[0] - a[0], vy = b[1] - a[1]; return ((x - a[0]) * vx + (y - a[1]) * vy) / (vx * vx + vy * vy); };
      // thigh art: [pelvis, thigh, shin]; extra channel = kneecap weight
      const thighW = (x, y) => {
        const tau = tauOf(x, y, Jw.hip, Jw.kneeA);
        const ws = 0.85 * smooth(clamp01((tau - 0.55) / 0.6));
        const wp = 0.45 * (1 - smooth(clamp01(tau / 0.3)));
        return [wp, 1 - ws - wp, ws, smooth(clamp01((tau - 0.55) / 0.6))];
      };
      // shin art: [thigh, shin]
      const shinW = (knee, ankle) => (x, y) => {
        const sg = tauOf(x, y, knee, ankle);
        const wt = 0.5 * (1 - smooth(clamp01((sg + 0.05) / 0.3)));
        return [wt, 1 - wt];
      };
      // far leg (behind): its sandal, then the drawn leg
      this._footDB = new DrawnFoot(add(scene, 'g', {}), defs, 'footclipB');
      this._legDrawB = new DrawnLeg(add(scene, 'g', {}), data.scale, {});
      // near leg: a thin incised contour where it crosses the far leg (clipped to the far leg)
      const sepClip = add(defs, 'clipPath', { id: 'sepclip', clipPathUnits: 'userSpaceOnUse' });
      this._sepClipT = add(sepClip, 'path', { d: '' }); this._sepClipS = add(sepClip, 'path', { d: '' });
      const sepG = add(scene, 'g', { 'clip-path': 'url(#sepclip)' });
      this._sepT = add(sepG, 'path', { class: 'sep', d: '' }); this._sepS = add(sepG, 'path', { class: 'sep', d: '' });
      this._pelvis = add(scene, 'circle', { class: 'ink', r: 19 });
      this._footDA = new DrawnFoot(add(scene, 'g', {}), defs, 'footclipA');
      this._legDrawA = new DrawnLeg(add(scene, 'g', {}), data.scale, {});
      // upper body: [pelvis, chest, head, hair, tunicA, tunicB]
      const upperW = (x, y) => {
        const chest = smooth(clamp01((640 - y) / 45));
        const head = smooth(clamp01((522 - y) / 26));
        const hair = smooth(clamp01((356 - x) / 30)) * smooth(clamp01((565 - y) / 45)) * 0.9 + head * smooth(clamp01((420 - y) / 18)) * 0.6;
        const hr = Math.min(1, hair);
        const ramp = smooth(clamp01((y - 635) / 30));
        const fa = 1 - smooth(clamp01((segDist(x, y, Jw.hip, Jw.kneeA) - 12) / 60));
        const fb = 1 - smooth(clamp01((segDist(x, y, [357, 681], Jw.kneeB) - 12) / 60));
        const lower = 1 - chest;
        const ta = lower * ramp * fa, tb = lower * ramp * fb * (1 - fa);
        const wHR = chest * hr, rest = chest * (1 - hr);
        return [lower - ta - tb, rest * (1 - head), rest * head, wHR, ta, tb];
      };
      const beltClip = add(defs, 'clipPath', { id: 'beltclip', clipPathUnits: 'userSpaceOnUse' });
      this._beltClipP = add(beltClip, 'path', { d: '' });
      this._upper = new Skin2(add(scene, 'g', { 'clip-path': 'url(#beltclip)' }), data.parts.upper, data.scale, null, 6, upperW);
      this._tunic = new DrawnTunic(add(scene, 'g', {}));
      // forearm: [chest, forearm] with the elbow blended
      const foreW = (x, y) => {
        const d = Math.hypot(x - Jw.elbow[0], y - Jw.elbow[1]);
        const wf = smooth(clamp01((d - 10) / 40));
        return [1 - wf, wf];
      };
      this._forearm = new Skin2(add(scene, 'g', {}), data.parts.forearm, data.scale, null, 2, foreW);

      // vase bands: one traced period, repeated
      const tileBand = (name) => {
        const t = data.parts[name].tile;
        const g = add(bandsG, 'g', { transform: `scale(${K})` });
        const W = 1920 / K + 2;
        if (t.stripes) {
          for (const [a, b2, c] of t.stripes) add(g, 'rect', { class: c === 'k' ? 'band' : 'bandbg', x: -1, y: a, width: W, height: b2 - a + (c === 'k' ? 0.3 : 0) });
        } else {
          add(g, 'rect', { class: 'bandbg', x: -1, y: t.y0, width: W, height: t.y1 - t.y0 });
        }
        const ink = pathsGroup(data.parts[name].black, t.ink === 'o' ? 'bandbg' : 'band', data.scale);
        // keep the panel at the original's phase; repeat the traced period both ways
        const start = OX / K + t.x - Math.ceil((OX / K + t.x) / t.p) * t.p;
        const overlap = t.stripes ? '' : ` translate(${t.x},0) scale(1.012,1) translate(${-t.x},0)`;
        for (let x = start, i = 0; x < W + t.p; x += t.p, i++) {
          const c = add(g, 'g', { transform: `translate(${(x - t.x).toFixed(3)},0)${overlap}` });
          c.appendChild(i === 0 ? ink : ink.cloneNode(true));
        }
      };
      tileBand('meander');
      tileBand('tongues');

      // rig constants from the joints
      const v = (a, b) => [b[0] - a[0], b[1] - a[1]];
      const len = (p) => Math.hypot(p[0], p[1]);
      const ang = (p) => Math.atan2(p[1], p[0]);
      const side = (h, k, a) => Math.sign((a[0] - h[0]) * (k[1] - h[1]) - (a[1] - h[1]) * (k[0] - h[0]));
      this._legA = { hip: J.hip, knee: J.kneeA, ankle: J.ankleA, L1: len(v(J.hip, J.kneeA)), L2: len(v(J.kneeA, J.ankleA)),
        t0: ang(v(J.hip, J.kneeA)), s0: ang(v(J.kneeA, J.ankleA)), side: side(J.hip, J.kneeA, J.ankleA) };
      this._hipBoff = [-26, -14];
      this._thB0 = Math.atan2(J.kneeB[1] - 681, J.kneeB[0] - 357); // far hip joint sits a little higher/back under the chiton
      this._legB = { hip: J.hip, knee: J.kneeB, ankle: J.ankleB, L1: len(v(J.hip, J.kneeA)), L2: len(v(J.kneeB, J.ankleB)),
        t0: ang(v(J.hip, J.kneeB)), s0: ang(v(J.kneeB, J.ankleB)), side: side(J.hip, J.kneeA, J.ankleA) };
      this._C = [B.cx || 597, B.cy || 347];
      this._C = [597, 347];
      this._R = 247;
      this._D0 = len(v(this._C, J.hand));
      if (this._flat) this._C = FLAT_C;
    }

    _time() {
      const ph = parseFloat(this.getAttribute('phase'));
      if (!isNaN(ph)) return ph * CYCLE;
      const reduced = this._reduced && this._reduced.matches;
      if (reduced && !this.hasAttribute('paused')) return 0;
      if (this.hasAttribute('paused')) {
        if (this._frozenAt == null) this._frozenAt = (performance.now() - this._t0) / 1000;
        return this._frozenAt;
      }
      if (this._frozenAt != null) { this._t0 = performance.now() - this._frozenAt * 1000; this._frozenAt = null; }
      return (performance.now() - this._t0) / 1000;
    }
    _loop() { this._render(this._time()); this._raf = requestAnimationFrame(this._loop); }

    // two-bone IK; returns knee and the rotations (deg) for thigh and shin relative to rest
    _ik(leg, hip, ankle) {
      let dx = ankle[0] - hip[0], dy = ankle[1] - hip[1];
      let d = Math.hypot(dx, dy);
      const maxd = (leg.L1 + leg.L2) * 0.999;
      if (d > maxd) { dx *= maxd / d; dy *= maxd / d; d = maxd; }
      const base = Math.atan2(dy, dx);
      const c = Math.min(1, Math.max(-1, (leg.L1 * leg.L1 + d * d - leg.L2 * leg.L2) / (2 * leg.L1 * d)));
      const th = base + leg.side * Math.acos(c);
      const knee = [hip[0] + leg.L1 * Math.cos(th), hip[1] + leg.L1 * Math.sin(th)];
      const sh = Math.atan2(hip[1] + dy - knee[1], hip[0] + dx - knee[0]);
      return { knee, ankle: [hip[0] + dx, hip[1] + dy], th, sh };
    }

    // ground travel: constant mean speed with a swell after each plant (period CYCLE/2)
    _ground(t) {
      const w = 4 * Math.PI / CYCLE, ph = -0.6;
      return this._v * (t + SURGE / w * (Math.sin(w * t + ph) - Math.sin(ph)));
    }

    // foot state for a leg at time t with phase offset; returns toe point and foot rotation (deg)
    _foot(t, off) {
      const F = this._F, R = this._toeR;
      const p = (((t / CYCLE + off) % 1) + 1) % 1;
      const tPlant = t - p * CYCLE;
      const L = this._stride;
      if (p < DUTY) {
        const sgm = Math.min(1, (this._ground(t) - this._ground(tPlant)) / L);
        const toe = [F[0] + (R[0] - F[0]) * sgm, F[1] + (R[1] - F[1]) * sgm];
        const rot = this._rotFlat + (sgm > 0.66 ? (this._rotToe - this._rotFlat) * easeIO((sgm - 0.66) / 0.34) : 0);
        return { toe, rot, planted: true, stance: p / DUTY };
      }
      const s = (p - DUTY) / (1 - DUTY);
      // knee drives early: the foot leaves fast, peaks high before mid-swing, then reaches and settles
      const e = 1 - Math.pow(1 - smooth(s), 1.7);
      const lift = LIFT * Math.pow(Math.sin(Math.PI * Math.min(1, s * 1.04)), 0.8) * (1 - 0.25 * s);
      const toe = [R[0] + (F[0] - R[0]) * e - this._n[0] * lift, R[1] + (F[1] - R[1]) * e - this._n[1] * lift];
      const rot = this._rotToe + (this._rotFlat - 6 - this._rotToe) * easeIO(Math.min(1, s / 0.75)) + 6 * smooth(clamp01((s - 0.75) / 0.25));
      return { toe, rot, planted: false, stance: 0 };
    }

    _init() {
      if (this._toeR) return;
      const J = this._J;
      const T0 = this._T, nn = this._n, uu = this._u;
      const dHip = (T0[0] - J.hip[0]) * nn[0] + (T0[1] - J.hip[1]) * nn[1];
      const P = [J.hip[0] + dHip * nn[0], J.hip[1] + dHip * nn[1]];
      this._F = [P[0] + STEP_AHEAD * uu[0], P[1] + STEP_AHEAD * uu[1]];
      this._toeR = [P[0] - STEP_BACK * uu[0], P[1] - STEP_BACK * uu[1]];
      this._stride = STEP_AHEAD + STEP_BACK;
      const w = 4 * Math.PI / CYCLE, ph = -0.6;
      this._v = this._stride / (DUTY * CYCLE + SURGE / w * (Math.sin(w * DUTY * CYCLE + ph) - Math.sin(ph)));
      this._toeOff = [J.ankleA[0] - J.toe[0], J.ankleA[1] - J.toe[1]];
    }

    _pose(t) {
      const J = this._J;
      const w2 = 4 * Math.PI / CYCLE;
      // weight shift: the body drops into each plant and rises as the legs pass
      const heave = 0.5 - 0.5 * Math.cos(w2 * t - 0.9);
      const breath = 0.25 * Math.sin(w2 * t * 0.5 + 1.0);
      const hip = [J.hip[0] + HIP_SURGE * heave, J.hip[1] - HIP_BOB * (0.5 - 0.5 * Math.cos(w2 * t - 2.6)) + breath];
      const rotU = HEAVE_ROT * heave + 0.3 * breath;
      const fA = this._foot(t, 0), fB = this._foot(t, 0.5);
      const ankleOf = (f) => { const a = f.rot * D2R, o = this._toeOff; return [f.toe[0] + o[0] * Math.cos(a) - o[1] * Math.sin(a), f.toe[1] + o[0] * Math.sin(a) + o[1] * Math.cos(a)]; };
      const ankA0 = ankleOf(fA), ankB0 = ankleOf(fB);
      const ho = this._hipBoff;
      for (const [leg, an, f, o] of [[this._legB, ankB0, fB, ho], [this._legA, ankA0, fA, [0, 0]]]) {
        if (!f.planted) continue;
        const hx = hip[0] + o[0], hy = hip[1] + o[1];
        const dx = hx - an[0], dy = hy - an[1], d = Math.hypot(dx, dy), m = (leg.L1 + leg.L2) * 0.995;
        if (d > m) { hip[0] = an[0] + dx * m / d - o[0]; hip[1] = an[1] + dy * m / d - o[1]; }
      }
      const hipB = [hip[0] + ho[0], hip[1] + ho[1]];
      const a = this._ik(this._legA, hip, ankA0), b = this._ik(this._legB, hipB, ankB0);
      return { hip, hipB, rotU, a, b, fA, fB, heave, breath };
    }

    _render(t) {
      if (!this._built) return;
      const J = this._J;
      this._init();
      if (false) {
        // the point on the slope straight below the hip, then the front and rear contacts along it
        const T0 = this._T, nn = this._n, uu = this._u;
        const dHip = (T0[0] - J.hip[0]) * nn[0] + (T0[1] - J.hip[1]) * nn[1];
        const P = [J.hip[0] + dHip * nn[0], J.hip[1] + dHip * nn[1]];
        this._F = [P[0] + STEP_AHEAD * uu[0], P[1] + STEP_AHEAD * uu[1]];
        this._toeR = [P[0] - STEP_BACK * uu[0], P[1] - STEP_BACK * uu[1]];
        this._stride = STEP_AHEAD + STEP_BACK;
        // mean speed so a planted foot covers exactly one stride while in contact
        const w = 4 * Math.PI / CYCLE, ph = -0.6;
        this._v = this._stride / (DUTY * CYCLE + SURGE / w * (Math.sin(w * DUTY * CYCLE + ph) - Math.sin(ph)));
        this._toeOff = [J.ankleA[0] - J.toe[0], J.ankleA[1] - J.toe[1]];
      }
      const g = this._ground(t);
      const P0 = this._pose(t);
      const { hip, hipB, rotU, a, b, fA, fB, heave } = P0;
      // overlapping action: secondary parts follow lagged versions of the body motion
      const P1 = this._pose(t - 0.16), P2 = this._pose(t - 0.42), P3 = this._pose(t - 0.3);
      const T = (p, deg, piv, w) => `translate(${w[0].toFixed(2)},${w[1].toFixed(2)}) rotate(${deg.toFixed(3)}) translate(${-piv[0]},${-piv[1]})`;
      const L = this._legA;
      const restBend = L.s0 - L.t0;
      const openA = clamp01(1 - (a.sh - a.th) / restBend), openB = clamp01(1 - (b.sh - b.th) / restBend);
      // feet (rigid sandals)
      const setT = (el, tr) => { el.setAttribute('transform', tr); if (el._halo) el._halo.setAttribute('transform', tr); };
      const toeRot = (f) => f.planted ? this._rotFlat : f.rot - 14;
      this._footDA.draw(a.ankle, J.ankleA, fA.rot, toeRot(fA));
      this._footDB.draw(b.ankle, J.ankleA, fB.rot, toeRot(fB));
      // bones
      const ru = rotU * D2R;
      const Pm = boneMat(J.hip, 0, hip, ru);
      const thighMat = (hp, ik, open, load) => boneMat(J.hip, L.t0, hp, ik.th, (1 + 0.08 * load * (1 - open)) * (1 - 0.14 * open), 1 - 0.03 * load);
      const loadA = fA.planted ? Math.sin(Math.PI * clamp01(fA.stance)) : 0, loadB = fB.planted ? Math.sin(Math.PI * clamp01(fB.stance)) : 0;
      const TAm = thighMat(hip, a, openA, loadA), TBm = thighMat(hipB, b, openB, loadB);
      const SAm = boneMat(J.kneeA, L.s0, a.knee, a.sh, 1 + 0.04 * loadA);
      const SBm = boneMat(J.kneeB, this._legB.s0, b.knee, b.sh, 1 + 0.04 * loadB);
      const SBinA = boneMat(J.kneeA, L.s0, b.knee, b.sh);              // far shin bone, in thigh-art rest space
      const TBinB = boneMat(J.kneeB, this._thB0, b.knee, b.th);         // far thigh bone, in shin-B rest space
      const TAinS = boneMat(J.kneeA, L.t0, a.knee, a.th);
      const kneePost = (ik, open) => (X, Y, wk, r) => {
        if (!wk) return;
        const f = 1 - KNEE_SHRINK * open * wk;
        r[0] = ik.knee[0] + (X - ik.knee[0]) * f; r[1] = ik.knee[1] + (Y - ik.knee[1]) * f;
      };
      // drawn legs: hip joint sits a little inside the hip of the traced torso
      const hipJA = apply(Pm, [J.hip[0] - 6, J.hip[1] - 4]), hipJB = [hipJA[0] + this._hipBoff[0], hipJA[1] + this._hipBoff[1]];
      const sideA = this._legA.side > 0 ? -1 : 1;
      const lgB = this._legDrawB.draw(hipJB, b.knee, b.ankle, sideA, loadB, 0.9);
      const lgA = this._legDrawA.draw(hipJA, a.knee, a.ankle, sideA, loadA, 1.08);
      this._sepClipT.setAttribute("d", lgB.thigh); this._sepClipS.setAttribute("d", lgB.shin);
      this._sepT.setAttribute("d", lgA.thigh); this._sepS.setAttribute("d", lgA.shin);
      const pel = apply(Pm, [378, 668]);
      this._pelvis.setAttribute('cx', pel[0].toFixed(2)); this._pelvis.setAttribute('cy', pel[1].toFixed(2));

      // upper body: chest drives into the stone with each push, head braces, hair and tunic lag
      const chestAng = (CHEST_DRIVE * heave + 0.6 * P0.breath) * D2R;
      const W0 = [335, 615], N0 = [352, 508];
      const W0w = apply(Pm, W0);
      const CHm = boneMat(W0, 0, W0w, ru + chestAng);
      const N0w = apply(CHm, N0);
      const headAng = -0.55 * chestAng;
      const HDm = boneMat(N0, 0, N0w, ru + chestAng + headAng);
      const lagRot = (P3.rotU - rotU) * D2R + (P3.hip[1] - hip[1]) * 0.012 + (P3.hip[0] - hip[0]) * 0.006;
      const HRm = boneMat(N0, 0, N0w, ru + chestAng + headAng + HAIR_FOLLOW * lagRot);
      const dA = (P) => P.a.th - L.t0, dB = (P) => P.b.th - this._thB0;
      const tunic = (d) => CHITON_FOLLOW * (d(P1) + 0.45 * (d(P1) - d(P2)));
      const flutter = (P1.hip[1] - P2.hip[1]) * 0.006;
      const TUAm = boneMat(J.hip, 0, hip, ru + tunic(dA) + flutter);
      const TUBm = boneMat(J.hip, 0, hip, ru + tunic(dB) + flutter);
      this._upper.update([Pm, CHm, HDm, HRm, TUAm, TUBm]);
      {
        const bp = [[120, 260], [700, 260], [700, 600], [460, 628], [392, 641], [340, 647], [292, 651], [200, 655], [120, 655]].map((q) => apply(Pm, q));
        this._beltClipP.setAttribute('d', 'M' + bp.map((q) => q[0].toFixed(2) + ' ' + q[1].toFixed(2)).join(' L') + 'Z');
        // tunic swing: follows each thigh with lag and overshoot (momentum)
        const dN = (Q) => Q.a.th - L.t0, dF = (Q) => Q.b.th - this._thB0;
        const sw = (d) => 0.62 * d(P0) * 0.55 + 0.45 * (d(P1) + 0.5 * (d(P1) - d(P2)));
        const fr = Math.max(-0.9, Math.min(0.9, 0.55 * sw(dN))), bk = Math.max(-0.6, Math.min(0.6, 0.35 * sw(dF)));
        const sway = (P1.hip[1] - P2.hip[1]) * 0.008 + (P1.rotU - P2.rotU) * D2R * 0.8;
        this._tunic.draw(Pm, hip, fr, bk, sway);
      }
      // forearm: bend at the elbow so the hand stays on the stone
      const el = apply(CHm, J.elbow), hd = apply(CHm, J.hand);
      const C = this._C, D0 = this._D0;
      const handAt = (al) => { const c = Math.cos(al), s = Math.sin(al), x = hd[0] - el[0], y = hd[1] - el[1]; return [el[0] + x * c - y * s, el[1] + x * s + y * c]; };
      const f = (al) => Math.hypot(...((p) => [p[0] - C[0], p[1] - C[1]])(handAt(al))) - D0;
      // best elbow angle (closest hand-to-stone), then the forearm stretches/compresses the rest
      let al = 0, best = 1e9;
      const cost = (x) => Math.abs(f(x)) + 15 * Math.abs(x);
      for (let k = -28; k <= 28; k++) { const v = cost(k * 0.75 * D2R); if (v < best) { best = v; al = k * 0.75 * D2R; } }
      for (let it = 0, step = 0.375 * D2R; it < 12; it++, step /= 2) {
        for (const c of [al - step, al + step]) { const v = cost(c); if (v < best) { best = v; al = c; } }
      }
      const lf = Math.hypot(hd[0] - el[0], hd[1] - el[1]);
      const sAl = Math.max(0.9, Math.min(1.12, 1 - f(al) / lf));
      const a0 = Math.atan2(J.hand[1] - J.elbow[1], J.hand[0] - J.elbow[0]);
      const FAm = boneMat(J.elbow, a0, el, a0 + ru + chestAng + al, 1, sAl);
      this._forearm.update([CHm, FAm]);

      // boulder roll and ground scroll advance together during the press
      this._boulder.setAttribute('transform', `rotate(${((g / this._R) / D2R % 360).toFixed(3)} 597 347)`);  // art coordinates (flat: inside the move)
      const ux = this._u[0], uy = this._u[1];
      for (const [el2, i] of this._cracks) {
        const o = i * CRACK_PERIOD - (g % CRACK_PERIOD);
        el2.setAttribute('transform', `translate(${(ux * o).toFixed(2)},${(uy * o).toFixed(2)})`);
      }
    }
  }
  customElements.define('sisyphus-backdrop-v3', SisyphusBackdrop);
})();
