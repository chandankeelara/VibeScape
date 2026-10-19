/**
 * Per-device lyrics preferences in localStorage: whether the panel is on, and
 * the sync offset for each recording.
 *
 * Every call is wrapped: storage can be blocked (private mode, a sandboxed
 * frame) or full, and the lyrics must work without it — just unremembered.
 *
 * Offsets are keyed by PLAYBACK SOURCE (`spotify:<id>`, `yt:<id>`, see
 * offsetKey in lyricsMath.js), not by track: the same song as a music video
 * is a different recording with a different intro, and needs its own offset.
 */

const ON_KEY = 'vs.lyrics.on';
const OFFSET_PREFIX = 'vs.lyrics.offset.';

export function readLyricsOn() {
  try {
    return localStorage.getItem(ON_KEY) === '1';
  } catch {
    return false;
  }
}

export function writeLyricsOn(on) {
  try {
    localStorage.setItem(ON_KEY, on ? '1' : '0');
  } catch { /* not remembered */ }
}

/** The stored offset in ms, or null when this recording was never synced. */
export function readOffset(key) {
  if (!key) return null;
  try {
    const raw = localStorage.getItem(OFFSET_PREFIX + key);
    if (raw == null) return null;
    const ms = Number(raw);
    return Number.isFinite(ms) ? ms : null;
  } catch {
    return null;
  }
}

/** Zero removes the entry, so a reset recording stops counting as synced. */
export function writeOffset(key, ms) {
  if (!key) return;
  try {
    if (!ms) localStorage.removeItem(OFFSET_PREFIX + key);
    else localStorage.setItem(OFFSET_PREFIX + key, String(Math.round(ms)));
  } catch { /* not remembered */ }
}
