/**
 * frontend-next/src/features/lyrics/{lyricsMath,lyricsStore}.js — the pure
 * half of the lyrics feature: the active line under an offset, tap-to-sync,
 * the YouTube duration gate, which state a response becomes, and the
 * per-recording offset storage (including storage that throws).
 */
import {
  activeLineIndex, tapSyncOffset, stepOffset, formatOffset, seekFractionForLine,
  youtubeDurationMatches, playbackPath, offsetKey, deriveView, plainLines, OFFSET_MAX_MS,
} from '../../frontend-next/src/features/lyrics/lyricsMath';
import {
  readLyricsOn, writeLyricsOn, readOffset, writeOffset,
} from '../../frontend-next/src/features/lyrics/lyricsStore';

const LINES = [
  { t_ms: 1000, text: 'one' },
  { t_ms: 4000, text: 'two' },
  { t_ms: 9000, text: '' },
  { t_ms: 12000, text: 'four' },
];

describe('activeLineIndex', () => {
  it('is -1 before the first line and the last line after the end', () => {
    expect(activeLineIndex(LINES, 0)).toBe(-1);
    expect(activeLineIndex(LINES, 999)).toBe(-1);
    expect(activeLineIndex(LINES, 1000)).toBe(0);
    expect(activeLineIndex(LINES, 3999)).toBe(0);
    expect(activeLineIndex(LINES, 4000)).toBe(1);
    expect(activeLineIndex(LINES, 600000)).toBe(3);
  });

  it('a positive offset delays every line by that much', () => {
    // +1.5s: line 1 (4000) now starts at 5500.
    expect(activeLineIndex(LINES, 5400, 1500)).toBe(0);
    expect(activeLineIndex(LINES, 5500, 1500)).toBe(1);
  });

  it('a negative offset brings them forward', () => {
    expect(activeLineIndex(LINES, 3500, -500)).toBe(1);
    expect(activeLineIndex(LINES, 500, -500)).toBe(0);
  });

  it('copes with nothing to search', () => {
    expect(activeLineIndex([], 5000)).toBe(-1);
    expect(activeLineIndex(null, 5000)).toBe(-1);
    expect(activeLineIndex(LINES, NaN)).toBe(-1);
  });
});

describe('tapSyncOffset', () => {
  it('makes the highlighted line start now', () => {
    // Line 1 (t 4000) is highlighted and the singer reaches it at 5200.
    const off = tapSyncOffset(LINES, 1, 5200);
    expect(off).toBe(1200);
    expect(activeLineIndex(LINES, 5200, off)).toBe(1);
    expect(activeLineIndex(LINES, 5199, off)).toBe(0);
  });

  it('pulls lyrics forward when they run late', () => {
    expect(tapSyncOffset(LINES, 3, 11000)).toBe(-1000);
  });

  it('before any line is highlighted, a tap means the first line', () => {
    expect(tapSyncOffset(LINES, -1, 2500)).toBe(1500);
  });

  it('is clamped to the sync range', () => {
    expect(tapSyncOffset(LINES, 0, 999999)).toBe(OFFSET_MAX_MS);
  });
});

describe('offset stepping and display', () => {
  it('steps and clamps', () => {
    expect(stepOffset(0, 500)).toBe(500);
    expect(stepOffset(1500, -100)).toBe(1400);
    expect(stepOffset(OFFSET_MAX_MS, 500)).toBe(OFFSET_MAX_MS);
    expect(stepOffset(undefined, -500)).toBe(-500);
  });

  it('formats like the sync bar: signed, one decimal, real minus', () => {
    expect(formatOffset(1500)).toBe('+1.5s');
    expect(formatOffset(-500)).toBe('−0.5s');
    expect(formatOffset(0)).toBe('0.0s');
    expect(formatOffset(30)).toBe('0.0s');
  });

  it('seeks to where an offset line starts', () => {
    expect(seekFractionForLine(4000, 1000, 200000)).toBeCloseTo(0.025);
    expect(seekFractionForLine(4000, 0, 0)).toBeNull();
    expect(seekFractionForLine(1000, -5000, 200000)).toBe(0);
  });
});

describe('the YouTube duration gate', () => {
  it('trusts timings within 2s of the matched recording', () => {
    expect(youtubeDurationMatches(229000, 230000)).toBe(true);
    expect(youtubeDurationMatches(232000, 230000)).toBe(true);
    expect(youtubeDurationMatches(232001, 230000)).toBe(false);
    expect(youtubeDurationMatches(251000, 230000)).toBe(false);
  });

  it('cannot tell before the player or the backend has a duration', () => {
    expect(youtubeDurationMatches(0, 230000)).toBeNull();
    expect(youtubeDurationMatches(230000, null)).toBeNull();
  });
});

