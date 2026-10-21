// @vitest-environment jsdom
/**
 * Bit's size setting (frontend-next/src/state/ThemeContext.jsx) and the PiP
 * spot that follows it (features/player/MiniPlayer.jsx):
 *   - Off / Medium / Large, per device, replacing the old on/off switch;
 *   - the old `vs.showBit` migrates: '0' was Off and stays Off, anything else
 *     (or nothing) was the S2 rig beside the DJ line, which is Medium;
 *   - the PiP window shows Bit where the vibe readout was, asleep while the
 *     DJ is off, and nothing at all when Bit is Off.
 */
import { act, cleanup, render } from '@testing-library/react';

const player = { current: { id: 1, title: 'Runway', artist: 'Lio' }, mode: 'audio', playing: true, vibe: 69, mood: 'hype' };
const queue = { djEnabled: true };
vi.mock('../../frontend-next/src/state/PlayerContext', () => ({ usePlayer: () => player }));
vi.mock('../../frontend-next/src/features/queue', () => ({ useQueueState: () => queue }));

let ThemeProvider;
let useTheme;
let readBitSize;
let MiniPlayer;

beforeAll(async () => {
  ({ ThemeProvider, useTheme, readBitSize } = await import('../../frontend-next/src/state/ThemeContext'));
  ({ default: MiniPlayer } = await import('../../frontend-next/src/features/player/MiniPlayer'));
});
beforeEach(() => { localStorage.clear(); queue.djEnabled = true; });
afterEach(cleanup);

describe('readBitSize', () => {
  it('is Medium on a device that never chose', () => {
    expect(readBitSize()).toBe('medium');
  });

  it("migrates the old switch: '0' is Off, anything else is Medium", () => {
    localStorage.setItem('vs.showBit', '0');
    expect(readBitSize()).toBe('off');
    localStorage.setItem('vs.showBit', '1');
    expect(readBitSize()).toBe('medium');
  });

  it('prefers the new key, and ignores a value it does not know', () => {
    localStorage.setItem('vs.showBit', '0');
    localStorage.setItem('vs.bitSize', 'large');
    expect(readBitSize()).toBe('large');
    localStorage.setItem('vs.bitSize', 'huge');
    expect(readBitSize()).toBe('off');
  });
});

let theme;
function Probe() {
  theme = useTheme();
  return null;
}

describe('ThemeProvider bit size', () => {
  it('stores the choice under the new key and drops the old one', () => {
    localStorage.setItem('vs.showBit', '0');
    render(<ThemeProvider><Probe /></ThemeProvider>);
    expect(theme.bitSize).toBe('off');
    expect(localStorage.getItem('vs.showBit')).toBeNull();
    act(() => theme.setBitSize('large'));
    expect(theme.bitSize).toBe('large');
    expect(localStorage.getItem('vs.bitSize')).toBe('large');
    act(() => theme.setBitSize('nonsense'));
    expect(theme.bitSize).toBe('large');
  });
});

describe('MiniPlayer Bit spot', () => {
  const pip = () => {
    const host = document.createElement('div');
    document.body.appendChild(host);
    return { document: { body: host }, host };
  };
  const mount = (size) => {
    localStorage.setItem('vs.bitSize', size);
    const win = pip();
    render(<ThemeProvider><MiniPlayer pipWindow={win} /></ThemeProvider>);
    return win.host;
  };

  it('shows Bit instead of the vibe readout', () => {
    const host = mount('medium');
    expect(host.querySelector('[data-testid="pip-bit"] svg')).not.toBeNull();
    expect(host.textContent).not.toMatch(/69/);
    expect(host.querySelector('[aria-label^="Vibe"]')).toBeNull();
  });

  it('puts him to sleep while the DJ is off', () => {
    queue.djEnabled = false;
    const host = mount('large');
    const svg = host.querySelector('[data-testid="pip-bit"] svg');
    expect(svg.getAttribute('class')).toMatch(/asleep/);
  });

  it('is awake while the DJ is on', () => {
    const host = mount('medium');
    const svg = host.querySelector('[data-testid="pip-bit"] svg');
    expect(svg.getAttribute('class')).not.toMatch(/asleep/);
  });

  it('leaves the spot empty when Bit is off', () => {
    const host = mount('off');
    expect(host.querySelector('[data-testid="pip-bit"]')).toBeNull();
    expect(host.textContent).not.toMatch(/69/);
  });
});
