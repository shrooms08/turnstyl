/**
 * Where the particle scene sits in the frame, and how far back it sits.
 *
 * One canvas per appearance: the brain behind beats 1-2, and the delete/mark
 * canvas for beats 6-7. Beats 3-5 carry themselves on black, so no canvas is
 * mounted for them at all.
 *
 * The scene's weight in the frame is a composite opacity on the canvas layer,
 * not the material's alpha. 5000 additively blended wireframes pile up on each
 * other, so a low material alpha still saturates to a solid mass; scaling the
 * finished render is the only thing that actually puts the form behind the type.
 */
import {ThreeCanvas} from '@remotion/three';
import React from 'react';
import {AbsoluteFill, Easing, interpolate, useCurrentFrame} from 'remotion';
import {CAMERA_FOV, CAMERA_Z, Layout} from '../layout';
import {Particles, SceneGeometry, SceneMode} from './Particles';

const cubic = Easing.bezier(0.33, 0, 0.15, 1);

export const SceneCanvas: React.FC<{
  readonly mode: SceneMode;
  readonly span: number;
  readonly layout: Layout;
  /** How far back the brain sits behind the refusal line, in beats 1-2. */
  readonly brainOpacity: number;
}> = ({mode, span, layout, brainOpacity}) => {
  const frame = useCurrentFrame();

  // The mark and the wordmark straddle the frame centre: the mark's centre sits
  // half a gap plus half its own width to the left of it.
  const markCenterX = -(layout.lockupGap / 2 + layout.markSizePx / 2);

  // Beat 6 keeps the scatter behind the cards; beat 7 brings the mark forward,
  // because by then it is the only thing on the frame.
  const layerOpacity =
    mode === 'intro'
      ? brainOpacity
      : interpolate(frame, [86, 96], [0.55, 1], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
          easing: cubic,
        });

  const geometry: SceneGeometry = {
    pxPerUnit: layout.pxPerUnit,
    brainCenterX: layout.brainCenterX,
    brainHeightPx: layout.brainHeightPx,
    markSizePx: layout.markSizePx,
    markCenterX,
    markCenterY: layout.lockupY,
  };

  return (
    <AbsoluteFill style={{opacity: layerOpacity}}>
      <ThreeCanvas
        width={layout.width}
        height={layout.height}
        camera={{fov: CAMERA_FOV, position: [0, 0, CAMERA_Z], near: 0.1, far: 100}}
        gl={{antialias: true, preserveDrawingBuffer: true, alpha: true}}
        style={{background: 'transparent'}}
      >
        <Particles mode={mode} span={span} geometry={geometry} />
      </ThreeCanvas>
    </AbsoluteFill>
  );
};