describe('playback path and offset key', () => {
  it('follows player.js loadTrack', () => {
    expect(playbackPath('video', null)).toBe('youtube');
    expect(playbackPath('video', 'spotify')).toBe('youtube');
    expect(playbackPath('audio', 'spotify')).toBe('spotify');
    expect(playbackPath('audio', 'preview')).toBe('preview');
    expect(playbackPath('audio', null)).toBeNull();
  });

  it('keys by recording, and a preview has none', () => {
    const t = { id: 7, spotify_id: 'abc', youtube_id: 'yt1' };
    expect(offsetKey('spotify', t)).toBe('spotify:abc');
    expect(offsetKey('youtube', t, 'yt1')).toBe('yt:yt1');
    expect(offsetKey('youtube', t, null)).toBeNull();
    expect(offsetKey('preview', t)).toBeNull();
  });
});

describe('deriveView — one state per response', () => {
  const synced = {
    status: 'synced', lines: LINES, plain: 'one\ntwo\n\nfour', matched_duration_ms: 230000, credit: 'Lyrics from LRCLIB',
  };
  const plain = { status: 'plain', lines: [], plain: 'a\nb', credit: 'Lyrics from LRCLIB' };

  it('loading', () => {
    expect(deriveView({ pending: true }).kind).toBe('loading');
  });

  it('synced on Spotify', () => {
    const v = deriveView({ data: synced, path: 'spotify' });
    expect(v.kind).toBe('synced');
    expect(v.lines).toBe(LINES);
  });

  it('YouTube: synced only when the durations match or the user synced by hand', () => {
    expect(deriveView({ data: synced, path: 'youtube', ytMatch: true }).kind).toBe('synced');
    expect(deriveView({ data: synced, path: 'youtube', ytMatch: false }).kind).toBe('mismatch');
    expect(deriveView({ data: synced, path: 'youtube', ytMatch: null }).kind).toBe('mismatch');
    expect(deriveView({ data: synced, path: 'youtube', ytMatch: false, manualSync: true }).kind).toBe('synced');
  });

  it('preview: plain with the note, never synced', () => {
    const v = deriveView({ data: synced, path: 'preview' });
    expect(v.kind).toBe('preview');
    expect(v.plain).toEqual(['one', 'two', '', 'four']);
  });

  it('plain', () => {
    const v = deriveView({ data: plain, path: 'spotify' });
    expect(v.kind).toBe('plain');
    expect(v.plain).toEqual(['a', 'b']);
  });

  it('instrumental', () => {
    expect(deriveView({ data: { status: 'instrumental', lines: [] }, path: 'spotify' }).kind).toBe('instrumental');
  });

  it('none, and a 404 is "none" too', () => {
    expect(deriveView({ data: { status: 'none', lines: [] }, path: 'spotify' }).kind).toBe('none');
    expect(deriveView({ error: { status: 404 } }).kind).toBe('none');
  });

  it('upstream error, and a failed request', () => {
    expect(deriveView({ data: { status: 'none', error: 'upstream', lines: [] } }).kind).toBe('error');
    expect(deriveView({ error: { status: 502 } }).kind).toBe('error');
  });

  it('plain text falls back to the synced lines when plain is empty', () => {
    expect(plainLines({ plain: '', lines: LINES })).toEqual(['one', 'two', '', 'four']);
  });
});

describe('lyricsStore', () => {
  it('remembers on/off', () => {
    expect(readLyricsOn()).toBe(false);
    writeLyricsOn(true);
    expect(readLyricsOn()).toBe(true);
    writeLyricsOn(false);
    expect(readLyricsOn()).toBe(false);
  });

  it('keeps one offset per source, so a video and the Spotify track differ', () => {
    writeOffset('spotify:abc', 1500);
    writeOffset('yt:xyz', -3200);
    expect(readOffset('spotify:abc')).toBe(1500);
    expect(readOffset('yt:xyz')).toBe(-3200);
    expect(readOffset('yt:other')).toBeNull();
  });

  it('a reset to zero forgets the recording', () => {
    writeOffset('yt:xyz', 800);
    writeOffset('yt:xyz', 0);
    expect(readOffset('yt:xyz')).toBeNull();
  });

  it('never throws when storage does', () => {
    const boom = () => { throw new Error('SecurityError'); };
    globalThis.localStorage = { getItem: boom, setItem: boom, removeItem: boom };
    expect(readLyricsOn()).toBe(false);
    expect(() => writeLyricsOn(true)).not.toThrow();
    expect(readOffset('spotify:abc')).toBeNull();
    expect(() => writeOffset('spotify:abc', 500)).not.toThrow();
    expect(() => writeOffset('spotify:abc', 0)).not.toThrow();
  });

  it('ignores junk in storage', () => {
    localStorage.setItem('vs.lyrics.offset.spotify:abc', 'NaN-ish');
    expect(readOffset('spotify:abc')).toBeNull();
  });
});
