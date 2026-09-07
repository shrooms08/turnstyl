/**
 * The particle forms, ported from the three.js scene in web/index.html.
 *
 * The generators below are the page's, translated rather than re-derived: the
 * brain silhouette and its streamline walker, the logo mark, the coin, and the
 * scatter cloud. Two things changed in the port:
 *
 *   1. Every Math.random() became a draw from a seeded mulberry32, so all
 *      Remotion render workers build identical geometry.
 *   2. Nothing here reads a clock. The build runs once per worker; everything
 *      time-varying is a pure function of the frame, over in Particles.tsx.
 *
 * The bulb form is not ported: no beat calls for it.
 */
import * as THREE from 'three';
import {noise2} from './noise';
import {mulberry32, SCENE_SEED} from './rng';

export const COUNT = 5000;
const AMBIENT = 300; // excluded from every form
export const FORM_N = COUNT - AMBIENT;

const LOGO_DIAMETER = 2.6;
const LOGO_STROKE = 1.4;
const LOGO_DEPTH = 0.7;
const COIN_R = 1.35;
const COIN_STROKE = 0.16;
const COIN_DEPTH = 0.35;
const COIN_GLYPH_H = 1.7;
const COIN_GLYPH_Z = 0.12;
const COIN_DISC_R = 1.2;
const COIN_DISC_Z = -0.2;

const GOLD = 0xe2b04a;
const VIOLET = 0x7f77dd;
const WHITE = 0x9a9890;
const LOGO_WHITE = 0xf2f0ea;
const DIM_WHITE = 0x6f6e68;
const PINK = 0xed93b1;
const TEAL = 0x5dcaa5;

/** The brain's pose on the page: front-left three-quarter, seen from above. */
export const BRAIN_POSE = {x: 0.12, y: 0.35};
export const BRAIN_SWAY_AMP = 0.3;
export const LOGO_SWAY_AMP = 0.25;
/** Peak sway rate of about 0.05 rad/s, as on the page. */
export const SWAY_W = (2 * Math.PI) / 44;

const F = Float32Array;

export type Forms = {
  posBrain: Float32Array;
  posLogo: Float32Array;
  posCoin: Float32Array;
  posScatter: Float32Array;
  colBrain: Float32Array;
  colLogo: Float32Array;
  colCoin: Float32Array;
  colScatter: Float32Array;
  sBrain: Float32Array;
  sLogo: Float32Array;
  sScatter: Float32Array;
  /** Unit tumble axis and starting angle / rate per instance. */
  axis: Float32Array;
  spinAng: Float32Array;
  spinVel: Float32Array;
  /** Unit direction each instance flees along when a form comes apart. */
  drift: Float32Array;
  brainMinY: number;
  brainMaxY: number;
  logoMinY: number;
  logoMaxY: number;
};

/* ── outline geometry helpers, verbatim from the page ────────────────── */

type Pt = [number, number];

