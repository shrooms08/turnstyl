/**
 * Seeded PRNG. The page scene the forms come from calls Math.random() a few
 * hundred thousand times while it builds; Remotion renders frames in parallel
 * processes, so a random draw that differs between workers would show up as a
 * flicker halfway through the file. Every draw in scene/forms.ts goes through
 * one of these, started from a fixed seed, so every worker builds bit-identical
 * geometry.
 */
export type Rng = () => number;

export function mulberry32(seed: number): Rng {
  let a = seed >>> 0;
  return function random() {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** The one seed the whole teaser is built from. */
export const SCENE_SEED = 0x7175915;
