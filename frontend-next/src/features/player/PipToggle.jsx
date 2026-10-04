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
export default function PipToggle({ open, auto = false, onToggle, className = '' }) {
  /*
   * `aria-pressed` tracks the WINDOW, not the automatic-open preference.
   *
   * The two could not share one indicator: with auto on, the window exists
   * only while the user is looking at another tab, so an auto-tracking
   * pressed state would read `true` at the exact moments nobody can see it and
   * `false` whenever they can. The preference is carried by the tooltip, where
   * it can be stated in words, and by the toast that announces every change
   * to it.
   */
  const title = open
    ? 'Close miniplayer'
    : auto
      ? 'Miniplayer — opens by itself when you switch tabs'
      : 'Miniplayer — opens now, and when you switch tabs';

  return (
    <button
      className={`${styles.btn} ${className}`}
      type="button"
      onClick={onToggle}
      aria-pressed={open}
      aria-label={open ? 'Close miniplayer' : 'Open miniplayer'}
      title={title}
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
