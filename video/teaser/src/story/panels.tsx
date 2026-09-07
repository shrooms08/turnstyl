/**
 * The panels, built from the real app.
 *
 * Every surface, border, type size and colour below traces back to
 * web/static/turnstyl.css and the markup app.html renders: .chead for the
 * header, .steps / .step for the metered work, .pill and .tag for the badges,
 * .facts for the per-step numbers, .cmd for a command line. The data is the
 * repo's own: job 9e333f58d973 and buyer 0x0964…eff8 from docs/api_samples.json,
 * and the four step prices from src/turnstyl/jobtypes/audit.py.
 *
 * Sizes are design units; each panel multiplies by the layout scale.
 */
import React from 'react';
import {Easing, interpolate, spring, useVideoConfig} from 'remotion';
import {STEPS} from '../data';
import {StoryLayout} from './config';
import {alpha, APP, fonts, PANEL_BORDER, PANEL_RADIUS, trunc, usdc} from './panelStyle';

const cubic = Easing.bezier(0.33, 0, 0.15, 1);

export const JOB_ID = '9e333f58d973';
export const BUYER = '0x0964dc1e37aca77c6df395db7c0eec848b1ceff8';

const ease = (frame: number, from: number, to: number) =>
  interpolate(frame, [from, to], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: cubic,
  });

/* ── the panel shell ─────────────────────────────────────────────────── */

const Shell: React.FC<{readonly s: number; readonly children: React.ReactNode}> = ({s, children}) => (
  <div
    style={{
      width: '100%',
      height: '100%',
      boxSizing: 'border-box',
      background: APP.card,
      border: `${Math.max(1, s)}px solid ${PANEL_BORDER}`,
      borderRadius: PANEL_RADIUS * s,
      padding: 34 * s,
      display: 'flex',
      flexDirection: 'column',
    }}
  >
    {children}
  </div>
);

/** .chead: a display title on the left, mono meta on the right. */
const Head: React.FC<{
  readonly s: number;
  readonly title: string;
  readonly meta: string;
}> = ({s, title, meta}) => (
  <div
    style={{
      display: 'flex',
      alignItems: 'baseline',
      justifyContent: 'space-between',
      gap: 20 * s,
      paddingBottom: 18 * s,
      borderBottom: `${Math.max(1, s)}px solid ${APP.line}`,
    }}
  >
    <div style={{fontFamily: fonts.display, fontWeight: 300, fontSize: 30 * s, color: APP.ink, lineHeight: 1.1}}>
      {title}
    </div>
    <div
      style={{
        fontFamily: fonts.mono,
        fontSize: 13 * s,
        letterSpacing: 0.14 * 13 * s,
        textTransform: 'uppercase',
        color: APP.mute,
        whiteSpace: 'nowrap',
      }}
    >
      {meta}
    </div>
  </div>
);

/* ── job panel ───────────────────────────────────────────────────────── */

export type JobVariant = 'first' | 'memory';

/**
 * Shot 2 counts each price up as its card passes through frame; shot 6 halves
 * them on the second panel and runs the token counts to zero. These are the
 * frames those two things happen on.
 */
const PRICE_FIRST_AT = (i: number) => 112 + i * 16;
const MEMORY_HALVE_AT = 600;
const MEMORY_TOKENS_AT = 612;

