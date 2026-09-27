/**
 * One track row in the sidebar, for both lists.
 *
 * The legacy renderQueueRow(track, index, kind) was already a component with a
 * variant prop in all but name — it built one HTML string and swapped the
 * right-hand button and the click action on `kind`. This is that, literally.
 *
 *   kind="queue" — activating jumps the queue to this track; right button removes.
 *   kind="rec"   — activating plays the track now; right button adds to queue.
 */

import { memo } from 'react';
import { isMetadataOnly, trackKey } from '../../lib/vibe';
import { languageLabel, subtitleFor, trackVibe } from './labels';
import styles from './QueueRow.module.css';

function GripIcon() {
  return (
    <svg viewBox="0 0 16 16" width="10" height="14" fill="currentColor" aria-hidden="true">
      <circle cx="5" cy="4" r="1" /><circle cx="11" cy="4" r="1" />
      <circle cx="5" cy="8" r="1" /><circle cx="11" cy="8" r="1" />
      <circle cx="5" cy="12" r="1" /><circle cx="11" cy="12" r="1" />
    </svg>
  );
}

function CrossIcon() {
  return (
    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  );
}

function QueueRow({ track, index, kind, onActivate, onAction, onMove, consuming = false }) {
  const key = trackKey(track);
  const isQueue = kind === 'queue';
  const vibe = trackVibe(track);
  const lang = languageLabel(track.language || '');
  const sdkOnly = isMetadataOnly(track);
  const title = track.title || '(unknown)';
  const sub = subtitleFor(track);

  const onGripKeyDown = (ev) => {
    if (!isQueue || !onMove) return;
    if (ev.key === 'ArrowUp') {
      ev.preventDefault();
      onMove(index, index - 1);
    } else if (ev.key === 'ArrowDown') {
      ev.preventDefault();
      // reorderQueue takes an insert-before index in the pre-removal list, so
      // moving down one slot means targeting index + 2.
      onMove(index, index + 2);
    }
  };

  return (
    <li
      className={`${styles.row} ${consuming ? styles.consuming : ''}`}
      data-drag-source={kind}
      {...(isQueue ? { 'data-drag-idx': index } : { 'data-drag-key': key })}
    >
      <button
        type="button"
        className={styles.main}
        onClick={() => onActivate(track, index)}
        aria-label={isQueue ? `Play ${title} now and skip ahead` : `Play ${title} now`}
      >
        {track.artwork_url
          ? <img className={styles.art} src={track.artwork_url} alt="" loading="lazy" />
          : <span className={styles.art} aria-hidden="true" />}
        <span className={styles.body}>
          <span className={styles.title}>{title}</span>
          <span className={styles.sub}>{sub}</span>
          {(vibe != null || lang || sdkOnly) && (
            <span className={styles.meta}>
              {vibe != null && <span className={styles.vibe}>vibe {vibe}</span>}
              {lang && <span className={styles.lang}>{lang}</span>}
              {sdkOnly && (
                <span className={styles.sdk} title="No local audio — playable only via Spotify Premium">
                  sdk-only
                </span>
              )}
            </span>
          )}
        </span>
      </button>

      <div className={styles.actions}>
        <button
          type="button"
          className={styles.grip}
          data-drag-handle
          // Queue rows get a keyboard path to reordering that the legacy
          // pointer-only grip never had. Rec rows drag into the queue, and the
          // add button below is already the keyboard equivalent of that.
          tabIndex={isQueue ? 0 : -1}
          aria-hidden={isQueue ? undefined : 'true'}
          aria-label={isQueue ? `Reorder ${title}: drag, or use the arrow keys` : undefined}
          title="Drag to reorder"
          onKeyDown={onGripKeyDown}
        >
          <GripIcon />
        </button>
        <button
          type="button"
          className={`${styles.btn} ${isQueue ? '' : styles.add}`}
          onClick={() => onAction(track, index)}
          aria-label={isQueue ? `Remove ${title} from queue` : `Add ${title} to queue`}
          title={isQueue ? 'Remove' : 'Add to queue'}
        >
          {isQueue ? <CrossIcon /> : <PlusIcon />}
        </button>
      </div>
    </li>
  );
}

export default memo(QueueRow);
