/**
 * Art-glow RMS analyser. Ported from frontend/app.js:1541-1630.
 *
 * CRITICAL — read src/media/README.md before touching this:
 *   - `createMediaElementSource` is callable ONCE per element for the lifetime
 *     of the page. The module-level cache below is load-bearing, not an
 *     optimization. A second call throws InvalidStateError permanently.
 *   - Audio is routed through ctx.destination, so if this graph breaks the
 *     symptom is SILENCE, not an error.
 *   - The rAF loop writes a CSS variable directly at ~30Hz. It must never go
 *     through React state.
 */

const GLOW_MIN = 0.5;
const GLOW_MAX = 0.9;
const GLOW_LERP = 0.15;
const FRAME_INTERVAL = 33; // ~30Hz

const glow = {
  ctx: null,
  source: null,
  analyser: null,
  buffer: null,
  rafId: null,
  smoothed: 0.65,
};

const reducedMotion = () =>
  window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

export function setAlpha(a) {
  document.documentElement.style.setProperty('--art-glow-alpha', String(a));
}

function ensure(audioEl) {
  if (reducedMotion()) return false;
  if (glow.analyser && glow.source) return true;
  try {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return false;
    if (!glow.ctx) glow.ctx = new AC();

    // ONCE per element, ever. Do not "clean up" and recreate.
    if (!glow.source) glow.source = glow.ctx.createMediaElementSource(audioEl);

    if (!glow.analyser) {
      glow.analyser = glow.ctx.createAnalyser();
      glow.analyser.fftSize = 512;
      glow.buffer = new Uint8Array(glow.analyser.fftSize);
      glow.source.connect(glow.analyser);
      // Analyser → destination, or audio never reaches the speakers.
      glow.analyser.connect(glow.ctx.destination);
    }
    return true;
  } catch (e) {
    console.warn('[VibeScape] glow analyser init failed:', e);
    return false;
  }
}

export function start(audioEl) {
  if (!ensure(audioEl)) return;
  if (glow.ctx.state === 'suspended') glow.ctx.resume().catch(() => {});
  if (glow.rafId) return;

  let lastFrame = 0;
  const step = (ts) => {
    glow.rafId = requestAnimationFrame(step);
    if (ts - lastFrame < FRAME_INTERVAL) return;
    lastFrame = ts;
    if (!glow.analyser) return;

    glow.analyser.getByteTimeDomainData(glow.buffer);
    let sum = 0;
    for (let i = 0; i < glow.buffer.length; i++) {
      const v = (glow.buffer[i] - 128) / 128;
      sum += v * v;
    }
    const rms = Math.sqrt(sum / glow.buffer.length);
    const mapped = GLOW_MIN + Math.min(1, rms * 2.4) * (GLOW_MAX - GLOW_MIN);
    glow.smoothed += (mapped - glow.smoothed) * GLOW_LERP;
    setAlpha(glow.smoothed.toFixed(3));
  };
  glow.rafId = requestAnimationFrame(step);
}

export function stop() {
  if (glow.rafId) cancelAnimationFrame(glow.rafId);
  glow.rafId = null;
}
