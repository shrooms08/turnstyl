# turnstyl video

Two pieces, built with [Remotion](https://remotion.dev), sharing one set of
fonts, colours, easing and the particle scene.

**The teaser** — 20 seconds, silent, seven beats. It plays muted, so the
on-screen text carries the whole message and every line is sized to read at
400px wide.

| composition | size | output |
| --- | --- | --- |
| `teaser-square` | 1080x1080 | `out/turnstyl-teaser-square.mp4` |
| `teaser-wide` | 1920x1080 | `out/turnstyl-teaser-wide.mp4` |

**The story** — 41 seconds, narrated, ten shots. One camera moves continuously
through a dark 3D space of floating UI panels, cut only at frames 390, 864 and
1040. This one plays *with* sound: there are no burned-in subtitles, and the
only words on screen are design elements at display size.

| composition | size | output |
| --- | --- | --- |
| `story-4k` | 3840x2160 | `out/turnstyl-story-4k.mp4` |
| `story-wide` | 1920x1080 | `out/turnstyl-story-wide.mp4` |
| `story-square` | 1080x1080 | `out/turnstyl-story-square.mp4` |

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

npx remotion render story-4k out/turnstyl-story-4k.mp4 \
  --codec=h264 --crf=16 --concurrency=4

npx remotion render story-wide out/turnstyl-story-wide.mp4 \
  --codec=h264 --crf=18 --concurrency=4

npx remotion render story-square out/turnstyl-story-square.mp4 \
  --codec=h264 --crf=18 --concurrency=4
```

`out/` is gitignored.

## What is in here

```
public/vo/          the nine narration files, 01.mp3 to 09.mp3
src/
  Root.tsx          all five compositions
  Teaser.tsx        the silent teaser's beat sequence
  layout.ts         square vs wide, for the teaser
  theme.ts          Outfit and JetBrains Mono, and the four brand colours
  timing.ts         every teaser beat boundary, in frames
  data.ts           every figure on screen, with where it came from
  beats/            one file per teaser beat
  components/       the typed line, the counters, the card face
  scene/            the particle scene, shared by both pieces
  story/
    Story.tsx       the narrated piece: camera, panels, overlays, narration
    config.ts       design units, the scale factor, and the 3D world
    camera.ts       the camera path and the projection helper
    Stage.tsx       the CSS perspective container and panel placement
    panels.tsx      the four panels, built from the app's own CSS
    panelStyle.ts   the tokens lifted from web/static/turnstyl.css
    overlays.tsx    the only words on screen, and the red bleed
    scene.tsx       how the story drives the shared particle field
    Narration.tsx   the nine <Audio> placements and the duration check
    timing.ts       shot boundaries and the VO table
```

### Resolution

Nothing in the story is written in output pixels. Every size, offset, radius and
camera distance is a *design unit*, multiplied at render time by
`scale = width / designWidth`. `story-4k` and `story-wide` share a design width
of 1920, so 4K is exactly 2x wide and the same picture — frame 500 rendered at
both and compared aligns at offset (0,0). `story-square` has its own design
width because a 1:1 frame is a different composition, not a cropped one, and
`config.ts` gives it its own panel placements.

The particle canvas renders above 1:1 (`dpr` in `config.ts`) so its 1px
wireframes supersample instead of aliasing at 4K.

No type layer uses a CSS filter, blur or `opacity`; text fades on its colour's
own alpha channel so it is never rasterised as a scaled bitmap. Panels do use
`opacity`, which is what fades them in and out.

### Narration

The nine files are placed at their own start frames and never concatenated, so
re-recording one line moves nothing else. `story/Narration.tsx` measures each
file on build and warns if it has drifted more than 3 frames from the duration
recorded in `story/timing.ts`, or if it has grown long enough to overrun into
the next line.

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
