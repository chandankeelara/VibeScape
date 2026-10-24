// @vitest-environment jsdom
/**
 * Genre tags in the UI (frontend-next/src/lib/genres.js, ui/GenreChips.jsx):
 *
 *   - one label per taxonomy slug, in the taxonomy's order (the backend test
 *     tests/backend/test_track_genres.py checks the slugs against
 *     ingest_pipeline/genre_taxonomy.py);
 *   - chips are ordered score high first, then by taxonomy order, whatever
 *     order the payload has; unknown slugs and scores are dropped;
 *   - now-playing shows every tag as a chip, primary (100) and secondary (50)
 *     told apart, and falls back to the iTunes `genre` text, exactly as
 *     before, when `genres` is empty or missing;
 *   - a queue row carries one chip, the first one.
 */
import { cleanup, render, screen } from '@testing-library/react';
import {
  GENRE_LABELS, GENRE_ORDER, genreLabel, primaryGenre, trackGenres,
} from '../../frontend-next/src/lib/genres';

vi.mock('../../frontend-next/src/lib/api', () => ({
  libraryAdd: async () => null,
  libraryRemove: async () => null,
  libraryContains: async () => ({}),
}));

let current = null;
vi.mock('../../frontend-next/src/state/PlayerContext', () => ({
  usePlayer: () => ({
    current,
    source: null,
    loadingTrack: false,
    verifying: false,
    startVerify() {},
    stopVerify() {},
    canVerify: false,
  }),
}));

let TrackMeta;
let QueueRow;
let TrackRow;
let resetLibraryStore;
let ToastProvider;
beforeAll(async () => {
  window.matchMedia ||= (q) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {} });
  ({ default: TrackMeta } = await import('../../frontend-next/src/features/player/TrackMeta'));
  ({ default: QueueRow } = await import('../../frontend-next/src/features/queue/QueueRow'));
  ({ default: TrackRow } = await import('../../frontend-next/src/features/search/TrackRow'));
  ({ resetLibraryStore } = await import('../../frontend-next/src/features/library'));
  ({ ToastProvider } = await import('../../frontend-next/src/state/ToastContext'));
});
beforeEach(() => resetLibraryStore());
afterEach(cleanup);

const renderMeta = () => render(<ToastProvider><TrackMeta /></ToastProvider>);

const TRACK = {
  id: 7, spotify_id: 'sp7', title: 'Song', artist: 'Singer', album: 'Album',
  genre: 'Indian Pop', in_library: true, discover: false,
};
const chips = (root = document) => [...root.querySelectorAll('[data-genre]')]
  .map((el) => [el.dataset.genre, el.textContent, el.dataset.primary]);

describe('the label map', () => {
  it('labels the 17 taxonomy slugs, in taxonomy order', () => {
    expect(GENRE_ORDER).toEqual([
      'pop', 'hiphop_rap', 'dance_edm', 'ambient_chill', 'rock_alt_metal', 'rnb_soul',
      'film_melody', 'film_mass', 'folk', 'devotional', 'classical', 'instrumental_score',
      'latin_afro', 'reggae_dancehall', 'country', 'jazz_blues', 'other',
    ]);
    expect(Object.keys(GENRE_LABELS)).toEqual(GENRE_ORDER);
    expect(GENRE_LABELS).toEqual({
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
    });
  });

  it('has no label for an unknown slug, inherited names included', () => {
    expect(genreLabel('polka')).toBeNull();
    expect(genreLabel('constructor')).toBeNull();
    expect(genreLabel(undefined)).toBeNull();
  });
});

describe('chip order', () => {
  it('puts primary before secondary, then follows the taxonomy', () => {
    const t = {
      genres: [
        { slug: 'other', score: 50 },
        { slug: 'film_mass', score: 100 },
        { slug: 'pop', score: 50 },
        { slug: 'dance_edm', score: 100 },
      ],
    };
    expect(trackGenres(t).map((g) => [g.slug, g.primary])).toEqual([
      ['dance_edm', true], ['film_mass', true], ['pop', false], ['other', false],
    ]);
    expect(primaryGenre(t)).toMatchObject({ slug: 'dance_edm', label: 'Dance / EDM', score: 100 });
  });

  it('drops unknown slugs and scores, and is empty without tags', () => {
    const t = { genres: [{ slug: 'polka', score: 100 }, { slug: 'folk', score: 75 }, { slug: 'folk2' }, null,
      { slug: 'country', score: 50 }] };
    expect(trackGenres(t).map((g) => g.slug)).toEqual(['country']);
    for (const x of [{}, { genres: [] }, { genres: null }, null, undefined]) {
      expect(trackGenres(x)).toEqual([]);
      expect(primaryGenre(x)).toBeNull();
    }
  });

  it('a track tagged only secondary still has a first chip', () => {
    expect(primaryGenre({ genres: [{ slug: 'jazz_blues', score: 50 }, { slug: 'folk', score: 50 }] }))
      .toMatchObject({ slug: 'folk', primary: false });
  });
});

describe('now playing', () => {
  it('shows every tag as a chip, primary first, in place of the iTunes text', () => {
    current = { ...TRACK, genres: [{ slug: 'pop', score: 50 }, { slug: 'film_melody', score: 100 }] };
    renderMeta();
    expect(chips()).toEqual([
      ['film_melody', 'Film · Melody', 'true'],
      ['pop', 'Pop', 'false'],
    ]);
    expect(screen.queryByText('Indian Pop')).toBeNull();
  });

  it('falls back to the iTunes genre text when untagged', () => {
    for (const genres of [[], undefined]) {
      current = { ...TRACK, genres };
      const { container, unmount } = renderMeta();
      expect(chips(container)).toEqual([]);
      expect(screen.getByText('Indian Pop').tagName).toBe('P');
      unmount();
    }
  });

  it('keeps the empty genre line when there is neither', () => {
    current = { ...TRACK, genre: null, genres: [] };
    const { container } = renderMeta();
    expect(chips(container)).toEqual([]);
    expect(screen.getByText('Singer')).toBeTruthy();
  });
});

describe('queue row', () => {
  it('carries one chip, the first genre', () => {
    const t = { ...TRACK, genres: [{ slug: 'rock_alt_metal', score: 50 }, { slug: 'hiphop_rap', score: 100 }] };
    const { container } = render(<ul><QueueRow track={t} kind="rec" /></ul>);
    expect(chips(container)).toEqual([['hiphop_rap', 'Hip-hop / Rap', 'true']]);
  });

  it('has no chip when untagged', () => {
    const { container } = render(<ul><QueueRow track={{ ...TRACK, genres: [] }} kind="rec" /></ul>);
    expect(chips(container)).toEqual([]);
  });
});

describe('search row', () => {
  it('a library hit carries one chip, the first genre; a Spotify hit none', () => {
    const t = { ...TRACK, genres: [{ slug: 'folk', score: 100 }, { slug: 'classical', score: 100 }] };
    const lib = render(<TrackRow track={t} />);
    expect(chips(lib.container)).toEqual([['folk', 'Folk', 'true']]);
    lib.unmount();
    const sp = render(<TrackRow track={{ title: 'X', artist: 'Y', spotify_id: 'sp' }} variant="spotify" />);
    expect(chips(sp.container)).toEqual([]);
  });
});
