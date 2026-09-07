/**
 * The narrated product video: 41 seconds, ten shots, one camera.
 *
 * Everything is generated. The panels are the app's own surfaces built in code,
 * the field behind them is the ported particle scene, and the camera is a
 * single CSS 3D transform driven by the frame. Nothing is written in output
 * pixels: every size below is a design unit multiplied by the layout's scale,
 * which is why story-4k is exactly story-wide at 2x and not a re-layout.
 */
import React from 'react';
import {AbsoluteFill, Easing, interpolate, useCurrentFrame} from 'remotion';
import {forms} from '../scene/forms';
import {BLACK} from '../theme';
import {Narration} from './Narration';
import {PERSPECTIVE, StoryLayout, worldFor} from './config';
import {depthOf, project} from './camera';
import {EndCard, RedBleed, Shot3Wordmark, Shot5Words, Shot7Figures} from './overlays';
import {ExplorerPanel, InvoicePanel, JobPanel, TerminalPanel} from './panels';
import {APP} from './panelStyle';
import {pxPerUnitFor, SceneSizing, StoryScene} from './scene';
import {Panel3D, Stage} from './Stage';
import {SHOT9_CUT} from './timing';

const cubic = Easing.bezier(0.33, 0, 0.15, 1);

const ease = (frame: number, from: number, to: number) =>
  interpolate(frame, [from, to], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: cubic,
  });

/**
 * A panel is pulled just before the camera reaches its plane. CSS perspective
 * magnifies anything between the eye and the projection plane without bound, so
 * flying "through" a panel means fading it out on the approach instead.
 *
 * The band is deliberately tight. It has to be: a panel inside it is partly
 * transparent, and the field behind shows through, which at any wider setting
 * veils the panel through shots where the camera is nowhere near it.
 */
const notTooClose = (panelZ: number, frame: number) =>
  1 - ease(depthOf(panelZ, frame), -420, -150);

/* ── the two terminals ───────────────────────────────────────────────── */

/**
 * Shot 4. The real command surface: `turnstyl serve` from src/turnstyl/cli.py,
 * an ALLOW decision line in the vocabulary src/turnstyl/policy.py uses, and the
 * process going down.
 */
const killLines = [
  {text: 'turnstyl serve --port 8787 --db ./data/turnstyl.db', colour: APP.dim, prompt: true},
  {text: 'ALLOW  buyer 0x0964…eff8  job 9e33  step 4 verify', colour: APP.gold},
  {text: 'kill 4182', colour: APP.dim, prompt: true, typeFrom: 398, typeTo: 414},
  {text: '[turnstyl] SIGTERM — draining', colour: APP.mute, typeFrom: 418, typeTo: 419},
  {text: '[turnstyl] stopped', colour: APP.red, typeFrom: 426, typeTo: 427},
];

/** Shot 8. */
const rmLines = [
  {text: 'rm memory.db', colour: APP.red, prompt: true, typeFrom: 872, typeTo: 900},
];

/* ── the two payments on the explorer ────────────────────────────────── */

/**
 * Illustrative receipts in the app's shape, not a record of two specific
 * transactions: the repo's sample fixture carries placeholder hashes only, so
 * these are stable stand-ins for the two identical payments the shot is about.
 */
const PAYMENTS = [
  {hash: '0x7c41ab93e2d5f0864b17c9a3e5d2018f4b6c9d7a3e15f0842c6b9d3a7e105f284', ts: '14:02:11'},
  {hash: '0x3e15f0842c6b9d3a7e105f2847c41ab93e2d5f0864b17c9a3e5d2018f4b6c9d7a', ts: '16:47:53'},
] as const;

