// frontend-next/src/lib/spotifyGuard.js — the browser twin of spotify_guard.py.
import { blockedFor, note, spotifyFetch } from '../../frontend-next/src/lib/spotifyGuard.js';

const res = (status, retryAfter) =>
  new Response('{}', { status, headers: retryAfter == null ? {} : { 'Retry-After': String(retryAfter) } });

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(1_800_000_000_000);
  globalThis.fetch = vi.fn(async () => res(200));
});
afterEach(() => vi.useRealTimers());

it('passes calls through while nothing is blocked', async () => {
  expect((await spotifyFetch('https://api.spotify.com/v1/me')).status).toBe(200);
  expect(fetch).toHaveBeenCalledTimes(1);
});

it('one 429 holds every later call, with no request', async () => {
  fetch.mockResolvedValueOnce(res(429, 70320));
  await spotifyFetch('https://api.spotify.com/v1/me/player/play');
  const held = await spotifyFetch('https://api.spotify.com/v1/me');
  expect(held.status).toBe(429);
  expect(Number(held.headers.get('Retry-After'))).toBe(70320);
  expect(fetch).toHaveBeenCalledTimes(1);
});

it('lifts when the time is up', async () => {
  note(res(429, 10));
  vi.setSystemTime(Date.now() + 11_000);
  expect(blockedFor()).toBe(0);
  await spotifyFetch('https://api.spotify.com/v1/me');
  expect(fetch).toHaveBeenCalledTimes(1);
});

it('survives a reload (kept in localStorage) and never shortens', () => {
  note(res(429, 1000));
  note(res(429, 5));
  expect(Math.round(blockedFor())).toBe(1000);
  expect(Number(localStorage.getItem('vibescape.spotifyBlockedUntil'))).toBeGreaterThan(Date.now());
});
