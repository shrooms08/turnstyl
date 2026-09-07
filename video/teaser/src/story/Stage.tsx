/**
 * The 3D stage: a CSS perspective container with one camera transform on the
 * world, and panels placed at fixed points inside it.
 *
 * The world element carries the inverse of the camera pose, which is what makes
 * a single transform move everything at once:
 *
 *     rotateX(-pitch) rotateY(-yaw) translate3d(-cx, -cy, -cz)
 *
 * CSS's y axis points down, so a positive y in a placement is below the centre
 * line. Nothing here needs three.js; the particle field behind it is the only
 * WebGL in the piece.
 */
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {cameraAt} from './camera';
import {Placement, PERSPECTIVE, StoryLayout} from './config';

export const Stage: React.FC<{
  readonly layout: StoryLayout;
  readonly frame: number;
  readonly children: React.ReactNode;
}> = ({layout, frame, children}) => {
  const cam = cameraAt(frame);
  const s = layout.scale;

  return (
    <AbsoluteFill
      style={{
        perspective: PERSPECTIVE * s,
        perspectiveOrigin: '50% 50%',
        overflow: 'hidden',
      }}
    >
      <AbsoluteFill
        style={{
          transformStyle: 'preserve-3d',
          transform:
            `rotateX(${-cam.rx}deg) rotateY(${-cam.ry}deg) ` +
            `translate3d(${-cam.x * s}px, ${-cam.y * s}px, ${-cam.z * s}px)`,
        }}
      >
        {children}
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

/**
 * One panel, parked in the world at an angle. `opacity` is on the panel shell,
 * which the brief allows; it is never put on a type layer.
 */
export const Panel3D: React.FC<{
  readonly layout: StoryLayout;
  readonly at: Placement;
  readonly opacity: number;
  /** Added to the placement's own yaw, for panels that swing. */
  readonly ryOffset?: number;
  /** Added to the placement's own z, for panels that move in. */
  readonly zOffset?: number;
  readonly children: React.ReactNode;
}> = ({layout, at, opacity, ryOffset = 0, zOffset = 0, children}) => {
  if (opacity <= 0.002) return null;
  const s = layout.scale;

  return (
    <div
      style={{
        position: 'absolute',
        left: '50%',
        top: '50%',
        width: at.w * s,
        height: at.h * s,
        marginLeft: (-at.w * s) / 2,
        marginTop: (-at.h * s) / 2,
        transform:
          `translate3d(${at.x * s}px, ${at.y * s}px, ${(at.z + zOffset) * s}px) ` +
          `rotateY(${at.ry + ryOffset}deg) rotateX(${at.rx}deg)`,
        transformStyle: 'preserve-3d',
        opacity,
      }}
    >
      {children}
    </div>
  );
};
