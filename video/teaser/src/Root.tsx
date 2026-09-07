import React from 'react';
import {Composition} from 'remotion';
import {square, wide} from './layout';
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
    </>
  );
};
