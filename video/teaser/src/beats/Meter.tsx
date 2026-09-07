/**
 * Beats 3 and 4, the cards.
 *
 * Beat 3 deals the four steps in from the right, 8 frames apart, and stamps a
 * violet PAID on each one 6 frames after it lands. Beat 4 leaves the cards
 * exactly where they are, halves every price, and swaps PAID for SERVED FROM
 * MEMORY. The two beats share one component because they share one object.
 */
import React from 'react';
import {AbsoluteFill, Easing, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {CardFace} from '../components/CardFace';
import {STEPS} from '../data';
import {cardRect, Layout} from '../layout';
import {CARD_STAGGER, CARD_DEAL_FRAMES, PAID_AFTER_SETTLE} from '../timing';
import {DIM, MONO} from '../theme';

const cubic = Easing.bezier(0.33, 0, 0.15, 1);

/** Local frame at which beat 4 begins (absolute 300, span starts at 180). */
const MEMORY_AT = 120;
/** Local frame at which the cards clear for beat 5. */
const CLEAR_AT = 202;

const Card: React.FC<{readonly layout: Layout; readonly index: number}> = ({layout, index}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const step = STEPS[index];
  const rect = cardRect(layout, index);
  const dealAt = index * CARD_STAGGER;

  const deal = spring({
    frame: frame - dealAt,
    fps,
    config: {damping: 200, mass: 0.9, stiffness: 95},
    durationInFrames: CARD_DEAL_FRAMES,
  });

  // Beat 4: the same spring drives the price down and the pill over.
  const toMemory = spring({
    frame: frame - MEMORY_AT,
    fps,
    config: {damping: 200, mass: 0.8, stiffness: 100},
    durationInFrames: 28,
  });

  const priceUp = spring({
    frame: frame - dealAt - 6,
    fps,
    config: {damping: 200, mass: 0.7, stiffness: 110},
    durationInFrames: 24,
  });
  const price = step.price * priceUp + (step.memoryPrice - step.price) * toMemory;

  const paidAt = dealAt + CARD_DEAL_FRAMES + PAID_AFTER_SETTLE;
  const stamp = spring({
    frame: frame - paidAt,
    fps,
    config: {damping: 14, mass: 0.5, stiffness: 220},
    durationInFrames: 16,
  });
  const memoryStamp = spring({
    frame: frame - MEMORY_AT,
    fps,
    config: {damping: 14, mass: 0.5, stiffness: 220},
    durationInFrames: 16,
  });

  const onMemory = frame >= MEMORY_AT;
  const pillVisible = frame >= paidAt;
  // Both pills stamp on from 1.4.
  const pillScale = onMemory ? 1.4 + (1 - 1.4) * memoryStamp : 1.4 + (1 - 1.4) * stamp;
  const pillOpacity = onMemory ? Math.min(1, memoryStamp * 2.5) : Math.min(1, stamp * 2.5);

  const clear = interpolate(frame, [CLEAR_AT, CLEAR_AT + 8], [1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: cubic,
  });

  // Dealt in from off the right edge.
  const enterX = (layout.width - rect.left) * (1 - deal);

  return (
    <div
      style={{
        position: 'absolute',
        left: rect.left,
        top: rect.top,
        width: rect.width,
        height: rect.height,
        transform: `translateX(${enterX}px)`,
        opacity: Math.min(1, deal * 3) * clear,
      }}
    >
      <CardFace
        step={step.step}
        name={step.name}
        price={price.toFixed(2)}
        pill={pillVisible ? (onMemory ? 'memory' : 'paid') : null}
        pillScale={pillScale}
        pillOpacity={pillOpacity}
      />
    </div>
  );
};

export const Meter: React.FC<{readonly layout: Layout}> = ({layout}) => {
  const frame = useCurrentFrame();

  // "USDC on Base. no gas." belongs to beat 3 only; beat 4 puts the token
  // counters in that space instead.
  const captionIn = interpolate(frame, [40, 58], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: cubic,
  });
  const captionOut = interpolate(frame, [MEMORY_AT - 10, MEMORY_AT], [1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: cubic,
  });

  return (
    <AbsoluteFill>
      {STEPS.map((s, i) => (
        <Card key={s.name} layout={layout} index={i} />
      ))}
      <div
        style={{
          position: 'absolute',
          left: 0,
          right: 0,
          top: layout.captionTop,
          textAlign: 'center',
          fontFamily: MONO,
          fontWeight: 400,
          fontSize: 20,
          color: DIM,
          opacity: captionIn * captionOut,
        }}
      >
        USDC on Base. no gas.
      </div>
    </AbsoluteFill>
  );
};
