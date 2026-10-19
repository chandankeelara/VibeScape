// @vitest-environment jsdom
/**
 * The PiP miniplayer is a second document. tokens.css rides across with
 * copyStyles(), but the theme attribute and the live accent sit on the main
 * document's <html> as an attribute and inline style, in no stylesheet — so
 * until 2026-10-18 the window was dark with the default purple whatever the
 * app showed. followTheme() copies both, and must keep copying while the
 * window is open.
 */
import { applyAccent } from '../../frontend-next/src/lib/vibe.js';
import { followTheme } from '../../frontend-next/src/features/player/usePictureInPicture.js';

function fakePip() {
  return { document: document.implementation.createHTMLDocument('pip') };
}

const root = () => document.documentElement;

afterEach(() => {
  delete root().dataset.theme;
  root().removeAttribute('style');
});

describe('followTheme', () => {
  it('copies the theme and the accent at open', () => {
    root().dataset.theme = 'light';
    applyAccent(80, 'light');
    const pip = fakePip();
    const stop = followTheme(pip);
    const to = pip.document.documentElement;
    expect(to.dataset.theme).toBe('light');
    for (const name of ['--acc', '--acc-text', '--vibe-accent']) {
      expect(to.style.getPropertyValue(name)).toBe(root().style.getPropertyValue(name));
      expect(to.style.getPropertyValue(name)).not.toBe('');
    }
    stop();
  });

  it('follows a theme flip and a vibe change while open, and stops when closed', () => {
    applyAccent(20, 'dark');
    const pip = fakePip();
    const stop = followTheme(pip);
    const to = pip.document.documentElement;
    expect(to.dataset.theme).toBeUndefined();

    // What useApplyTheme does on a flip: attribute first, then the re-solve.
    root().dataset.theme = 'light';
    applyAccent(20, 'light');
    expect(to.dataset.theme).toBe('light');
    expect(to.style.getPropertyValue('--acc-text')).toBe(root().style.getPropertyValue('--acc-text'));

    // And back — the attribute is REMOVED in dark, not set to "dark".
    delete root().dataset.theme;
    applyAccent(90, 'dark');
    expect(to.dataset.theme).toBeUndefined();
    expect(to.style.getPropertyValue('--acc')).toBe(root().style.getPropertyValue('--acc'));

    stop();
    const before = to.style.getPropertyValue('--acc');
    applyAccent(5, 'dark');
    expect(to.style.getPropertyValue('--acc')).toBe(before);
  });
});
