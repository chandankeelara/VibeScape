/**
 * Spotify access-token refresh, as plain logic with every side effect
 * injected. SpotifyAuthContext owns the React state and builds one of these
 * per user namespace; tests/frontend/spotifyTokens.test.js drives it directly.
 *
 * Kept out of the component on purpose (2026-10-10): this is the code that
 * decides whether a long session stays signed in, and it must be testable
 * without rendering anything.
 */

/**
 * Refresh when less than this is left. Covers clock skew and a request that
 * takes a while to reach Spotify, so a token is never sent seconds from death.
 */
export const REFRESH_MARGIN_MS = 5 * 60 * 1000;

const SPOTIFY_TOKEN_URL = 'https://accounts.spotify.com/api/token';

/**
 * @param {object}   o
 * @param {object}   o.keys        localStorage keys: token, refresh, expiry, source
 * @param {Function} o.persist     (accessToken, refreshToken, expiresIn, source) => void
 * @param {Function} o.adopt       (token, expiresAt) => void — another tab's token, already stored
 * @param {Function} o.endSession  () => void — clear tokens and tell the user
 * @param {Function} o.getClientId () => string — '' while config is still loading
 * @param {object}   o.api         { spotifyRefresh(rt), spotifyConfig() }
 * @param {Storage}  [o.storage]   defaults to localStorage
 * @param {Function} [o.fetchImpl] defaults to fetch
 * @param {Function} [o.now]       defaults to Date.now
 */
export function createSpotifyTokens({
  keys, persist, adopt, endSession, getClientId, api,
  storage = globalThis.localStorage, fetchImpl = (...a) => globalThis.fetch(...a), now = () => Date.now(),
}) {
  let inflight = null;

  const read = () => {
    try {
      return {
        tok: storage.getItem(keys.token) || '',
        exp: parseInt(storage.getItem(keys.expiry) || '0', 10),
        rt: storage.getItem(keys.refresh) || '',
        src: storage.getItem(keys.source) || '',
      };
    } catch {
      return { tok: '', exp: 0, rt: '', src: '' };
    }
  };

  /**
   * Trade the refresh token for a new access token.
   *
   * Single-flight: Spotify may rotate the refresh token on every use, so two
   * concurrent refreshes would race and the loser would present a dead one.
   *
   * Server-minted tokens need the client secret, so they go through
   * /api/spotify/refresh. PKCE tokens refresh straight against Spotify with
   * only the client id — that is what PKCE is for, and the secret stays on
   * the server either way. Tokens stored before the source was recorded try
   * the server first, then PKCE.
   *
   * Rejects with `rejected: true` only when Spotify refused the refresh token
   * itself (revoked, invalid) — the one case that should end the session.
   */
  function refreshNow() {
    if (inflight) return inflight;
    const run = (async () => {
      const { rt, src } = read();
      if (!rt) throw Object.assign(new Error('no refresh token'), { rejected: true });

      const viaServer = async () => {
        try {
          return await api.spotifyRefresh(rt);
        } catch (e) {
          // 400 = Spotify said no (backend maps it to spotify_refresh_failed).
          throw Object.assign(e, { rejected: e?.status === 400 });
        }
      };
      const viaPkce = async () => {
        let clientId = getClientId();
        if (!clientId) clientId = (await api.spotifyConfig())?.client_id || '';
        if (!clientId) throw new Error('spotify not configured');
        const r = await fetchImpl(SPOTIFY_TOKEN_URL, {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: new URLSearchParams({
            grant_type: 'refresh_token', refresh_token: rt, client_id: clientId,
          }).toString(),
        });
        if (!r.ok) {
          throw Object.assign(new Error(`spotify refresh ${r.status}`), {
            rejected: r.status === 400 || r.status === 401,
          });
        }
        return r.json();
      };

      let j, used = src;
      if (src === 'pkce') j = await viaPkce();
      else if (src === 'server') j = await viaServer();
      else {
        try { j = await viaServer(); used = 'server'; }
        catch { j = await viaPkce(); used = 'pkce'; }
      }
      if (!j?.access_token) throw new Error('empty refresh response');
      // Spotify omits refresh_token when it does not rotate — keep ours.
      persist(j.access_token, j.refresh_token || rt, j.expires_in, used);
      return j.access_token;
    })();
    inflight = run;
    const done = () => { inflight = null; };
    run.then(done, done);
    return run;
  }

  /**
   * The one way to get a Spotify token. Every caller — a new song, the SDK's
   * own token callback, search — goes through here, so a token is refreshed
   * exactly when it is about to be used and never silently goes stale.
   *
   * Returns the stored token while it has more than REFRESH_MARGIN_MS left,
   * otherwise refreshes first. `force` refreshes regardless (after a 401).
   * Resolves to null when there is no usable token.
   *
   * Reads storage rather than state, so it is never a stale closure and a
   * refresh made by another tab is picked up for free.
   */
  async function getValidToken({ force = false } = {}) {
    const before = read();
    if (!before.tok && !before.rt) return null;
    if (!force && before.tok && before.exp - now() > REFRESH_MARGIN_MS) return before.tok;
    try {
      return await refreshNow();
    } catch (e) {
      const after = read();
      // Another tab refreshed first and Spotify rotated the refresh token:
      // ours was spent, theirs is good. Adopt it instead of signing out.
      if (after.rt && after.rt !== before.rt && after.tok && after.exp > now()) {
        adopt(after.tok, after.exp);
        return after.tok;
      }
      if (e?.rejected) {
        endSession();
        return null;
      }
      // Network or server trouble: keep the session and use what is left.
      return before.tok && before.exp > now() ? before.tok : null;
    }
  }

  return { refreshNow, getValidToken };
}