const StepCard: React.FC<{
  readonly s: number;
  readonly frame: number;
  readonly fps: number;
  readonly index: number;
  readonly variant: JobVariant;
}> = ({s, frame, fps, index, variant}) => {
  const step = STEPS[index];
  const memory = variant === 'memory';

  const rise = spring({
    frame: frame - PRICE_FIRST_AT(index),
    fps,
    config: {damping: 200, mass: 0.7, stiffness: 110},
    durationInFrames: 24,
  });
  const halve = spring({
    frame: frame - MEMORY_HALVE_AT,
    fps,
    config: {damping: 200, mass: 0.8, stiffness: 100},
    durationInFrames: 26,
  });

  // The first panel's price springs up once and stays; the second panel starts
  // from the same number and comes down to the cached price.
  const price = memory ? step.price + (step.memoryPrice - step.price) * halve : step.price * rise;

  const tokens = memory
    ? Math.round(
        step.tokens *
          (1 -
            spring({
              frame: frame - MEMORY_TOKENS_AT,
              fps,
              config: {damping: 200, mass: 0.8, stiffness: 100},
              durationInFrames: 28,
            })),
      )
    : step.tokens;

  const paidStamp = spring({
    frame: frame - (PRICE_FIRST_AT(index) + 26),
    fps,
    config: {damping: 14, mass: 0.5, stiffness: 220},
    durationInFrames: 16,
  });
  const memStamp = spring({
    frame: frame - MEMORY_HALVE_AT,
    fps,
    config: {damping: 14, mass: 0.5, stiffness: 220},
    durationInFrames: 16,
  });

  const showMemoryTag = memory && frame >= MEMORY_HALVE_AT;
  const stampT = showMemoryTag ? memStamp : paidStamp;
  const badgeAlpha = Math.min(1, stampT * 2.5);
  const badgeScale = 1.4 + (1 - 1.4) * stampT;

  const badgeColour = showMemoryTag ? APP.violet : APP.gold;
  const badgeText = showMemoryTag ? 'served from memory' : 'paid';

  return (
    <div
      style={{
        flex: 1,
        minWidth: 0,
        background: APP.card2,
        border: `${Math.max(1, s)}px solid ${APP.line}`,
        borderRadius: 8 * s,
        padding: `${18 * s}px ${16 * s}px ${20 * s}px`,
        display: 'flex',
        flexDirection: 'column',
        gap: 12 * s,
      }}
    >
      <div
        style={{
          fontFamily: fonts.mono,
          fontSize: 12 * s,
          letterSpacing: 0.22 * 12 * s,
          textTransform: 'uppercase',
          color: APP.mute,
          lineHeight: 1,
        }}
      >
        step {step.step}
      </div>
      <div style={{fontFamily: fonts.display, fontWeight: 300, fontSize: 24 * s, color: APP.ink, lineHeight: 1.1}}>
        {step.name}
      </div>
      <div
        style={{
          fontFamily: fonts.mono,
          fontSize: 22 * s,
          color: APP.ink,
          fontVariantNumeric: 'tabular-nums',
          lineHeight: 1,
        }}
      >
        {usdc(price)}
        <span style={{color: APP.mute, fontSize: 11 * s, letterSpacing: 0.16 * 11 * s, marginLeft: 5 * s}}>USDC</span>
      </div>
      <div style={{height: 26 * s, display: 'flex', alignItems: 'center'}}>
        {badgeAlpha <= 0.01 ? null : (
          <span
            style={{
              display: 'inline-block',
              fontFamily: fonts.mono,
              fontSize: 11 * s,
              letterSpacing: 0.16 * 11 * s,
              textTransform: 'uppercase',
              padding: `${4 * s}px ${9 * s}px`,
              border: `${Math.max(1, s)}px solid ${alpha(badgeColour, 0.5 * badgeAlpha)}`,
              borderRadius: 4 * s,
              color: alpha(badgeColour, badgeAlpha),
              whiteSpace: 'nowrap',
              transform: `scale(${badgeScale})`,
              transformOrigin: 'left center',
              lineHeight: 1,
            }}
          >
            {badgeText}
          </span>
        )}
      </div>
      <div style={{fontFamily: fonts.mono, fontSize: 13 * s, color: APP.mute, lineHeight: 1.5}}>{tokens} tokens</div>
    </div>
  );
};

