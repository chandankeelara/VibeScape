// @vitest-environment jsdom
/**
 * frontend-next/src/features/player/useKeyboardShortcuts.js — the up / down
 * arrows fetch a track for the vibe they move TO.
 *
 * They used to call shiftVibe(±5) and then fetchForVibe(), whose default
 * argument is the render-time vibe, so every press fetched for the vibe it had
 * just left. The real PlayerProvider and the real shortcut hook are mounted;
 * only the network and the media layer are faked.
 */
import { act, render } from '@testing-library/react';

// A plain recorder, not vi.fn: the config's restoreMocks wipes vi.fn
// implementations between tests.
const fetched = [];
async function randomTrack(q) {
  fetched.push(q.vibe);
  return { id: 1000 + q.vibe, title: `t${q.vibe}`, spotify_id: `s${q.vibe}` };
}

vi.mock('../../frontend-next/src/lib/api', () => ({ randomTrack: (...a) => randomTrack(...a) }));
vi.mock('../../frontend-next/src/media/player', () => ({
  init: vi.fn(), subscribe: vi.fn(() => () => {}), setHooks: vi.fn(), stop: vi.fn(),
  loadTrack: vi.fn(), getState: vi.fn(() => ({ playing: false })),
  restoreAfterVerify: vi.fn(), cueVideo: vi.fn(), setMode: vi.fn(),
  subscribeTime: vi.fn(() => () => {}),
}));
vi.mock('../../frontend-next/src/media/verify', () => ({ subscribe: vi.fn(() => () => {}), stop: vi.fn() }));
vi.mock('../../frontend-next/src/lib/listenLog', () => ({
  setContextProvider: vi.fn(), logEvent: vi.fn(), logTrackEvent: vi.fn(),
}));

let ctx;
async function mount() {
  const { ToastProvider } = await import('../../frontend-next/src/state/ToastContext');
  const { PlayerProvider, usePlayer } = await import('../../frontend-next/src/state/PlayerContext');
  const { default: useKeyboardShortcuts } = await import('../../frontend-next/src/features/player/useKeyboardShortcuts');
  function Probe() { ctx = usePlayer(); useKeyboardShortcuts(); return null; }
  render(<ToastProvider><PlayerProvider><Probe /></PlayerProvider></ToastProvider>);
}

const press = (key) => act(async () => {
  window.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
});
const fetchedVibes = () => fetched;

beforeEach(() => { fetched.length = 0; });

it('ArrowUp at 50 fetches for 55, not 50', async () => {
  await mount();
  expect(ctx.vibe).toBe(50);
  await press('ArrowUp');
  expect(fetchedVibes()).toEqual([55]);
  expect(ctx.vibe).toBe(55);
});

it('ArrowDown at 50 fetches for 45', async () => {
  await mount();
  await press('ArrowDown');
  expect(fetchedVibes()).toEqual([45]);
});

it('ArrowUp at 98 fetches for the clamped 100', async () => {
  await mount();
  act(() => ctx.setVibe(98));
  await press('ArrowUp');
  expect(fetchedVibes()).toEqual([100]);
  expect(ctx.vibe).toBe(100);
});

it('two presses before a render compound', async () => {
  await mount();
  await act(async () => {
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp' }));
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp' }));
  });
  expect(fetchedVibes()).toEqual([55, 60]);
  expect(ctx.vibe).toBe(60);
});
