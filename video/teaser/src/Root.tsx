import React from 'react';
import {Composition} from 'remotion';
import {square, wide} from './layout';
import {story4k, storySquare, storyWide} from './story/config';
import {Story} from './story/Story';
import {STORY_DURATION, STORY_FPS} from './story/timing';
import {Teaser} from './Teaser';
import {DURATION, FPS} from './timing';

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="teaser-square"
        component={Teaser}
        durationInFrames={DURATION}
        fps={FPS}
        width={square.width}
        height={square.height}
        defaultProps={{layout: square}}
      />
      <Composition
        id="teaser-wide"
        component={Teaser}
        durationInFrames={DURATION}
        fps={FPS}
        width={wide.width}
        height={wide.height}
        defaultProps={{layout: wide}}
      />
      <Composition
        id="story-4k"
        component={Story}
        durationInFrames={STORY_DURATION}
        fps={STORY_FPS}
        width={story4k.width}
        height={story4k.height}
        defaultProps={{layout: story4k}}
      />
      <Composition
        id="story-wide"
        component={Story}
        durationInFrames={STORY_DURATION}
        fps={STORY_FPS}
        width={storyWide.width}
        height={storyWide.height}
        defaultProps={{layout: storyWide}}
      />
      <Composition
        id="story-square"
        component={Story}
        durationInFrames={STORY_DURATION}
        fps={STORY_FPS}
        width={storySquare.width}
        height={storySquare.height}
        defaultProps={{layout: storySquare}}
      />
    </>
  );
};
