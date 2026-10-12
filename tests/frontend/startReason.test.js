import { describe, expect, it } from 'vitest';
import { startKind } from '../../frontend-next/src/lib/startReason.js';

describe('startKind (the DJ line)', () => {
  it('a finished track followed by the DJ reads as "because you played"', () => {
    expect(startKind({ source: 'dj', trigger: 'next_button', ended: true })).toBe('ended');
    expect(startKind({ source: 'autoplay', trigger: 'next_button', ended: true })).toBe('ended');
  });

  it('a next-press followed by the DJ reads as a skip, not a finish', () => {
    expect(startKind({ source: 'dj', trigger: 'next_button', ended: false })).toBe('skip');
    expect(startKind({ source: 'autoplay', trigger: 'media_next', ended: false })).toBe('skip');
  });

  it('a slider move reads as easing toward the band', () => {
    expect(startKind({ source: 'autoplay', trigger: 'vibe_change', ended: false })).toBe('vibe');
  });

  it('a stale ended flag never relabels a search or a pick', () => {
    expect(startKind({ source: 'search', trigger: 'search', ended: true })).toBe('search');
    expect(startKind({ source: 'pick', trigger: 'pick', ended: true })).toBe('pick');
  });

  it('anything off Up next says so, whether by hand or after a finish', () => {
    expect(startKind({ source: 'queue', trigger: 'queue_jump', ended: false })).toBe('queue');
    expect(startKind({ source: 'queue', trigger: 'next_button', ended: true })).toBe('queue');
  });
});
