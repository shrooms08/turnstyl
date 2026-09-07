/**
 * The particle field, driven entirely by the frame.
 *
 * There is no requestAnimationFrame, no clock and no component state here:
 * every position, colour, scale and rotation below is a pure function of
 * useCurrentFrame(), so any worker rendering any frame produces the same
 * pixels. The materials are the page's — wireframe tetrahedra, additive
 * blending, depthWrite off — and the count is the page's 5000.
 */
import React, {useEffect, useLayoutEffect, useMemo, useRef} from 'react';
import {Easing, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import * as THREE from 'three';
import {RED} from '../theme';
import {
  BRAIN_POSE,
  BRAIN_SWAY_AMP,
  COUNT,
  FORM_N,
  forms,
  SWAY_W,
} from './forms';

export type SceneMode = 'intro' | 'outro';

const cubic = Easing.bezier(0.33, 0, 0.15, 1);
const cRed = new THREE.Color(RED);

/** What the field is doing on one frame. Pure. */
export type SceneState = {
  visible: boolean;
  /** 0 = wholly form A, 1 = wholly form B. */
  blend: number;
  from: 'brain' | 'scatter';
  to: 'brain' | 'scatter' | 'logo';
  opacity: number;
  redMix: number;
  /** Radial expansion applied after the blend, plus how far each instance
   *  runs along its own drift direction. */
  push: number;
  posX: number;
  posY: number;
  scale: number;
  rotX: number;
  rotY: number;
  /** Seconds of tumble accumulated by this frame. */
  spinT: number;
  /**
   * The 300-instance ambient field belongs to every form on the page. Beat 7
   * says the particles form the mark, so for the mark they stand down: their
   * scale goes to zero and the end card is the mark and nothing else.
   */
  hideAmbient: boolean;
};

export type SceneGeometry = {
  /** Device pixels per world unit, from the camera. */
  pxPerUnit: number;
  brainCenterX: number;
  brainHeightPx: number;
  markSizePx: number;
  markCenterX: number;
  markCenterY: number;
};

/**
 * Material opacity per form, as on the page. This is not how far back the
 * scene sits in the frame: additive blending means 5000 overlapping wireframes
 * saturate whatever their per-fragment alpha is, so holding the brain behind
 * the type is done by compositing the whole canvas (see SceneCanvas), not by
 * turning this down.
 */
const BRAIN_MATERIAL_OPACITY = 0.7;
const LOGO_MATERIAL_OPACITY = 0.85;

/* ── beat 6 and 7 local frames, mirrored from timing.ts ──────────────── */
const FLASH_IN = 36;
const SCATTER_START = 46;
const SCATTER_FRAMES = 25;
const MARK_START = 90;
const MARK_FRAMES = 18;

function introState(f: number, fps: number, g: SceneGeometry, span: number, formH: number): SceneState {
  // The brain establishes behind the refusal and leaves before the meter.
  const inOp = interpolate(f, [0, 18], [0, 1], {extrapolateRight: 'clamp', easing: cubic});
  const outOp = interpolate(f, [span - 15, span], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: cubic});
  const t = f / fps;
  return {
    visible: true,
    blend: 0,
    from: 'brain',
    to: 'brain',
    opacity: BRAIN_MATERIAL_OPACITY * inOp * outOp,
    redMix: 0,
    push: 0,
    posX: g.brainCenterX / g.pxPerUnit,
    posY: 0,
    scale: g.brainHeightPx / (g.pxPerUnit * formH),
    rotX: BRAIN_POSE.x,
    rotY: BRAIN_POSE.y + BRAIN_SWAY_AMP * Math.sin(t * SWAY_W),
    spinT: t,
    hideAmbient: false,
  };
}

function outroState(
  f: number,
  fps: number,
  g: SceneGeometry,
  brainH: number,
  logoH: number,
): SceneState {
  const t = f / fps;

  if (f < FLASH_IN) {
    return {
      visible: false, blend: 0, from: 'brain', to: 'brain', opacity: 0, redMix: 0, push: 0,
      posX: 0, posY: 0, scale: 1, rotX: 0, rotY: 0, spinT: t, hideAmbient: false,
    };
  }

  // beat 6: the brain snaps back with the cards, then goes outward and red.
  if (f < MARK_START) {
    const flash = interpolate(f, [FLASH_IN, FLASH_IN + 4], [0, 1], {
      extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: cubic,
    });
    const e = interpolate(f, [SCATTER_START, SCATTER_START + SCATTER_FRAMES], [0, 1], {
      extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: cubic,
    });
    return {
      visible: true,
      blend: e,
      from: 'brain',
      to: 'scatter',
      opacity: BRAIN_MATERIAL_OPACITY * flash * (1 - e),
      redMix: Math.min(1, e * 2.2),
      push: e * 1.4,
      posX: 0,
      posY: 0,
      scale: (g.brainHeightPx / (g.pxPerUnit * brainH)) * (1 + e * 0.25),
      rotX: BRAIN_POSE.x,
      rotY: BRAIN_POSE.y + 0.85 * e,
      spinT: t,
      hideAmbient: false,
    };
  }

  // beat 7: the field gathers into the mark and holds.
  const s = spring({
    frame: f - MARK_START,
    fps,
    config: {damping: 200, mass: 0.7, stiffness: 110},
    durationInFrames: MARK_FRAMES,
  });
  return {
    visible: true,
    blend: s,
    from: 'scatter',
    to: 'logo',
    opacity: LOGO_MATERIAL_OPACITY * interpolate(s, [0, 0.35], [0, 1], {extrapolateRight: 'clamp'}),
    redMix: 0,
    push: (1 - s) * 0.35,
    posX: g.markCenterX / g.pxPerUnit,
    posY: -g.markCenterY / g.pxPerUnit,
    scale: g.markSizePx / (g.pxPerUnit * logoH),
    rotX: 0,
    rotY: 0.9 * (1 - s),
    spinT: t,
    hideAmbient: true,
  };
}

