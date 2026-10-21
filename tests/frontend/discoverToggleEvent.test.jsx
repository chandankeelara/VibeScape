// @vitest-environment jsdom
/**
 * The Discovery switch logs a `discover_toggle` app event, like DJ mode's
 * `dj_toggle` (features/queue/useDj.js). The backend accepts the type since
 * PR #30; before that the client deliberately sent nothing.
 */
import { act, renderHook } from '@testing-library/react';

const logged = [];
vi.mock('../../frontend-next/src/lib/listenLog', () => ({
  logEvent: (type, payload) => logged.push([type, payload]),
  logTrackEvent: vi.fn(),
  setContextProvider: vi.fn(),
}));
vi.mock('../../frontend-next/src/state/PlayerContext', () => ({
  usePlayer: () => ({ current: null, queue: [], getSeenIds: () => [] }),
  usePlaybackTime: () => 0,
}));
vi.mock('../../frontend-next/src/state/ToastContext', () => ({
  useToast: () => vi.fn(),
}));

let useDj;
beforeAll(async () => {
  ({ useDj } = await import('../../frontend-next/src/features/queue/useDj'));
});
beforeEach(() => {
  logged.length = 0;
  localStorage.clear();
});

test('flipping Discovery logs discover_toggle with the new state', () => {
  const { result } = renderHook(() => useDj());
  act(() => result.current.toggleDiscover());
  act(() => result.current.toggleDiscover());
  expect(logged.filter(([t]) => t === 'discover_toggle')).toEqual([
    ['discover_toggle', { data: { on: true } }],
    ['discover_toggle', { data: { on: false } }],
  ]);
});
