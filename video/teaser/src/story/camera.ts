/**
 * The virtual camera.
 *
 * One camera flies the whole piece. It is cut, never dissolved, at three
 * places — frames 390 and 864, and the cut inside shot 9 at 1040 — and those
 * three cuts are the only discontinuities. Everything between them is one
 * continuous move.
 *
 * Keyframes are interpolated with cubic Hermite using Catmull-Rom tangents, not
 * eased per segment: an ease-in-out on every leg would bring the camera to a
 * stop at every keyframe, and the brief asks it never to rest. Velocity is
 * carried across a keyframe unless the keyframe is explicitly marked `stop`,
 * which is how shot 5's 1.5-second hold and the end card are pinned still.
 */
import {SHOT5_HOLD_FROM, SHOT9_CUT} from './timing';

export type Pose = {
  readonly x: number;
  readonly y: number;
  readonly z: number;
  /** Yaw and pitch, in degrees. */
  readonly ry: number;
  readonly rx: number;
};

type Key = Pose & {
  readonly at: number;
  /** Zero the tangent here: the camera arrives and stays until the next key. */
  readonly stop?: boolean;
};

/** One uninterrupted move. A new run means a cut. */
type Run = {readonly from: number; readonly to: number; readonly keys: readonly Key[]};

const RUNS: readonly Run[] = [
  // Shots 1-3. Push in on the job panel, drift right along its step cards,
  // then ease back out while the particles gather into the mark behind it.
  {
    from: 0,
    to: 390,
    keys: [
      {at: 0, x: -210, y: 70, z: 1900, ry: 8, rx: -2},
      {at: 108, x: -60, y: 25, z: 260, ry: 4, rx: -1},
      {at: 189, x: 780, y: 0, z: 60, ry: -9, rx: 0},
      {at: 390, x: 190, y: 35, z: 420, ry: 2, rx: -1},
    ],
  },
  // Shots 4-7. Cut in hard on the kill terminal, pull back off it to find the
  // job panel still standing, hold for the silence, then swing across to the
  // second panel and push on through both into open dark.
  {
    from: 390,
    to: 864,
    keys: [
      {at: 390, x: 40, y: 25, z: 60, ry: -3, rx: 1},
      {at: 438, x: 25, y: 18, z: -20, ry: -1, rx: 1},
      {at: SHOT5_HOLD_FROM, x: 0, y: 45, z: 900, ry: 1, rx: -1, stop: true},
      {at: 558, x: 0, y: 45, z: 900, ry: 1, rx: -1, stop: true},
      {at: 690, x: 950, y: 10, z: -160, ry: 0, rx: 0},
      {at: 864, x: 1000, y: 0, z: -1700, ry: 1, rx: 0},
    ],
  },
  // Shot 8 and the first half of shot 9. Cut in on the rm terminal, then carry
  // on past it and settle onto the fresh invoice.
  {
    from: 864,
    to: SHOT9_CUT,
    keys: [
      {at: 864, x: 1055, y: 14, z: -2110, ry: 13, rx: 0},
      {at: 924, x: 1040, y: 10, z: -2170, ry: 14, rx: 0},
      {at: SHOT9_CUT, x: 780, y: 0, z: -2700, ry: 15, rx: -1},
    ],
  },
  // The second half of shot 9 and the end card. A short push right onto the
  // chain explorer, then the only other place the camera is allowed to rest.
  {
    from: SHOT9_CUT,
    to: 1230,
    keys: [
      {at: SHOT9_CUT, x: 2050, y: 8, z: -2640, ry: -9, rx: -1},
      {at: 1155, x: 2375, y: 5, z: -2740, ry: -12, rx: -1},
      {at: 1180, x: 2410, y: 5, z: -2750, ry: -12, rx: -1, stop: true},
      {at: 1230, x: 2410, y: 5, z: -2750, ry: -12, rx: -1, stop: true},
    ],
  },
];

type Channel = 'x' | 'y' | 'z' | 'ry' | 'rx';
const CHANNELS: readonly Channel[] = ['x', 'y', 'z', 'ry', 'rx'];

/** Catmull-Rom tangent, zeroed at a key that is meant to hold. */
function tangent(keys: readonly Key[], i: number, c: Channel): number {
  if (keys[i].stop) return 0;
  if (i === 0) return (keys[1][c] - keys[0][c]) / (keys[1].at - keys[0].at);
  if (i === keys.length - 1) {
    return (keys[i][c] - keys[i - 1][c]) / (keys[i].at - keys[i - 1].at);
  }
  return (keys[i + 1][c] - keys[i - 1][c]) / (keys[i + 1].at - keys[i - 1].at);
}

function hermite(keys: readonly Key[], frame: number, c: Channel): number {
  if (frame <= keys[0].at) return keys[0][c];
  const last = keys.length - 1;
  if (frame >= keys[last].at) return keys[last][c];

  let i = 0;
  while (i < last - 1 && frame >= keys[i + 1].at) i++;

  const dt = keys[i + 1].at - keys[i].at;
  const t = (frame - keys[i].at) / dt;
  const t2 = t * t;
  const t3 = t2 * t;
  const h00 = 2 * t3 - 3 * t2 + 1;
  const h10 = t3 - 2 * t2 + t;
  const h01 = -2 * t3 + 3 * t2;
  const h11 = t3 - t2;

  return (
    h00 * keys[i][c] +
    h10 * dt * tangent(keys, i, c) +
    h01 * keys[i + 1][c] +
    h11 * dt * tangent(keys, i + 1, c)
  );
}

const runAt = (frame: number): Run => {
  for (let i = RUNS.length - 1; i >= 0; i--) {
    if (frame >= RUNS[i].from) return RUNS[i];
  }
  return RUNS[0];
};

/** The camera pose on one frame, in design units and degrees. Pure. */
export function cameraAt(frame: number): Pose {
  const run = runAt(frame);
  const out = {} as Record<Channel, number>;
  for (const c of CHANNELS) out[c] = hermite(run.keys, frame, c);
  return out as Pose;
}

/**
 * Roughly how far a panel is in front of the eye, ignoring the camera's small
 * rotations. Used to take a panel out of the frame before the camera flies into
 * it, where CSS perspective would blow it up to nothing useful.
 */
export const depthOf = (panelZ: number, frame: number): number => panelZ - cameraAt(frame).z;

/**
 * Where a world point lands on screen, in design units from the frame centre,
 * and how much perspective magnifies it there. Used to park the particle mark
 * behind the job panel in shot 3: the field is a separate canvas in screen
 * space, so it has to be told where the panel actually projected to.
 */
export function project(
  x: number,
  y: number,
  z: number,
  frame: number,
  perspective: number,
): {x: number; y: number; factor: number} {
  const cam = cameraAt(frame);
  const ry = (cam.ry * Math.PI) / 180;
  const rx = (cam.rx * Math.PI) / 180;

  const dx = x - cam.x;
  const dy = y - cam.y;
  const dz = z - cam.z;

  // The world carries rotateX(-rx) rotateY(-ry) translate3d(-c), so a world
  // point reaches camera space through the same two rotations, in that order.
  const x1 = Math.cos(ry) * dx - Math.sin(ry) * dz;
  const z1 = Math.sin(ry) * dx + Math.cos(ry) * dz;
  const y2 = Math.cos(rx) * dy + Math.sin(rx) * z1;
  const z2 = -Math.sin(rx) * dy + Math.cos(rx) * z1;

  const factor = perspective / (perspective - z2);
  return {x: x1 * factor, y: y2 * factor, factor};
}
