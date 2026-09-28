/**
 * Row-level label helpers, ported from frontend/app.js:4193-4247.
 *
 * These live here rather than in lib/vibe.js because they are presentation
 * concerns of the track rows (pills and badges), not vibe math.
 */

/**
 * The 0-100 number shown in a row's "vibe" pill, or null when there isn't an
 * honest one to show.
 *
 * Metadata-only tracks carry a placeholder vibe_score of 50 purely to satisfy
 * a NOT NULL constraint — it is not a prediction, so the pill is suppressed
 * rather than showing every unanalyzed song as a confident "vibe 50".
 * Otherwise the ML score (0-1) wins over the legacy scalar score (0-100).
 */
export function trackVibe(t) {
  if (!t) return null;
  if (t.classification_source === 'metadata_only') return null;
  const ml = t.vibe_score_ml;
  if (ml != null && !Number.isNaN(Number(ml))) {
    return Math.round(Math.max(0, Math.min(100, Number(ml) * 100)));
  }
  const raw = t.vibe_score;
  if (raw != null && !Number.isNaN(Number(raw))) {
    return Math.round(Math.max(0, Math.min(100, Number(raw))));
  }
  return null;
}

/** ISO 639-1 codes Whisper returns. Unknown codes fall back to the uppercased code. */
const LANG_NAMES = {
  en: 'English', es: 'Spanish', fr: 'French', de: 'German', it: 'Italian',
  pt: 'Portuguese', ru: 'Russian', ja: 'Japanese', ko: 'Korean', zh: 'Chinese',
  ar: 'Arabic', hi: 'Hindi', bn: 'Bengali', pa: 'Punjabi', ta: 'Tamil',
  te: 'Telugu', ml: 'Malayalam', kn: 'Kannada', mr: 'Marathi', gu: 'Gujarati',
  ur: 'Urdu', tr: 'Turkish', vi: 'Vietnamese', th: 'Thai', id: 'Indonesian',
  ms: 'Malay', tl: 'Filipino', nl: 'Dutch', sv: 'Swedish', no: 'Norwegian',
  da: 'Danish', fi: 'Finnish', pl: 'Polish', cs: 'Czech', el: 'Greek',
  he: 'Hebrew', fa: 'Persian', uk: 'Ukrainian', ro: 'Romanian', hu: 'Hungarian',
};

export function languageLabel(code) {
  if (!code) return '';
  const key = String(code).toLowerCase().trim();
  return LANG_NAMES[key] || key.toUpperCase();
}

/** "Artist · Album", collapsing gracefully when either is missing. */
export function subtitleFor(t) {
  const artist = t?.artist || '';
  const album = t?.album || '';
  if (artist && album) return `${artist} · ${album}`;
  return artist || album;
}
