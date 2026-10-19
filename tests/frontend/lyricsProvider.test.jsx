// @vitest-environment jsdom
/**
 * frontend-next/src/features/lyrics/LyricsContext.jsx — the provider against
 * a faked player: fetching only when on, the active line following the clock
 * and the offset, tap-to-sync and the ± steps writing the offset under the
 * playback source's key, and the YouTube duration gate.
 */
import { act, render, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const LINES = [
  { t_ms: 1000, text: 'one' },
  { t_ms: 4000, text: 'two' },
  { t_ms: 9000, text: 'three' },
];
const RESPONSE = {
  status: 'synced', source: 'lrclib', lines: LINES, plain: 'one\ntwo\nthree',
  matched_duration_ms: 200000, track_duration_ms: 200000, credit: 'Lyrics from LRCLIB',
};

const fetched = [];
async function trackLyrics(key) { fetched.push(key); return RESPONSE; }
vi.mock('../../frontend-next/src/lib/api', () => ({ trackLyrics: (...a) => trackLyrics(...a) }));

// The player context, as the provider sees it. `tick` is the clock.
let playerState;
let tick = () => {};
const seeks = [];
vi.mock('../../frontend-next/src/state/PlayerContext', () => ({
  usePlayer: () => playerState,
  usePlaybackClock: (fn) => { tick = fn; },
}));

const TRACK = { id: 42, spotify_id: 'sp42', youtube_id: 'yt42', title: 'T', artist: 'A' };

let lyrics;
let activeSeen;
async function mount() {
  const { LyricsProvider, useLyrics, useActiveLine } = await import('../../frontend-next/src/features/lyrics/LyricsContext');
  function Probe() { lyrics = useLyrics(); activeSeen = useActiveLine(); return null; }
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const utils = render(
    <QueryClientProvider client={client}><LyricsProvider><Probe /></LyricsProvider></QueryClientProvider>,
  );
  return utils;
}

const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const clock = (positionS, durationS) => act(() => { tick({ position: positionS, duration: durationS }); });

beforeEach(() => {
  fetched.length = 0;
  seeks.length = 0;
  localStorage.clear();
  playerState = {
    current: TRACK, mode: 'audio', source: 'spotify', videoState: 'idle', playing: false,
    seek: (f) => seeks.push(f),
  };
});
afterEach(cleanup);

it('fetches nothing while off, and the track once it is turned on', async () => {
  await mount();
  await flush();
  expect(lyrics.on).toBe(false);
  expect(fetched).toEqual([]);
  act(() => lyrics.toggle());
  await flush();
  await flush();
  expect(fetched).toEqual([42]);
  expect(lyrics.view.kind).toBe('synced');
  expect(localStorage.getItem('vs.lyrics.on')).toBe('1');
});

it('the active line follows the clock and the offset; taps and steps are stored per source', async () => {
  localStorage.setItem('vs.lyrics.on', '1');
  await mount();
  await flush();
  clock(4.5, 200);
  expect(activeSeen).toBe(1);

  // The singer reaches line "two" at 5.0s: tap.
  clock(5.0, 200);
  act(() => lyrics.tapSync());
  expect(lyrics.offset).toBe(1000);
  expect(localStorage.getItem('vs.lyrics.offset.spotify:sp42')).toBe('1000');
  // 4.5s is now before "two" (5000).
  clock(4.5, 200);
  expect(activeSeen).toBe(0);

  act(() => lyrics.nudge(500));
  act(() => lyrics.nudge(-100));
  expect(lyrics.offset).toBe(1400);
  expect(localStorage.getItem('vs.lyrics.offset.spotify:sp42')).toBe('1400');

  // Clicking "three" seeks to 9000 + 1400 of 200000.
  act(() => lyrics.seekToLine(2));
  expect(seeks[0]).toBeCloseTo(10400 / 200000);

  act(() => lyrics.reset());
  expect(lyrics.offset).toBe(0);
  expect(localStorage.getItem('vs.lyrics.offset.spotify:sp42')).toBeNull();
});

it('the YouTube path stays plain until the video length matches', async () => {
  localStorage.setItem('vs.lyrics.on', '1');
  playerState = { ...playerState, mode: 'video', source: null, videoState: 'ready' };
  await mount();
  await flush();
  expect(lyrics.view.kind).toBe('mismatch');
  clock(10, 230); // 30s longer than the lyrics' recording: an intro.
  expect(lyrics.view.kind).toBe('mismatch');
  expect(lyrics.canManualSync).toBe(true);
  act(() => lyrics.startManualSync());
  expect(lyrics.view.kind).toBe('synced');
  // A sync made here belongs to the video, not the Spotify track.
  clock(10, 230);
  act(() => lyrics.nudge(-500));
  expect(localStorage.getItem('vs.lyrics.offset.yt:yt42')).toBe('-500');
  expect(localStorage.getItem('vs.lyrics.offset.spotify:sp42')).toBeNull();
});

it('the YouTube path is synced straight away when the length matches', async () => {
  localStorage.setItem('vs.lyrics.on', '1');
  playerState = { ...playerState, mode: 'video', source: null, videoState: 'ready' };
  await mount();
  await flush();
  clock(10, 201);
  expect(lyrics.view.kind).toBe('synced');
});

it('a preview plays plain lyrics with no seeking and no offset', async () => {
  localStorage.setItem('vs.lyrics.on', '1');
  playerState = { ...playerState, source: 'preview' };
  await mount();
  await flush();
  expect(lyrics.view.kind).toBe('preview');
  expect(lyrics.canSeek).toBe(false);
  act(() => lyrics.nudge(500));
  expect(lyrics.offset).toBe(0);
});
