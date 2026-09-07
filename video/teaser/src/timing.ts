/**
 * Beat boundaries, in frames at 30fps. Both compositions run the same 600
 * frames on the same clock; only layout differs between them.
 */
export const FPS = 30;
export const DURATION = 600;

export const BEAT = {
  refusal: {from: 0, duration: 90},
  who: {from: 90, duration: 90},
  meter: {from: 180, duration: 120},
  memory: {from: 300, duration: 90},
  numbers: {from: 390, duration: 90},
  del: {from: 480, duration: 90},
  name: {from: 570, duration: 30},
} as const;

/** The red refusal line is one continuous object across beats 1 and 2, so it
 *  owns a sequence that spans both. */
export const REFUSAL_SPAN = {from: 0, duration: 180};

/** The four cards are dealt in beat 3 and stay in place through beat 4. */
export const CARDS_SPAN = {from: 180, duration: 210};

/** The particle scene appears twice: behind beats 1-2, and again for beats
 *  6-7. It is absent for beats 3-5, which play on black. */
export const SCENE_INTRO = {from: 0, duration: 180};
export const SCENE_OUTRO = {from: 480, duration: 120};

/* ── beat 3: dealing the meter ──────────────────────────────────────── */
export const CARD_STAGGER = 8; // frames between one card and the next
export const CARD_DEAL_FRAMES = 30; // spring settle
export const PAID_AFTER_SETTLE = 6; // the pill stamps 6 frames after a card lands

/* ── beat 6: local frames inside the delete beat ────────────────────── */
export const DEL = {
  washIn: 8,
  typeFrames: 26, // "rm memory.db"
  commandOut: [34, 40] as const,
  flashIn: 36, // cards and brain snap back
  scatterStart: 46,
  scatterFrames: 25, // out and gone
  lineIn: [50, 62] as const,
  outroFade: [78, 90] as const,
} as const;

/* ── beat 7: the name ───────────────────────────────────────────────── */
export const NAME = {
  markFrames: 18, // particles travel into the mark
  wordmarkIn: [6, 20] as const,
  urlIn: [12, 26] as const,
} as const;
