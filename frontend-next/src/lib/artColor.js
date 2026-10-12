/**
 * The average colour of a piece of album art, for S2's static room tint and
 * the light pool under the cover.
 *
 * Reads the image through a 1x1 canvas, which needs a CORS-clean image. If
 * the art host does not send Access-Control-Allow-Origin the canvas is
 * tainted, getImageData throws, and this resolves null — callers fall back to
 * a vibe-derived hue. Nothing here can break playback or the visible art: the
 * <img> on screen is a separate, non-CORS element.
 *
 * Cached per URL for the session (a handful of bytes each).
 */

const cache = new Map();

/** sRGB 0-255 -> OKLCH hue in degrees (the space lib/vibe.js tints in). */
export function hueOf([r, g, b]) {
  const lin = (c) => { const x = c / 255; return x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; };
  const [R, G, B] = [lin(r), lin(g), lin(b)];
  const l = Math.cbrt(0.4122214708 * R + 0.5363325363 * G + 0.0514459929 * B);
  const m = Math.cbrt(0.2119034982 * R + 0.6806995451 * G + 0.1073969566 * B);
  const s = Math.cbrt(0.0883024619 * R + 0.2817188376 * G + 0.6299787005 * B);
  const A = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s;
  const Bk = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s;
  const h = (Math.atan2(Bk, A) * 180) / Math.PI;
  return h < 0 ? h + 360 : h;
}

/** Resolves { rgb: [r,g,b], hue } or null. Never rejects. */
export function artColor(url) {
  if (!url) return Promise.resolve(null);
  if (cache.has(url)) return cache.get(url);
  const p = new Promise((resolve) => {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.decoding = 'async';
    img.onload = () => {
      try {
        const c = document.createElement('canvas');
        c.width = 1;
        c.height = 1;
        const ctx = c.getContext('2d', { willReadFrequently: false });
        ctx.drawImage(img, 0, 0, 1, 1);
        const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data;
        resolve({ rgb: [r, g, b], hue: hueOf([r, g, b]) });
      } catch {
        resolve(null); // tainted canvas: the host sent no CORS header
      }
    };
    img.onerror = () => resolve(null);
    img.src = url;
  });
  cache.set(url, p);
  return p;
}
