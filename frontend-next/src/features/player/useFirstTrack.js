import { useEffect, useRef } from 'react';
import { usePlayer } from '../../state/PlayerContext';
import { useQueueState } from '../queue';

/**
 * Seed the first track once, on mount: a resumed DJ pick or a vibe-band
 * pull (QueueState's startFirst). Must render inside QueueStateProvider.
 *
 * The ref, not the load generation, is what keeps StrictMode's second mount
 * effect from fetching again: refs survive its simulated remount, and the
 * generation would only drop the first result after both requests had gone
 * out.
 */
export default function useFirstTrack() {
  const { current, vibe } = usePlayer();
  const { startFirst } = useQueueState();
  const started = useRef(false);
  useEffect(() => {
    if (current || started.current) return;
    started.current = true;
    startFirst(vibe);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
