/**
 * Story geometry, in design units.
 *
 * Nothing in the story is written in output pixels. Every size, offset, radius
 * and camera distance below is a design unit, and the renderer multiplies it by
 * `scale = width / designWidth`. story-4k and story-wide share a design width,
 * so 4K is exactly 2x wide and the two layouts are the same picture. The square
 * has its own design width because a 1:1 frame is a different composition, not
 * a cropped one.
 */

export type StoryLayoutId = 'story-4k' | 'story-wide' | 'story-square';

export type StoryLayout = {
  readonly id: StoryLayoutId;
  readonly width: number;
  readonly height: number;
  /** The frame these design units were authored against. */
  readonly designWidth: number;
  readonly designHeight: number;
  readonly scale: number;
  /** The 1:1 frame re-places the panels rather than cropping the wide one. */
  readonly square: boolean;
  /** Drawing-buffer multiplier for the particle canvas. */
  readonly dpr: number;
};

const layout = (
  id: StoryLayoutId,
  width: number,
  height: number,
  designWidth: number,
  designHeight: number,
  dpr: number,
): StoryLayout => ({
  id,
  width,
  height,
  designWidth,
  designHeight,
  scale: width / designWidth,
  square: designWidth === designHeight,
  dpr,
});

/** 4K and wide are the same layout; only `scale` differs, and it is exactly 2. */
export const story4k = layout('story-4k', 3840, 2160, 1920, 1080, 2);
export const storyWide = layout('story-wide', 1920, 1080, 1920, 1080, 1.5);
export const storySquare = layout('story-square', 1080, 1080, 1080, 1080, 1.5);

/* ── the 3D world ───────────────────────────────────────────────────────
 * Right-handed enough for our purposes, with CSS's axes: +x right, +y DOWN,
 * -z away from the viewer. Panels live at negative z; the camera starts well
 * in front of them and travels down the corridor.
 * -------------------------------------------------------------------- */

/**
 * CSS perspective, in design units. Smaller is a wider lens, and a wider lens is
 * what makes a dolly read as travel rather than a zoom: at 1100 the panels grow
 * and their angles open out as the camera closes on them.
 */
export const PERSPECTIVE = 1100;

export type Placement = {
  readonly x: number;
  readonly y: number;
  readonly z: number;
  /** Degrees. Panels are always at an angle; none of them sits flat on. */
  readonly ry: number;
  readonly rx: number;
  readonly w: number;
  readonly h: number;
};

export type World = {
  readonly job: Placement;
  readonly jobMemory: Placement;
  readonly termKill: Placement;
  readonly termRm: Placement;
  readonly invoice: Placement;
  readonly explorer: Placement;
};

const wideWorld: World = {
  job: {x: 0, y: 0, z: -700, ry: 17, rx: -3, w: 1500, h: 400},
  jobMemory: {x: 1900, y: -10, z: -720, ry: -15, rx: -3, w: 1500, h: 400},
  // The kill terminal sits between the camera and the job panel, so pulling
  // back off it in shot 5 reveals the job panel still standing there.
  termKill: {x: 40, y: 20, z: -240, ry: -19, rx: 2, w: 1560, h: 430},
  termRm: {x: 1050, y: 10, z: -2400, ry: 14, rx: -2, w: 1200, h: 250},
  invoice: {x: 760, y: -20, z: -3200, ry: 16, rx: -3, w: 1180, h: 370},
  explorer: {x: 2380, y: 10, z: -3260, ry: -13, rx: -2, w: 1400, h: 330},
};

/**
 * The square frame is 1080 wide where the wide frame is 1920, so the panels
 * come in off the edges, sit a little further away, and the two job panels are
 * closer together — the camera still passes between them, but it does not have
 * 1920 units of width to do it in.
 */
const squareWorld: World = {
  job: {x: 0, y: 0, z: -700, ry: 17, rx: -3, w: 1080, h: 430},
  jobMemory: {x: 1380, y: -10, z: -720, ry: -15, rx: -3, w: 1080, h: 430},
  termKill: {x: 30, y: 20, z: -240, ry: -19, rx: 2, w: 1080, h: 470},
  termRm: {x: 760, y: 10, z: -2400, ry: 14, rx: -2, w: 1000, h: 260},
  invoice: {x: 550, y: -20, z: -3200, ry: 16, rx: -3, w: 920, h: 400},
  explorer: {x: 1720, y: 10, z: -3260, ry: -13, rx: -2, w: 1020, h: 350},
};

export const worldFor = (l: StoryLayout): World => (l.square ? squareWorld : wideWorld);
