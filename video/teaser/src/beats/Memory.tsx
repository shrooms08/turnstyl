/**
 * Beat 4's own content: what the second audit of the same contract costs.
 *
 * The four token counters are the median tokens each step burned across the
 * 18 audits in docs/EVALS.md; they run to zero together because every step of
 * every second pass was served from memory in 100% of runs.
 */
import React from 'react';
import {AbsoluteFill, Easing, interpolate, useCurrentFrame} from 'remotion';
import {Whole} from '../components/Counter';
import {STEPS} from '../data';
import {cardBlockWidth, Layout} from '../layout';
import {DIM, DISPLAY, GOLD, MONO, OFF_WHITE} from '../theme';

const cubic = Easing.bezier(0.33, 0, 0.15, 1);

const COUNTERS_IN = 4;
const COUNT_TO_ZERO = 25;

export const Memory: React.FC<{readonly layout: Layout}> = ({layout}) => {
  const frame = useCurrentFrame();
  const blockW = cardBlockWidth(layout);

  const countersOpacity = interpolate(frame, [COUNTERS_IN, COUNTERS_IN + 10], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: cubic,
  });

  const line = (start: number) =>
    interpolate(frame, [start, start + 16], [0, 1], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
      easing: cubic,
    });

  return (
    <AbsoluteFill>
      <div
        style={{
          position: 'absolute',
          left: (layout.width - blockW) / 2,
          width: blockW,
          top: layout.captionTop,
          display: 'flex',
          opacity: countersOpacity,
        }}
      >
        {STEPS.map((s) => (
          <div
            key={s.name}
            style={{
              flex: 1,
              textAlign: 'center',
              fontFamily: MONO,
              fontWeight: 400,
              fontSize: 26,
              color: DIM,
              lineHeight: 1,
            }}
          >
            <Whole from={s.tokens} to={0} startAt={COUNTERS_IN + 10} durationInFrames={COUNT_TO_ZERO} />
          </div>
        ))}
      </div>

      <div
        style={{
          position: 'absolute',
          left: 0,
          right: 0,
          top: layout.memoryLineTop,
          textAlign: 'center',
          fontFamily: DISPLAY,
          fontWeight: 300,
          fontSize: 40,
          color: OFF_WHITE,
          lineHeight: 1.2,
          opacity: line(40),
        }}
      >
        same contract, second time
      </div>

      <div
        style={{
          position: 'absolute',
          left: 0,
          right: 0,
          top: layout.memoryMonoTop,
          textAlign: 'center',
          fontFamily: MONO,
          fontWeight: 400,
          fontSize: 24,
          color: GOLD,
          lineHeight: 1.2,
          opacity: line(52),
        }}
      >
        served from memory. 0 tokens.
      </div>
    </AbsoluteFill>
  );
};
