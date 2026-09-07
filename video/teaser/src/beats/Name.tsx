/**
 * Beat 7, the name. The mark is the particle form (see scene/), so the only
 * thing drawn here is the wordmark beside it and the address under both.
 */
import React from 'react';
import {AbsoluteFill, Easing, interpolate, useCurrentFrame} from 'remotion';
import {URL} from '../data';
import {Layout} from '../layout';
import {NAME} from '../timing';
import {DIM, DISPLAY, MONO, OFF_WHITE} from '../theme';

const cubic = Easing.bezier(0.33, 0, 0.15, 1);

export const Name: React.FC<{readonly layout: Layout}> = ({layout}) => {
  const frame = useCurrentFrame();

  const wordmark = interpolate(frame, [NAME.wordmarkIn[0], NAME.wordmarkIn[1]], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: cubic,
  });
  const url = interpolate(frame, [NAME.urlIn[0], NAME.urlIn[1]], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: cubic,
  });

  return (
    <AbsoluteFill>
      {/* The wordmark starts half a gap right of the frame centre; the mark
          fills the mirror image of that space, drawn by the particles. */}
      <div
        style={{
          position: 'absolute',
          left: layout.width / 2 + layout.lockupGap / 2,
          top: layout.height / 2 + layout.lockupY,
          transform: 'translateY(-50%)',
          fontFamily: DISPLAY,
          fontWeight: 300,
          fontSize: 64,
          lineHeight: 1,
          color: OFF_WHITE,
          opacity: wordmark,
        }}
      >
        turnstyl
      </div>
      <div
        style={{
          position: 'absolute',
          left: 0,
          right: 0,
          top: layout.height / 2 + layout.urlY,
          textAlign: 'center',
          fontFamily: MONO,
          fontWeight: 400,
          fontSize: 22,
          lineHeight: 1,
          color: DIM,
          opacity: url,
        }}
      >
        {URL}
      </div>
    </AbsoluteFill>
  );
};
