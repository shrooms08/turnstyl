/**
 * One step of the meter: what the buyer is being charged for, and what it
 * costs. Near-black with a hairline border, no shadow and no gradient.
 *
 * The pill sits on its own row rather than beside the step name: SERVED FROM
 * MEMORY is 18 mono glyphs and would push the name into a second line inside a
 * 300px card. Giving it a row of its own keeps both pills in the same place, so
 * beat 4 reads as a swap rather than a re-layout.
 */
import React from 'react';
import {CARD_BG, CARD_BORDER, DIM, GOLD, MONO, VIOLET} from '../theme';

export type PillKind = 'paid' | 'memory';

export const PILL_TEXT: Record<PillKind, string> = {
  paid: 'PAID',
  memory: 'SERVED FROM MEMORY',
};

export const CardFace: React.FC<{
  readonly step: number;
  readonly name: string;
  /** Already formatted, so the caller owns the spring that produced it. */
  readonly price: React.ReactNode;
  readonly pill: PillKind | null;
  readonly pillScale?: number;
  readonly pillOpacity?: number;
}> = ({step, name, price, pill, pillScale = 1, pillOpacity = 1}) => {
  return (
    <div
      style={{
        width: '100%',
        height: '100%',
        background: CARD_BG,
        border: CARD_BORDER,
        boxSizing: 'border-box',
        padding: 24,
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'space-between',
      }}
    >
      <div style={{fontFamily: MONO, fontWeight: 400, fontSize: 18, color: DIM, lineHeight: 1, whiteSpace: 'nowrap'}}>
        {step} {name}
      </div>
      <div style={{height: 22, display: 'flex', alignItems: 'center'}}>
        {pill === null ? null : (
          <div
            style={{
              fontFamily: MONO,
              fontWeight: 400,
              fontSize: 14,
              color: VIOLET,
              border: `1px solid ${VIOLET}`,
              borderRadius: 4,
              padding: '4px 8px',
              lineHeight: 1,
              whiteSpace: 'nowrap',
              opacity: pillOpacity,
              transform: `scale(${pillScale})`,
              transformOrigin: 'left center',
            }}
          >
            {PILL_TEXT[pill]}
          </div>
        )}
      </div>
      <div style={{fontFamily: MONO, fontWeight: 400, fontSize: 44, color: GOLD, lineHeight: 1}}>
        {price}
      </div>
    </div>
  );
};
