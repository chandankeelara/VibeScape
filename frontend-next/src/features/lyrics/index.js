/**
 * Lyrics: a toggle, a panel laid out for each player view, and manual sync.
 *
 * <LyricsProvider> must sit inside <PlayerProvider> and a React Query
 * provider, and wrap everything that shows lyrics. See LyricsContext.jsx.
 *
 * Only the state is exported here. The views (LyricsPanel, LyricsStrip,
 * LyricsSheet) are imported lazily by NowPlaying, so nobody who never turns
 * lyrics on downloads them; re-exporting them from this file would pull them
 * into the main chunk.
 */
export { LyricsProvider, useLyrics, useActiveLine } from './LyricsContext';
