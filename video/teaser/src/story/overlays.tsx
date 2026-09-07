/**
 * The only words on screen, and the red bleed.
 *
 * These are design elements at display size, not subtitles: the piece plays
 * with sound and carries no burned-in captions.
 *
 * None of the type here fades on `opacity` or goes near a filter. Chrome
 * rasterises a composited layer once and then scales it, which at 4K is exactly
 * the softness the brief rules out, so every fade is done on the colour's own
 * alpha channel and every reveal that can be is done by revealing characters.
 */
import React from 'react';
import {AbsoluteFill, Easing, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {DISPLAY, MONO, OFF_WHITE, RED} from '../theme';
import {StoryLayout} from './config';
import {alpha, APP} from './panelStyle';
import {MARK_HOLD_TO, MARK_IN} from './scene';

const cubic = Easing.bezier(0.33, 0, 0.15, 1);

const ease = (frame: number, from: number, to: number) =>
  interpolate(frame, [from, to], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: cubic,
  });

/* ── shot 3: the wordmark, in and out with the mark ──────────────────── */

export const Shot3Wordmark: React.FC<{readonly layout: StoryLayout}> = ({layout}) => {
  const frame = useCurrentFrame();
  const s = layout.scale;
  const a = ease(frame, MARK_IN + 14, MARK_IN + 34) * (1 - ease(frame, MARK_HOLD_TO, MARK_HOLD_TO + 22));
  if (a <= 0.004) return null;

  return (
    <AbsoluteFill style={{alignItems: 'center', justifyContent: 'flex-end', paddingBottom: 132 * s}}>
      <div
        style={{
          fontFamily: DISPLAY,
          fontWeight: 300,
          fontSize: 72 * s,
          lineHeight: 1,
          letterSpacing: 0.01 * 72 * s,
          color: alpha(OFF_WHITE, a),
        }}
      >
        turnstyl
      </div>
    </AbsoluteFill>
  );
};

/* ── shot 5: "it" | panel | "remembers" ──────────────────────────────── */

/**
 * The words flank the job panel rather than sitting over it: "it" to the left of
 * the frame's middle, "remembers" to the right, the panel in the gap between
 * them. They arrive 8 frames apart and then everything holds for the silence.
 */
export const Shot5Words: React.FC<{readonly layout: StoryLayout}> = ({layout}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const s = layout.scale;

  // The square has no room to put a word either side of the panel, so it stacks
  // them above and below it instead.
  const stacked = layout.square;
  const size = (stacked ? 84 : 96) * s;
  const inset = (stacked ? 0 : 56) * s;

  const words = [
    {text: 'it', at: 452},
    {text: 'remembers', at: 460},
  ];

  // They hold through the silence and clear as shot 6 starts moving again.
  const out = ease(frame, 558, 576);

  const render = (w: {text: string; at: number}, i: number) => {
    const a = ease(frame, w.at, w.at + 20) * (1 - out);
    if (a <= 0.004) return null;
    const rise = spring({
      frame: frame - w.at,
      fps,
      config: {damping: 200, mass: 0.8, stiffness: 90},
      durationInFrames: 26,
    });
    const lift = (1 - rise) * 22 * s;
    return (
      <div
        key={w.text}
        style={{
          fontFamily: DISPLAY,
          fontWeight: 300,
          fontSize: size,
          lineHeight: 1,
          color: alpha(OFF_WHITE, a),
          transform: `translateY(${lift}px)`,
          whiteSpace: 'nowrap',
          textAlign: stacked ? 'center' : i === 0 ? 'right' : 'left',
        }}
      >
        {w.text}
      </div>
    );
  };

  if (stacked) {
    return (
      <AbsoluteFill
        style={{
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'space-between',
          paddingTop: 118 * s,
          paddingBottom: 118 * s,
        }}
      >
        {words.map(render)}
      </AbsoluteFill>
    );
  }

  return (
    <AbsoluteFill style={{flexDirection: 'row', alignItems: 'center'}}>
      <div style={{flex: 1, display: 'flex', justifyContent: 'flex-end', paddingRight: inset}}>
        {render(words[0], 0)}
      </div>
      {/* the gap the panel sits in */}
      <div style={{width: 640 * s, flex: 'none'}} />
      <div style={{flex: 1, display: 'flex', justifyContent: 'flex-start', paddingLeft: inset}}>
        {render(words[1], 1)}
      </div>
    </AbsoluteFill>
  );
};

