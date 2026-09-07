/**
 * Beat 6, the delete.
 *
 * The frame washes red, the command types out, and then the meter comes back
 * to say what the memory was worth: the same four cards, still marked PAID,
 * thrown outward and gone. The command clears as the cards land so the two
 * never share the middle of the frame.
 */
import React from 'react';
import {AbsoluteFill, Easing, interpolate, useCurrentFrame} from 'remotion';
import {CardFace} from '../components/CardFace';
import {TypeLine} from '../components/TypeLine';
import {DELETE_COMMAND, INVOICE_LINE, STEPS} from '../data';
import {cardRect, Layout} from '../layout';
import {DEL} from '../timing';
import {DISPLAY, MARGIN, RED} from '../theme';

const cubic = Easing.bezier(0.33, 0, 0.15, 1);

/** Fixed per-card tumble, in degrees at full scatter. Deterministic on purpose. */
const CARD_SPIN = [-14, 9, -7, 16];

const ScatterCard: React.FC<{readonly layout: Layout; readonly index: number}> = ({layout, index}) => {
  const frame = useCurrentFrame();
  const step = STEPS[index];
  const rect = cardRect(layout, index);

  const flash = interpolate(frame, [DEL.flashIn, DEL.flashIn + 3], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: cubic,
  });
  const e = interpolate(frame, [DEL.scatterStart, DEL.scatterStart + DEL.scatterFrames], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: cubic,
  });

  // Outward, along the ray from the frame centre through the card's centre.
  const cx = rect.left + rect.width / 2 - layout.width / 2;
  const cy = rect.top + rect.height / 2 - layout.height / 2;
  const len = Math.hypot(cx, cy) || 1;
  const throwBy = 760 * e;

  return (
    <div
      style={{
        position: 'absolute',
        left: rect.left,
        top: rect.top,
        width: rect.width,
        height: rect.height,
        transform: `translate(${(cx / len) * throwBy}px, ${(cy / len) * throwBy}px) rotate(${
          CARD_SPIN[index] * e
        }deg) scale(${1 + e * 0.1})`,
        opacity: flash * (1 - e),
      }}
    >
      <CardFace step={step.step} name={step.name} price={step.price.toFixed(2)} pill="paid" />
    </div>
  );
};

export const Delete: React.FC<{readonly layout: Layout}> = ({layout}) => {
  const frame = useCurrentFrame();

  const washIn = interpolate(frame, [0, DEL.washIn], [0, 0.1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: cubic,
  });
  const washOut = interpolate(frame, [DEL.outroFade[0], DEL.outroFade[1]], [1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: cubic,
  });

  const commandOut = interpolate(frame, [DEL.commandOut[0], DEL.commandOut[1]], [1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: cubic,
  });

  const lineIn = interpolate(frame, [DEL.lineIn[0], DEL.lineIn[1]], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: cubic,
  });

  return (
    <AbsoluteFill>
      <AbsoluteFill style={{background: RED, opacity: washIn * washOut}} />

      {commandOut > 0.001 ? (
        <AbsoluteFill
          style={{
            padding: MARGIN,
            alignItems: 'center',
            justifyContent: 'center',
            opacity: commandOut,
          }}
        >
          <TypeLine
            text={DELETE_COMMAND}
            breakAt={null}
            typeFrames={DEL.typeFrames}
            fontSize={42}
            color={RED}
          />
        </AbsoluteFill>
      ) : null}

      {STEPS.map((s, i) => (
        <ScatterCard key={s.name} layout={layout} index={i} />
      ))}

      <AbsoluteFill style={{padding: MARGIN, alignItems: 'center', justifyContent: 'center'}}>
        <div
          style={{
            fontFamily: DISPLAY,
            fontWeight: 300,
            fontSize: 44,
            lineHeight: 1.22,
            color: RED,
            textAlign: 'center',
            maxWidth: layout.content,
            whiteSpace: 'pre-line',
            opacity: lineIn * washOut,
          }}
        >
          {layout.stacked ? INVOICE_LINE.square : INVOICE_LINE.wide}
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
