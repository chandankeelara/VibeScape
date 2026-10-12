import { useSyncExternalStore } from 'react';
import * as volume from '../media/volume';

/**
 * Output volume for the UI. Reads the media-layer singleton directly rather
 * than through PlayerContext: a slider drag changes it many times a second,
 * and only the volume control needs to re-render for that.
 */
export function useVolume() {
  const { volume: level, muted } = useSyncExternalStore(volume.subscribe, volume.getSnapshot);
  return {
    volume: level,
    muted,
    setVolume: volume.setVolume,
    toggleMute: volume.toggleMute,
  };
}

export default useVolume;
