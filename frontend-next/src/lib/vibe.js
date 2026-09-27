/**
 * Vibe / mood math. Ported verbatim from frontend/app.js:243-425 — these
 * values drive the accent gradient and must stay identical to the legacy app
 * so the two look the same during the migration.
 */

export const MOODS = [
  { name: 'sleep',  min: 0,   max: 20,  a: [76, 91, 138],  b: [91, 127, 189] },
  { name: 'chill',  min: 20,  max: 40,  a: [0, 180, 216],  b: [34, 193, 227] },
  { name: 'steady', min: 40,  max: 60,  a: [124, 58, 237], b: [167, 139, 250] },
  { name: 'hype',   min: 60,  max: 80,  a: [236, 72, 153], b: [244, 63, 94] },
  { name: 'beast',  min: 80,  max: 100, a: [249, 115, 22], b: [220, 38, 38] },
];

export const MOOD_NAMES = MOODS.map((m) => m.name);

const lerp = (a, b, t) => a + (b - a) * t;
const rgb = ([r, g, b]) => `rgb(${Math.round(r)}, ${Math.round(g)}, ${Math.round(b)})`;

export function moodFor(vibe) {
  for (const m of MOODS) if (vibe >= m.min && vibe < m.max) return m;
  return MOODS[MOODS.length - 1];
}

export function accentFor(vibe) {
  const m = moodFor(vibe);
  const span = m.max - m.min;
  const t = span > 0 ? Math.min(1, Math.max(0, (vibe - m.min) / span)) : 0;
  return {
    a: rgb(m.a.map((c, i) => lerp(c, m.b[i], t))),
    b: rgb(m.b),
    mood: m.name,
  };
}

/**
 * Writes the accent CSS variables. Direct DOM write by design — these drive
 * gradients across the whole page and change on every slider tick, so routing
 * them through React state would re-render the tree during a drag.
 */
export function applyAccent(vibe) {
  const { a, b } = accentFor(vibe);
  const root = document.documentElement;
  root.style.setProperty('--vibe-accent', a);
  root.style.setProperty('--vibe-accent-2', b);
}

export function fmtTime(sec) {
  if (!Number.isFinite(sec) || sec < 0) sec = 0;
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s < 10 ? '0' : ''}${s}`;
}

/** Track has no local audio — playable only through the Spotify SDK. */
export const isMetadataOnly = (t) => t?.classification_source === 'metadata_only';

/**
 * Stable identity for a track — safe both as a React key and as the
 * `{track_key}` path segment on the API.
 *
 * MUST stay spotify_id-first. The backend's `_resolve_anchor`
 * (backend/app.py:924) accepts a spotify_id or the internal numeric
 * `tracks.id` ONLY — its docstring notes the apple_id lookup path was
 * retired because every row now has a spotify_id. An apple_id-keyed request
 * to /similar or /features 404s.
 */
export const trackKey = (t) =>
  String(t?.spotify_id || (t?.id != null ? t.id : '') || t?.apple_id || '');

/**
 * Predicted vibe (0-100 int) for a track, or null when there isn't one.
 * Ported from frontend/app.js `trackVibe()`.
 *
 * Prefers the ML prediction (stored 0-1), falls back to the formula-based
 * vibe_score (already 0-100). Metadata-only tracks carry a placeholder
 * vibe_score of 50 purely to satisfy a NOT NULL constraint — that is not a
 * real prediction, so it is suppressed rather than shown as a misleading
 * "vibe 50" chip.
 */
export function trackVibe(t) {
  if (!t || isMetadataOnly(t)) return null;
  const clamp = (n) => Math.round(Math.max(0, Math.min(100, n)));

  const ml = t.vibe_score_ml;
  if (ml != null && Number.isFinite(Number(ml))) return clamp(Number(ml) * 100);

  const raw = t.vibe_score;
  if (raw != null && Number.isFinite(Number(raw))) return clamp(Number(raw));

  return null;
}

/**
 * The key to send to the API — path segments and id payloads alike.
 *
 * Prefers the internal numeric `tracks.id`. The backend's `_resolve_anchor`
 * (app.py:924) and `_resolve_ids_to_track_ids` (app.py:1168) both short-circuit
 * on ints and skip SQL entirely; a spotify_id string costs a batched lookup.
 * On a 50-entry DJ exclude list that difference used to be ~750ms.
 *
 * Falls back to spotify_id for tracks that aren't in the library yet — Spotify
 * catalog search results have no internal id until they're ingested.
 *
 * NOTE: internal ids are environment-specific. Never persist them without
 * stamping the origin they came from (see queue/dj.js).
 */
export const apiKey = (t) =>
  t?.id != null ? Number(t.id) : String(t?.spotify_id || '');

/** Human labels for `classification_source` (legacy app.js:1351). */
export const CLASSIFICATION_LABELS = {
  spotify_preview: 'Spotify preview',
  itunes_isrc: 'iTunes ISRC lookup',
  itunes_term_search: 'iTunes term search',
  deezer_isrc: 'Deezer ISRC lookup',
  deezer_search: 'Deezer search',
  ml_mert: 'MERT (remote GPU)',
  metadata_only: 'Metadata only',
  none: 'No classification audio',
};

export const classificationLabel = (src) =>
  !src ? 'Unknown' : CLASSIFICATION_LABELS[src] || src;

/**
 * Can we play back the exact clip this track was classified from?
 * Needs a preview_url and a real classification source.
 */
export const canVerify = (t) =>
  !!t && t.classification_source !== 'none' && !!t.preview_url;
