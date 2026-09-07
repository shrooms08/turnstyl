/**
 * Beat 2's two display lines. They arrive 10 frames apart under the demoted
 * refusal, and say who is doing the refusing without naming the product.
 */
import React from 'react';
import {AbsoluteFill, Easing, interpolate, useCurrentFrame} from 'remotion';
import {WHO_LINES} from '../data';
import {Layout} from '../layout';
import {DISPLAY, MARGIN, OFF_WHITE} from '../theme';

const cubic = Easing.bezier(0.33, 0, 0.15, 1);

export const WhoIsRefusing: React.FC<{readonly layout: Layout}> = ({layout}) => {
  const frame = useCurrentFrame();

  return (
    <AbsoluteFill
      style={{
        padding: MARGIN,
        alignItems: 'center',
        justifyContent: 'center',
        // clear of the demoted refusal line in the upper third
        paddingTop: layout.height * 0.42,
      }}
    >
      <div style={{display: 'flex', flexDirection: 'column', gap: 20, alignItems: 'center'}}>
        {WHO_LINES.map((line, i) => {
          const start = 14 + i * 10;
          const opacity = interpolate(frame, [start, start + 18], [0, 1], {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
            easing: cubic,
          });
          const lift = interpolate(frame, [start, start + 18], [14, 0], {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
            easing: cubic,
          });
          return (
            <div
              key={line.wide}
              style={{
                fontFamily: DISPLAY,
                fontWeight: 300,
                fontSize: 56,
                lineHeight: 1.22,
                color: OFF_WHITE,
                textAlign: 'center',
                maxWidth: layout.content,
                whiteSpace: 'pre-line',
                opacity,
                transform: `translateY(${lift}px)`,
              }}
            >
              {layout.stacked ? line.square : line.wide}
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