export const Story: React.FC<{readonly layout: StoryLayout}> = ({layout}) => {
  const frame = useCurrentFrame();
  const s = layout.scale;
  const world = worldFor(layout);
  const f = forms();

  const pxPerUnit = pxPerUnitFor(layout.height);
  const logoH = Math.max(0.001, f.logoMaxY - f.logoMinY);

  // Shot 3 gathers the field into the mark *behind the job panel*, so it has to
  // be told where the panel projected to on this frame: the field is a separate
  // screen-space canvas and knows nothing about the CSS camera otherwise. The
  // end card's mark is the lockup instead, half a gap left of centre.
  const onEndCard = frame >= 1155;
  const jobOnScreen = project(world.job.x, world.job.y, world.job.z, frame, PERSPECTIVE);

  const sizing: SceneSizing = React.useMemo(
    () => ({
      pxPerUnit,
      markSizePx: (onEndCard ? 300 : 460) * s,
      markCenterX: onEndCard ? -(48 * s) / 2 - (300 * s) / 2 : jobOnScreen.x * s,
      markCenterY: onEndCard ? -45 * s : jobOnScreen.y * s,
      driftScale: 0.85,
    }),
    [pxPerUnit, s, onEndCard, jobOnScreen.x, jobOnScreen.y],
  );

  /* ── panel visibility, shot by shot ────────────────────────────────── */

  // The job panel resolves out of the dark in shot 1, dims behind the kill
  // terminal in shot 4, comes back for shot 5, and is left behind in shot 7.
  const jobOpacity =
    ease(frame, 10, 76) *
    (1 - 0.62 * ease(frame, 390, 398) * (1 - ease(frame, 440, 470))) *
    notTooClose(world.job.z, frame);

  // Shot 6: the second panel arrives beside the first.
  const jobMemoryOpacity = ease(frame, 566, 600) * notTooClose(world.jobMemory.z, frame);

  // Shot 4 cuts in on the terminal, which swings in and then clears as the
  // camera pulls back off it.
  const killOpacity = (frame < 386 ? 0 : 1) * (1 - ease(frame, 442, 486));
  const killSwing = (1 - ease(frame, 390, 424)) * -15;
  const killZ = (1 - ease(frame, 390, 424)) * -260;

  // Shot 8 cuts in on the rm terminal; shot 9 leaves it behind. It is close
  // enough to the eye to sit inside the notTooClose band for its whole shot, so
  // like the kill terminal it carries its own exit instead.
  const rmOpacity = (frame < 860 ? 0 : 1) * (1 - ease(frame, 930, 975));

  const invoiceOpacity = ease(frame, 930, 984) * (1 - ease(frame, 1020, 1044));
  const explorerOpacity = ease(frame, SHOT9_CUT - 6, SHOT9_CUT + 26) * (1 - ease(frame, 1128, 1168));

  return (
    <AbsoluteFill style={{backgroundColor: BLACK}}>
      {/* The field is the environment: it sits behind every panel. */}
      <AbsoluteFill style={{zIndex: 0}}>
        <StoryScene layout={layout} logoH={logoH} sizing={sizing} />
      </AbsoluteFill>

      {/* translateZ(0) here is load-bearing: it flattens the panels' 3D
          rendering context into a render surface of its own. Without it the
          compositor is free to sort the WebGL canvas against panels that sit at
          large negative Z, and the field paints over the panels regardless of
          z-index. */}
      <AbsoluteFill style={{zIndex: 1}}>
        <Stage layout={layout} frame={frame}>
        <Panel3D layout={layout} at={world.job} opacity={jobOpacity}>
          <JobPanel layout={layout} frame={frame} variant="first" />
        </Panel3D>

        <Panel3D layout={layout} at={world.jobMemory} opacity={jobMemoryOpacity}>
          <JobPanel layout={layout} frame={frame} variant="memory" />
        </Panel3D>

        <Panel3D
          layout={layout}
          at={world.termKill}
          opacity={killOpacity}
          ryOffset={killSwing}
          zOffset={killZ}
        >
          <TerminalPanel
            layout={layout}
            frame={frame}
            title="agent"
            meta="pid 4182"
            lines={killLines}
            cursor={frame < 422}
          />
        </Panel3D>

        <Panel3D layout={layout} at={world.termRm} opacity={rmOpacity}>
          <TerminalPanel
            layout={layout}
            frame={frame}
            title="memory"
            meta="./data"
            lines={rmLines}
            cursor
          />
        </Panel3D>

        <Panel3D layout={layout} at={world.invoice} opacity={invoiceOpacity}>
          <InvoicePanel layout={layout} frame={frame} />
        </Panel3D>

        <Panel3D layout={layout} at={world.explorer} opacity={explorerOpacity}>
          <ExplorerPanel layout={layout} payments={PAYMENTS} />
        </Panel3D>
        </Stage>
      </AbsoluteFill>

      <AbsoluteFill style={{zIndex: 2}}>
        <RedBleed />

        <Shot3Wordmark layout={layout} />
        <Shot5Words layout={layout} />
        <Shot7Figures layout={layout} />
        <EndCard layout={layout} />
      </AbsoluteFill>

      <Narration />
    </AbsoluteFill>
  );
};
