/**
 * The teaser. Both compositions render this same component; the only thing
 * that differs is the layout it is handed.
 */
import React from 'react';
import {AbsoluteFill, Sequence} from 'remotion';
import {Delete} from './beats/Delete';
import {Memory} from './beats/Memory';
import {Meter} from './beats/Meter';
import {Name} from './beats/Name';
import {Numbers} from './beats/Numbers';
import {Refusal} from './beats/Refusal';
import {WhoIsRefusing} from './beats/WhoIsRefusing';
import {Layout} from './layout';
import {SceneCanvas} from './scene/SceneCanvas';
import {BEAT, CARDS_SPAN, REFUSAL_SPAN, SCENE_INTRO, SCENE_OUTRO} from './timing';
import {BLACK} from './theme';

/**
 * The brain sits behind the refusal line rather than beside it. At 400px wide
 * the red type has to win, so it is held at 25% rather than 35%: the form is
 * still legible against black and the line reads clean over it.
 */
const BRAIN_OPACITY = 0.25;

export const Teaser: React.FC<{readonly layout: Layout}> = ({layout}) => {
  return (
    <AbsoluteFill style={{backgroundColor: BLACK}}>
      {/* Beats 1-2: the brain, right of centre, behind the type. */}
      <Sequence from={SCENE_INTRO.from} durationInFrames={SCENE_INTRO.duration} layout="none">
        <SceneCanvas
          mode="intro"
          span={SCENE_INTRO.duration}
          layout={layout}
          brainOpacity={BRAIN_OPACITY}
        />
      </Sequence>

      <Sequence from={REFUSAL_SPAN.from} durationInFrames={REFUSAL_SPAN.duration} layout="none">
        <Refusal layout={layout} />
      </Sequence>

      <Sequence from={BEAT.who.from} durationInFrames={BEAT.who.duration} layout="none">
        <WhoIsRefusing layout={layout} />
      </Sequence>

      {/* Beats 3-4: the cards are one object across both. */}
      <Sequence from={CARDS_SPAN.from} durationInFrames={CARDS_SPAN.duration} layout="none">
        <Meter layout={layout} />
      </Sequence>

      <Sequence from={BEAT.memory.from} durationInFrames={BEAT.memory.duration} layout="none">
        <Memory layout={layout} />
      </Sequence>

      <Sequence from={BEAT.numbers.from} durationInFrames={BEAT.numbers.duration} layout="none">
        <Numbers layout={layout} />
      </Sequence>

      {/* Beats 6-7: the scene comes back to scatter, then to make the mark. */}
      <Sequence from={SCENE_OUTRO.from} durationInFrames={SCENE_OUTRO.duration} layout="none">
        <SceneCanvas
          mode="outro"
          span={SCENE_OUTRO.duration}
          layout={layout}
          brainOpacity={BRAIN_OPACITY}
        />
      </Sequence>

      <Sequence from={BEAT.del.from} durationInFrames={BEAT.del.duration} layout="none">
        <Delete layout={layout} />
      </Sequence>

      <Sequence from={BEAT.name.from} durationInFrames={BEAT.name.duration} layout="none">
        <Name layout={layout} />
      </Sequence>
    </AbsoluteFill>
  );
};
