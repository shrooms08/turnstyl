/**
 * Panel styling, taken from web/static/turnstyl.css so the panels in the video
 * are the app's surfaces rather than a lookalike.
 *
 * From the stylesheet's :root and its .card / .step / .pill / .tag rules:
 *   --card   #070707     the panel surface
 *   --card-2 #0B0B0B     the step card inside it
 *   --line   rgba(255,255,255,.09)
 *   --ink    #EFECE6   --dim #9C968C   --mute #615C55
 *   --gold   #E2B04A   --violet #7F77DD   --red #E5484D
 *
 * The panel shell uses the 8% border the brief asks for rather than the
 * stylesheet's 9%; everything inside keeps the app's own value.
 *
 * Every number is a design unit. Multiply by the layout's scale before use.
 */
import {DISPLAY, MONO} from '../theme';

export const APP = {
  card: '#070707',
  card2: '#0B0B0B',
  line: 'rgba(255,255,255,.09)',
  line2: 'rgba(255,255,255,.16)',
  ink: '#EFECE6',
  dim: '#9C968C',
  mute: '#615C55',
  gold: '#E2B04A',
  violet: '#7F77DD',
  red: '#E5484D',
} as const;

/** The panel shell: rounded, near-black, 1px at 8% white, lit by its own content. */
export const PANEL_BORDER = 'rgba(255,255,255,0.08)';
export const PANEL_RADIUS = 14;

export const fonts = {display: DISPLAY, mono: MONO};

/** rgba() for a hex colour, so type can fade on its colour instead of opacity. */
export const alpha = (hex: string, a: number): string => {
  const h = hex.replace('#', '');
  const n = parseInt(h.length === 3 ? h.split('').map((c) => c + c).join('') : h, 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
};

/** app.html's trunc(): six from the front, four from the back. */
export const trunc = (v: string): string => (v.length <= 13 ? v : `${v.slice(0, 6)}…${v.slice(-4)}`);

/** app.html's usdc(). */
export const usdc = (n: number): string => n.toFixed(2);
