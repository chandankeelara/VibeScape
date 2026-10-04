import styles from './PipToggle.module.css';

/**
 * Open / close the picture-in-picture miniplayer.
 *
 * NOT RENDERED AT ALL where Document PiP is unavailable (Firefox, Safari,
 * every mobile browser). The caller checks `supported` from
 * usePictureInPicture and simply omits this — a disabled control is right when
 * an action exists but is momentarily unavailable, and this one does not exist
 * on those engines.
 *
 * ICON: deliberately the same frame-plus-block grammar as TheaterToggle, which
 * sits a few pixels away in theater mode, with the block in the bottom-right
 * corner — the universal PiP glyph. The two read as members of one family and
 * are still told apart at 24px.
 *
 * The state is carried by the accent tint and `aria-pressed` rather than by a
 * second glyph: at this size a "come back" variant of a corner block is a
 * smudge, and the label already changes.
 *
 * Stateless and context-free, like TheaterToggle — the owner of the window
 * (PlayerPage -> usePictureInPicture) owns the state, and this is the handle.
 */
export default function PipToggle({ open, onToggle, className = '' }) {
  return (
    <button
      className={`${styles.btn} ${className}`}
      type="button"
      onClick={onToggle}
      aria-pressed={open}
      aria-label={open ? 'Close miniplayer' : 'Open miniplayer'}
      title={open ? 'Close miniplayer' : 'Miniplayer — keep controls on top of other windows'}
    >
      <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">
        <rect
          className={styles.screen}
          x="2.5" y="4.5" width="19" height="15" rx="2.5"
          fill="none" stroke="currentColor" strokeWidth="1.5"
        />
        <rect className={styles.content} x="12" y="11" width="8" height="6" rx="1" />
      </svg>
    </button>
  );
}
