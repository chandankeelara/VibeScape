// @vitest-environment jsdom
/**
 * The heart (frontend-next/src/features/library): add / remove the playing
 * track from the user's library.
 *   - optimistic: the button flips before the request settles;
 *   - a failed request rolls it back and says so in a toast;
 *   - removing raises "Removed from your library" with an Undo that re-adds;
 *   - the key is apiKey(track): the id, else the spotify_id;
 *   - initial state comes from `in_library` on the payload, and only when a
 *     payload has none from the batch GET /api/me/library/contains;
 *   - a toggle on one copy of a track shows on every other copy.
 */
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

// Each call parks a promise the test settles by hand.
const calls = [];
function park(kind, key) {
  let settle;
  const p = new Promise((resolve, reject) => { settle = { resolve, reject }; });
  calls.push({ kind, key, ...settle });
  return p;
}
let containsAnswer = {};
const containsAsked = [];
vi.mock('../../frontend-next/src/lib/api', () => ({
  libraryAdd: (k) => park('add', k),
  libraryRemove: (k) => park('remove', k),
  libraryContains: (keys) => { containsAsked.push(keys); return Promise.resolve(containsAnswer); },
}));

let HeartButton;
let ToastProvider;
let resetLibraryStore;
beforeAll(async () => {
  ({ HeartButton, resetLibraryStore } = await import('../../frontend-next/src/features/library'));
  ({ ToastProvider } = await import('../../frontend-next/src/state/ToastContext'));
});
beforeEach(() => {
  resetLibraryStore();
  calls.length = 0;
  containsAsked.length = 0;
  containsAnswer = {};
});
afterEach(cleanup);

const mount = (track) => render(<ToastProvider><HeartButton track={track} /></ToastProvider>);
const heart = () => screen.getByRole('button', { name: /library$/ });
// Requests start a microtask after the click (each waits on its track's
// previous one), so let those run before looking at `calls`.
const click = async (el) => { fireEvent.click(el); await act(async () => {}); };
const settle = async (i, ok = true) => {
  await act(async () => {
    if (ok) calls[i].resolve(null); else calls[i].reject(new Error('HTTP 500'));
  });
};

describe('heart: add', () => {
  it('fills at once, before the PUT settles, and keys on the track id', async () => {
    mount({ id: 7, spotify_id: 'sp7', title: 'Runway', in_library: false });
    expect(heart().getAttribute('aria-pressed')).toBe('false');
    expect(heart().getAttribute('aria-label')).toBe('Add to library');
    await click(heart());
    expect(heart().getAttribute('aria-pressed')).toBe('true');
    expect(heart().getAttribute('aria-label')).toBe('Remove from library');
    expect(calls).toEqual([expect.objectContaining({ kind: 'add', key: 7 })]);
    await settle(0);
    expect(heart().getAttribute('aria-pressed')).toBe('true');
    expect(screen.queryByText(/Couldn’t/)).toBeNull();
  });

  it('falls back to the spotify_id when there is no internal id', async () => {
    mount({ spotify_id: 'abc123', title: 'Runway', in_library: false });
    await click(heart());
    expect(calls[0]).toEqual(expect.objectContaining({ kind: 'add', key: 'abc123' }));
  });

  it('rolls back and toasts the error when the PUT fails', async () => {
    mount({ id: 7, title: 'Runway', in_library: false });
    await click(heart());
    await settle(0, false);
    expect(heart().getAttribute('aria-pressed')).toBe('false');
    expect(screen.getByText('Couldn’t add to your library')).toBeTruthy();
  });
});

describe('heart: remove and undo', () => {
  it('empties at once and offers Undo, which re-adds', async () => {
    mount({ id: 9, title: 'Orchard', in_library: true });
    expect(heart().getAttribute('aria-pressed')).toBe('true');
    await click(heart());
    expect(heart().getAttribute('aria-pressed')).toBe('false');
    expect(calls[0]).toEqual(expect.objectContaining({ kind: 'remove', key: 9 }));
    expect(screen.getByText('Removed from your library')).toBeTruthy();
    await settle(0);

    await click(screen.getByRole('button', { name: 'Undo' }));
    expect(heart().getAttribute('aria-pressed')).toBe('true');
    expect(calls[1]).toEqual(expect.objectContaining({ kind: 'add', key: 9 }));
    expect(screen.queryByText('Removed from your library')).toBeNull();
    await settle(1);
    expect(heart().getAttribute('aria-pressed')).toBe('true');
  });

  it('puts the heart back and drops the Undo when the DELETE fails', async () => {
    mount({ id: 9, title: 'Orchard', in_library: true });
    await click(heart());
    await settle(0, false);
    expect(heart().getAttribute('aria-pressed')).toBe('true');
    expect(screen.queryByText('Removed from your library')).toBeNull();
    expect(screen.getByText('Couldn’t remove from your library')).toBeTruthy();
  });

  it('sends the toggles in the order they were made, and a superseded failure changes nothing', async () => {
    mount({ id: 9, title: 'Orchard', in_library: true });
    fireEvent.click(heart()); // remove
    fireEvent.click(heart()); // add again, before the DELETE settles
    await act(async () => {});
    expect(calls.map((c) => c.kind)).toEqual(['remove']); // the PUT waits its turn
    await settle(0, false);
    expect(calls.map((c) => c.kind)).toEqual(['remove', 'add']);
    expect(heart().getAttribute('aria-pressed')).toBe('true');
    expect(screen.queryByText(/Couldn’t/)).toBeNull();
    await settle(1);
    expect(heart().getAttribute('aria-pressed')).toBe('true');
  });
});

describe('heart: initial state', () => {
  it('reads in_library from the payload and never asks the batch endpoint', async () => {
    mount({ id: 3, title: 'Static Bloom', in_library: true });
    expect(heart().getAttribute('aria-pressed')).toBe('true');
    await act(async () => { await new Promise((r) => setTimeout(r, 5)); });
    expect(containsAsked).toEqual([]);
  });

  it('asks /contains when the payload has no in_library ({key: bool} answer)', async () => {
    containsAnswer = { 4: true };
    mount({ id: 4, title: 'Night Bus' });
    expect(heart().getAttribute('aria-pressed')).toBe('false');
    await waitFor(() => expect(heart().getAttribute('aria-pressed')).toBe('true'));
    expect(containsAsked).toEqual([[4]]);
  });

  it('accepts a bool array in the asked order', async () => {
    containsAnswer = [true];
    mount({ spotify_id: 'sp5', title: 'Night Bus' });
    await waitFor(() => expect(heart().getAttribute('aria-pressed')).toBe('true'));
    expect(containsAsked).toEqual([['sp5']]);
  });

  it('shows a toggle on every copy of the same track, stale payloads included', async () => {
    render(
      <ToastProvider>
        <HeartButton track={{ id: 11, title: 'Runway', in_library: false }} />
        <HeartButton track={{ id: 11, title: 'Runway', in_library: false }} />
      </ToastProvider>,
    );
    const [a, b] = screen.getAllByRole('button', { name: /library$/ });
    await click(a);
    expect(b.getAttribute('aria-pressed')).toBe('true');
    await settle(0);
  });
});
