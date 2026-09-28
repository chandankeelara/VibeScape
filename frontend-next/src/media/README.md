# `src/media/` — the imperative boundary

**Nothing in this folder may be a React component, and nothing here may be
constructed inside a `useEffect`.**

These four subsystems are stateful, single-instance browser objects. They are
initialized exactly once at app startup (from `main.jsx`, before React renders)
and reached only through `PlayerProvider` context, which exposes imperative
methods (`play()`, `pause()`, `seek()`) plus low-frequency state React can
usefully render (current track, is-playing, queue).

## Why, concretely

| Module | Constraint |
|---|---|
| `glow.js` | `ctx.createMediaElementSource(el)` is callable **once per element, ever**. A second call throws `InvalidStateError` — permanently, there is no teardown. React StrictMode double-invokes effects in dev, so a naive `useEffect` crashes on the second run. Because audio is routed through `ctx.destination`, the symptom is **silence, not an error**. |
| `youtube.js` | `window.onYouTubeIframeAPIReady` is a one-shot global set by an external `<script>`; it fires once and never again. `new YT.Player(nodeId)` takes over a real DOM node and replaces it with an `<iframe>` — if React's reconciler recreates that node, playback dies mid-song and the player instance is orphaned. |
| `spotify.js` | `window.onSpotifyWebPlaybackSDKReady` — same one-shot global problem. Also browser-only: there is no native SDK, which is why a React Native / Flutter client was ruled out. |
| `glow.js` (rAF loop) | Reads RMS off an `AnalyserNode` and writes `--art-glow-alpha` at ~60fps. It must write the CSS variable directly via `documentElement.style.setProperty`. Routed through `useState`, it would re-render the player tree 60×/second. |

## Porting rule

When the player is ported (last in the order — see `MIGRATION.md`), copy the
working logic from `frontend/app.js` **as-is** into these modules. Do not
"Reactify" it. React owns the UI; these modules own the media pipeline. The
only thing that changes is how the UI talks to them.

Legacy source lines worth reading before touching any of this:

- `frontend/app.js:1541` — art-glow RMS analyser section
- `frontend/app.js:1726` — video mode / YouTube IFrame
- `frontend/app.js:1967` — `onYouTubeIframeAPIReady`
- `frontend/app.js:2762` — Spotify PKCE + Web Playback SDK
- `frontend/app.js:3566` — `onSpotifyWebPlaybackSDKReady`
