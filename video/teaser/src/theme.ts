/**
 * turnstyl teaser — brand tokens.
 *
 * Colours and type are the product's, not the template's. Outfit carries the
 * display voice; JetBrains Mono carries anything the machine said (commands,
 * prices, token counts). Nothing else is used anywhere in the teaser.
 */
import {loadFont as loadJetBrainsMono} from '@remotion/google-fonts/JetBrainsMono';
import {loadFont as loadOutfit} from '@remotion/google-fonts/Outfit';

const outfit = loadOutfit('normal', {weights: ['300', '400'], subsets: ['latin']});
const mono = loadJetBrainsMono('normal', {weights: ['400'], subsets: ['latin']});

export const DISPLAY = outfit.fontFamily;
export const MONO = mono.fontFamily;

export const BLACK = '#000000';
export const OFF_WHITE = '#F2F0EA';
export const GOLD = '#E2B04A';
export const VIOLET = '#7F77DD';
export const RED = '#E5484D';

/** Secondary type: off-white held back so the gold and violet stay the loudest
 *  things on the frame, but still legible when the video plays 400px wide. */
export const DIM = 'rgba(242, 240, 234, 0.55)';

/** Card surface, per the brief: near-black with a hairline border. */
export const CARD_BG = '#0a0a0a';
export const CARD_BORDER = '1px solid rgba(255, 255, 255, 0.08)';

/** Every text element sits on this margin grid. */
export const MARGIN = 64;

/** JetBrains Mono advances 0.6em per glyph; used to size the block cursor. */
export const MONO_ADVANCE = 0.6;
