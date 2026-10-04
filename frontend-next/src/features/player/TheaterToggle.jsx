import styles from './TheaterToggle.module.css';

/**
 * Theater on/off. One implementation, placed by `className`, rendered in two
 * places — and the two places are a deliberate answer to a hard constraint,
 * not an oversight. See ArtStage.jsx / TheaterBar.jsx.
 *
 * ICON: YouTube's grammar. The outer rounded rect is the screen and is
 * identical in both states; the inner filled block is the content and changes
 * proportion — wide and short for "go wide", compact for "come back". Both
 * show the DESTINATION, matching the dock button beside them, which draws
 * inward arrows for where it will go.
 *
 * Deliberately stateless and context-free: the owner of the layout owns the
 * state (PlayerPage -> useTheaterMode), and this is the handle.
 */
export default function TheaterToggle({ theater, onToggle, className = '' }) {
  return (
    <button
      className={`${styles.btn} ${className}`}
      type="button"
      onClick={onToggle}
      aria-pressed={theater}
      aria-label={theater ? 'Exit theater mode' : 'Theater mode'}
      title={theater ? 'Default view' : 'Theater mode'}
    >
      <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">
        <rect
          className={styles.screen}
          x="2.5" y="4.5" width="19" height="15" rx="2.5"
          fill="none" stroke="currentColor" strokeWidth="1.5"
        />
        {theater ? (
          <rect className={styles.content} x="7" y="7" width="10" height="10" rx="1" />
        ) : (
          <rect className={styles.content} x="4.5" y="9" width="15" height="6" rx="1" />
        )}
      </svg>
    </button>
  );
}
