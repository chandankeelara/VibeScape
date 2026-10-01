import styles from './Skeleton.module.css';

/**
 * A shimmering placeholder bar.
 *
 * Used while the next track is being chosen. The player's metadata and
 * transport are both showing the track that just FINISHED at that moment —
 * `current` only changes once loadTrack() runs, which is after the fetch
 * resolves. Leaving them up means the hero card says "finding your next
 * track" while the title underneath still names the old one.
 *
 * Sizes are passed in by the caller rather than fixed here, so each bar can
 * match the real element it stands in for — including the clamp() font sizes
 * the title and subtitle use, which keeps the layout from jumping when the
 * real content lands.
 */
export default function Skeleton({ w, h = '14px', r, className = '' }) {
  return (
    <span
      className={`${styles.bar} ${className}`}
      style={{ width: w, height: h, borderRadius: r }}
      aria-hidden="true"
    />
  );
}
