// @vitest-environment jsdom
/**
 * frontend-next/src/state/PlayerContext.jsx — a DJ pick that comes back after
 * the user already chose something else is dropped, not played.
 *
 * next() with an empty queue in DJ mode awaits POST /similar and used to load
 * whatever came back unconditionally. Only fetchForVibe had a stale guard, so
 * a search pick, a queue jump or prev made while /similar was in flight was
 * replaced a moment later, and telemetry logged a skip of the song the user
 * had just chosen plus a play_start for one they never asked for.
 *
 * The real PlayerProvider, the real media facade (media/player.js) and the
 * real listening log run here. Faked: the network (lib/api and the telemetry
 * transport lib/events, whose queue is what this asserts on), the YouTube /
 * Spotify / Web Audio / verify modules, and the <audio> element's play/load,
 * which jsdom does not implement.
 */
import { act, render } from '@testing-library/react';

// Plain recorders, not vi.fn: the config's restoreMocks wipes vi.fn
// implementations between tests.
const sent = [];
const randomCalls = [];

vi.mock('../../frontend-next/src/lib/events', () => ({
  enqueue: (ev) => { sent.push(ev); },
  flush: () => {},
}));
vi.mock('../../frontend-next/src/lib/api', () => ({
  randomTrack: async (q) => { randomCalls.push(q); return track(999, 'random'); },
}));
vi.mock('../../frontend-next/src/media/glow', () => ({ start() {}, stop() {}, setAlpha() {} }));
vi.mock('../../frontend-next/src/media/youtube', () => ({
  setHandlers() {}, init() {}, stop() {}, play() {}, pause() {}, seekTo() {},
  cueOrPlay() {}, getCurrentTime: () => 0, getDuration: () => 0,
}));
vi.mock('../../frontend-next/src/media/spotify', () => ({
  setOnState() {}, setOnReady() {}, setOnEnded() {}, setOnError() {}, init() {},
  isActive: () => false, pause() {}, resume() {}, seek() {},
  getDuration: () => 0, getPosition: () => 0,
}));
vi.mock('../../frontend-next/src/media/verify', () => ({
  init() {}, subscribe: () => () => {}, stop() {}, start: async () => true,
}));

function track(id, title) {
  return { id, title, spotify_id: `sp${id}`, preview_url: `https://cdn.test/${id}.mp3` };
}
const A = track(1, 'playing before next');
const PICKED = track(2, 'picked by the user');
const DJ = track(3, 'late DJ pick');

function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

let ctx;
let player;
async function mount() {
  window.HTMLMediaElement.prototype.play = function play() { return Promise.resolve(); };
  window.HTMLMediaElement.prototype.pause = function pause() {};
  window.HTMLMediaElement.prototype.load = function load() {};
  player = await import('../../frontend-next/src/media/player');
  const { ToastProvider } = await import('../../frontend-next/src/state/ToastContext');
  const { PlayerProvider, usePlayer } = await import('../../frontend-next/src/state/PlayerContext');
  function Probe() { ctx = usePlayer(); return null; }
  render(<ToastProvider><PlayerProvider><Probe /></PlayerProvider></ToastProvider>);
  await act(async () => { ctx.loadTrack(A, { source: 'search' }); });
}

const plays = (from) => sent.slice(from).filter((e) => e.type === 'play_start' || e.type === 'play_end');
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

beforeEach(() => { sent.length = 0; randomCalls.length = 0; });

it('a DJ pick resolving after the user picked a track is dropped', async () => {
  await mount();
  const dj = deferred();
  act(() => ctx.setNextFallback(() => dj.promise));

  act(() => ctx.next());
  expect(ctx.loadingTrack).toBe(true);

  // The user picks a search result while /similar is still in flight.
  await act(async () => { ctx.loadTrack(PICKED, { source: 'search' }); });
  const overlayAfterPick = ctx.loadingTrack;
  const mark = sent.length;

  await act(async () => { dj.resolve(DJ); });
  await settle();

  expect(ctx.current.id).toBe(PICKED.id);
  expect(player.getState().track.id).toBe(PICKED.id);
  // No skip of the picked track, no play_start for the DJ one.
  expect(plays(mark)).toEqual([]);
  // ...and no fallback to a random vibe pull either.
  expect(randomCalls).toEqual([]);
  // The pick itself ends the "picking the next track" overlay; it must not
  // hang on until the dropped request comes back.
  expect(overlayAfterPick).toBe(false);
  expect(ctx.loadingTrack).toBe(false);
});

it('an empty DJ result after a pick does not fall back to a random pull', async () => {
  await mount();
  const dj = deferred();
  act(() => ctx.setNextFallback(() => dj.promise));
  act(() => ctx.next());
  await act(async () => { ctx.loadTrack(PICKED, { source: 'search' }); });
  const mark = sent.length;

  await act(async () => { dj.resolve(null); });
  await settle();

  expect(randomCalls).toEqual([]);
  expect(ctx.current.id).toBe(PICKED.id);
  expect(plays(mark)).toEqual([]);
});

it('of two quick nexts, only the second DJ pick plays, whatever order they land in', async () => {
  await mount();
  const first = deferred();
  const second = deferred();
  const pending = [first, second];
  act(() => ctx.setNextFallback(() => pending.shift().promise));

  act(() => ctx.next());
  act(() => ctx.next());
  await act(async () => { second.resolve(track(5, 'second')); });
  const mark = sent.length;
  await act(async () => { first.resolve(track(4, 'first')); });
  await settle();

  expect(ctx.current.id).toBe(5);
  expect(plays(mark)).toEqual([]);
  expect(ctx.loadingTrack).toBe(false);
});

it('a DJ pick nobody overtook still plays and clears the overlay', async () => {
  await mount();
  const mark = sent.length;
  act(() => ctx.setNextFallback(async () => DJ));
  act(() => ctx.next());
  await settle();

  expect(ctx.current.id).toBe(DJ.id);
  expect(ctx.loadingTrack).toBe(false);
  expect(plays(mark).map((e) => [e.type, e.spotify_id ?? e.track_id, e.source ?? e.end_reason ?? null]))
    .toEqual([
      ['play_end', A.spotify_id, expect.anything()],
      ['play_start', DJ.spotify_id, 'dj'],
    ]);
});
