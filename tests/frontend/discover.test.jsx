// @vitest-environment jsdom
/**
 * Discovery mode, client side (frontend-next/src/features/queue).
 *
 *   - the switch persists in localStorage, like the DJ toggle;
 *   - off sends exactly what the app sent before discovery existed, on sends
 *     `discover: true` (POST) / `discover=1` (GET);
 *   - the recs query key carries the flag, so flipping it refetches instead of
 *     serving the other pool's cached list;
 *   - a row shows "Discover" only for `discover: true`, and stops once the
 *     track is hearted;
 *   - a discovered track (`in_library: false`) gets an empty heart that adds
 *     it with a PUT and asks /contains nothing.
 */
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const similarCalls = [];
const djCalls = [];
const libCalls = [];
const containsAsked = [];
vi.mock('../../frontend-next/src/lib/api', () => ({
  similarTracks: async (key, params) => { similarCalls.push([key, params]); return { tracks: [] }; },
  similarTracksDj: async (key, body) => { djCalls.push([key, body]); return { tracks: [] }; },
  libraryAdd: async (k) => { libCalls.push(['add', k]); return null; },
  libraryRemove: async (k) => { libCalls.push(['remove', k]); return null; },
  libraryContains: async (keys) => { containsAsked.push(keys); return {}; },
}));

// useRecs only needs these three from the player.
const SEED = { id: 5, spotify_id: 'sp5', title: 'Seed' };
vi.mock('../../frontend-next/src/state/PlayerContext', () => ({
  usePlayer: () => ({ current: SEED, queue: [], getSeenIds: () => [] }),
}));

let dj;
let useRecs;
let recsQueryKey;
let QueueRow;
let HeartButton;
let ToastProvider;
let resetLibraryStore;
beforeAll(async () => {
  dj = await import('../../frontend-next/src/features/queue/dj');
  ({ useRecs, recsQueryKey } = await import('../../frontend-next/src/features/queue/useRecs'));
  ({ default: QueueRow } = await import('../../frontend-next/src/features/queue/QueueRow'));
  ({ HeartButton, resetLibraryStore } = await import('../../frontend-next/src/features/library'));
  ({ ToastProvider } = await import('../../frontend-next/src/state/ToastContext'));
});
beforeEach(() => {
  similarCalls.length = 0;
  djCalls.length = 0;
  libCalls.length = 0;
  containsAsked.length = 0;
  localStorage.clear();
  resetLibraryStore();
});
afterEach(cleanup);

describe('the persisted switch', () => {
  it('defaults to off and round-trips through localStorage', () => {
    expect(dj.loadDiscover()).toBe(false);
    dj.persistDiscover(true);
    expect(localStorage.getItem(dj.DJ_DISCOVER_KEY)).toBe('1');
    expect(dj.loadDiscover()).toBe(true);
    dj.persistDiscover(false);
    expect(dj.loadDiscover()).toBe(false);
  });

  it('is its own key, not the DJ toggle', () => {
    expect(dj.DJ_DISCOVER_KEY).toBe('vibescape.djDiscover');
    dj.persistEnabled(true);
    expect(dj.loadDiscover()).toBe(false);
  });
});

describe('what goes on the wire', () => {
  it('off: the DJ body and the GET params are what they were before discovery', async () => {
    await dj.fetchDjPicks(SEED, { events: [], limit: 8 });
    await dj.fetchSimilar(SEED, { limit: 8 });
    expect(Object.keys(djCalls[0][1]).sort()).toEqual(['events', 'exclude_ids', 'limit', 'mode']);
    expect(similarCalls[0][1]).toEqual({ limit: 8 });
  });

  it('on: POST carries discover:true and GET carries discover=1', async () => {
    await dj.fetchDjPicks(SEED, { events: [], limit: 8, discover: true });
    await dj.fetchSimilar(SEED, { limit: 8, discover: true });
    expect(djCalls[0][1].discover).toBe(true);
    expect(similarCalls[0][1]).toEqual({ limit: 8, discover: 1 });
  });
});

describe('the recs query key', () => {
  it('differs by the flag, on both paths', () => {
    for (const djEnabled of [false, true]) {
      const base = { seedKey: 5, djEnabled, signature: '3:99' };
      expect(recsQueryKey({ ...base, discover: true }))
        .not.toEqual(recsQueryKey({ ...base, discover: false }));
    }
  });

  it('flipping the switch refetches instead of serving the library list from cache', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
    const { rerender } = renderHook((props) => useRecs(props), {
      wrapper,
      initialProps: { djEnabled: false, discover: false, events: [], signature: '0:0' },
    });
    await waitFor(() => expect(similarCalls).toHaveLength(1));
    rerender({ djEnabled: false, discover: true, events: [], signature: '0:0' });
    await waitFor(() => expect(similarCalls).toHaveLength(2));
    expect(similarCalls[1][1]).toEqual({ limit: 8, discover: 1 });
  });

  it('the DJ path refetches too, and asks the catalogue', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
    const { rerender } = renderHook((props) => useRecs(props), {
      wrapper,
      initialProps: { djEnabled: true, discover: false, events: [], signature: '0:0' },
    });
    await waitFor(() => expect(djCalls).toHaveLength(1));
    expect(djCalls[0][1]).not.toHaveProperty('discover');
    rerender({ djEnabled: true, discover: true, events: [], signature: '0:0' });
    await waitFor(() => expect(djCalls).toHaveLength(2));
    expect(djCalls[1][1].discover).toBe(true);
  });
});

const FOUND = { id: 41, spotify_id: 'sp41', title: 'Found', artist: 'Elsewhere', in_library: false, discover: true };
const OWNED = { id: 42, spotify_id: 'sp42', title: 'Owned', artist: 'Here', in_library: true, discover: false };

const row = (track, kind = 'rec') => render(
  <ToastProvider>
    <ul><QueueRow track={track} kind={kind} onActivate={() => {}} onAction={() => {}} /></ul>
  </ToastProvider>,
);

describe('the Discover badge', () => {
  it('shows on a discovered rec and a discovered queue row', () => {
    row(FOUND, 'rec');
    row(FOUND, 'queue');
    expect(screen.getAllByText('Discover')).toHaveLength(2);
  });

  it('does not show for a library track, or a payload without the field', () => {
    row(OWNED);
    row({ id: 43, title: 'Plain' });
    expect(screen.queryByText('Discover')).toBeNull();
  });

  it('never asks /contains for the rows it renders', async () => {
    row({ id: 43, title: 'Plain' });
    row(FOUND);
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(containsAsked).toEqual([]);
  });
});

describe('a discovered track and the heart', () => {
  it('starts empty, adds with a PUT, and the row drops its badge', async () => {
    render(
      <ToastProvider>
        <HeartButton track={FOUND} />
        <ul><QueueRow track={{ ...FOUND }} kind="rec" onActivate={() => {}} onAction={() => {}} /></ul>
      </ToastProvider>,
    );
    const heart = screen.getByRole('button', { name: /library$/ });
    expect(heart.getAttribute('aria-pressed')).toBe('false');
    expect(screen.getByText('Discover')).toBeTruthy();

    fireEvent.click(heart);
    await act(async () => {});
    expect(heart.getAttribute('aria-pressed')).toBe('true');
    expect(libCalls).toEqual([['add', 41]]);
    // A payload that says `in_library: false` is an answer, not a question.
    expect(containsAsked).toEqual([]);
    expect(screen.queryByText('Discover')).toBeNull();
  });
});
