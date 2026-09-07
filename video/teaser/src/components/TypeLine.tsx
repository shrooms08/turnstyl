/**
 * A mono line that types itself out, with a block cursor blinking at 2Hz.
 * The reveal is eased, not linear: it opens fast and settles into the last
 * few characters.
 */
import React from 'react';
import {Easing, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import {MONO, MONO_ADVANCE} from '../theme';

const typeEase = Easing.bezier(0.2, 0.6, 0.25, 1);

export const TypeLine: React.FC<{
  readonly text: string;
  /** Character index to replace with a hard line break, or null to keep one line. */
  readonly breakAt: number | null;
  readonly typeFrames: number;
  readonly fontSize: number;
  readonly color: string;
  readonly startAt?: number;
}> = ({text, breakAt, typeFrames, fontSize, color, startAt = 0}) => {
  const frame = useCurrentFrame() - startAt;
  const {fps} = useVideoConfig();

  const laidOut = breakAt === null ? text : text.slice(0, breakAt) + '\n' + text.slice(breakAt + 1);

  const shownChars = Math.round(
    interpolate(frame, [0, typeFrames], [0, laidOut.length], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
      easing: typeEase,
    }),
  );

  // 2Hz: on for half of every half-second, from the first frame.
  const period = fps / 2;
  const cursorOn = frame >= 0 && frame % period < period / 2;

  return (
    <div
      style={{
        fontFamily: MONO,
        fontWeight: 400,
        fontSize,
        lineHeight: 1.4,
        color,
        whiteSpace: 'pre-wrap',
        textAlign: 'center',
      }}
    >
      {laidOut.slice(0, shownChars)}
      <span
        style={{
          display: 'inline-block',
          width: fontSize * MONO_ADVANCE,
          height: fontSize * 0.95,
          background: color,
          opacity: cursorOn ? 1 : 0,
          verticalAlign: -fontSize * 0.16,
        }}
      />
    </div>
  );
};
