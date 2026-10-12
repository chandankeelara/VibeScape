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

/* ------------------------------------------------------------------------
 * The S2 accent: OKLCH, contrast-solved. Ported from
 * design/premium-player/s2-familiar.html (solveAccent).
 *
 * Hue walks cool -> warm across the five bands (sleep blue, chill cyan,
 * steady violet, hype pink, beast orange). The TEXT and FILL tones are then
 * stepped in lightness until they clear 4.6:1 on every surface they can land
 * on, in the given theme — so accent text is AA at every vibe 0-100. Pinned by
 * tests/frontend/accent.test.js.
 * ---------------------------------------------------------------------- */

// [vibe, hue°, chroma]
const ANCH = [[0, 258, 0.12], [25, 212, 0.12], [50, 292, 0.16], [75, 352, 0.17], [100, 400, 0.17]];

function hueChroma(v) {
  for (let i = 0; i < ANCH.length - 1; i++) {
    const [v0, h0, c0] = ANCH[i];
    const [v1, h1, c1] = ANCH[i + 1];
    if (v <= v1) {
      const t = (v - v0) / (v1 - v0);
      return [h0 + (h1 - h0) * t, c0 + (c1 - c0) * t];
    }
  }
  return [ANCH[4][1], ANCH[4][2]];
}

/** OKLCH -> sRGB 0-255 triple, gamut-clipped. */
export function oklch(L, C, h) {
  const a = C * Math.cos((h * Math.PI) / 180);
  const b = C * Math.sin((h * Math.PI) / 180);
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const lin = [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ];
  return lin.map((x) => {
    const c = Math.min(1, Math.max(0, x));
    return Math.round(255 * (c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055));
  });
}

const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
function lum([r, g, b]) {
  const f = (c) => { const x = c / 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}
export function contrast(a, b) {
  const x = lum(a);
  const y = lum(b);
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}
const over = (fg, bg, alpha) => fg.map((c, i) => Math.round(c * alpha + bg[i] * (1 - alpha)));
const rgbStr = (c) => `rgb(${c[0]} ${c[1]} ${c[2]})`;

/** Must match tokens.css: [--panel, --raised, --hover, --bg] per theme. */
export const ACCENT_SURFACES = {
  dark: ['#121316', '#1b1c20', '#23252a', '#08090b'],
  light: ['#ffffff', '#f4f5f7', '#e9ebef', '#eceef2'],
};
export const ACCENT_SOFT_ALPHA = 0.16;
export const ACCENT_TARGET = 4.6;

/**
 * Pure: the accent family for a vibe in a theme. Returned as 0-255 triples so
 * tests can check contrast without parsing CSS.
 */
export function solveAccent(v, theme = 'dark') {
  const [h, C] = hueChroma(Math.max(0, Math.min(100, Number(v) || 0)));
  const dark = theme !== 'light';
  const surf = ACCENT_SURFACES[dark ? 'dark' : 'light'].map(hex);
  const mark = oklch(dark ? 0.72 : 0.6, C, h);
  // Accent text also sits on the soft VIBE badge over panel and raised.
  const textOn = [...surf, ...surf.slice(0, 2).map((s) => over(mark, s, ACCENT_SOFT_ALPHA))];
  let L = dark ? 0.76 : 0.54;
  let text = oklch(L, C, h);
  for (let i = 0; i < 40; i++) {
    text = oklch(L, C, h);
    if (Math.min(...textOn.map((s) => contrast(text, s))) >= ACCENT_TARGET) break;
    L += dark ? 0.01 : -0.01;
  }
  const ink = dark ? hex('#0b0c0e') : [255, 255, 255];
  let F = dark ? 0.78 : 0.56;
  let fill = oklch(F, C, h);
  for (let i = 0; i < 40; i++) {
    fill = oklch(F, C, h);
    if (contrast(fill, ink) >= ACCENT_TARGET) break;
    F += dark ? 0.01 : -0.01;
  }
  return { mark, text, fill, ink, textOn };
}

/** The current theme, as set on <html> by ThemeContext. */
const currentTheme = () =>
  (typeof document !== 'undefined' && document.documentElement?.dataset?.theme === 'light' ? 'light' : 'dark');

/**
 * Writes the accent CSS variables. Direct DOM write by design — these drive
 * colour across the whole page and change on every slider tick, so routing
 * them through React state would re-render the tree during a drag.
 *
 * Also writes the pre-S2 --vibe-accent / --vibe-accent-2 names, because the
 * PiP miniplayer copies those by name into its own document.
 */
export function applyAccent(vibe, theme = currentTheme()) {
  const a = solveAccent(vibe, theme);
  const root = document.documentElement;
  root.style.setProperty('--acc', rgbStr(a.mark));
  root.style.setProperty('--acc-text', rgbStr(a.text));
  root.style.setProperty('--acc-fill', rgbStr(a.fill));
  root.style.setProperty('--acc-ink', rgbStr(a.ink));
  root.style.setProperty('--acc-soft', `rgb(${a.mark.join(' ')} / ${ACCENT_SOFT_ALPHA})`);
  root.style.setProperty('--vibe-accent', rgbStr(a.mark));
  root.style.setProperty('--vibe-accent-2', rgbStr(a.fill));
}

/**
 * Accent colours for a TRACK's own vibe score, as inline CSS custom
 * properties. A "VIBE 61" badge must be coloured by 61, not by wherever the
 * user's slider happens to be — otherwise one track shows in two colours
 * (the badge follows the slider, the queue dot follows the score).
 * Same contrast guarantee as the global accent. Null when there is no score.
 */
export function trackAccentStyle(v, theme = currentTheme()) {
  if (v == null) return undefined;
  const a = solveAccent(v, theme);
  return {
    '--t-acc': rgbStr(a.mark),
    '--t-acc-text': rgbStr(a.text),
    '--t-acc-soft': `rgb(${a.mark.join(' ')} / ${ACCENT_SOFT_ALPHA})`,
  };
}

/**
 * The static now-playing tint for a track, from a hue. Fixed LOW lightness in
 * dark / HIGH in light, so text on the brightest point still clears AA.
 */
export function tintFor(hue, theme = currentTheme()) {
  const dark = theme !== 'light';
  return rgbStr(oklch(dark ? 0.27 : 0.935, dark ? 0.07 : 0.04, hue));
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