function catmullRom(points: Pt[], perSeg: number): Pt[] {
  const n = points.length;
  const out: Pt[] = [];
  for (let i = 0; i < n; i++) {
    const p0 = points[(i - 1 + n) % n];
    const p1 = points[i];
    const p2 = points[(i + 1) % n];
    const p3 = points[(i + 2) % n];
    for (let k = 0; k < perSeg; k++) {
      const t = k / perSeg;
      const t2 = t * t;
      const t3 = t2 * t;
      out.push([
        0.5 *
          (2 * p1[0] +
            (-p0[0] + p2[0]) * t +
            (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 +
            (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3),
        0.5 *
          (2 * p1[1] +
            (-p0[1] + p2[1]) * t +
            (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 +
            (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3),
      ]);
    }
  }
  return out;
}

function insidePolygon(x: number, y: number, poly: Pt[]): boolean {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const xi = poly[i][0];
    const yi = poly[i][1];
    const xj = poly[j][0];
    const yj = poly[j][1];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

function polygonCentroid(poly: Pt[]): Pt {
  let a = 0;
  let cx = 0;
  let cy = 0;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const cross = poly[j][0] * poly[i][1] - poly[i][0] * poly[j][1];
    a += cross;
    cx += (poly[j][0] + poly[i][0]) * cross;
    cy += (poly[j][1] + poly[i][1]) * cross;
  }
  a *= 0.5;
  return [cx / (6 * a), cy / (6 * a)];
}

function segDist(px: number, py: number, ax: number, ay: number, bx: number, by: number): number {
  const vx = bx - ax;
  const vy = by - ay;
  const wx = px - ax;
  const wy = py - ay;
  const L = vx * vx + vy * vy || 1e-9;
  const t = Math.min(1, Math.max(0, (wx * vx + wy * vy) / L));
  return Math.hypot(px - (ax + vx * t), py - (ay + vy * t));
}

function polylineDist(x: number, y: number, poly: Pt[], closed: boolean): number {
  let d = Infinity;
  for (let i = 0, n = poly.length; i < (closed ? n : n - 1); i++) {
    const a = poly[i];
    const b = poly[(i + 1) % n];
    const s = segDist(x, y, a[0], a[1], b[0], b[1]);
    if (s < d) d = s;
  }
  return d;
}

/* ── the brain silhouette ────────────────────────────────────────────── */
// Closed outline in a unit square, x right, y up, front of the brain on the
// LEFT. Cerebrum clockwise from the frontal lobe, then cerebellum, brainstem,
// temporal lobe, back to the start.
const OUTLINE: Pt[] = [
  [0.08, 0.45], [0.06, 0.55], [0.1, 0.66], [0.18, 0.76], [0.3, 0.84], [0.45, 0.88], [0.6, 0.87],
  [0.74, 0.82], [0.86, 0.72], [0.93, 0.6], [0.92, 0.48], [0.86, 0.4], [0.78, 0.36],
  [0.8, 0.3], [0.74, 0.22], [0.64, 0.2], [0.58, 0.24], [0.56, 0.3],
  [0.55, 0.22], [0.52, 0.12], [0.46, 0.1], [0.44, 0.2], [0.46, 0.3],
  [0.42, 0.3], [0.34, 0.28], [0.24, 0.31], [0.16, 0.37], [0.1, 0.42],
];
const CEREBELLUM: Pt[] = [[0.78, 0.36], [0.8, 0.3], [0.74, 0.22], [0.64, 0.2], [0.58, 0.24], [0.56, 0.3]];
const BRAINSTEM_AXIS: Pt[] = [[0.51, 0.3], [0.49, 0.11]]; // centreline, top to bottom

// The smoothed silhouette, centred on its centroid and scaled anisotropically:
// width 3.2, height 3.2 / 1.35. Everything below works in these world units.
const SIL_U = catmullRom(OUTLINE, 7); // 196 points, unit square
const [XC, YC] = polygonCentroid(SIL_U);
let oMinX = Infinity;
let oMaxX = -Infinity;
let oMinY = Infinity;
let oMaxY = -Infinity;
for (const p of SIL_U) {
  if (p[0] < oMinX) oMinX = p[0];
  if (p[0] > oMaxX) oMaxX = p[0];
  if (p[1] < oMinY) oMinY = p[1];
  if (p[1] > oMaxY) oMaxY = p[1];
}
const SX = 3.2 / (oMaxX - oMinX);
const SY = 3.2 / 1.35 / (oMaxY - oMinY);
const toWorld = (p: Pt): Pt => [(p[0] - XC) * SX, (p[1] - YC) * SY];
const SIL = SIL_U.map(toWorld);
const CEREB = CEREBELLUM.map(toWorld);
const STEM = BRAINSTEM_AXIS.map(toWorld);
const wMinX = (oMinX - XC) * SX;
const wMaxX = (oMaxX - XC) * SX;
const wMinY = (oMinY - YC) * SY;
const wMaxY = (oMaxY - YC) * SY;
const HALF_H = Math.max(wMaxY, -wMinY);

/** Half-thickness: the y profile closes at the silhouette, the x taper is 1.75. */
function halfThick(x: number, y: number): number {
  const ty = 1 - (y / HALF_H) * (y / HALF_H);
  const tx = 1 - (x / 1.75) * (x / 1.75);
  return 0.62 * Math.sqrt(Math.max(0, ty)) * Math.sqrt(Math.max(0.15, tx));
}

// grooves: the Sylvian fissure (a bowed curve) and the cerebellum boundary
const SYLVIAN: Pt[] = [];
for (let k = 0; k <= 24; k++) {
  const t = k / 24;
  const mt = 1 - t; // quadratic Bezier
  SYLVIAN.push(
    toWorld([mt * mt * 0.2 + 2 * mt * t * 0.36 + t * t * 0.55, mt * mt * 0.42 + 2 * mt * t * 0.4 + t * t * 0.5]),
  );
}
const CEREB_LINE: Pt[] = [toWorld([0.78, 0.36]), toWorld([0.56, 0.3])];
function grooveDist(x: number, y: number): number {
  return Math.min(polylineDist(x, y, SYLVIAN, false), polylineDist(x, y, CEREB_LINE, false));
}

const STEM_POLY: Pt[] = ([[0.56, 0.3], [0.55, 0.22], [0.52, 0.12], [0.46, 0.1], [0.44, 0.2], [0.46, 0.3]] as Pt[]).map(
  toWorld,
);

/* ── the build ───────────────────────────────────────────────────────── */

function build(): Forms {
  const rnd = mulberry32(SCENE_SEED);

  const posBrain = new F(COUNT * 3);
  const posLogo = new F(COUNT * 3);
  const posCoin = new F(COUNT * 3);
  const posScatter = new F(COUNT * 3);
  const colBrain = new F(COUNT * 3);
  const colLogo = new F(COUNT * 3);
  const colCoin = new F(COUNT * 3);
  const colScatter = new F(COUNT * 3);
  const sBrain = new F(COUNT);
  const sLogo = new F(COUNT);
  const sCoin = new F(COUNT);
  const sScatter = new F(COUNT);
  const axis = new F(COUNT * 3);
  const spinAng = new F(COUNT);
  const spinVel = new F(COUNT);
  const drift = new F(COUNT * 3);

  const cGold = new THREE.Color(GOLD);
  const cViolet = new THREE.Color(VIOLET);
  const cWhite = new THREE.Color(WHITE);
  const cLogoWhite = new THREE.Color(LOGO_WHITE);
  const cDim = new THREE.Color(DIM_WHITE);
  const cPink = new THREE.Color(PINK);
  const cTeal = new THREE.Color(TEAL);

  const put = (arr: Float32Array, i: number, c: THREE.Color) => {
    arr[i * 3] = c.r;
    arr[i * 3 + 1] = c.g;
    arr[i * 3 + 2] = c.b;
  };
  const ambientXYZ = (arr: Float32Array, i: number) => {
    arr[i * 3] = (rnd() - 0.5) * 12;
    arr[i * 3 + 1] = (rnd() - 0.5) * 7;
    arr[i * 3 + 2] = (rnd() - 0.5) * 6;
  };

  // sizes. Brain, logo and coin particles are the fine class only
  // (0.014-0.024); scatter keeps the three classes; the 2% large shards belong
  // to the ambient field and the scatter form only.
  for (let i = 0; i < COUNT; i++) {
    const r = rnd();
    let s: number;
    if (i >= FORM_N) s = r < 0.02 ? 0.18 + rnd() * 0.17 : 0.012 + rnd() * 0.018;
    else if (r < 0.92) s = 0.012 + rnd() * 0.018;
    else if (r < 0.98) s = 0.04 + rnd() * 0.04;
    else s = 0.18 + rnd() * 0.17;
    sScatter[i] = s;
    sBrain[i] = i >= FORM_N ? s : 0.014 + rnd() * 0.01;
    sLogo[i] = i >= FORM_N ? s : 0.014 + rnd() * 0.01;
    sCoin[i] = i >= FORM_N ? s : 0.014 + rnd() * 0.01;
    const ax = rnd() * 2 - 1;
    const ay = rnd() * 2 - 1;
    const az = rnd() * 2 - 1;
    const al = Math.hypot(ax, ay, az) || 1;
    axis[i * 3] = ax / al;
    axis[i * 3 + 1] = ay / al;
    axis[i * 3 + 2] = az / al;
    spinAng[i] = rnd() * Math.PI * 2;
    spinVel[i] = (rnd() - 0.5) * 0.5; // slow tumble
    const dx = rnd() * 2 - 1;
    const dy = rnd() * 2 - 1;
    const dz = rnd() * 2 - 1;
    const dl = Math.hypot(dx, dy, dz) || 1;
    drift[i * 3] = dx / dl;
    drift[i * 3 + 1] = dy / dl;
    drift[i * 3 + 2] = dz / dl;
  }
  const isTeal = new Uint8Array(COUNT);
  for (let i = 0; i < COUNT; i++) isTeal[i] = rnd() < 0.04 ? 1 : 0;

  let brainMinY = Infinity;
  let brainMaxY = -Infinity;
  let slot = 0; // next free instance index

  const placeBrain = (x: number, y: number, z: number, c: THREE.Color): boolean => {
    if (slot >= FORM_N) return false;
    posBrain[slot * 3] = x;
    posBrain[slot * 3 + 1] = y;
    posBrain[slot * 3 + 2] = z;
    if (y < brainMinY) brainMinY = y;
    if (y > brainMaxY) brainMaxY = y;
    put(colBrain, slot, c);
    slot++;
    return true;
  };

  /* particle budget over the 4700 form instances */
  const N_BAND = Math.round(FORM_N * 0.22);
  const N_STREAM = Math.round(FORM_N * 0.62);
  const N_INTERIOR = Math.round(FORM_N * 0.1);
  const N_STEM = 40;
  const N_CEREB = FORM_N - N_BAND - N_STREAM - N_INTERIOR; // 6%, absorbs rounding

  // 22%: the outline band. Points within 0.06 of the silhouette, on both skins.
  const outlineBand = (n: number) => {
    const cum = [0];
    for (let i = 0; i < SIL.length; i++) {
      const a = SIL[i];
      const b = SIL[(i + 1) % SIL.length];
      cum.push(cum[i] + Math.hypot(b[0] - a[0], b[1] - a[1]));
    }
    const total = cum[cum.length - 1];
    let placed = 0;
    let tries = 0;
    while (placed < n && tries < n * 20) {
      tries++;
      const s = rnd() * total;
      let i = 0;
      while (i < SIL.length - 1 && cum[i + 1] < s) i++;
      const a = SIL[i];
      const b = SIL[(i + 1) % SIL.length];
      const t = (s - cum[i]) / (cum[i + 1] - cum[i] || 1e-9);
      const qx = a[0] + (b[0] - a[0]) * t;
      const qy = a[1] + (b[1] - a[1]) * t;
      const ex = b[0] - a[0];
      const ey = b[1] - a[1];
      const el = Math.hypot(ex, ey) || 1e-9;
      let nx = ey / el;
      let ny = -ex / el; // one of the two normals
      if (!insidePolygon(qx + nx * 0.02, qy + ny * 0.02, SIL)) {
        nx = -nx;
        ny = -ny;
      }
      const d = rnd() * 0.06;
      const jt = (rnd() - 0.5) * 0.02;
      const x = qx + nx * d - ny * jt;
      const y = qy + ny * d + nx * jt;
      if (!insidePolygon(x, y, SIL)) continue;
      const z = (rnd() < 0.5 ? 1 : -1) * halfThick(x, y) * 0.9;
      if (placeBrain(x, y, z, cGold)) placed++;
    }
    return placed;
  };

  // 62%: gyri streamlines along a rotated noise gradient, kept 0.055 apart.
  const SPACING = 0.055;
  const gCols = Math.ceil((wMaxX - wMinX) / SPACING) + 2;
  const gRows = Math.ceil((wMaxY - wMinY) / SPACING) + 2;
  // One occupancy grid per surface. Each cell remembers which curve owns it and
  // where that curve's point is, so spacing is a real distance test, not a
  // cell-adjacency test that would over-exclude to ~2 cells.
  type Grid = {id: Int16Array; px: Float32Array; py: Float32Array};
  const makeGrid = (): Grid => ({
    id: new Int16Array(gCols * gRows).fill(-1),
    px: new F(gCols * gRows),
    py: new F(gCols * gRows),
  });
  const gridFront = makeGrid();
  const gridBack = makeGrid();
  const cellOf = (x: number, y: number) => {
    const cx = Math.floor((x - wMinX) / SPACING) + 1;
    const cy = Math.floor((y - wMinY) / SPACING) + 1;
    if (cx < 0 || cy < 0 || cx >= gCols || cy >= gRows) return -1;
    return cy * gCols + cx;
  };
  const occupiedByOther = (g: Grid, x: number, y: number, self: number) => {
    const c = cellOf(x, y);
    if (c < 0) return true;
    const cx = c % gCols;
    const cy = (c - cx) / gCols;
    for (let dy = -1; dy <= 1; dy++)
      for (let dx = -1; dx <= 1; dx++) {
        const nx = cx + dx;
        const ny = cy + dy;
        if (nx < 0 || ny < 0 || nx >= gCols || ny >= gRows) continue;
        const k = ny * gCols + nx;
        const id = g.id[k];
        if (id === -1 || id === self) continue;
        if (Math.hypot(g.px[k] - x, g.py[k] - y) < SPACING) return true;
      }
    return false;
  };
  const claim = (g: Grid, x: number, y: number, id: number) => {
    const c = cellOf(x, y);
    if (c >= 0) {
      g.id[c] = id;
      g.px[c] = x;
      g.py[c] = y;
    }
  };
  // regions: cerebrum curves stay out of the cerebellum and the brainstem
  const inCerebrum = (x: number, y: number) =>
    insidePolygon(x, y, SIL) && !insidePolygon(x, y, CEREB) && !insidePolygon(x, y, STEM_POLY);

  const _d = [0, 0];
  const _in = [0, 0];
  const STEER_R = 0.18;
  const inwardDir = (x: number, y: number, poly: Pt[], out: number[]) => {
    // gradient of distance-to-edge
    const h = 0.01;
    out[0] = (polylineDist(x + h, y, poly, true) - polylineDist(x - h, y, poly, true)) / (2 * h);
    out[1] = (polylineDist(x, y + h, poly, true) - polylineDist(x, y - h, poly, true)) / (2 * h);
    const l = Math.hypot(out[0], out[1]) || 1e-9;
    out[0] /= l;
    out[1] /= l;
  };
  const steer = (x: number, y: number, poly: Pt[], d: number[]) => {
    const e = polylineDist(x, y, poly, true);
    if (e >= STEER_R) return;
    inwardDir(x, y, poly, _in);
    if (d[0] * _in[0] + d[1] * _in[1] >= 0) return; // already heading in
    const k = ((STEER_R - e) / STEER_R) * 1.4;
    d[0] += _in[0] * k;
    d[1] += _in[1] * k;
    const l = Math.hypot(d[0], d[1]) || 1e-9;
    d[0] /= l;
    d[1] /= l;
  };
  const fieldDir = (x: number, y: number, out: number[]) => {
    // rotate90(grad noise2(p * 2.6))
    const h = 0.01;
    const k = 2.6;
    const gx = (noise2((x + h) * k, y * k) - noise2((x - h) * k, y * k)) / (2 * h);
    const gy = (noise2(x * k, (y + h) * k) - noise2(x * k, (y - h) * k)) / (2 * h);
    const l = Math.hypot(gx, gy) || 1e-9;
    out[0] = -gy / l;
    out[1] = gx / l;
  };
  const walkStreamline = (
    id: number,
    sx: number,
    sy: number,
    steps: number,
    stepLen: number,
    region: (x: number, y: number) => boolean,
    poly: Pt[],
    g: Grid,
    side: number,
    mirror: boolean,
    colour: THREE.Color,
    jitterZ: number,
  ) => {
    let x = sx;
    let y = sy;
    let pdx = 0;
    let pdy = 0;
    let ang = 0;
    let placed = 0;
    const driftRate = (rnd() - 0.5) * 0.16; // slow per-curve meander
    for (let s = 0; s < steps; s++) {
      if (!region(x, y)) break;
      if (grooveDist(x, y) < 0.03) break;
      if (occupiedByOther(g, x, y, id)) break;
      claim(g, x, y, id);
      if (mirror) claim(gridBack, x, y, id);
      const w = halfThick(x, y);
      if (!placeBrain(x, y, side * (w - rnd() * jitterZ), colour)) return placed;
      placed++;
      if (mirror) {
        if (!placeBrain(x, y, -side * (w - rnd() * jitterZ), colour)) return placed;
        placed++;
      }
      fieldDir(x, y, _d);
      ang += driftRate + (rnd() - 0.5) * 0.06;
      const ca = Math.cos(ang);
      const sa = Math.sin(ang);
      let dx = _d[0] * ca - _d[1] * sa;
      let dy = _d[0] * sa + _d[1] * ca;
      if (pdx * dx + pdy * dy < 0) {
        dx = -dx;
        dy = -dy;
      } // never reverse
      _d[0] = dx;
      _d[1] = dy;
      steer(x, y, poly, _d); // curl along the rim
      dx = _d[0];
      dy = _d[1];
      pdx = dx;
      pdy = dy;
      x += dx * stepLen;
      y += dy * stepLen;
    }
    return placed;
  };
  // Clearance to the nearest point of any other curve, looked up over a 5x5
  // neighbourhood. Used to pick starts with room to run.
  const clearance = (g: Grid, x: number, y: number) => {
    const c = cellOf(x, y);
    if (c < 0) return 0;
    const cx = c % gCols;
    const cy = (c - cx) / gCols;
    let best = 9;
    for (let dy = -2; dy <= 2; dy++)
      for (let dx = -2; dx <= 2; dx++) {
        const nx = cx + dx;
        const ny = cy + dy;
        if (nx < 0 || ny < 0 || nx >= gCols || ny >= gRows) continue;
        const k = ny * gCols + nx;
        if (g.id[k] === -1) continue;
        const d = Math.hypot(g.px[k] - x, g.py[k] - y);
        if (d < best) best = d;
      }
    return best;
  };
  const _start = [0, 0];
  const pickStart = (region: (x: number, y: number) => boolean, edgeClear: number, g: Grid) => {
    // best of 40 valid candidates by clearance: a curve that starts with room
    // runs long before it meets another trail, which is what fills the budget
    let bestD = -1;
    let found = false;
    let cands = 0;
    for (let t = 0; t < 400 && cands < 40; t++) {
      const x = wMinX + rnd() * (wMaxX - wMinX);
      const y = wMinY + rnd() * (wMaxY - wMinY);
      if (!region(x, y)) continue;
      const e = polylineDist(x, y, SIL, true);
      if (e < edgeClear) continue;
      const gdist = grooveDist(x, y);
      if (gdist < 0.03) continue;
      const d = Math.min(clearance(g, x, y), e, gdist);
      if (d < SPACING) continue;
      cands++;
      if (d > bestD) {
        bestD = d;
        _start[0] = x;
        _start[1] = y;
        found = true;
      }
    }
    return found ? _start : null;
  };
  const inCereb = (x: number, y: number) => insidePolygon(x, y, CEREB);
  const curveColour = () => {
    const r = rnd();
    return r < 0.5 ? cLogoWhite : r < 0.8 ? cViolet : r < 0.9 ? cPink : cTeal;
  };

  // An instance the brain cannot place on the outline, a streamline, the
  // cerebellum, the brainstem or the 10% interior is parked: ambient position,
  // scale 0.004 (effectively invisible), dim white. It still morphs with every
  // form change, so the logo and scatter keep their full count.
  const PARKED_SCALE = 0.004;
  const parkInstance = () => {
    ambientXYZ(posBrain, slot);
    put(colBrain, slot, cDim);
    sBrain[slot] = PARKED_SCALE;
    slot++;
  };
  const interiorPoint = (c: THREE.Color) => {
    for (let t = 0; t < 200; t++) {
      const x = wMinX + rnd() * (wMaxX - wMinX);
      const y = wMinY + rnd() * (wMaxY - wMinY);
      if (!insidePolygon(x, y, SIL)) continue;
      return placeBrain(x, y, (rnd() * 2 - 1) * halfThick(x, y) * 0.9, c);
    }
    ambientXYZ(posBrain, slot); // unreachable in practice
    put(colBrain, slot, c);
    slot++;
    return true;
  };

  /* ── form: brain ───────────────────────────────────────────────────── */
  outlineBand(N_BAND);

  // 62%: 90 front-surface curves, 35% of them mirrored
  const streamEnd = slot + N_STREAM;
  let id = 0;
  while (slot < streamEnd && id < 90) {
    const start = pickStart(inCerebrum, 0.08, gridFront);
    if (!start) break;
    const mirror = rnd() < 0.35;
    walkStreamline(id, start[0], start[1], 38, 0.035, inCerebrum, SIL, gridFront, 1, mirror, curveColour(), 0.03);
    id++;
  }
  // the front is now as sparse as the spacing rule allows; the rest of the
  // streamline budget goes on the back surface, on its own spacing grid
  id = 500;
  while (slot < streamEnd && id < 700) {
    const start = pickStart(inCerebrum, 0.08, gridBack);
    if (!start) break;
    walkStreamline(id, start[0], start[1], 38, 0.035, inCerebrum, SIL, gridBack, -1, false, curveColour(), 0.03);
    id++;
  }
  while (slot < streamEnd) parkInstance();

  // 10%: interior at random depth
  for (let k = 0; k < N_INTERIOR; k++) interiorPoint(cDim);

  // 6%: cerebellum streamlines in their own sub-polygon, then the brainstem
  const cerebEnd = slot + N_CEREB - N_STEM;
  let cid = 1000;
  while (slot < cerebEnd && cid < 1040) {
    const start = pickStart(inCereb, 0.02, gridFront);
    if (!start) break;
    walkStreamline(
      cid, start[0], start[1], 16, 0.022, inCereb, CEREB, gridFront, 1, rnd() < 0.35,
      rnd() < 0.3 ? cGold : cViolet, 0.02,
    );
    cid++;
  }
  while (slot < cerebEnd) parkInstance();
  for (let k = 0; k < N_STEM && slot < FORM_N; k++) {
    const t = (k + 0.5) / N_STEM;
    const x = STEM[0][0] + (STEM[1][0] - STEM[0][0]) * t + (rnd() - 0.5) * 0.03;
    const y = STEM[0][1] + (STEM[1][1] - STEM[0][1]) * t + (rnd() - 0.5) * 0.03;
    placeBrain(x, y, (rnd() - 0.5) * 0.1, cLogoWhite);
  }
  while (slot < FORM_N) parkInstance();

  /* ── form: logo, from the mark geometry (24x24 grid, stroke 1.4) ───── */
  let logoMinY = Infinity;
  let logoMaxY = -Infinity;
  {
    const R = 9;
    const SW = LOGO_STROKE;
    const DEPTH = LOGO_DEPTH;
    const S = LOGO_DIAMETER / (2 * R);
    const deg = Math.PI / 180;
    const nRing = Math.round(FORM_N * 0.62);
    const nArm = Math.round(FORM_N * 0.12);
    const nGold = FORM_N - nRing - 2 * nArm; // 14%, absorbs rounding
    let i = 0;
    const place = (gx: number, gy: number, c: THREE.Color) => {
      // grid -> world
      const gz = (rnd() - 0.5) * DEPTH;
      const y = (gy - 12) * S;
      posLogo[i * 3] = (gx - 12) * S;
      posLogo[i * 3 + 1] = y;
      posLogo[i * 3 + 2] = gz * S;
      if (y < logoMinY) logoMinY = y;
      if (y > logoMaxY) logoMaxY = y;
      put(colLogo, i, c);
      i++;
    };
    for (let k = 0; k < nRing; k++) {
      const a = (24 + rnd() * (336 - 24)) * deg; // gap on the right
      const r = R + (rnd() - 0.5) * SW;
      place(12 + r * Math.cos(a), 12 + r * Math.sin(a), rnd() < 0.06 ? cViolet : cLogoWhite);
    }
    const arms: [number, number, number, THREE.Color][] = [
      [120, 7, nArm, cLogoWhite],
      [240, 7, nArm, cLogoWhite],
      [0, 10.5, nGold, cGold],
    ];
    for (const [ang, len, n, c] of arms) {
      const a = ang * deg;
      const ux = Math.cos(a);
      const uy = Math.sin(a);
      const px = -uy;
      const py = ux;
      for (let k = 0; k < n; k++) {
        const t = rnd() * len;
        const o = (rnd() - 0.5) * SW;
        place(12 + ux * t + px * o, 12 + uy * t + py * o, c);
      }
    }
  }

  /* ── form: scatter ─────────────────────────────────────────────────── */
  for (let i = 0; i < FORM_N; i++) {
    posScatter[i * 3] = (rnd() - 0.5) * 9;
    posScatter[i * 3 + 1] = (rnd() - 0.5) * 5;
    posScatter[i * 3 + 2] = (rnd() - 0.5) * 5;
    put(colScatter, i, isTeal[i] ? cTeal : cWhite);
  }

  /* ── ambient field: same place in every form ───────────────────────── */
  for (let i = FORM_N; i < COUNT; i++) {
    ambientXYZ(posBrain, i);
    posLogo[i * 3] = posCoin[i * 3] = posScatter[i * 3] = posBrain[i * 3];
    posLogo[i * 3 + 1] = posCoin[i * 3 + 1] = posScatter[i * 3 + 1] = posBrain[i * 3 + 1];
    posLogo[i * 3 + 2] = posCoin[i * 3 + 2] = posScatter[i * 3 + 2] = posBrain[i * 3 + 2];
    const c = isTeal[i] ? cTeal : cWhite;
    put(colBrain, i, c);
    put(colLogo, i, c);
    put(colCoin, i, c);
    put(colScatter, i, c);
  }

  /* ── form: coin ────────────────────────────────────────────────────────
   * Ported from the page for completeness. The teaser's beat list never puts
   * a coin on screen, so buildCoin is exported rather than run here: its glyph
   * is rasterised from a font, which is the one thing in the scene that could
   * differ between render workers. Call it only from a context that has waited
   * on document.fonts.
   * ------------------------------------------------------------------- */
  const buildCoin = () => {
    const coinRnd = mulberry32(SCENE_SEED ^ 0x5eed);
    const rasteriseGlyph = () => {
      // "$" in the page's display font, sampled from an offscreen 2D canvas.
      const size = 512;
      const cw = 768;
      const ch = 768;
      const cv = document.createElement('canvas');
      cv.width = cw;
      cv.height = ch;
      const ctx = cv.getContext('2d', {willReadFrequently: true});
      if (!ctx) return null;
      ctx.clearRect(0, 0, cw, ch);
      ctx.fillStyle = '#fff';
      ctx.font = '300 ' + size + 'px Outfit, system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('$', cw / 2, ch / 2);
      const data = ctx.getImageData(0, 0, cw, ch).data;
      const xs: number[] = [];
      const ys: number[] = [];
      let minX = cw;
      let maxX = 0;
      let minY = ch;
      let maxY = 0;
      for (let y = 0; y < ch; y++)
        for (let x = 0; x < cw; x++) {
          if (data[(y * cw + x) * 4 + 3] > 128) {
            // alpha > 128: ink
            xs.push(x);
            ys.push(y);
            if (x < minX) minX = x;
            if (x > maxX) maxX = x;
            if (y < minY) minY = y;
            if (y > maxY) maxY = y;
          }
        }
      if (xs.length === 0) return null;
      return {xs, ys, cx: (minX + maxX) / 2, cy: (minY + maxY) / 2, h: Math.max(1, maxY - minY)};
    };
    const nRing = Math.round(FORM_N * 0.34);
    const nGlyph = Math.round(FORM_N * 0.46);
    const nDisc = FORM_N - nRing - nGlyph; // 20%, absorbs rounding
    let i = 0;
    const place = (x: number, y: number, z: number, c: THREE.Color) => {
      posCoin[i * 3] = x;
      posCoin[i * 3 + 1] = y;
      posCoin[i * 3 + 2] = z;
      put(colCoin, i, c);
      i++;
    };
    // ring: evenly spaced around the circle, jittered across the stroke and depth
    for (let k = 0; k < nRing; k++) {
      const a = (k / nRing) * Math.PI * 2;
      const r = COIN_R + (coinRnd() - 0.5) * COIN_STROKE;
      place(r * Math.cos(a), r * Math.sin(a), (coinRnd() - 0.5) * COIN_DEPTH, cGold);
    }
    // glyph: random picks from the ink pixels, mapped to 1.7 units tall, centred
    const g = rasteriseGlyph();
    if (g) {
      const sc = COIN_GLYPH_H / g.h;
      for (let k = 0; k < nGlyph; k++) {
        const p = (coinRnd() * g.xs.length) | 0;
        place(
          (g.xs[p] - g.cx) * sc, -(g.ys[p] - g.cy) * sc, (coinRnd() - 0.5) * 2 * COIN_GLYPH_Z,
          coinRnd() < 0.08 ? cViolet : cLogoWhite,
        );
      }
    } else {
      // no canvas or no ink: the glyph's budget joins the disc so the count holds
      for (let k = 0; k < nGlyph; k++) {
        const r = COIN_DISC_R * Math.sqrt(coinRnd());
        const a = coinRnd() * Math.PI * 2;
        place(r * Math.cos(a), r * Math.sin(a), COIN_DISC_Z, cDim);
      }
    }
    // faint disc fill behind the glyph
    for (let k = 0; k < nDisc; k++) {
      const r = COIN_DISC_R * Math.sqrt(coinRnd());
      const a = coinRnd() * Math.PI * 2;
      place(r * Math.cos(a), r * Math.sin(a), COIN_DISC_Z, cDim);
    }
    return i;
  };
  coinBuilder = buildCoin;

  return {
    posBrain, posLogo, posCoin, posScatter,
    colBrain, colLogo, colCoin, colScatter,
    sBrain, sLogo, sScatter,
    axis, spinAng, spinVel, drift,
    brainMinY, brainMaxY, logoMinY, logoMaxY,
  };
}

let coinBuilder: (() => number) | null = null;
let cached: Forms | null = null;

/** Built once per worker, then reused for every frame that worker renders. */
export function forms(): Forms {
  if (!cached) cached = build();
  return cached;
}

/** The ported coin generator. Fills forms().posCoin / colCoin when called. */
export function buildCoinForm(): number {
  forms();
  return coinBuilder ? coinBuilder() : 0;
}
