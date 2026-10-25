import styles from './GenreChips.module.css';

/**
 * A row of genre chips, from lib/genres.js trackGenres(). A primary genre
 * (score 100) is filled and in --text-2; a secondary one (50) is outlined and
 * quieter. Neutral tokens only: the accent belongs to the track's VIBE badge.
 *
 * The row never wraps visibly: chips wrap inside a one-chip-tall box with
 * overflow hidden, so one that does not fit drops whole out of sight rather
 * than half-cut, and the list is ordered so the least important goes first.
 *
 * Positioning is the caller's: wrap this in an element of your own module.
 */
export default function GenreChips({ genres }) {
  if (!genres?.length) return null;
  return (
    <span className={styles.chips}>
      {genres.map((g) => (
        <span
          key={g.slug}
          className={`${styles.chip} ${g.primary ? styles.primary : styles.secondary}`}
          data-genre={g.slug}
          data-primary={g.primary ? 'true' : 'false'}
          title={g.primary ? `Genre: ${g.label}` : `Also: ${g.label}`}
        >
          {g.label}
        </span>
      ))}
    </span>
  );
}
