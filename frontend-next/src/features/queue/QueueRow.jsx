/**
 * One track row in the queue panel and on the phone home list.
 *
 *   kind="now"   — the playing track. Not interactive; carries the equalizer.
 *   kind="queue" — activating jumps the queue to this track; grip reorders
 *                  (drag, or arrow keys); right button removes.
 *   kind="rec"   — activating plays the track now; right button adds to queue;
 *                  the row can be dragged into Up next.
 *
 * The whole row is the hit area for "play" (the main button's ::before covers
 * it); grip and actions sit above that layer.
 *
 * A track the backend marks `discover: true` (Discovery mode: from the
 * catalogue, not the user's library) carries a "Discover" badge until the
 * user hearts it — membership is read live from the library store, so the
 * badge goes the moment the heart fills, on every copy of the track.
 *
 * The primary genre (lib/genres.js) shows as one chip, only in a row wide
 * enough to keep the artist readable beside it.
 *
 * The drag engine (useQueueDrag) finds rows by data-drag-* attributes and the
 * grip by data-drag-handle — keep both.
 */

import { memo, useSyncExternalStore } from 'react';
import { isMetadataOnly, trackAccentStyle, trackKey, trackVibe } from '../../lib/vibe';
import { inLibrary, subscribe as subscribeLibrary } from '../library/libraryStore';
import { languageLabel } from './labels';
import Icon from '../../ui/Icon';
import Equalizer from '../../ui/Equalizer';
import GenreChips from '../../ui/GenreChips';
import { primaryGenre } from '../../lib/genres';
import ui from '../../ui/controls.module.css';
import styles from './QueueRow.module.css';

/**
 * True while `track` is a Discovery find that is still not in the library.
 * Reads the store directly rather than through useInLibrary, which would ask
 * GET /contains for every row without an `in_library`; a row that is not a
 * find never needs to know.
 */
function useDiscovered(track) {
  const snap = () => track?.discover === true && inLibrary(track) !== true;
  return useSyncExternalStore(subscribeLibrary, snap, snap);
}

const langCode = (code) => (code ? String(code).trim().slice(0, 4).toUpperCase() : '');

function QueueRow({
  track, index = 0, kind, onActivate, onAction, onMove, consuming = false, draggable = true,
}) {
  const key = trackKey(track);
  const isQueue = kind === 'queue';
  const isNow = kind === 'now';
  const vibe = trackVibe(track);
  const lang = langCode(track.language);
  const sdkOnly = isMetadataOnly(track);
  const title = track.title || '(unknown)';
  const artist = track.artist || track.album || '';
  const hasGrip = isQueue && draggable;
  const discovered = useDiscovered(track) && !isNow;
  const genre = primaryGenre(track);

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

  const dragAttrs = isNow || !draggable
    ? {}
    : { 'data-drag-source': kind, ...(isQueue ? { 'data-drag-idx': index } : { 'data-drag-key': key }) };

  const rowClass = [
    styles.row,
    hasGrip ? styles.withGrip : '',
    isNow ? styles.current : '',
    consuming ? styles.consuming : '',
  ].filter(Boolean).join(' ');

  const body = (
    <>
      <span className={styles.thumb}>
        {track.artwork_url
          ? <img src={track.artwork_url} alt="" loading="lazy" />
          : <Icon name="disc" size={18} />}
      </span>
      <span className={styles.text}>
        <span className={styles.title}>
          <span className={styles.titleText}>{title}</span>
          {isNow && <Equalizer />}
        </span>
        <span className={styles.sub}>
          {vibe != null && (
            <span className={`${ui.badge} ${ui.badgeSmall} ${ui.badgeVibe}`} style={trackAccentStyle(vibe)}>
              VIBE {vibe}
            </span>
          )}
          {lang && (
            <span className={`${ui.badge} ${ui.badgeSmall}`} title={languageLabel(track.language)}>{lang}</span>
          )}
          {sdkOnly && (
            <span className={`${ui.badge} ${ui.badgeSmall}`} title="No local audio — playable only via Spotify Premium">
              SDK
            </span>
          )}
          {discovered && (
            <span
              className={`${ui.badge} ${ui.badgeSmall} ${ui.badgeDiscover}`}
              title="Not in your library — found by Discovery"
            >
              Discover
            </span>
          )}
          {/* Shown only where the row is wide enough (the container query
              in QueueRow.module.css): never at the desktop column's width. */}
          {genre && (
            <span className={styles.genre}><GenreChips genres={[genre]} /></span>
          )}
          <span className={styles.artist}>{artist}</span>
        </span>
      </span>
    </>
  );

  return (
    <li className={rowClass} {...dragAttrs}>
      {hasGrip && (
        <button
          type="button"
          className={styles.grip}
          data-drag-handle
          aria-label={`Reorder ${title}: drag, or use the arrow keys`}
          title="Drag to reorder"
          onKeyDown={onGripKeyDown}
        >
          <Icon name="grip" size={16} />
        </button>
      )}

      {isNow ? (
        <div className={styles.main}>{body}</div>
      ) : (
        <button
          type="button"
          className={`${styles.main} ${styles.mainBtn}`}
          onClick={() => onActivate?.(track, index)}
          aria-label={`${isQueue ? `Play ${title} now and skip ahead` : `Play ${title} now`}${discovered ? ' (Discover: not in your library)' : ''}`}
        >
          {body}
        </button>
      )}

      {!isNow && onAction && (
        <div className={styles.actions}>
          <button
            type="button"
            className={`${ui.ib} ${styles.act} ${isQueue ? '' : styles.reveal}`}
            onClick={() => onAction(track, index)}
            aria-label={isQueue ? `Remove ${title} from queue` : `Add ${title} to queue`}
            title={isQueue ? 'Remove' : 'Add to queue'}
          >
            <Icon name={isQueue ? 'x' : 'plus'} size={18} />
          </button>
        </div>
      )}
    </li>
  );
}

export default memo(QueueRow);
