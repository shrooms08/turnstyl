/**
 * The particle environment for the narrated story.
 *
 * Same field as the teaser — the ported scene in src/scene, 5000 wireframe
 * tetrahedra, additive, seeded — driven a different way. It drifts behind
 * everything at low brightness for the whole piece, gathers into the turnstyl
 * mark twice (shot 3 and the end card), and blows outward in shot 8.
 *
 * The field is a separate WebGL canvas and does not share the CSS camera, so it
 * borrows the camera's motion instead: the group's offset and scale are driven
 * from the same pose, which is what keeps the dark feeling like one space
 * rather than a backdrop pasted behind a 3D layer.
 */
import React from 'react';
import {Easing, interpolate, spring} from 'remotion';
import {CAMERA_FOV, CAMERA_Z, Layout} from '../layout';
import {SceneDriver, SceneState} from '../scene/Particles';
import {SceneCanvas} from '../scene/SceneCanvas';
import {cameraAt} from './camera';
import {StoryLayout} from './config';
import {SHOT9_CUT} from './timing';

const cubic = Easing.bezier(0.33, 0, 0.15, 1);

/* ── where the field does something other than drift ─────────────────── */

/** Shot 3: gather into the mark, hold 40 frames, release back to drift. */
export const MARK_IN = 232;
export const MARK_HOLD_FROM = 272;
export const MARK_HOLD_TO = 312;
export const MARK_OUT = 352;

/** Shot 8: the scatter, as the frame bleeds red. */
const BLOW_FROM = 872;
const BLOW_TO = 924;

/** Shot 10: the mark reforms for the end card and holds. */
const END_MARK_FROM = 1160;
const END_MARK_TO = 1196;

const DRIFT_OPACITY = 0.09;
const MARK_OPACITY = 0.38;
const END_OPACITY = 0.85;

/**
 * The field's world-space size. The mark is sized from the layout so the end
 * card's lockup matches the wordmark beside it at any scale.
 */
export type SceneSizing = {
  readonly pxPerUnit: number;
  readonly markSizePx: number;
  readonly markCenterX: number;
  readonly markCenterY: number;
  readonly driftScale: number;
};

export function storyDriver(sizing: SceneSizing, logoH: number): SceneDriver {
  return (frame: number, fps: number): SceneState => {
    const t = frame / fps;
    const cam = cameraAt(frame);

    // Parallax: the field takes a fraction of the camera's travel, so it slides
    // and swells with the move without racing the panels.
    // Plus a slow drift of its own, so the field is never frozen even where the
    // camera is holding still.
    const parallaxX = -cam.x * 0.00042 + Math.sin(t * 0.11) * 0.22;
    const parallaxY = -cam.y * 0.00042 + Math.sin(t * 0.07 + 1.3) * 0.12;
    const push = interpolate(cam.z, [-2800, 1900], [1.15, 0.72], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    });

    // Shot 3's mark: in, hold, out.
    const markIn = interpolate(frame, [MARK_IN, MARK_HOLD_FROM], [0, 1], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
      easing: cubic,
    });
    const markOut = interpolate(frame, [MARK_HOLD_TO, MARK_OUT], [0, 1], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
      easing: cubic,
    });
    const shot3Mark = frame < MARK_OUT ? markIn * (1 - markOut) : 0;

    // Shot 8's blow-out.
    const blow = interpolate(frame, [BLOW_FROM, BLOW_TO], [0, 1], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
      easing: cubic,
    });
    const blowing = frame >= BLOW_FROM && frame < SHOT9_CUT;

    // The end card's mark.
    const endMark = spring({
      frame: frame - END_MARK_FROM,
      fps,
      config: {damping: 200, mass: 0.7, stiffness: 110},
      durationInFrames: END_MARK_TO - END_MARK_FROM,
    });
    const ending = frame >= END_MARK_FROM;

    if (ending) {
      return {
        visible: true,
        blend: endMark,
        from: 'scatter',
        to: 'logo',
        opacity: END_OPACITY * Math.min(1, endMark * 3),
        redMix: 0,
        push: (1 - endMark) * 0.3,
        posX: sizing.markCenterX / sizing.pxPerUnit,
        posY: -sizing.markCenterY / sizing.pxPerUnit,
        scale: sizing.markSizePx / (sizing.pxPerUnit * logoH),
        rotX: 0,
        rotY: 0.9 * (1 - endMark),
        spinT: t,
        hideAmbient: true,
      };
    }

    if (blowing) {
      return {
        visible: true,
        blend: 0,
        from: 'scatter',
        to: 'scatter',
        opacity: DRIFT_OPACITY * 1.5 * (1 - blow * 0.8),
        redMix: Math.min(1, blow * 1.6),
        push: blow * 1.5,
        posX: parallaxX,
        posY: parallaxY,
        scale: sizing.driftScale * push,
        rotX: 0,
        rotY: 0.35 + t * 0.03,
        spinT: t,
        hideAmbient: false,
      };
    }

    // Everything else: drift, with shot 3's mark blended over the top of it.
    // The mark's centre is where the job panel projected to on this frame, so
    // the field gathers behind the panel rather than beside it.
    const markScale = sizing.markSizePx / (sizing.pxPerUnit * logoH);
    const driftScale = sizing.driftScale * push;
    const markX = sizing.markCenterX / sizing.pxPerUnit;
    const markY = -sizing.markCenterY / sizing.pxPerUnit;
    return {
      visible: true,
      blend: shot3Mark,
      from: 'scatter',
      to: 'logo',
      opacity: DRIFT_OPACITY + (MARK_OPACITY - DRIFT_OPACITY) * shot3Mark,
      redMix: 0,
      push: 0,
      posX: parallaxX * (1 - shot3Mark) + markX * shot3Mark,
      posY: parallaxY * (1 - shot3Mark) + markY * shot3Mark,
      scale: driftScale + (markScale - driftScale) * shot3Mark,
      rotX: 0,
      rotY: 0.35 + t * 0.02 - 0.35 * shot3Mark,
      spinT: t,
      hideAmbient: false,
    };
  };
}

/** Device pixels per world unit at this frame height, from the scene camera. */
export const pxPerUnitFor = (height: number): number =>
  height / (2 * CAMERA_Z * Math.tan(((CAMERA_FOV * Math.PI) / 180) / 2));

/**
 * The field's canvas. The teaser's SceneCanvas takes its geometry from a teaser
 * Layout, but a supplied driver computes its own placement, so the object below
 * exists only to give the canvas its size and is not read for anything else.
 */
export const StoryScene: React.FC<{
  readonly layout: StoryLayout;
  readonly logoH: number;
  readonly sizing: SceneSizing;
}> = ({layout, logoH, sizing}) => {
  const driver = React.useMemo(() => storyDriver(sizing, logoH), [sizing, logoH]);

  const canvasLayout: Layout = {
    id: 'wide',
    width: layout.width,
    height: layout.height,
    pxPerUnit: sizing.pxPerUnit,
    content: layout.width,
    refusalBreakAt: null,
    stacked: false,
    brainCenterX: 0,
    brainHeightPx: 0,
    cardColumns: 4,
    cardsTop: 0,
    captionTop: 0,
    memoryLineTop: 0,
    memoryMonoTop: 0,
    markSizePx: sizing.markSizePx,
    lockupGap: 0,
    lockupY: 0,
    urlY: 0,
  };

  return (
    <SceneCanvas
      mode="intro"
      span={1230}
      layout={canvasLayout}
      brainOpacity={1}
      layerOpacityOverride={1}
      driver={driver}
      dpr={layout.dpr}
    />
  );
};
