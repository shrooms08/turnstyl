/**
 * A number that springs from one value to another. Used for the step prices,
 * the token counters and the three figures in beat 5 — never a linear ramp.
 */
import React from 'react';
import {spring, useCurrentFrame, useVideoConfig} from 'remotion';

export const useCount = (from: number, to: number, startAt: number, durationInFrames: number) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const s = spring({
    frame: frame - startAt,
    fps,
    config: {damping: 200, mass: 0.7, stiffness: 110},
    durationInFrames,
  });
  return from + (to - from) * s;
};

export const Money: React.FC<{
  readonly from: number;
  readonly to: number;
  readonly startAt: number;
  readonly durationInFrames: number;
  readonly style?: React.CSSProperties;
}> = ({from, to, startAt, durationInFrames, style}) => {
  const v = useCount(from, to, startAt, durationInFrames);
  return <span style={style}>{v.toFixed(2)}</span>;
};

export const Whole: React.FC<{
  readonly from: number;
  readonly to: number;
  readonly startAt: number;
  readonly durationInFrames: number;
  readonly style?: React.CSSProperties;
}> = ({from, to, startAt, durationInFrames, style}) => {
  const v = useCount(from, to, startAt, durationInFrames);
  return <span style={style}>{Math.round(v)}</span>;
};
