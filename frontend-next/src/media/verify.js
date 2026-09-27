/**
 * "Verify" playback — plays the exact 30s clip that was used to classify the
 * current track, so you can hear what the model actually heard.
 *
 * Ported from frontend/app.js:1338-1541.
 *
 * CRITICAL: this owns its OWN <audio> element, separate from the main player.
 * It must never share one, for two reasons:
 *   1. The main element is wired into the Web Audio graph via
 *      createMediaElementSource, which is callable once per element for the
 *      life of the page (see glow.js). Swapping its src to the verify clip
 *      and back would work, but any failure leaves the graph pointing at the
 *      wrong source with no way to rebuild it.
 *   2. Verify has to be able to restore main playback exactly — position,
 *      src and play state — which is only possible if it never touched it.
 *
 * Like the rest of src/media, this is a plain module with no React in it.
 */

export const VERIFY_MAX_MS = 30_000;
const TICK_MS = 250;

const v = {
  el: null,
  active: false,
  timerId: null,
  startedAt: 0,
};

const listeners = new Set();

function emit() {
  const remainingMs = v.active ? Math.max(0, VERIFY_MAX_MS - (performance.now() - v.startedAt)) : 0;
  const snapshot = { active: v.active, remainingMs };
  listeners.forEach((fn) => fn(snapshot));
}

/** Subscribe to {active, remainingMs}. Ticks ~4x/sec while running, so only
 *  the overlay should use it — not the whole player tree. */
export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function init() {
  if (v.el) return;
  const el = document.createElement('audio');
  el.id = 'verifyAudio';
  el.preload = 'none';
  // Set before any src, same rule as the main element.
  el.crossOrigin = 'anonymous';
  el.setAttribute('aria-hidden', 'true');
  document.body.appendChild(el);
  v.el = el;

  el.addEventListener('ended', () => stop());
}

export const isActive = () => v.active;

/**
 * Start playing `url`. Returns a promise resolving to false when the clip
 * couldn't be played (dead CDN link, CORS), so the caller can surface it and
 * restore main playback.
 */
export async function start(url) {
  if (v.active || !url) return false;
  init();

  v.active = true;
  v.startedAt = performance.now();

  if (v.timerId) clearInterval(v.timerId);
  v.timerId = setInterval(() => {
    if (performance.now() - v.startedAt >= VERIFY_MAX_MS) {
      stop();
      return;
    }
    emit();
  }, TICK_MS);
  emit();

  try {
    v.el.src = url;
    v.el.currentTime = 0;
    v.el.volume = 0.85;
    await v.el.play();
    return true;
  } catch (e) {
    console.warn('[VibeScape] verify audio play error:', e);
    stop();
    return false;
  }
}

export function stop() {
  if (!v.active) return;
  v.active = false;
  if (v.timerId) clearInterval(v.timerId);
  v.timerId = null;
  try { v.el?.pause(); } catch { /* not started */ }
  try {
    v.el?.removeAttribute('src');
    v.el?.load();
  } catch { /* already torn down */ }
  emit();
}
