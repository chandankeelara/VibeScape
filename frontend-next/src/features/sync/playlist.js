/**
 * Spotify playlist-link parsing.
 *
 * Both regexes are copied verbatim from frontend/app.js:389-390 — do not
 * "simplify" them. The URL form tolerates:
 *   https://open.spotify.com/playlist/<22>
 *   https://open.spotify.com/intl-de/playlist/<22>?si=...   (locale segment)
 *   spotify:playlist:<22>                                    (URI scheme)
 * The leading `(?:^|\/|:)` is what makes the `intl-*` segment and the URI
 * colon both work; the trailing group is what lets a query string follow.
 */

export const SPOTIFY_PLAYLIST_URL_RE =
  /(?:^|\/|:)playlist(?::|\/)([A-Za-z0-9]{22})(?:$|[/?#])/;

export const SPOTIFY_BARE_ID_RE = /^[A-Za-z0-9]{22}$/;

/** Extract a playlist ID from a URL, URI, or bare ID. Returns null if invalid. */
export function parsePlaylistId(raw) {
  if (!raw) return null;
  const s = String(raw).trim();
  if (!s) return null;
  if (SPOTIFY_BARE_ID_RE.test(s)) return s;
  const m = s.match(SPOTIFY_PLAYLIST_URL_RE);
  return m ? m[1] : null;
}

/** True when the raw string is a real link/URI rather than a bare ID. */
export const isPlaylistLink = (raw) => /^https?:|^spotify:/.test(String(raw || ''));