/**
 * A driver the caller supplies instead of `mode`. The teaser uses the two
 * built-in modes; the narrated story brings its own (see story/scene.tsx), so
 * the shared field can be flown a different way without either piece having to
 * know about the other.
 */
export type SceneDriver = (frame: number, fps: number) => SceneState;

export const Particles: React.FC<{
  readonly mode: SceneMode;
  readonly span: number;
  readonly geometry: SceneGeometry;
  readonly driver?: SceneDriver;
}> = ({mode, span, geometry, driver}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const f = forms();

  const mesh = useMemo(() => {
    const geo = new THREE.TetrahedronGeometry(1, 0);
    const mat = new THREE.MeshBasicMaterial({
      wireframe: true,
      transparent: true,
      opacity: 1,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });
    const m = new THREE.InstancedMesh(geo, mat, COUNT);
    m.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    m.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(COUNT * 3), 3);
    m.instanceColor.setUsage(THREE.DynamicDrawUsage);
    m.frustumCulled = false;
    return m;
  }, []);

  useEffect(() => {
    return () => {
      mesh.geometry.dispose();
      (mesh.material as THREE.Material).dispose();
    };
  }, [mesh]);

  const groupRef = useRef<THREE.Group>(null);

  const brainH = Math.max(0.001, f.brainMaxY - f.brainMinY);
  const logoH = Math.max(0.001, f.logoMaxY - f.logoMinY);

  useLayoutEffect(() => {
    const st = driver
      ? driver(frame, fps)
      : mode === 'intro'
        ? introState(frame, fps, geometry, span, brainH)
        : outroState(frame, fps, geometry, brainH, logoH);

    mesh.visible = st.visible && st.opacity > 0.001;
    const group = groupRef.current;
    if (group) {
      group.position.set(st.posX, st.posY, 0);
      group.rotation.set(st.rotX, st.rotY, 0);
      group.scale.setScalar(st.scale);
    }
    (mesh.material as THREE.MeshBasicMaterial).opacity = st.opacity;
    if (!mesh.visible) return;

    const srcA = st.from === 'brain' ? f.posBrain : f.posScatter;
    const srcB = st.to === 'brain' ? f.posBrain : st.to === 'scatter' ? f.posScatter : f.posLogo;
    const colA = st.from === 'brain' ? f.colBrain : f.colScatter;
    const colB = st.to === 'brain' ? f.colBrain : st.to === 'scatter' ? f.colScatter : f.colLogo;
    const sizA = st.from === 'brain' ? f.sBrain : f.sScatter;
    const sizB = st.to === 'brain' ? f.sBrain : st.to === 'scatter' ? f.sScatter : f.sLogo;

    const w = st.blend;
    const iw = 1 - w;
    const colOut = (mesh.instanceColor as THREE.InstancedBufferAttribute).array as Float32Array;

    const dummy = new THREE.Object3D();
    const q = new THREE.Quaternion();
    const ax = new THREE.Vector3();

    for (let i = 0; i < COUNT; i++) {
      const j = i * 3;
      let x = srcA[j] * iw + srcB[j] * w;
      let y = srcA[j + 1] * iw + srcB[j + 1] * w;
      let z = srcA[j + 2] * iw + srcB[j + 2] * w;

      if (st.push > 0) {
        // outward, then along the instance's own drift direction
        x = x * (1 + st.push) + f.drift[j] * st.push * 0.9;
        y = y * (1 + st.push) + f.drift[j + 1] * st.push * 0.9;
        z = z * (1 + st.push) + f.drift[j + 2] * st.push * 0.9;
      }

      let r = colA[j] * iw + colB[j] * w;
      let g = colA[j + 1] * iw + colB[j + 1] * w;
      let b = colA[j + 2] * iw + colB[j + 2] * w;
      if (st.redMix > 0) {
        r += (cRed.r - r) * st.redMix;
        g += (cRed.g - g) * st.redMix;
        b += (cRed.b - b) * st.redMix;
      }
      colOut[j] = r;
      colOut[j + 1] = g;
      colOut[j + 2] = b;

      const sc = st.hideAmbient && i >= FORM_N ? 0 : sizA[i] * iw + sizB[i] * w;
      dummy.position.set(x, y, z);
      ax.set(f.axis[j], f.axis[j + 1], f.axis[j + 2]);
      q.setFromAxisAngle(ax, f.spinAng[i] + f.spinVel[i] * st.spinT);
      dummy.scale.set(sc, sc, sc);
      dummy.matrix.compose(dummy.position, q, dummy.scale);
      mesh.setMatrixAt(i, dummy.matrix);
    }
    mesh.instanceMatrix.needsUpdate = true;
    (mesh.instanceColor as THREE.InstancedBufferAttribute).needsUpdate = true;
  }, [frame, fps, mode, span, geometry, driver, mesh, f, brainH, logoH]);

  return (
    <group ref={groupRef}>
      <primitive object={mesh} />
    </group>
  );
};
