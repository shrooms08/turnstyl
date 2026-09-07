/**
 * Shot boundaries and the narration track, in frames at 30fps.
 *
 * Every number here is from the brief. The VO files are placed at their exact
 * start frames and never concatenated, so a re-recorded line moves nothing else.
 */
export const STORY_FPS = 30;
export const STORY_DURATION = 1230;

export type Shot = {
  readonly n: number;
  readonly from: number;
  readonly to: number;
  /** True where the camera is cut to rather than moved. */
  readonly cutIn: boolean;
};

export const SHOTS: readonly Shot[] = [
  {n: 1, from: 0, to: 108, cutIn: false},
  {n: 2, from: 108, to: 189, cutIn: false},
  {n: 3, from: 189, to: 390, cutIn: false},
  {n: 4, from: 390, to: 438, cutIn: true},
  {n: 5, from: 438, to: 558, cutIn: false},
  {n: 6, from: 558, to: 690, cutIn: false},
  {n: 7, from: 690, to: 864, cutIn: false},
  {n: 8, from: 864, to: 924, cutIn: true},
  {n: 9, from: 924, to: 1155, cutIn: false},
  {n: 10, from: 1155, to: 1230, cutIn: false},
];

export const shot = (n: number): Shot => {
  const s = SHOTS.find((x) => x.n === n);
  if (!s) throw new Error(`story: no shot ${n}`);
  return s;
};

/** The cut inside shot 9, from the invoice panel to the chain explorer. */
export const SHOT9_CUT = 1040;

/** Shot 5 holds everything still for the 1.5s of silence after the line. */
export const SHOT5_HOLD_FROM = 513;

export type VoLine = {
  readonly n: number;
  readonly file: string;
  readonly at: number;
  /**
   * The room the brief gives this line: the frames between its start and the
   * next line's start, less any silence the brief asks to be held.
   */
  readonly slotFrames: number;
  /**
   * What the file on disk actually measures, in frames, recorded when it was
   * placed. The build warns if a file drifts more than 3 frames from this, which
   * is what catches a re-recorded line that no longer fits its shot.
   */
  readonly expectedFrames: number;
  readonly text: string;
};

/**
 * Eight of the nine lines are 16-28 frames shorter than the room the brief
 * gives them, which is the pause before the next line rather than a fault.
 * VO05 is the exception and lands on its slot exactly, because the brief spends
 * that slack explicitly: 1.5s of silence, held.
 */
export const VO: readonly VoLine[] = [
  {n: 1, file: 'vo/01.mp3', at: 15, slotFrames: 93, expectedFrames: 75, text: 'This agent just did four steps of work.'},
  {n: 2, file: 'vo/02.mp3', at: 108, slotFrames: 81, expectedFrames: 65, text: 'It charged for each one separately.'},
  {n: 3, file: 'vo/03.mp3', at: 189, slotFrames: 201, expectedFrames: 181, text: 'No account. No subscription. USDC on Base, one signature per step.'},
  {n: 4, file: 'vo/04.mp3', at: 390, slotFrames: 48, expectedFrames: 20, text: 'Kill it.'},
  {n: 5, file: 'vo/05.mp3', at: 438, slotFrames: 75, expectedFrames: 75, text: 'It comes back knowing exactly who you are.'},
  {n: 6, file: 'vo/06.mp3', at: 558, slotFrames: 132, expectedFrames: 115, text: 'Buy the same work twice and the second time is half price and instant.'},
  {n: 7, file: 'vo/07.mp3', at: 690, slotFrames: 174, expectedFrames: 156, text: 'Eighteen audits. Six of seven bugs found every run. Every patch compiled.'},
  {n: 8, file: 'vo/08.mp3', at: 864, slotFrames: 60, expectedFrames: 38, text: 'Delete its memory.'},
  {n: 9, file: 'vo/09.mp3', at: 924, slotFrames: 231, expectedFrames: 212, text: 'Now it invoices you again for work you already paid for. Both payments are on chain. Only the memory is gone.'},
];

/** How far a file may drift from `expectedFrames` before the build complains. */
export const VO_TOLERANCE_FRAMES = 3;
