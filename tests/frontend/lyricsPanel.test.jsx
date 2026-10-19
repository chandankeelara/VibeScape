// @vitest-environment jsdom
/**
 * frontend-next/src/features/lyrics/LyricsPanel.jsx — what each lyrics state
 * renders. The context is faked so every state can be set directly; the
 * provider itself is covered by lyricsProvider.test.jsx.
 */
import { act, fireEvent, render, screen, cleanup } from '@testing-library/react';

const LINES = [
  { t_ms: 1000, text: 'first line' },
  { t_ms: 4000, text: 'second line' },
  { t_ms: 8000, text: '' },
  { t_ms: 12000, text: 'last line' },
];

// Plain recorders, not vi.fn: the config's restoreMocks wipes vi.fn
// implementations between tests.
const calls = [];
// Primitive arguments only: an onClick handed straight through passes the event.
const record = (name) => (...a) => { calls.push([name, ...a.filter((x) => typeof x !== 'object')]); };

let ctx;
let active = -1;

vi.mock('../../frontend-next/src/features/lyrics/LyricsContext', () => ({
  useLyrics: () => ctx,
  useActiveLine: () => active,
}));

function baseCtx(view, extra = {}) {
  return {
    on: true,
    track: { title: 'Song', artist: 'Artist', artwork_url: '' },
    view,
    credit: 'Lyrics from LRCLIB',
    offset: 0,
    nudge: record('nudge'),
    reset: record('reset'),
    tapSync: record('tapSync'),
    startManualSync: record('startManualSync'),
    canManualSync: true,
    canSeek: true,
    seekToLine: record('seekToLine'),
    retry: record('retry'),
    close: record('close'),
    ...extra,
  };
}

async function panel(view, { variant = 'stage', ...extra } = {}) {
  const { default: LyricsPanel } = await import('../../frontend-next/src/features/lyrics/LyricsPanel');
  ctx = baseCtx(view, extra);
  return render(<LyricsPanel variant={variant} />);
}

beforeEach(() => {
  calls.length = 0;
  active = -1;
  // jsdom has no layout: scrollTo does not exist on elements.
  Element.prototype.scrollTo = function scrollTo() {};
  window.matchMedia = window.matchMedia || (() => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
});
afterEach(cleanup);

it('loading', async () => {
  await panel({ kind: 'loading' });
  expect(screen.getByText('Loading lyrics')).toBeTruthy();
  expect(screen.getByText('Lyrics from LRCLIB')).toBeTruthy();
});

it('synced: lines, the current one marked, and the sync bar', async () => {
  active = 1;
  await panel({ kind: 'synced', lines: LINES });
  const cur = screen.getByText('second line');
  expect(cur.getAttribute('aria-current')).toBe('true');
  expect(screen.getByText('first line').getAttribute('aria-current')).toBeNull();
  // An empty LRC line is a break, drawn as a note.
  expect(screen.getByText('♪')).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Tap to sync' })).toBeTruthy();
  expect(screen.getByRole('group', { name: 'Lyrics timing' })).toBeTruthy();
  // Only the current line is a tab stop.
  expect(cur.tabIndex).toBe(0);
  expect(screen.getByText('first line').tabIndex).toBe(-1);
});

it('synced: clicking a line seeks to it', async () => {
  await panel({ kind: 'synced', lines: LINES });
  fireEvent.click(screen.getByText('last line'));
  expect(calls).toContainEqual(['seekToLine', 3]);
});

it('synced: the sync bar steps, taps and resets', async () => {
  await panel({ kind: 'synced', lines: LINES }, { offset: 1500 });
  expect(screen.getByText('+1.5s')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Lyrics earlier by half a second' }));
  fireEvent.click(screen.getByRole('button', { name: 'Lyrics later by half a second' }), { shiftKey: true });
  fireEvent.click(screen.getByRole('button', { name: 'Tap to sync' }));
  fireEvent.click(screen.getByRole('button', { name: 'Reset' }));
  expect(calls).toEqual([['nudge', -500], ['nudge', 100], ['tapSync'], ['reset']]);
});

it('synced: holding a step button steps by 0.1s and the release adds nothing', async () => {
  vi.useFakeTimers();
  try {
    await panel({ kind: 'synced', lines: LINES });
    const plus = screen.getByRole('button', { name: 'Lyrics later by half a second' });
    fireEvent.pointerDown(plus, { button: 0 });
    act(() => { vi.advanceTimersByTime(400 + 140 * 2); });
    fireEvent.pointerUp(plus);
    fireEvent.click(plus);
    expect(calls).toEqual([['nudge', 100], ['nudge', 100], ['nudge', 100]]);
  } finally {
    vi.useRealTimers();
  }
});

it('synced: Reset is disabled at zero', async () => {
  await panel({ kind: 'synced', lines: LINES });
  expect(screen.getByRole('button', { name: 'Reset' }).disabled).toBe(true);
});

it('plain: the words, no sync bar, no seeking', async () => {
  await panel({ kind: 'plain', plain: ['alpha', '', 'beta'] });
  expect(screen.getByText('alpha')).toBeTruthy();
  expect(screen.queryByRole('group', { name: 'Lyrics timing' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'alpha' })).toBeNull();
});

it('preview: plain plus the note', async () => {
  await panel({ kind: 'preview', plain: ['alpha'] });
  expect(screen.getByText('Lyrics sync on full playback')).toBeTruthy();
  expect(screen.queryByRole('group', { name: 'Lyrics timing' })).toBeNull();
});

it('YouTube mismatch: plain plus "Sync manually"', async () => {
  await panel({ kind: 'mismatch', plain: ['alpha'] }, { variant: 'side' });
  expect(screen.getByText('alpha')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Sync manually' }));
  expect(calls).toEqual([['startManualSync']]);
});

it('instrumental', async () => {
  await panel({ kind: 'instrumental' });
  expect(screen.getByText('Instrumental')).toBeTruthy();
  expect(screen.getByText('Lyrics from LRCLIB')).toBeTruthy();
});

it('none: no source, so no credit line', async () => {
  await panel({ kind: 'none' }, { credit: null });
  expect(screen.getByText('No lyrics for this song yet')).toBeTruthy();
  expect(screen.queryByText('Lyrics from LRCLIB')).toBeNull();
});

it('upstream error, with retry', async () => {
  await panel({ kind: 'error' });
  expect(screen.getByText('Lyrics unavailable right now')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
  expect(calls).toEqual([['retry']]);
});

it('theater dock: synced words are on the video, the bar stays here', async () => {
  await panel({ kind: 'synced', lines: LINES }, { variant: 'dock' });
  expect(screen.queryByText('first line')).toBeNull();
  expect(screen.getByText('Lyrics are on the video.')).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Tap to sync' })).toBeTruthy();
});

it('arrow keys move between lines and do not reach the global shortcuts', async () => {
  active = 0;
  await panel({ kind: 'synced', lines: LINES });
  const seen = [];
  const onWindow = (e) => seen.push(e.key);
  window.addEventListener('keydown', onWindow);
  try {
    const first = screen.getByText('first line');
    first.focus();
    fireEvent.keyDown(first, { key: 'ArrowDown' });
    expect(document.activeElement.textContent).toBe('second line');
    expect(seen).toEqual([]);
  } finally {
    window.removeEventListener('keydown', onWindow);
  }
});
