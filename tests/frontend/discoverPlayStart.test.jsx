// @vitest-environment jsdom
/**
 * A track the DJ serves carries the Discovery mode on its play_start
 * (`data: { discover }`), so real listening can be split by pool later.
 *
 * Runs the real chain: useDj reads the switch from localStorage,
 * QueueStateProvider registers the DJ picker with `{ discover }`,
 * PlayerContext.next() loads the pick with that data, media/player.js hands
 * it to listenLog.startPlay. Faked as in djStalePick.test.jsx: the network,
 * the telemetry transport (its queue is what this asserts on) and the
 * YouTube / Spotify / Web Audio / verify modules.
 */
import { act, render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const sent = [];
const djBodies = [];

vi.mock('../../frontend-next/src/lib/events', () => ({
  enqueue: (ev) => { sent.push(ev); },
  flush: () => {},
}));
vi.mock('../../frontend-next/src/lib/api', () => ({
  randomTrack: async () => track(999, 'random'),
  similarTracks: async () => ({ tracks: [] }),
  similarTracksDj: async (_key, body) => {
    djBodies.push(body);
    return { tracks: [{ ...track(3, 'dj pick'), in_library: false, discover: !!body.discover }, track(4, 'next')] };
  },
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

let ctx;
async function mount({ discover }) {
  localStorage.clear();
  localStorage.setItem('vibescape.djEnabled', '1');
  localStorage.setItem('vibescape.djDiscover', discover ? '1' : '0');
  window.HTMLMediaElement.prototype.play = function play() { return Promise.resolve(); };
  window.HTMLMediaElement.prototype.pause = function pause() {};
  window.HTMLMediaElement.prototype.load = function load() {};
  const { ToastProvider } = await import('../../frontend-next/src/state/ToastContext');
  const { PlayerProvider, usePlayer } = await import('../../frontend-next/src/state/PlayerContext');
  const { QueueStateProvider } = await import('../../frontend-next/src/features/queue/QueueState');
  function Probe() { ctx = usePlayer(); return null; }
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <PlayerProvider>
          <QueueStateProvider><Probe /></QueueStateProvider>
        </PlayerProvider>
      </ToastProvider>
    </QueryClientProvider>,
  );
  await act(async () => { ctx.loadTrack(A, { source: 'search' }); });
  return view;
}

const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const starts = (from) => sent.slice(from).filter((e) => e.type === 'play_start');

beforeEach(() => { sent.length = 0; djBodies.length = 0; });

for (const discover of [true, false]) {
  it(`a DJ-served play_start says discover: ${discover}`, async () => {
    const view = await mount({ discover });
    // The search pick that seeded the session is not DJ-served: no data.
    expect(starts(0)[0]).not.toHaveProperty('data');
    const mark = sent.length;

    await act(async () => { ctx.next(); });
    await settle();

    expect(ctx.current.id).toBe(3);
    const [start] = starts(mark);
    expect(start).toMatchObject({ type: 'play_start', spotify_id: 'sp3', source: 'dj' });
    expect(start.data).toEqual({ discover });
    // ...and the pick itself came from the pool the switch names.
    expect(djBodies.at(-1).discover).toBe(discover ? true : undefined);
    view.unmount();
  });
}
