import { describe, expect, it } from 'vitest';
import {
  ACCENT_SURFACES, ACCENT_TARGET, contrast, solveAccent, trackAccentStyle,
} from '../../frontend-next/src/lib/vibe.js';

const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));

describe('solveAccent', () => {
  for (const theme of ['dark', 'light']) {
    it(`accent text clears ${ACCENT_TARGET}:1 on every ${theme} surface and badge, at every vibe`, () => {
      for (let v = 0; v <= 100; v++) {
        const { text, textOn } = solveAccent(v, theme);
        // The surfaces plus the two soft-badge composites.
        expect(textOn.length).toBe(ACCENT_SURFACES[theme].length + 2);
        for (const s of textOn) expect(contrast(text, s)).toBeGreaterThanOrEqual(ACCENT_TARGET);
      }
    });

    it(`ink on the accent fill clears ${ACCENT_TARGET}:1 in ${theme}, at every vibe`, () => {
      for (let v = 0; v <= 100; v++) {
        const { fill, ink } = solveAccent(v, theme);
        expect(contrast(fill, ink)).toBeGreaterThanOrEqual(ACCENT_TARGET);
      }
    });
  }

  it('walks cool to warm: sleep is bluer than beast, beast is redder', () => {
    const sleep = solveAccent(5, 'dark').mark;
    const beast = solveAccent(95, 'dark').mark;
    expect(sleep[2]).toBeGreaterThan(sleep[0]);
    expect(beast[0]).toBeGreaterThan(beast[2]);
  });

  it('clamps out-of-range and non-numeric vibes instead of producing NaN colours', () => {
    for (const v of [-20, 140, NaN, undefined, '61']) {
      const { mark } = solveAccent(v, 'dark');
      for (const c of mark) expect(Number.isInteger(c)).toBe(true);
    }
  });

  it('the surfaces it solves against match tokens.css', async () => {
    const { readFileSync } = await import('node:fs');
    const css = readFileSync(new URL('../../frontend-next/src/styles/tokens.css', import.meta.url), 'utf8');
    const light = css.slice(css.indexOf("html[data-theme='light']"));
    const dark = css.slice(0, css.indexOf("html[data-theme='light']"));
    const pick = (block, name) => block.match(new RegExp(`--${name}:\\s*(#[0-9a-f]{6})`, 'i'))[1].toLowerCase();
    for (const [theme, block] of [['dark', dark], ['light', light]]) {
      const expected = ['panel', 'raised', 'hover', 'bg'].map((n) => pick(block, n));
      expect(ACCENT_SURFACES[theme]).toEqual(expected);
    }
    expect(hex(ACCENT_SURFACES.dark[0])).toHaveLength(3);
  });
});

describe('trackAccentStyle', () => {
  it('is undefined for a track with no score, so no badge colour is invented', () => {
    expect(trackAccentStyle(null)).toBeUndefined();
  });

  it('colours by the track score, independent of the global vibe', () => {
    const low = trackAccentStyle(10, 'dark');
    const high = trackAccentStyle(90, 'dark');
    expect(low['--t-acc']).not.toBe(high['--t-acc']);
  });
});
