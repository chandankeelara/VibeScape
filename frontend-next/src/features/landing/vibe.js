/**
 * The mood/energy model behind the landing page's live demo.
 *
 * Ported verbatim from frontend/login.js `animateMoodGrid` (the BANDS and
 * DEMOS tables). The five bands and their colour ramps are the same table the
 * player uses (frontend/login.css:7-14 calls it "memory locked"), so do not
 * retune them here — they are shared vocabulary, not decoration.
 */

/** [minVibe, maxVibeExclusive, startColor, endColor, mood] */
export const BANDS = [
  { min: 0, max: 20, from: [76, 91, 138], to: [91, 127, 189], mood: 'sleep' },
  { min: 20, max: 40, from: [0, 180, 216], to: [34, 193, 227], mood: 'chill' },
  { min: 40, max: 60, from: [124, 58, 237], to: [167, 139, 250], mood: 'steady' },
  { min: 60, max: 80, from: [236, 72, 153], to: [244, 63, 94], mood: 'hype' },
  { min: 80, max: 101, from: [249, 115, 22], to: [220, 38, 38], mood: 'beast' },
];

/** Ladder order: top of the ladder is the most energetic. */
export const LADDER = [...BANDS].reverse();

/**
 * Fallback demo tracks per mood.
 *
 * The legacy page upgraded these from `GET /api/demo/moods` (real tracks +
 * album art) with a bare fetch. `lib/api.js` has no wrapper for that route and
 * this feature may not add one, so the curated stubs are all we show — see the
 * report. The shape is kept identical so wiring the real endpoint later is a
 * one-line change.
 */
export const DEMOS = {
  sleep: { title: 'Weightless', sub: 'Marconi Union · Weightless' },
  chill: { title: 'Late Night Drive', sub: 'The Midnight · Kids' },
  steady: { title: 'Redbone', sub: 'Childish Gambino · "Awaken, My Love!"' },
  hype: { title: 'Get Lucky', sub: 'Daft Punk · Random Access Memories' },
  beast: { title: 'Bulls On Parade', sub: 'Rage Against the Machine · Evil Empire' },
};

export const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

const lerp = (a, b, t) => a + (b - a) * t;

export function bandFor(vibe) {
  return BANDS.find((b) => vibe >= b.min && vibe < b.max) ?? BANDS[BANDS.length - 1];
}

export const moodFor = (vibe) => bandFor(vibe).mood;

/** Interpolated rgb() for a 0..100 vibe — this is what drives `--accent`. */
export function colorFor(vibe) {
  const b = bandFor(vibe);
  const t = (vibe - b.min) / (b.max - b.min);
  const ch = (i) => Math.round(lerp(b.from[i], b.to[i], t));
  return `rgb(${ch(0)}, ${ch(1)}, ${ch(2)})`;
}
