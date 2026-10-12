// frontend-next/src/features/sync/playlist.js — validating a pasted playlist link.
import { SUPPORTED_SERVICES, detectPlaylistLink, parsePlaylistId } from '../../frontend-next/src/features/sync/playlist.js';

describe('detectPlaylistLink', () => {
  it.each([
    ['https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M', 'spotify', '37i9dQZF1DXcBWIGoYBM5M'],
    ['https://open.spotify.com/intl-de/playlist/37i9dQZF1DXcBWIGoYBM5M?si=x', 'spotify', '37i9dQZF1DXcBWIGoYBM5M'],
    ['spotify:playlist:37i9dQZF1DXcBWIGoYBM5M', 'spotify', '37i9dQZF1DXcBWIGoYBM5M'],
    ['37i9dQZF1DXcBWIGoYBM5M', 'spotify', '37i9dQZF1DXcBWIGoYBM5M'],
    ['https://music.apple.com/us/playlist/todays-hits/pl.f4d106fed2bd41149aaacabb233eb5eb', 'apple', 'pl.f4d106fed2bd41149aaacabb233eb5eb'],
    ['https://music.apple.com/in/playlist/mine/pl.u-abc123', 'apple', 'pl.u-abc123'],
    ['https://www.youtube.com/playlist?list=PL4fGSI1pDJn6puJdseH2Rt9sMvt9E2M4i', 'youtube', 'PL4fGSI1pDJn6puJdseH2Rt9sMvt9E2M4i'],
    ['https://music.youtube.com/playlist?list=PL4fGSI1pDJn6puJdseH2Rt9sMvt9E2M4i', 'youtube', 'PL4fGSI1pDJn6puJdseH2Rt9sMvt9E2M4i'],
    ['https://www.youtube.com/watch?v=abc&list=PL4fGSI1pDJn6puJdseH2Rt9sMvt9E2M4i', 'youtube', 'PL4fGSI1pDJn6puJdseH2Rt9sMvt9E2M4i'],
    ['https://www.deezer.com/en/playlist/3155776842', 'deezer', '3155776842'],
    ['deezer.com/fr/playlist/3155776842', 'deezer', '3155776842'],
    ['https://music.amazon.com/playlists/B01M11SBC8', 'amazon', 'B01M11SBC8'],
    ['https://music.amazon.com/user-playlists/abc123?ref=x', 'amazon', 'abc123'],
  ])('%s -> %s', (url, kind, id) => {
    expect(detectPlaylistLink(url)).toEqual({ kind, id });
  });

  it.each(['', '   ', 'hello', 'https://example.com/playlist/1', 'https://www.youtube.com/watch?v=abc',
    'https://music.apple.com/us/album/x/1'])('rejects %j', (url) => {
    expect(detectPlaylistLink(url)).toBeNull();
  });

  it('knows Deezer but does not claim to support it', () => {
    expect(SUPPORTED_SERVICES.has(detectPlaylistLink('https://www.deezer.com/en/playlist/1').kind)).toBe(false);
    for (const k of ['spotify', 'apple', 'youtube', 'amazon']) expect(SUPPORTED_SERVICES.has(k)).toBe(true);
  });

  it('rejects Amazon links that are not playlists', () => {
    expect(detectPlaylistLink('https://music.amazon.com/albums/B0ABC')).toBeNull();
  });

  it('keeps the original Spotify parser working', () => {
    expect(parsePlaylistId('https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M')).toBe('37i9dQZF1DXcBWIGoYBM5M');
  });
});
