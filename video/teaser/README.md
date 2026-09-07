# turnstyl teaser

A 20-second teaser for turnstyl, built with [Remotion](https://remotion.dev).
Two compositions, same beats, same 600 frames at 30fps:

| composition | size | output |
| --- | --- | --- |
| `teaser-square` | 1080x1080 | `out/turnstyl-teaser-square.mp4` |
| `teaser-wide` | 1920x1080 | `out/turnstyl-teaser-wide.mp4` |

It plays muted, so the on-screen text carries the whole message and every line
is sized to read at 400px wide.

This folder is deliberately separate from the Python package. It has its own
`package.json` and its own toolchain, and it touches nothing in `src/`,
`scripts/`, `web/` or `pyproject.toml`. The repo's no-TypeScript rule is scoped
to the product; this is not the product.

## Install

Node 20 or newer.

```bash
cd video/teaser
npm install
```

## Preview

```bash
npx remotion studio
```

Pick `teaser-square` or `teaser-wide` in the sidebar and scrub.

## Render

Both renders need a real WebGL context for the particle scene, which is why
`remotion.config.ts` sets the Chromium OpenGL renderer to `angle`. Run these
from `video/teaser`:

```bash
npx remotion render teaser-square out/turnstyl-teaser-square.mp4 \
  --codec=h264 --crf=18 --concurrency=4

npx remotion render teaser-wide out/turnstyl-teaser-wide.mp4 \
  --codec=h264 --crf=18 --concurrency=4
```

`out/` is gitignored.

## What is in here

```
src/
  Root.tsx          the two compositions
  Teaser.tsx        the beat sequence, shared by both
  layout.ts         the only thing that differs between square and wide
  theme.ts          Outfit and JetBrains Mono, and the four brand colours
  timing.ts         every beat boundary, in frames
  data.ts           every figure on screen, with where it came from
  beats/            one file per beat
  components/       the typed line, the counters, the card face
  scene/            the particle scene
```

### The numbers

Every figure in beats 4 and 5 comes from `docs/EVALS.md` at the repo root and
the run behind it, `evals/results/2026-09-07.json`. `src/data.ts` names the
source of each one. Nothing on screen is invented; if the eval is re-run and
the figures move, `src/data.ts` is the one file to change.

### The particle scene

`src/scene/` is a port of the three.js scene in `web/index.html` — the same
brain silhouette and streamline walker, the same logo mark, the same coin, the
same scatter cloud, the same 5000 wireframe tetrahedra with additive blending
and `depthWrite` off. Two rules govern the port:

- **Nothing random per frame.** Every `Math.random()` became a draw from a
  seeded mulberry32 (`src/scene/rng.ts`). Remotion renders frames in parallel
  processes, so an unseeded draw would flicker halfway through the file.
- **Nothing time-based.** No `requestAnimationFrame`, no clock, no state.
  Every position, colour, scale and rotation is a pure function of
  `useCurrentFrame()`. What the page derives from scroll position is derived
  here from the frame number.

Rendering the same frame twice in separate processes produces byte-identical
PNGs; that is the check to re-run after any change to `src/scene/`.

The coin form is ported but not placed: no beat calls for one. It is exported
from `src/scene/forms.ts` as `buildCoinForm()` and rasterises its glyph from a
font, which is the one part of the scene that could differ between workers — so
call it only from somewhere that has already waited on `document.fonts`.
