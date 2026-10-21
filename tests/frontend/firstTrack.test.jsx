// @vitest-environment jsdom
/**
 * The first track of a page load (useFirstTrack -> QueueState.startFirst).
 *
 * DJ mode on with a recent stored session resumes it: the DJ picks from the
 * last played track and the stored log, in the Discovery pool the switch
 * names. Anything else — DJ off, an empty or stale log, a DJ call that fails
 * or comes back empty — is the vibe-band pull. Rendered under StrictMode,
 * like main.jsx, so a doubled mount effect would show up as a second request.
 *
 * Faked as in discoverPlayStart.test.jsx: the network, the telemetry
 * transport and the YouTube / Spotify / Web Audio / verify modules.
 */
import { StrictMode } from 'react';
import { act, render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const sent = [];
const calls = { random: 0, dj: [] };
let djReply;

vi.mock('../../frontend-next/src/lib/events', () => ({
  enqueue: (ev) => { sent.push(ev); },
  flush: () => {},
}));
vi.mock('../../frontend-next/src/lib/api', () => ({
  randomTrack: async () => { calls.random += 1; return track(999, 'random'); },
  similarTracks: async () => ({ tracks: [] }),
  similarTracksDj: async (key, body) => {
    calls.dj.push({ key, body });
    return djReply();
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

const HOUR = 3_600_000;

/** A log whose last PLAYED track is 11; the trailing `queued` is not a play. */
function log(agoMs) {
  const ts = Date.now() - agoMs;
  return [
    { id: 10, action: 'completed', played_ratio: 1, ts: ts - 60_000 },
    { id: 11, action: 'skipped', played_ratio: 0.1, ts },
    { id: 12, action: 'queued', played_ratio: null, ts },
  ];
}

let ctx;
let queryClient;
async function mount({ dj, discover = false, events = [] }) {
  localStorage.clear();
  localStorage.setItem('vibescape.djEnabled', dj ? '1' : '0');
  localStorage.setItem('vibescape.djDiscover', discover ? '1' : '0');
  localStorage.setItem('vibescape.sessionEvents', JSON.stringify({
    stamp: `4:${window.location.origin}`, events,
  }));
  window.HTMLMediaElement.prototype.play = function play() { return Promise.resolve(); };
  window.HTMLMediaElement.prototype.pause = function pause() {};
  window.HTMLMediaElement.prototype.load = function load() {};
  const { ToastProvider } = await import('../../frontend-next/src/state/ToastContext');
  const { PlayerProvider, usePlayer } = await import('../../frontend-next/src/state/PlayerContext');
  const { QueueStateProvider } = await import('../../frontend-next/src/features/queue/QueueState');
  const { default: useFirstTrack } = await import('../../frontend-next/src/features/player/useFirstTrack');
  function Probe() { useFirstTrack(); ctx = usePlayer(); return null; }
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  let view;
  await act(async () => {
    view = render(
      <StrictMode>
        <QueryClientProvider client={queryClient}>
          <ToastProvider>
            <PlayerProvider>
              <QueueStateProvider><Probe /></QueueStateProvider>
            </PlayerProvider>
          </ToastProvider>
        </QueryClientProvider>
      </StrictMode>,
    );
  });
  await settle();
  return view;
}

const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const starts = () => sent.filter((e) => e.type === 'play_start');

beforeEach(() => {
  sent.length = 0;
  calls.random = 0;
  calls.dj.length = 0;
  djReply = async () => ({ tracks: [track(3, 'dj pick'), track(4, 'runner-up')] });
});

it('DJ off: the vibe-band pull, once, even with a fresh log', async () => {
  const view = await mount({ dj: false, events: log(60_000) });
  expect(calls.dj).toHaveLength(0);
  expect(calls.random).toBe(1);
  expect(ctx.current.id).toBe(999);
  expect(starts()).toHaveLength(1);
  view.unmount();
});

for (const discover of [true, false]) {
  it(`DJ on, fresh log: one DJ pick from the last play (discover ${discover})`, async () => {
    const events = log(60_000);
    const view = await mount({ dj: true, discover, events });

    expect(calls.random).toBe(0);
    expect(calls.dj).toHaveLength(1);
    const [{ key, body }] = calls.dj;
    expect(key).toBe(11);
    expect(body.events).toEqual(events);
    expect(body.discover).toBe(discover ? true : undefined);
    // The restored session's recent plays are excluded on this very first
    // fetch, before useDj's own mount effect has re-seeded the seen-set.
    expect(body.exclude_ids).toEqual(expect.arrayContaining([10, 11, 12]));

    expect(ctx.current.id).toBe(3);
    expect(ctx.startInfo.kind).toBe('resume');
    const s = starts();
    expect(s).toHaveLength(1);
    expect(s[0]).toMatchObject({ source: 'dj', spotify_id: 'sp3', data: { discover } });

    // The sidebar gets the remainder without a second POST.
    await settle();
    expect(calls.dj).toHaveLength(1);
    view.unmount();
  });
}

it('DJ on, a log whose last play is older than DJ_RESUME_HOURS: the vibe-band pull', async () => {
  const { DJ_RESUME_HOURS } = await import('../../frontend-next/src/features/queue/dj');
  const view = await mount({ dj: true, events: log((DJ_RESUME_HOURS + 1) * HOUR) });
  expect(calls.random).toBe(1);
  expect(ctx.current.id).toBe(999);
  expect(ctx.startInfo.kind).not.toBe('resume');
  // No DJ call for the first track. Once it plays, the sidebar's recs do
  // POST, seeded by the random track: that is the DJ taking over as before.
  expect(calls.dj.every((c) => c.key === 999)).toBe(true);
  view.unmount();
});

it('DJ on, empty log: the vibe-band pull', async () => {
  const view = await mount({ dj: true, events: [] });
  expect(calls.random).toBe(1);
  expect(calls.dj.every((c) => c.key === 999)).toBe(true);
  view.unmount();
});

for (const [name, reply] of [
  ['throws', async () => { throw Object.assign(new Error('boom'), { status: 500 }); }],
  ['comes back empty', async () => ({ tracks: [] })],
]) {
  it(`DJ call ${name}: falls back to the vibe-band pull, silently`, async () => {
    djReply = reply;
    const view = await mount({ dj: true, events: log(60_000) });
    expect(calls.dj[0].key).toBe(11);
    expect(calls.random).toBe(1);
    expect(ctx.current.id).toBe(999);
    expect(ctx.loadingTrack).toBe(false);
    expect(starts()).toHaveLength(1);
    view.unmount();
  });
}
