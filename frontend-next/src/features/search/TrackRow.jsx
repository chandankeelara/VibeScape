import { memo } from 'react';
import { isMetadataOnly, trackAccentStyle } from '../../lib/vibe';
import { trackVibe } from './trackVibe';
import { languageLabel } from '../queue/labels';
import { PlusIcon } from './icons';
import styles from './TrackRow.module.css';

/**
 * ONE row for every search result, library or catalog.
 *
 * The legacy `renderSearchResults()` built this markup twice — once at
 * app.js:4355 for library hits and again at app.js:4402 for Spotify hits —
 * with the artwork/title/sub/vibe-chip/sdk-badge/queue-button structure and a
 * verbatim copy of the plus-icon SVG duplicated across both. Collapsing that
 * into this component is the main point of the port; the two variants differ
 * only in the trailing action slot.
 *
 * Props
 *   track       raw track row from the API (library shape or Spotify shape)
 *   variant     'library' | 'spotify' — drives only the trailing slot
 *   onActivate  row click / Enter. Omit to make the row non-interactive
 *               (the legacy data-action="noop" case: a Spotify result that
 *               isn't in the library, where "Add" is the only affordance).
 *   onQueue     the "+" queue button. Omit to hide it.
 *   onAdd       'spotify' + not-in-library only — ingest and play.
 *   inLibrary   'spotify' only — renders the passive "in library" badge.
 *   busy        'add' | 'queue' | null — which trailing control is pending.
 *   selected    keyboard cursor is on this row (drives aria-selected + style)
 *   id          DOM id, referenced by the input's aria-activedescendant
 *   dragKey     opt in to drag-into-queue. See the note below.
 */
function TrackRow({
  track,
  variant = 'library',
  onActivate,
  onQueue,
  onAdd,
  inLibrary = false,
  busy = null,
  selected = false,
  id,
  dragKey = null,
}) {
  const vibe = trackVibe(track);
  const title = track.title || '(unknown)';
  const artist = track.artist || '';
  const album = track.album || '';
  // Two-letter code in the badge; the full name lives in the title tooltip.
  const lang = track.language ? String(track.language).trim().slice(0, 4).toUpperCase() : '';
  const interactive = typeof onActivate === 'function';

  const rowClass = [
    styles.row,
    interactive ? styles.interactive : '',
    selected ? styles.selected : '',
  ].filter(Boolean).join(' ');

  // A nested control must not also trigger the row's activate handler.
  const stop = (fn) => (ev) => { ev.preventDefault(); ev.stopPropagation(); fn(); };

  /*
   * Drag-into-queue. The queue's drag engine (features/queue/useQueueDrag.js)
   * is Pointer Events based, not HTML5 drag-and-drop — HTML5 DnD never fires
   * on touchscreens — so `draggable`/`onDragStart` would be inert here. It
   * picks up sources by a documented attribute pair on the row instead.
   *
   * Passing `dragKey` is still the opt-in: the attributes are the cross-
   * feature wire format, not a caller-facing API, and a row without the prop
   * renders neither. Neither feature imports the other; the engine dispatches
   * a `vibescape:queue-drop` CustomEvent with this key and SearchBar resolves
   * it back to the track.
   */
  const dragAttrs = dragKey
    ? { 'data-drag-source': 'search-lib', 'data-drag-key': dragKey }
    : null;

  return (
    <div
      id={id}
      className={rowClass}
      {...dragAttrs}
      role="option"
      aria-selected={selected}
      aria-disabled={interactive ? undefined : true}
      tabIndex={-1}
      onClick={interactive ? () => onActivate() : undefined}
      onKeyDown={
        interactive
          ? (ev) => {
              if (ev.key === 'Enter' || ev.key === ' ') {
                ev.preventDefault();
                onActivate();
              }
            }
          : undefined
      }
    >
      {track.artwork_url ? (
        <img className={styles.art} src={track.artwork_url} alt="" loading="lazy" />
      ) : (
        <div className={styles.art} aria-hidden="true" />
      )}

      <div className={styles.body}>
        <div className={styles.titleRow}>
          <span className={styles.title}>{title}</span>
        </div>
        <div className={styles.sub}>
          {/* Coloured by the TRACK's own score, never the global vibe — a
              "VIBE 61" badge that changed colour with the slider would show
              one song in two colours. Unscored tracks get no badge rather
              than an invented number. */}
          {vibe != null && (
            <span className={`${styles.badge} ${styles.vibe}`} style={trackAccentStyle(vibe)} title="Measured energy">
              VIBE {vibe}
            </span>
          )}
          {lang && <span className={styles.badge} title={languageLabel(track.language)}>{lang}</span>}
          {isMetadataOnly(track) && (
            <span className={styles.badge} title="No local audio — playable only via Spotify Premium">
              SDK only
            </span>
          )}
          <span className={styles.subText}>
            {artist}
            {artist && album ? ' · ' : ''}
            {album}
          </span>
        </div>
      </div>

      {onQueue && (
        <button
          className={`${styles.iconBtn} ${busy === 'queue' ? styles.busy : ''}`}
          type="button"
          disabled={busy === 'queue'}
          aria-label={`Add ${title} to queue`}
          title="Add to queue"
          onClick={stop(onQueue)}
        >
          <PlusIcon size={18} />
        </button>
      )}

      {variant === 'spotify' && (
        inLibrary ? (
          <span className={styles.inLib}>In library</span>
        ) : onAdd ? (
          <button
            className={`${styles.add} ${busy === 'add' ? styles.busy : ''}`}
            type="button"
            disabled={busy === 'add'}
            onClick={stop(onAdd)}
          >
            <PlusIcon size={16} />
            <span>{busy === 'add' ? 'Adding…' : 'Add'}</span>
          </button>
        ) : null
      )}
    </div>
  );
}

export default memo(TrackRow);
