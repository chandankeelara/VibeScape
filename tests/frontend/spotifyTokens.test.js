// frontend-next/src/lib/spotifyTokens.js — keeping a long session signed in.
import { createSpotifyTokens, REFRESH_MARGIN_MS } from '../../frontend-next/src/lib/spotifyTokens.js';

const KEYS = { token: 't', refresh: 'r', expiry: 'e', source: 's' };
const NOW = 1_800_000_000_000;

function setup({ token = 'old', refresh = 'rt-1', expiresIn = 60_000, source = 'server' } = {}) {
  const storage = new Map();
  const st = {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
  };
  if (token) st.setItem(KEYS.token, token);
  if (refresh) st.setItem(KEYS.refresh, refresh);
  st.setItem(KEYS.expiry, String(NOW + expiresIn));
  if (source) st.setItem(KEYS.source, source);

  const persist = vi.fn((at, rt, exp, src) => {
    st.setItem(KEYS.token, at); st.setItem(KEYS.refresh, rt);
    st.setItem(KEYS.expiry, String(NOW + exp * 1000)); st.setItem(KEYS.source, src);
  });
  const api = {
    spotifyRefresh: vi.fn(async () => ({ access_token: 'new', expires_in: 3600, refresh_token: 'rt-2' })),
    spotifyConfig: vi.fn(async () => ({ client_id: 'cid-from-config' })),
  };
  const fetchImpl = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ access_token: 'pkce-new', expires_in: 3600 }) }));
  const endSession = vi.fn();
  const adopt = vi.fn();
  const tokens = createSpotifyTokens({
    keys: KEYS, persist, adopt, endSession, getClientId: () => 'cid', api,
    storage: st, fetchImpl, now: () => NOW,
  });
  return { tokens, st, persist, api, fetchImpl, endSession, adopt };
}

it('returns the stored token while it has more than the margin left', async () => {
  const s = setup({ expiresIn: REFRESH_MARGIN_MS + 1000 });
  expect(await s.tokens.getValidToken()).toBe('old');
  expect(s.api.spotifyRefresh).not.toHaveBeenCalled();
});

it('refreshes a server token near expiry and keeps a rotated refresh token', async () => {
  const s = setup();
  expect(await s.tokens.getValidToken()).toBe('new');
  expect(s.api.spotifyRefresh).toHaveBeenCalledWith('rt-1');
  expect(s.persist).toHaveBeenCalledWith('new', 'rt-2', 3600, 'server');
});

it('keeps the old refresh token when Spotify does not rotate', async () => {
  const s = setup();
  s.api.spotifyRefresh.mockResolvedValueOnce({ access_token: 'new', expires_in: 3600 });
  await s.tokens.getValidToken();
  expect(s.persist).toHaveBeenCalledWith('new', 'rt-1', 3600, 'server');
});

it('refreshes a PKCE token directly with Spotify, never via our secret', async () => {
  const s = setup({ source: 'pkce' });
  expect(await s.tokens.getValidToken()).toBe('pkce-new');
  expect(s.api.spotifyRefresh).not.toHaveBeenCalled();
  const [url, init] = s.fetchImpl.mock.calls[0];
  expect(url).toBe('https://accounts.spotify.com/api/token');
  const body = new URLSearchParams(init.body);
  expect(body.get('grant_type')).toBe('refresh_token');
  expect(body.get('client_id')).toBe('cid');
  expect(body.has('client_secret')).toBe(false);
});

it('a token of unknown origin tries the server, then PKCE', async () => {
  const s = setup({ source: '' });
  s.api.spotifyRefresh.mockRejectedValueOnce(Object.assign(new Error('500'), { status: 500 }));
  expect(await s.tokens.getValidToken()).toBe('pkce-new');
  expect(s.persist.mock.calls[0][3]).toBe('pkce');
});

it('concurrent callers share one refresh', async () => {
  const s = setup();
  const [a, b, c] = await Promise.all([s.tokens.getValidToken(), s.tokens.getValidToken(), s.tokens.getValidToken()]);
  expect([a, b, c]).toEqual(['new', 'new', 'new']);
  expect(s.api.spotifyRefresh).toHaveBeenCalledTimes(1);
});

it('force refreshes even a fresh token (after a 401)', async () => {
  const s = setup({ expiresIn: 3_600_000 });
  expect(await s.tokens.getValidToken({ force: true })).toBe('new');
});

it('a refused refresh ends the session', async () => {
  const s = setup();
  s.api.spotifyRefresh.mockRejectedValueOnce(Object.assign(new Error('400'), { status: 400 }));
  expect(await s.tokens.getValidToken()).toBeNull();
  expect(s.endSession).toHaveBeenCalledOnce();
});

it('a network failure keeps the session and the still-valid token', async () => {
  const s = setup({ expiresIn: 60_000 });
  s.api.spotifyRefresh.mockRejectedValueOnce(new TypeError('Failed to fetch'));
  expect(await s.tokens.getValidToken()).toBe('old');
  expect(s.endSession).not.toHaveBeenCalled();
});

it('a network failure on an expired token returns null without signing out', async () => {
  const s = setup({ expiresIn: -1000 });
  s.api.spotifyRefresh.mockRejectedValueOnce(new TypeError('Failed to fetch'));
  expect(await s.tokens.getValidToken()).toBeNull();
  expect(s.endSession).not.toHaveBeenCalled();
});

it('adopts the token another tab rotated instead of signing out', async () => {
  const s = setup();
  s.api.spotifyRefresh.mockImplementationOnce(async () => {
    // the other tab won the race and stored its fresh pair
    s.st.setItem(KEYS.token, 'from-other-tab');
    s.st.setItem(KEYS.refresh, 'rt-other');
    s.st.setItem(KEYS.expiry, String(NOW + 3_600_000));
    throw Object.assign(new Error('400'), { status: 400 });
  });
  expect(await s.tokens.getValidToken()).toBe('from-other-tab');
  expect(s.adopt).toHaveBeenCalledWith('from-other-tab', NOW + 3_600_000);
  expect(s.endSession).not.toHaveBeenCalled();
});

it('no tokens at all is null, not an error', async () => {
  const s = setup({ token: '', refresh: '' });
  expect(await s.tokens.getValidToken()).toBeNull();
});
