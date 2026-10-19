/**
 * Lyrics, the pure part: which line is active, what the offset means, which
 * playback path may trust the timings, and which state the panel shows.
 *
 * No React and no media imports, so all of it is unit tested
 * (tests/frontend/lyrics.test.js).
 *
 * THE OFFSET. A line is shown at `t_ms + offset`. A positive offset delays
 * the lyrics ("+0.5s" means they come half a second later); negative brings
 * them forward. Every function here uses that one convention.
 */

/** ± limit of the sync bar. LRCLIB timings that are further out are wrong, not late. */
export const OFFSET_MAX_MS = 30000;
export const STEP_MS = 500;
export const FINE_STEP_MS = 100;

/** YouTube timings are trusted only if the video's length is this close to the lyrics'. */
export const YT_DURATION_TOLERANCE_MS = 2000;

const clampOffset = (ms) => Math.max(-OFFSET_MAX_MS, Math.min(OFFSET_MAX_MS, Math.round(ms)));

/**
 * Index of the line playing at `positionMs`, or -1 before the first one.
 * `lines` is sorted by t_ms (LRC order). Binary search: this runs on every
 * playhead tick.
 */
export function activeLineIndex(lines, positionMs, offsetMs = 0) {
  if (!lines || !lines.length || !Number.isFinite(positionMs)) return -1;
  const at = positionMs - offsetMs;
  let lo = 0;
  let hi = lines.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (lines[mid].t_ms <= at) { found = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return found;
}

/**
 * "Tap to sync": the offset that makes the highlighted line start NOW.
 * Before the first line nothing is highlighted, so the tap is taken to mean
 * the first line.
 */
export function tapSyncOffset(lines, activeIndex, positionMs) {
  if (!lines || !lines.length || !Number.isFinite(positionMs)) return 0;
  const line = lines[Math.max(0, Math.min(activeIndex, lines.length - 1))];
  return clampOffset(positionMs - line.t_ms);
}

export const stepOffset = (offsetMs, deltaMs) => clampOffset((offsetMs || 0) + deltaMs);

/** "+1.5s", "−0.5s" (a real minus sign), "0.0s". */
export function formatOffset(ms) {
  const v = Math.round((ms || 0) / 100) / 10;
  if (v === 0) return '0.0s';
  return `${v > 0 ? '+' : '−'}${Math.abs(v).toFixed(1)}s`;
}

/** Spoken form for the live region: "plus 1.5 seconds". */
export function speakOffset(ms) {
  const v = Math.round((ms || 0) / 100) / 10;
  if (v === 0) return 'Lyrics offset reset to zero';
  return `Lyrics offset ${v > 0 ? 'plus' : 'minus'} ${Math.abs(v).toFixed(1)} seconds`;
}

/**
 * Fraction of the track to seek to so that line `t_ms` starts, or null when
 * the duration is not known yet.
 */
export function seekFractionForLine(tMs, offsetMs, durationMs) {
  if (!(durationMs > 0)) return null;
  return Math.max(0, Math.min(1, (tMs + (offsetMs || 0)) / durationMs));
}

/**
 * Is the YouTube video the same length as the recording the lyrics were timed
 * against? A music video with an intro is not, and its timings would all be
 * early. null = cannot tell yet (the player has not reported a duration, or
 * the backend sent none).
 */
export function youtubeDurationMatches(videoDurationMs, matchedDurationMs, toleranceMs = YT_DURATION_TOLERANCE_MS) {
  if (!(videoDurationMs > 0) || !(matchedDurationMs > 0)) return null;
  return Math.abs(videoDurationMs - matchedDurationMs) <= toleranceMs;
}

/**
 * Which of the three playback paths is live: 'youtube' | 'spotify' |
 * 'preview' | null. Mirrors the branches of media/player.js loadTrack.
 */
export function playbackPath(mode, source) {
  if (mode === 'video') return 'youtube';
  if (source === 'spotify') return 'spotify';
  if (source === 'preview') return 'preview';
  return null;
}

/**
 * The storage key for a recording's offset: `spotify:<id>` or `yt:<id>`.
 * A preview has no sync, so it has no key.
 */
export function offsetKey(path, track, youtubeId) {
  if (path === 'spotify' && track?.spotify_id) return `spotify:${track.spotify_id}`;
  if (path === 'youtube' && youtubeId) return `yt:${youtubeId}`;
  return null;
}

/** Plain text as display lines; falls back to the synced lines' text. */
export function plainLines(data) {
  const text = typeof data?.plain === 'string' ? data.plain.trim() : '';
  if (text) return text.split(/\r?\n/);
  return (data?.lines || []).map((l) => l.text || '');
}

/**
 * The panel's state. One of:
 *   loading · synced · mismatch (YouTube, plain + "Sync manually") · plain ·
 *   preview (plain + note) · instrumental · none · error
 *
 * `error` is a failed request or the backend's `error: "upstream"` (the key
 * is absent unless LRCLIB is down). A 404 is an answer ("nothing for this
 * track"), not a failure. 'plain' also covers timings the backend rejected
 * (duration > 3s off), so manual sync is offered for 'synced' only.
 */
export function deriveView({ data, error, pending, path, ytMatch, manualSync }) {
  if (error) return error.status === 404 ? { kind: 'none' } : { kind: 'error' };
  if (pending || !data) return { kind: 'loading' };
  if (data.error === 'upstream') return { kind: 'error' };

  if (data.status === 'instrumental') return { kind: 'instrumental' };
  if (data.status === 'synced' && data.lines?.length) {
    if (path === 'spotify') return { kind: 'synced', lines: data.lines };
    if (path === 'youtube') {
      if (ytMatch === true || manualSync) return { kind: 'synced', lines: data.lines };
      return { kind: 'mismatch', plain: plainLines(data) };
    }
    if (path === 'preview') return { kind: 'preview', plain: plainLines(data) };
    return { kind: 'plain', plain: plainLines(data) };
  }
  if (data.status === 'plain' || data.status === 'synced') {
    const plain = plainLines(data);
    if (plain.some((l) => l.trim())) return { kind: 'plain', plain };
  }
  return { kind: 'none' };
}
