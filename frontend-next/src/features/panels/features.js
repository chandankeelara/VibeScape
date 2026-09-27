/**
 * Track-feature fetching + formatting shared by the metrics and debug panels.
 * Ports frontend/app.js:3757-3800 and 4000-4035.
 */

import { apiKey } from '../../lib/vibe';
import { useQuery } from '@tanstack/react-query';
import * as api from '../../lib/api';

/**
 * Key for /api/tracks/{key}/features.
 *
 * NOT lib/vibe.js `trackKey()` — that one is apple_id-first, but backend
 * `get_track_features` (backend/app.py:1945) resolves spotify_id OR the
 * numeric internal tracks.id and never apple_id, so an apple_id-keyed request
 * 404s. The legacy metrics panel had its own key function for exactly this
 * reason (frontend/app.js:3757, "the old apple_id-first order was a legacy
 * artifact from iTunes-era ingest").
 */
/**
 * Superseded by lib/vibe.js `apiKey` — re-exported so existing imports keep
 * working. It previously preferred spotify_id; internal ids are preferred now
 * because the backend skips resolution for them entirely.
 */
export const featureKey = (t) => (t ? apiKey(t) || null : null);

/**
 * Backend returns { features: {...}, axes: {...}, ...topLevel }. Flatten to a
 * single object so lookups don't have to walk the nesting. Top-level fields
 * win over features/axes on a collision.
 */
export function flattenFeaturesPayload(raw) {
  if (!raw || typeof raw !== 'object') return {};
  const flat = {};
  if (raw.features && typeof raw.features === 'object') Object.assign(flat, raw.features);
  if (raw.axes && typeof raw.axes === 'object') Object.assign(flat, raw.axes);
  for (const k of Object.keys(raw)) {
    if (k === 'features' || k === 'axes') continue;
    flat[k] = raw[k];
  }
  return flat;
}

/**
 * Features for a track. A 404 is a real answer ("not computed yet"), not a
 * failure — it resolves to null so callers render the empty state instead of
 * the error one. React Query's cache replaces the legacy state.featureCache.
 */
export function useTrackFeatures(track, { enabled = true } = {}) {
  const key = featureKey(track);
  return useQuery({
    queryKey: ['track-features', key],
    queryFn: async () => {
      try {
        return flattenFeaturesPayload(await api.trackFeatures(key));
      } catch (e) {
        if (e?.status === 404) return null;
        throw e;
      }
    },
    enabled: enabled && !!key,
    staleTime: Infinity,
  });
}

/* ------------------------------------------------------------- formatting */

/** Formatted value, or null when the value is absent / non-finite. */
export function fmtMetricValue(v, decimals = 2) {
  if (v === null || v === undefined) return null;
  if (typeof v === 'number') {
    if (!Number.isFinite(v)) return null;
    return v.toFixed(decimals);
  }
  return String(v);
}

/** Accept 0-1 or 0-100; normalize to 0-100 for a bar fill. */
export function pct01(v) {
  if (v === null || v === undefined || !Number.isFinite(v)) return null;
  return v > 1.5 ? Math.max(0, Math.min(100, v)) : Math.max(0, Math.min(100, v * 100));
}

/**
 * Map a raw value onto 0-100 with an explicit natural-scale ceiling — for
 * fields like bandwidth (~5000 Hz) that pct01's 0-1/0-100 auto-detect can't
 * read. `signed` maps abs(v)/max, for -1..+1 fields like valence_mode.
 */
export function pctMax(v, max, { signed = false } = {}) {
  if (v === null || v === undefined || !Number.isFinite(v)) return null;
  if (!Number.isFinite(max) || max <= 0) return null;
  const raw = signed ? Math.abs(v) : v;
  return Math.max(0, Math.min(100, (raw / max) * 100));
}

/** Merge features over the track dict — features endpoint wins. */
export function featureGetter(track, features) {
  const f = features || {};
  return (k) => {
    if (f[k] !== undefined && f[k] !== null) return f[k];
    if (track && track[k] !== undefined && track[k] !== null) return track[k];
    return null;
  };
}

export const CLASSIFICATION_LABELS = {
  spotify_preview: 'Spotify preview',
  itunes_isrc: 'iTunes ISRC lookup',
  itunes_term_search: 'iTunes term search',
  none: 'No classification audio',
};

export const classificationLabel = (src) =>
  !src ? 'Unknown' : CLASSIFICATION_LABELS[src] || src;
