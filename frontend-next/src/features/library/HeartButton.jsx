import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';
import { useToast } from '../../state/ToastContext';
import Icon from '../../ui/Icon';
import { askMembership, inLibrary, libraryKey, setInLibrary, subscribe } from './libraryStore';
import styles from './HeartButton.module.css';

/** How long "Removed from your library · Undo" stays up. */
export const UNDO_MS = 5000;

/** The track's membership, live: true / false, or null while unknown. */
export function useInLibrary(track) {
  const value = useSyncExternalStore(subscribe, () => inLibrary(track), () => inLibrary(track));
  const key = libraryKey(track);
  useEffect(() => {
    if (track && value == null) askMembership(track);
  }, [key, value]); // `track` is read through `key`: a new object for the same song is no change
  return value;
}

/**
 * The heart beside the now-playing title. Outline = not in your library,
 * filled = in it. Removing asks no question: it toasts an Undo instead,
 * because a heart is a one-tap gesture and a confirm dialog would make the
 * common case (a mis-tap, undone) slower than the rare one.
 *
 * Always rendered, unknown and loading states included, so the title box
 * beside it keeps one width whatever the song.
 */
export default function HeartButton({ track, className = '' }) {
  const toast = useToast();
  const value = useInLibrary(track);
  const on = value === true;
  // The pop plays on a click that fills the heart, never when a song that is
  // already liked comes on.
  const [popped, setPopped] = useState(false);
  const key = libraryKey(track);
  useEffect(() => setPopped(false), [key]);

  const toggle = useCallback(async () => {
    if (!track) return;
    const want = !on;
    setPopped(want);
    let dropUndo = null;
    if (!want) {
      dropUndo = toast('Removed from your library', 'info', {
        duration: UNDO_MS,
        action: {
          label: 'Undo',
          onClick: async () => {
            if ((await setInLibrary(track, true)) === 'failed') toast('Couldn’t put it back in your library', 'error');
          },
        },
      });
    }
    const result = await setInLibrary(track, want);
    if (result === 'failed') {
      dropUndo?.();
      toast(want ? 'Couldn’t add to your library' : 'Couldn’t remove from your library', 'error');
    }
  }, [track, on, toast]);

  const label = on ? 'Remove from library' : 'Add to library';
  return (
    <button
      type="button"
      className={`${styles.heart} ${popped && on ? styles.pop : ''} ${className}`}
      aria-pressed={on}
      aria-label={label}
      title={label}
      disabled={key == null}
      onClick={toggle}
      onAnimationEnd={() => setPopped(false)}
    >
      <Icon name={on ? 'heartFill' : 'heart'} size={24} />
    </button>
  );
}