/* ── shot 7: three figures, assembled letter by letter ───────────────── */

/**
 * Every figure is from docs/EVALS.md: 18 audits in total; six of the seven
 * planted bugs found in 3 of 3 runs (the seventh in 2 of 3); patch compiles
 * 100% over 18 runs.
 */
const FIGURES = [
  {number: '18', label: 'audits'},
  {number: '6 of 7', label: 'bugs, every run'},
  {number: '18 of 18', label: 'patches compiled'},
] as const;

const FIGURE_AT = [700, 754, 808];
const FIGURE_HOLD = 50;

export const Shot7Figures: React.FC<{readonly layout: StoryLayout}> = ({layout}) => {
  const frame = useCurrentFrame();
  const s = layout.scale;

  return (
    <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center'}}>
      {FIGURES.map((fig, i) => {
        const at = FIGURE_AT[i];
        const out = at + FIGURE_HOLD;
        // Letters land one at a time; the label follows once the number is whole.
        const chars = Math.round(
          interpolate(frame, [at, at + 16], [0, fig.number.length], {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
            easing: cubic,
          }),
        );
        const numberAlpha = 1 - ease(frame, out, out + 12);
        const labelAlpha = ease(frame, at + 14, at + 30) * numberAlpha;
        if (frame < at || numberAlpha <= 0.004) return null;

        return (
          <AbsoluteFill key={fig.label} style={{alignItems: 'center', justifyContent: 'center'}}>
            <div style={{textAlign: 'center'}}>
              <div
                style={{
                  fontFamily: MONO,
                  fontWeight: 400,
                  fontSize: 110 * s,
                  lineHeight: 1.05,
                  color: alpha(APP.gold, numberAlpha),
                  fontVariantNumeric: 'tabular-nums',
                  whiteSpace: 'pre',
                }}
              >
                {fig.number.slice(0, chars)}
              </div>
              <div
                style={{
                  fontFamily: DISPLAY,
                  fontWeight: 300,
                  fontSize: 36 * s,
                  lineHeight: 1.3,
                  marginTop: 18 * s,
                  color: alpha(OFF_WHITE, labelAlpha),
                }}
              >
                {fig.label}
              </div>
            </div>
          </AbsoluteFill>
        );
      })}
    </AbsoluteFill>
  );
};

/* ── shot 8: the red bleed ───────────────────────────────────────────── */

export const RedBleed: React.FC = () => {
  const frame = useCurrentFrame();
  const a = ease(frame, 872, 900) * 0.12 * (1 - ease(frame, 1120, 1160));
  if (a <= 0.002) return null;
  return <AbsoluteFill style={{background: RED, opacity: a}} />;
};

/* ── shot 10: the end card ───────────────────────────────────────────── */

export const EndCard: React.FC<{readonly layout: StoryLayout}> = ({layout}) => {
  const frame = useCurrentFrame();
  const s = layout.scale;
  const word = ease(frame, 1172, 1194);
  const url = ease(frame, 1182, 1206);
  if (word <= 0.004 && url <= 0.004) return null;

  const gap = 48 * s;
  const markSize = layout.square ? 300 * s : 300 * s;

  return (
    <AbsoluteFill>
      <div
        style={{
          position: 'absolute',
          left: layout.width / 2 + gap / 2,
          top: layout.height / 2 - 45 * s,
          transform: 'translateY(-50%)',
          fontFamily: DISPLAY,
          fontWeight: 300,
          fontSize: 72 * s,
          lineHeight: 1,
          color: alpha(OFF_WHITE, word),
          whiteSpace: 'nowrap',
        }}
      >
        turnstyl
      </div>
      <div
        style={{
          position: 'absolute',
          left: 0,
          right: 0,
          top: layout.height / 2 + markSize / 2 + 34 * s,
          textAlign: 'center',
          fontFamily: MONO,
          fontWeight: 400,
          fontSize: 24 * s,
          lineHeight: 1,
          color: alpha(OFF_WHITE, 0.55 * url),
        }}
      >
        shrooms08.github.io/turnstyl
      </div>
    </AbsoluteFill>
  );
};
