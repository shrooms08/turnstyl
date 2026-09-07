/**
 * Beat 5, the three figures. Each one gets 30 frames and cross-fades into the
 * next — no sliding, nothing moves horizontally. Every figure is from
 * docs/EVALS.md; see data.ts for which line each came from.
 */
import React from 'react';
import {AbsoluteFill, Easing, interpolate, useCurrentFrame} from 'remotion';
import {Whole} from '../components/Counter';
import {FIGURES} from '../data';
import {Layout} from '../layout';
import {DISPLAY, GOLD, MARGIN, MONO, OFF_WHITE} from '../theme';

const cubic = Easing.bezier(0.33, 0, 0.15, 1);
const HOLD = 30;
const FADE = 8;

export const Numbers: React.FC<{readonly layout: Layout}> = ({layout}) => {
  const frame = useCurrentFrame();

  return (
    <AbsoluteFill style={{padding: MARGIN}}>
      {FIGURES.map((fig, i) => {
        const start = i * HOLD;
        // The last figure has to be gone by the time the beat ends.
        const outAt = i === FIGURES.length - 1 ? FIGURES.length * HOLD - 8 : start + HOLD;
        const fadeIn = interpolate(frame, [start, start + FADE], [0, 1], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
          easing: cubic,
        });
        const fadeOut = interpolate(frame, [outAt, outAt + FADE], [1, 0], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
          easing: cubic,
        });
        const opacity = fadeIn * fadeOut;
        if (opacity <= 0.001) return null;
        return (
          <AbsoluteFill
            key={fig.label}
            style={{alignItems: 'center', justifyContent: 'center', opacity}}
          >
            <div style={{textAlign: 'center', maxWidth: layout.content}}>
              <div
                style={{
                  fontFamily: MONO,
                  fontWeight: 400,
                  fontSize: 110,
                  color: GOLD,
                  lineHeight: 1.05,
                }}
              >
                <Whole from={0} to={fig.to} startAt={start + 2} durationInFrames={20} />
                {fig.suffix}
              </div>
              <div
                style={{
                  fontFamily: DISPLAY,
                  fontWeight: 300,
                  fontSize: 34,
                  color: OFF_WHITE,
                  lineHeight: 1.3,
                  marginTop: 18,
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