export const JobPanel: React.FC<{
  readonly layout: StoryLayout;
  readonly frame: number;
  readonly variant: JobVariant;
}> = ({layout, frame, variant}) => {
  const s = layout.scale;
  const {fps} = useVideoConfig();
  const memory = variant === 'memory';

  const halve = spring({
    frame: frame - MEMORY_HALVE_AT,
    fps,
    config: {damping: 200, mass: 0.8, stiffness: 100},
    durationInFrames: 26,
  });
  const full = STEPS.reduce((a, x) => a + x.price, 0);
  const cached = STEPS.reduce((a, x) => a + x.memoryPrice, 0);
  const total = memory ? full + (cached - full) * halve : full * ease(frame, 112, 190);

  return (
    <Shell s={s}>
      <Head
        s={s}
        title={`audit · job ${JOB_ID.slice(0, 4)}`}
        meta={memory ? 'same contract · second run' : 'base sepolia · 4 steps'}
      />
      <div style={{display: 'flex', gap: 14 * s, marginTop: 24 * s, flex: 1, alignItems: 'flex-start'}}>
        {STEPS.map((step, i) => (
          <StepCard key={step.name} s={s} frame={frame} fps={fps} index={i} variant={variant} />
        ))}
      </div>
      <div
        style={{
          marginTop: 22 * s,
          paddingTop: 16 * s,
          borderTop: `${Math.max(1, s)}px solid ${APP.line}`,
          display: 'flex',
          alignItems: 'baseline',
          justifyContent: 'space-between',
          gap: 16 * s,
          fontFamily: fonts.mono,
          fontSize: 15 * s,
        }}
      >
        <span style={{color: APP.mute, letterSpacing: 0.14 * 15 * s, textTransform: 'uppercase'}}>
          {memory ? '0 tokens' : `buyer ${trunc(BUYER)}`}
        </span>
        <span style={{color: APP.gold, fontVariantNumeric: 'tabular-nums'}}>
          total {usdc(total)} USDC
          <span
            style={{
              color: APP.violet,
              marginLeft: 12 * s,
              letterSpacing: 0.14 * 12 * s,
              textTransform: 'uppercase',
              fontSize: 12 * s,
            }}
          >
            no gas
          </span>
        </span>
      </div>
    </Shell>
  );
};

/* ── terminal panel ──────────────────────────────────────────────────── */

export type TerminalLine = {
  readonly text: string;
  readonly colour: string;
  readonly typeFrom?: number;
  readonly typeTo?: number;
  readonly prompt?: boolean;
};

export const TerminalPanel: React.FC<{
  readonly layout: StoryLayout;
  readonly frame: number;
  readonly title: string;
  readonly meta: string;
  readonly lines: readonly TerminalLine[];
  readonly cursor: boolean;
}> = ({layout, frame, title, meta, lines, cursor}) => {
  const s = layout.scale;
  const size = 22 * s;

  // 2Hz, the same blink the teaser's terminal uses.
  const cursorOn = frame % 15 < 7.5;
  const visible = lines.filter((l) => l.typeFrom === undefined || frame >= l.typeFrom);

  return (
    <Shell s={s}>
      <Head s={s} title={title} meta={meta} />
      <div
        style={{
          marginTop: 22 * s,
          flex: 1,
          fontFamily: fonts.mono,
          fontSize: size,
          lineHeight: 1.75,
          whiteSpace: 'pre',
        }}
      >
        {visible.map((line, i) => {
          const from = line.typeFrom ?? -Infinity;
          const to = line.typeTo ?? from;
          const shown =
            line.typeFrom === undefined
              ? line.text
              : line.text.slice(
                  0,
                  Math.round(
                    interpolate(frame, [from, to], [0, line.text.length], {
                      extrapolateLeft: 'clamp',
                      extrapolateRight: 'clamp',
                      easing: cubic,
                    }),
                  ),
                );
          const isLast = i === visible.length - 1;
          return (
            <div key={line.text} style={{color: line.colour}}>
              {line.prompt ? <span style={{color: APP.mute}}>$ </span> : null}
              {shown}
              {cursor && isLast ? (
                <span
                  style={{
                    display: 'inline-block',
                    width: size * 0.6,
                    height: size * 0.95,
                    background: cursorOn ? line.colour : 'transparent',
                    verticalAlign: -size * 0.16,
                  }}
                />
              ) : null}
            </div>
          );
        })}
      </div>
    </Shell>
  );
};

/* ── invoice panel ───────────────────────────────────────────────────── */

