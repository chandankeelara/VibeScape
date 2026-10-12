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

/* ------------------------------------------------------- any service */

/**
 * Services POST /api/ingest/playlist-url can read (backend/playlist_import.py).
 * Deezer is recognised only so the user gets "not supported yet" rather than
 * "invalid link" — the backend refuses it with deezer_not_supported.
 * Amazon is read through its web player's own (undocumented) API — see
 * backend/playlist_import.py.
 */
export const PLAYLIST_SERVICES = {
  spotify: 'Spotify',
  apple: 'Apple Music',
  youtube: 'YouTube',
  deezer: 'Deezer',
  amazon: 'Amazon Music',
};
// Deezer is recognised, not read (backend: deezer_not_supported).
export const SUPPORTED_SERVICES = new Set(['spotify', 'apple', 'youtube', 'amazon']);

/**
 * Which service a pasted link belongs to: {kind, id} or null. Mirrors
 * playlist_import.detect() closely enough to validate as the user types; the
 * backend re-checks and is the authority.
 */
export function detectPlaylistLink(raw) {
  const s = String(raw || '').trim();
  if (!s) return null;
  const spotify = parsePlaylistId(s);
  if (spotify && (SPOTIFY_BARE_ID_RE.test(s) || /spotify/i.test(s))) return { kind: 'spotify', id: spotify };
  let u;
  try {
    u = new URL(/^https?:\/\//i.test(s) ? s : `https://${s}`);
  } catch {
    return null;
  }
  const host = u.hostname.toLowerCase();
  const parts = u.pathname.split('/').filter(Boolean);
  if (host.endsWith('music.apple.com')) {
    const id = parts.find((p) => p.startsWith('pl.'));
    return id ? { kind: 'apple', id } : null;
  }
  if (/(^|\.)youtube\.com$|^youtu\.be$/.test(host)) {
    const id = u.searchParams.get('list');
    return id && /^[A-Za-z0-9_-]{10,64}$/.test(id) ? { kind: 'youtube', id } : null;
  }
  if (host.endsWith('deezer.com')) {
    const i = parts.indexOf('playlist');
    return i >= 0 && /^\d+$/.test(parts[i + 1] || '') ? { kind: 'deezer', id: parts[i + 1] } : null;
  }
  if (host.endsWith('deezer.page.link') || host === 'link.deezer.com') return { kind: 'deezer', id: '' };
  if (host.startsWith('music.amazon.') || (host.includes('amazon.') && host.includes('music'))) {
    const at = parts.length - 2;
    return at >= 0 && ['playlists', 'user-playlists'].includes(parts[at])
      ? { kind: 'amazon', id: parts[at + 1] }
      : null;
  }
  return null;
}
