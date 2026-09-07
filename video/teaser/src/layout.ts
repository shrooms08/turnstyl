/**
 * The two compositions share every beat component; this is the only thing that
 * differs between them. Square stacks where wide spreads.
 *
 * All measurements are in device pixels on a 1080-tall frame, measured from the
 * frame centre unless a name says otherwise, and every text element is placed
 * so that it clears the 64px margin.
 */
import {MARGIN} from './theme';

/** Camera: 45deg vertical FOV at z = 6, looking at the origin. */
export const CAMERA_Z = 6;
export const CAMERA_FOV = 45;

const visibleHeight = 2 * CAMERA_Z * Math.tan((CAMERA_FOV * Math.PI) / 180 / 2);

export type Layout = {
  readonly id: 'square' | 'wide';
  readonly width: number;
  readonly height: number;
  /** World units per device pixel, for placing the 3D scene against 2D type. */
  readonly pxPerUnit: number;
  /** Usable width inside the 64px margin. */
  readonly content: number;

  /** beat 1-2: a hard wrap keeps 42px mono inside the margin on the square. */
  readonly refusalBreakAt: number | null;
  /** Display lines take their pre-broken form rather than the browser's. */
  readonly stacked: boolean;
  /** beat 1-2: the brain sits right of centre, in device pixels. */
  readonly brainCenterX: number;
  readonly brainHeightPx: number;

  /** beat 3-4: card grid. */
  readonly cardColumns: number;
  readonly cardsTop: number;
  /** beat 3-4 and beat 6: y of the caption / counters row, from the frame top. */
  readonly captionTop: number;
  readonly memoryLineTop: number;
  readonly memoryMonoTop: number;

  /** beat 7: the mark and the wordmark straddle the frame centre. */
  readonly markSizePx: number;
  readonly lockupGap: number;
  readonly lockupY: number;
  readonly urlY: number;
};

const CARD_W = 300;
const CARD_H = 180;
const CARD_GAP = 48;

export const CARD = {width: CARD_W, height: CARD_H, gap: CARD_GAP};

export const square: Layout = {
  id: 'square',
  width: 1080,
  height: 1080,
  pxPerUnit: 1080 / visibleHeight,
  content: 1080 - 2 * MARGIN,

  // 47 mono glyphs at 42px measure ~1184px, wider than the 952px of content
  // the margin leaves. The line breaks after "unpaid" rather than shrinking:
  // the type size is what has to survive a 400px-wide timeline.
  refusalBreakAt: 34,
  stacked: true,
  brainCenterX: 228,
  brainHeightPx: 620,

  cardColumns: 2,
  cardsTop: 250,
  captionTop: 690,
  memoryLineTop: 770,
  memoryMonoTop: 844,

  markSizePx: 260,
  lockupGap: 48,
  lockupY: -45,
  urlY: 150,
};

export const wide: Layout = {
  id: 'wide',
  width: 1920,
  height: 1080,
  pxPerUnit: 1080 / visibleHeight,
  content: 1920 - 2 * MARGIN,

  refusalBreakAt: null,
  stacked: false,
  brainCenterX: 456,
  brainHeightPx: 660,

  cardColumns: 4,
  cardsTop: 330,
  captionTop: 555,
  memoryLineTop: 625,
  memoryMonoTop: 697,

  markSizePx: 300,
  lockupGap: 56,
  lockupY: -45,
  urlY: 150,
};

/** Width of the dealt card block, used to align the caption and the counters. */
export const cardBlockWidth = (layout: Layout) =>
  layout.cardColumns * CARD_W + (layout.cardColumns - 1) * CARD_GAP;

/** Height of the dealt card block. */
export const cardBlockHeight = (layout: Layout) => {
  const rows = Math.ceil(4 / layout.cardColumns);
  return rows * CARD_H + (rows - 1) * CARD_GAP;
};

/** Top-left corner of card `i`, in device pixels from the frame's top-left. */
export const cardRect = (layout: Layout, i: number) => {
  const col = i % layout.cardColumns;
  const row = Math.floor(i / layout.cardColumns);
  const blockW = cardBlockWidth(layout);
  return {
    left: (layout.width - blockW) / 2 + col * (CARD_W + CARD_GAP),
    top: layout.cardsTop + row * (CARD_H + CARD_GAP),
    width: CARD_W,
    height: CARD_H,
  };
};
