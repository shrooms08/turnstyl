/**
 * Inline value noise, translated verbatim from the scene in web/index.html.
 * The gyri streamlines walk the rotated gradient of noise2, so this has to
 * behave exactly as it does on the page. It is already a pure hash of its
 * inputs, so it needs no seeding.
 */
export function hash3(i: number, j: number, k: number): number {
  let n = Math.imul(i, 374761393) ^ Math.imul(j, 668265263) ^ Math.imul(k, 1274126177);
  n = Math.imul(n ^ (n >>> 13), 1274126177);
  return ((n ^ (n >>> 16)) >>> 0) / 4294967295;
}

export function fade(t: number): number {
  return t * t * (3 - 2 * t);
}

export function lerp1(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

export function noise3(x: number, y: number, z: number): number {
  const xi = Math.floor(x);
  const yi = Math.floor(y);
  const zi = Math.floor(z);
  const xf = x - xi;
  const yf = y - yi;
  const zf = z - zi;
  const u = fade(xf);
  const v = fade(yf);
  const w = fade(zf);
  const c000 = hash3(xi, yi, zi);
  const c100 = hash3(xi + 1, yi, zi);
  const c010 = hash3(xi, yi + 1, zi);
  const c110 = hash3(xi + 1, yi + 1, zi);
  const c001 = hash3(xi, yi, zi + 1);
  const c101 = hash3(xi + 1, yi, zi + 1);
  const c011 = hash3(xi, yi + 1, zi + 1);
  const c111 = hash3(xi + 1, yi + 1, zi + 1);
  const x00 = lerp1(c000, c100, u);
  const x10 = lerp1(c010, c110, u);
  const x01 = lerp1(c001, c101, u);
  const x11 = lerp1(c011, c111, u);
  return lerp1(lerp1(x00, x10, v), lerp1(x01, x11, v), w) * 2 - 1;
}

/** A fixed slice of the 3D field. */
export function noise2(x: number, y: number): number {
  return noise3(x, y, 0.37);
}

export function smoothstep(e0: number, e1: number, x: number): number {
  const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0)));
  return t * t * (3 - 2 * t);
}