const Kv: React.FC<{readonly s: number; readonly k: string; readonly v: string}> = ({s, k, v}) => (
  <div style={{display: 'flex', flexDirection: 'column', gap: 6 * s}}>
    <div
      style={{
        fontFamily: fonts.mono,
        fontSize: 11 * s,
        letterSpacing: 0.2 * 11 * s,
        textTransform: 'uppercase',
        color: APP.mute,
        lineHeight: 1,
      }}
    >
      {k}
    </div>
    <div style={{fontFamily: fonts.mono, fontSize: 17 * s, color: APP.ink, lineHeight: 1.2}}>{v}</div>
  </div>
);

export const InvoicePanel: React.FC<{readonly layout: StoryLayout; readonly frame: number}> = ({layout, frame}) => {
  const s = layout.scale;
  const reveal = ease(frame, 946, 1000);

  return (
    <Shell s={s}>
      <Head s={s} title="invoice" meta="unpaid · step 2" />
      <div style={{marginTop: 26 * s, display: 'flex', flexDirection: 'column', gap: 20 * s, flex: 1}}>
        <Kv s={s} k="to" v={trunc(BUYER)} />
        <Kv s={s} k="for" v={`job ${JOB_ID.slice(0, 4)} · findings`} />
        <div
          style={{
            fontFamily: fonts.mono,
            fontSize: 56 * s,
            color: alpha(APP.gold, reveal),
            lineHeight: 1,
            fontVariantNumeric: 'tabular-nums',
          }}
        >
          0.50
          <span style={{fontSize: 20 * s, color: alpha(APP.mute, reveal), marginLeft: 10 * s}}>USDC</span>
        </div>
      </div>
      <div
        style={{
          marginTop: 18 * s,
          background: '#050505',
          border: `${Math.max(1, s)}px solid ${APP.line}`,
          borderRadius: 6 * s,
          padding: `${12 * s}px ${14 * s}px`,
          fontFamily: fonts.mono,
          fontSize: 15 * s,
          color: APP.gold,
          whiteSpace: 'nowrap',
          overflow: 'hidden',
        }}
      >
        turnstyl pay {JOB_ID.slice(0, 4)} --step 2
      </div>
    </Shell>
  );
};

/* ── chain explorer panel ────────────────────────────────────────────── */

export type Payment = {readonly hash: string; readonly ts: string};

export const ExplorerPanel: React.FC<{
  readonly layout: StoryLayout;
  readonly payments: readonly Payment[];
}> = ({layout, payments}) => {
  const s = layout.scale;
  const cols = '1.1fr 1.1fr 0.7fr 0.9fr';

  return (
    <Shell s={s}>
      <Head s={s} title="receipts" meta="base sepolia" />
      <div style={{marginTop: 22 * s, flex: 1}}>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: cols,
            gap: 14 * s,
            paddingBottom: 12 * s,
            borderBottom: `${Math.max(1, s)}px solid ${APP.line}`,
            fontFamily: fonts.mono,
            fontSize: 11 * s,
            letterSpacing: 0.2 * 11 * s,
            textTransform: 'uppercase',
            color: APP.mute,
          }}
        >
          <span>tx</span>
          <span>from</span>
          <span>amount</span>
          <span>when</span>
        </div>
        {payments.map((p) => (
          <div
            key={p.hash}
            style={{
              display: 'grid',
              gridTemplateColumns: cols,
              gap: 14 * s,
              padding: `${16 * s}px 0`,
              borderBottom: `${Math.max(1, s)}px solid ${APP.line}`,
              fontFamily: fonts.mono,
              fontSize: 16 * s,
              color: APP.dim,
              alignItems: 'center',
            }}
          >
            <span style={{color: APP.ink}}>{trunc(p.hash)}</span>
            <span>{trunc(BUYER)}</span>
            <span style={{color: APP.gold, fontVariantNumeric: 'tabular-nums'}}>0.50</span>
            <span>{p.ts}</span>
          </div>
        ))}
      </div>
    </Shell>
  );
};
