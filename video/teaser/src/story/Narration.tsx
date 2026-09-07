/**
 * The narration: nine files, each placed at its own start frame.
 *
 * Nothing is concatenated. Re-recording one line changes only that line's
 * placement, and the check below is what tells you when a re-recorded line no
 * longer fits: on build it measures every file and warns when one has drifted
 * more than VO_TOLERANCE_FRAMES from what was measured when it was placed, or
 * when a file has grown long enough to run into the next line.
 */
import {getAudioDurationInSeconds} from '@remotion/media-utils';
import React, {useEffect} from 'react';
import {Audio, Sequence, staticFile} from 'remotion';
import {STORY_FPS, VO, VO_TOLERANCE_FRAMES} from './timing';

let checked = false;

/** Measures the nine files once per bundle and warns about anything off. */
export function checkNarration(): void {
  if (checked) return;
  checked = true;
  for (const line of VO) {
    getAudioDurationInSeconds(staticFile(line.file))
      .then((seconds) => {
        const frames = seconds * STORY_FPS;
        const drift = frames - line.expectedFrames;
        if (Math.abs(drift) > VO_TOLERANCE_FRAMES) {
          console.warn(
            `turnstyl story: ${line.file} is ${frames.toFixed(1)} frames, ` +
              `${drift > 0 ? '+' : ''}${drift.toFixed(1)} against the ${line.expectedFrames} ` +
              `it was placed at. Re-check shot timing.`,
          );
        }
        if (frames > line.slotFrames) {
          console.warn(
            `turnstyl story: ${line.file} is ${frames.toFixed(1)} frames and overruns its ` +
              `${line.slotFrames}-frame slot; it will play over VO ${line.n + 1}.`,
          );
        }
      })
      .catch((err: unknown) => {
        console.warn(`turnstyl story: could not measure ${line.file}: ${String(err)}`);
      });
  }
}

export const Narration: React.FC = () => {
  useEffect(() => {
    checkNarration();
  }, []);

  return (
    <>
      {VO.map((line) => (
        <Sequence key={line.file} from={line.at} name={`vo ${line.n}`}>
          <Audio src={staticFile(line.file)} />
        </Sequence>
      ))}
    </>
  );
};
