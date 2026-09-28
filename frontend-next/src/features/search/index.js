/**
 * Persistent search bar (library + Spotify catalog).
 *
 * Drop <SearchBar /> in above the player stage. It needs to be inside
 * <PlayerProvider>, <SpotifyAuthProvider> and <ToastProvider>, and inside a
 * <QueryClientProvider>. No props are required.
 */
export { default as SearchBar } from './SearchBar';
export { default } from './SearchBar';
export { default as TrackRow } from './TrackRow';
export { trackVibe } from './trackVibe';
