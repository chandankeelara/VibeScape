// frontend-next/src/media/spotify.js — a new song is where a long session refreshes.
const MODULE = '../../frontend-next/src/media/spotify.js';

let player;
async function connected(provider) {
  vi.resetModules();
  const sp = await import(MODULE);
  window.Spotify = {
    Player: class {
      constructor(opts) { this.opts = opts; this.handlers = {}; player = this; }
      addListener(name, fn) { this.handlers[name] = fn; }
      connect() {}
      disconnect() {}
    },
  };
  globalThis.fetch = vi.fn(async () => ({ ok: true, status: 204 }));
  sp.init();
  window.onSpotifyWebPlaybackSDKReady();
  sp.setTokenProvider(provider);
  sp.setToken('cached');
  sp.setPremium(true);
  player.handlers.ready({ device_id: 'dev-1' });
  // 'ready' fires transferPlayback, which awaits the provider before its own
  // fetch — let it land before the test starts counting requests.
  await new Promise((r) => setTimeout(r, 0));
  fetch.mockClear();
  provider.mockClear?.();
  return sp;
}
const authOf = (call) => call[1].headers.Authorization;

it('every new song asks the provider for a token first', async () => {
  const provider = vi.fn(async () => 'fresh');
  const sp = await connected(provider);
  expect(await sp.playTrack('abc')).toBe(true);
  expect(provider).toHaveBeenCalled();
  expect(authOf(fetch.mock.calls[0])).toBe('Bearer fresh');
});

it('a 401 forces one refresh and retries once', async () => {
  const provider = vi.fn(async ({ force } = {}) => (force ? 'forced' : 'fresh'));
  const sp = await connected(provider);
  fetch.mockResolvedValueOnce({ ok: false, status: 401 }).mockResolvedValueOnce({ ok: true, status: 204 });
  expect(await sp.playTrack('abc')).toBe(true);
  expect(provider).toHaveBeenCalledWith({ force: true });
  expect(fetch.mock.calls.map(authOf)).toEqual(['Bearer fresh', 'Bearer forced']);
});

it('no usable token means no play request (falls back to preview)', async () => {
  const sp = await connected(vi.fn(async () => null));
  expect(await sp.playTrack('abc')).toBe(false);
  expect(fetch).not.toHaveBeenCalled();
});

it("the SDK's own token callback gets a fresh token too", async () => {
  await connected(vi.fn(async () => 'fresh-for-sdk'));
  const token = await new Promise((resolve) => player.opts.getOAuthToken(resolve));
  expect(token).toBe('fresh-for-sdk');
});

it('reports the playing track duration from the SDK', async () => {
  const sp = await connected(vi.fn(async () => 'x'));
  player.handlers.player_state_changed({ paused: true, position: 0, duration: 211000 });
  expect(sp.getDuration()).toBe(211);
});
