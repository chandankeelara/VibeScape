/**
 * One gate for every Spotify Web API call made from the browser — the
 * client-side twin of ingest/spotify_guard.py.
 *
 * Spotify rate-limits per APP, across every token, so a 429 the browser sees
 * (playback, device transfer, /v1/me) is the same block the server sees.
 * The first one records "blocked until T" in localStorage; until T,
 * spotifyFetch() answers with that same 429 locally and makes no request.
 * Callers already treat a 429 as "this didn't work" — playTrack() falls back
 * to the 30 s preview — so nothing else changes.
 */

const KEY = 'vibescape.spotifyBlockedUntil';
const now = () => Date.now();

function readUntil() {
  try {
    return Number(localStorage.getItem(KEY)) || 0;
  } catch {
    return 0;
  }
}

/** Seconds left on the current block, 0 when calls may go out. */
export function blockedFor() {
  return Math.max(0, (readUntil() - now()) / 1000);
}

function retryAfterS(res) {
  const v = Number(res.headers?.get?.('Retry-After'));
  return Number.isFinite(v) && v >= 0 ? v : 5;
}

/** Record a real 429. Never shortens a block already recorded. */
export function note(res) {
  if (!res || res.status !== 429) return;
  const until = now() + retryAfterS(res) * 1000;
  if (until <= readUntil()) return;
  try { localStorage.setItem(KEY, String(until)); } catch { /* storage off: in-call only */ }
}

function local429(secs) {
  return new Response(JSON.stringify({ error: { status: 429, message: 'rate_limited_locally' } }), {
    status: 429,
    headers: { 'Content-Type': 'application/json', 'Retry-After': String(Math.ceil(secs)) },
  });
}

/** fetch() for api.spotify.com, gated by the current block. */
export async function spotifyFetch(url, init) {
  const left = blockedFor();
  if (left > 0) return local429(left);
  const res = await fetch(url, init);
  note(res);
  return res;
}
