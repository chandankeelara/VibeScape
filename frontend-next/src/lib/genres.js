/**
 * A track's genre tags: the one place their labels and their order live.
 *
 * The backend sends `genres`, a list of {slug, score} (score 100 = primary,
 * 50 = secondary), already sorted, or [] when the track is untagged or the
 * database has no genre column yet (backend/track_genres.py). It is sorted
 * again here so a track from anywhere — a cached queue, an older payload —
 * shows the same chips in the same order.
 *
 * GENRE_ORDER is ingest_pipeline/genre_taxonomy.py's GENRES, append-only;
 * tests/backend/test_track_genres.py fails when a slug there has no label
 * here. A slug with no label is not shown.
 *
 * The iTunes `genre` text is a different, older field. TrackMeta falls back
 * to it when `genres` is empty.
 */

export const GENRE_ORDER = [
  'pop',
  'hiphop_rap',
  'dance_edm',
  'ambient_chill',
  'rock_alt_metal',
  'rnb_soul',
  'film_melody',
  'film_mass',
  'folk',
  'devotional',
  'classical',
  'instrumental_score',
  'latin_afro',
  'reggae_dancehall',
  'country',
  'jazz_blues',
  'other',
];

export const GENRE_LABELS = {
  pop: 'Pop',
  hiphop_rap: 'Hip-hop / Rap',
  dance_edm: 'Dance / EDM',
  ambient_chill: 'Ambient / Chill',
  rock_alt_metal: 'Rock / Alt',
  rnb_soul: 'R&B / Soul',
  film_melody: 'Film · Melody',
  film_mass: 'Film · Mass',
  folk: 'Folk',
  devotional: 'Devotional',
  classical: 'Classical',
  instrumental_score: 'Instrumental / Score',
  latin_afro: 'Latin / Afro',
  reggae_dancehall: 'Reggae / Dancehall',
  country: 'Country',
  jazz_blues: 'Jazz / Blues',
  other: 'Other',
};

export const PRIMARY_SCORE = 100;

const RANK = Object.fromEntries(GENRE_ORDER.map((slug, i) => [slug, i]));

/** Human label for a slug, or null for one this build does not know. */
export function genreLabel(slug) {
  return Object.prototype.hasOwnProperty.call(GENRE_LABELS, slug) ? GENRE_LABELS[slug] : null;
}

/**
 * The chips to show for a track: [{slug, score, label, primary}], score high
 * first, then taxonomy order. [] when the track has no usable tags.
 */
export function trackGenres(track) {
  const list = Array.isArray(track?.genres) ? track.genres : [];
  return list
    .filter((g) => g && genreLabel(g.slug) && (g.score === 50 || g.score === 100))
    .map((g) => ({
      slug: g.slug,
      score: g.score,
      label: genreLabel(g.slug),
      primary: g.score === PRIMARY_SCORE,
    }))
    .sort((a, b) => b.score - a.score || RANK[a.slug] - RANK[b.slug]);
}

/** The first chip (what a compact row shows), or null. */
export function primaryGenre(track) {
  return trackGenres(track)[0] ?? null;
}
