/**
 * Every number on screen comes from docs/EVALS.md and the run behind it,
 * evals/results/2026-09-07.json — generated 2026-09-07T11:01:58Z by
 * scripts/eval.py, claude-haiku-4-5, 3 runs per contract, 18 audits.
 *
 * Prices are turnstyl's per-step price list, not a figure from the eval.
 * Token counts are figures from the eval and are labelled here with where
 * each one comes from.
 */

export type Step = {
  readonly step: number;
  readonly name: string;
  /** What the step costs the first time, in USDC. */
  readonly price: number;
  /** What it costs when the same contract comes back — half. */
  readonly memoryPrice: number;
  /**
   * Median tokens (in + out) that step burned across the 18 audits, from
   * runs[].steps[] in evals/results/2026-09-07.json.
   */
  readonly tokens: number;
};

export const STEPS: readonly Step[] = [
  {step: 1, name: 'scope', price: 0.0, memoryPrice: 0.0, tokens: 714},
  {step: 2, name: 'findings', price: 0.5, memoryPrice: 0.25, tokens: 1039},
  {step: 3, name: 'patch', price: 0.75, memoryPrice: 0.38, tokens: 1847},
  {step: 4, name: 'verify', price: 0.25, memoryPrice: 0.12, tokens: 2164},
];

export type Figure = {
  /** The part of the figure that counts up. */
  readonly to: number;
  /** Rendered around the counted number. */
  readonly suffix: string;
  readonly label: string;
};

/**
 * beat 5, all three from docs/EVALS.md:
 *   18 audits in total (the header line);
 *   6 of the 7 planted bugs are found in 3/3 runs, the seventh in 2/3;
 *   patch compiles (forge build) 100% over 18 runs.
 */
export const FIGURES: readonly Figure[] = [
  {to: 18, suffix: '', label: 'audits run'},
  {to: 6, suffix: ' of 7', label: 'known bugs found, every run'},
  {to: 18, suffix: ' of 18', label: 'patches that compiled'},
];

export const REFUSAL_TEXT = 'REFUSED: you left 0.25 USDC unpaid on job 9e33.';

/**
 * Display lines that are wider than the square frame's 952px of content. The
 * break is chosen rather than left to the browser, so the square never orphans
 * a word. The wide frame takes them whole.
 */
export type WrappedLine = {readonly wide: string; readonly square: string};

export const WHO_LINES: readonly WrappedLine[] = [
  {
    wide: 'an agent that sells work one step at a time',
    square: 'an agent that sells work\none step at a time',
  },
  {
    wide: 'and remembers everyone who buys',
    square: 'and remembers everyone who buys',
  },
];

export const INVOICE_LINE: WrappedLine = {
  wide: 'now it invoices you again for work you already paid for',
  square: 'now it invoices you again for work\nyou already paid for',
};
export const DELETE_COMMAND = 'rm memory.db';
export const URL = 'shrooms08.github.io/turnstyl';
