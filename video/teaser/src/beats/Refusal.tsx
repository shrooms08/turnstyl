/**
 * Beats 1 and 2, the red line.
 *
 * It types itself out centred and full size, holds, then shrinks to 0.55 and
 * moves to the upper third at 40% opacity while the two display lines arrive
 * under it. One element across both beats, so the move reads as the same
 * sentence being demoted rather than a cut.
 */
import React from 'react';
import {AbsoluteFill, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {TypeLine} from '../components/TypeLine';
import {REFUSAL_TEXT} from '../data';
import {Layout} from '../layout';
import {MARGIN, RED} from '../theme';

const TYPE_FRAMES = 40;
/** Local frame at which beat 2 takes over. */
const DEMOTE_AT = 90;

export const Refusal: React.FC<{readonly layout: Layout}> = ({layout}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();

  const demote = spring({
    frame: frame - DEMOTE_AT,
    fps,
    config: {damping: 200, mass: 0.8, stiffness: 90},
    durationInFrames: 24,
  });

  const scale = 1 + (0.55 - 1) * demote;
  const opacity = 1 + (0.4 - 1) * demote;
  // Centre of the frame to the centre of the upper third.
  const y = (layout.height / 3 - layout.height / 2) * demote;

  return (
    <AbsoluteFill
      style={{
        padding: MARGIN,
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <div style={{transform: `translateY(${y}px) scale(${scale})`, opacity}}>
        <TypeLine
          text={REFUSAL_TEXT}
          breakAt={layout.refusalBreakAt}
          typeFrames={TYPE_FRAMES}
          fontSize={42}
          color={RED}
        />
      </div>
    </AbsoluteFill>
  );
};
