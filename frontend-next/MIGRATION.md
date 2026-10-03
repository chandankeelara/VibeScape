# frontend-next — migration status

React rewrite of the VibeScape UI. **Strangler-fig**: the legacy vanilla app in
`frontend/` stays fully functional at `/` until this reaches parity. Never
break `/`.

Owned by the `frontend-owner` agent (`.claude/agents/frontend-owner.md`).

## Where things are served

| App | Source | URL |
|---|---|---|
| Legacy | `frontend/` | `/`, `/app`, `/admin` |
| React | `frontend-next/dist/` | `/next`, `/next/admin` |

Both come from the same FastAPI process, same origin, **same session token**
(`localStorage['vibescape_session_token']`) — sign in on one, you're signed in
on the other.

## Port status — feature-complete

| Screen | Legacy source | Status |
|---|---|---|
| API client | scattered, 18 `fetch` sites | ✅ `src/lib/api.js` |
| Session handling | `app.js` / `admin.js` | ✅ `src/lib/session.js` |
| Admin panel | `admin.{html,js,css}` (677) | ✅ `src/features/admin/` |
| Auth / login | `login.*` (3,459) + `app.js:641-1040` | ✅ `src/features/login/` |
| Search (library + Spotify) | `app.js:4173-4800` | ✅ `src/features/search/` |
| Queue + recs + DJ mode | `app.js:4800-5674` | ✅ `src/features/queue/` |
| Library sync modal | `app.js:5674-6250` | ✅ `src/features/sync/` |
| Metrics / debug / help | `app.js:3577-4173` | ✅ `src/features/panels/` |
| Player shell + mood grid | `app.js:1127-1360`, `index.html` | ✅ `src/features/player/` |
| Verify (classification clip) | `app.js:1338-1541` | ✅ `src/media/verify.js` + chip/overlay |
| Media layer | `app.js:1541-2450`, `2762-3576` | ✅ `src/media/` |
| Listening telemetry | new — no legacy equivalent | ✅ `src/lib/{events,listenLog}.js` |

~9,850 lines across 77 files, replacing ~16,600 lines of legacy.

## Verified working

Guest sign-in → track loads → audio plays → autoplay advances → recs load.
All routes 200 (`/`, `/app`, `/next`, `/next/admin`, `/api/health`), no console
errors, `npm run build` clean (157 modules, 125 KB gzipped main chunk + two
lazy panel chunks).

## Commands

```bash
cd frontend-next
npm install
npm run dev      # :5173, proxies /api → localhost:8000
npm run build    # → dist/, served at /next
```

For the dev proxy to work, run the backend separately on :8000.

## Architecture

**The media layer is outside React.** `src/media/{player,glow,youtube,spotify}.js`
are plain modules booted once from `main.jsx` before render. React reaches them
only through `PlayerContext`. Read `src/media/README.md` before touching any of
it — the constraints there are load-bearing, not style preferences.

**Two subscription channels.** `usePlayer()` carries low-frequency state
(current track, playing, queue). `usePlaybackTime()` carries ~4Hz position and
is subscribed only by the progress bar and the DJ ratio probe, so playback
ticks never re-render the player tree.

**Provider order** (`main.jsx` → `App.jsx`):
`QueryClient` → `Toast` → `Router` → `AuthGate` → `SpotifyAuth` → `Player`.
`SpotifyAuthProvider` sits *below* the gate deliberately: its storage keys are
`spotify_{userId}_*`, so mounting it above would write tokens under `_anon`
and orphan them once the user resolves.

## Conventions

- No bare `fetch` outside `src/lib/api.js`. One documented exception: the debug
  panel's "test /v1/me" hits `api.spotify.com` directly, since api.js owns the
  VibeScape backend only.
- No `innerHTML`, ever. The legacy app had 22 sites with hand-rolled
  `escapeHtml`; all of it is JSX now.
- Server state via React Query. No hand-rolled caching/retry/loading.
- CSS Modules per component; values from `src/styles/tokens.css`. Use the
  `--z-*` scale — legacy `style.css` has 43 unscaled `z-index` values.
- Keyboard reachable, visible focus. Legacy is good here; don't regress.

## Listening telemetry (`POST /api/events`)

Three plain modules, no React lifecycle, nothing that can re-render the player:

- `lib/listenLog.js` — one open play at a time; `endPlay()` emits and nulls it,
  so a duplicate `ended`, a pagehide followed by a real transition, and the
  teardown in `PlayerProvider`'s cleanup are all idempotent by construction.
- `lib/events.js` — 200-event buffer (drops oldest), 5s timer, flush on
  `visibilitychange → hidden` and `pagehide`, 50 per request. A failed batch is
  dropped, never retried; three consecutive failures kill the module for the
  page's life with one `console.warn`.
- `lib/api.js postEvents()` — the only `fetch` in the app that deliberately
  does **not** go through `request()`: a telemetry 401 must not `clearToken()`.

**End-of-track attribution** is hooked in `media/player.js`, not in React:
`'completed'` is emitted by the three real end-of-media events *before*
`hooks.onEnded()` hands control to `next()`, so the replacement reaching
`loadTrack()` finds no open play. Everything else that displaces an open play
through `loadTrack()` defaults to `'skipped'`; `player.stop()` and the
post-sync re-roll pass `'replaced'`; `pagehide` sends **no** `reason` (the
contract has no word for "closed the tab", and the backend buckets a null
reason nowhere while still counting `total_played_ms`).

**`vibe_source`** is a ref in `PlayerContext`, stamped outside the state
updaters: `setVibe`/`shiftVibe` → `'user'`, `setVibeFromTrack` → `'system'`.
It starts `null` and unknown provenance sends nothing. `dj_mode` is read off
`nextFallbackRef` — that ref *is* DJ mode, so no new state was needed.

## Theater mode (video)

A YouTube-style layout toggle, not a port — the legacy app has no equivalent.
`useTheaterMode.js` owns one persisted boolean (`vs.player.theater`); the
effective state also requires video mode, so switching to audio drops the
layout but keeps the preference.

- The control is in `.videoTopbar`, the one strip of the video card we own.
  It cannot go over the player's own controls: that is a cross-origin iframe
  and any surface floating on it eats a `pointerdown` we can never give back.
- Hidden (not disabled) in audio mode — the control's whole context is the
  video chrome, which isn't on screen. `display: none` below 1024px too, where
  the stage is already one column.
- **Both rails stay put.** The shell keeps its two columns, so the queue
  flanks on the right and the recent trail stays a vertical strip on the left.
  The video widens by absorbing the **meta column**: the stage goes from
  `[64px | 1.05fr | 1fr]` to `[64px | 1fr]`, and track meta + transport + mood
  slider drop to row 2 under the video.
- **The meta column is replaced, not re-flowed.** `TheaterBar.jsx` is one
  ~55px strip: title + `artist · album` on the left, `TransportButtons` +
  `ModeToggle` on the right. No mood slider, no vibe meter, no chips, no
  second progress bar (the YouTube iframe carries its own, a few pixels up).
  `TrackMeta`, `Transport` and `MoodSlider` are untouched and still own
  normal mode.
- **Shared controls, never copied.** `TransportButtons.jsx` (a fragment, so
  the caller's flex row keeps owning the gap) and `ModeToggle.jsx` were
  lifted out of `Transport.jsx`. Transport now places the pill with its own
  `.modeAnchor`; the theater bar just drops it in a row.
- **The stage no longer scrolls**, at 1280x700, 1440x900 or 1920x1080 — the
  strip gave back the 180-230px the full meta column cost. `overflow-y: auto`
  stays as a safety valve, with `scrollbar-gutter: stable` so a few pixels of
  overflow cannot start a scrollbar/width/height oscillation.
- `--theater-h` (tokens.css) is now mostly a **safety cap**: at 72dvh the
  video is limited by the column's width at every size checked. The card is
  ~2.0-2.7x the width of the square it replaces, 4.0-7.1x the picture area.
- **The viewport gate moved into JS.** Theater now swaps the DOM, and CSS
  cannot undo that, so `useTheaterMode` carries a `matchMedia('(min-width:
  1024px)')` listener. Without it, narrowing the window with theater on would
  hide the mood slider with no visible control to bring it back.
- **Theater and detach are mutually exclusive**, enforced in both directions:
  entering theater calls `dock()`, and `useVideoFrame`'s new `onDetach` hook
  exits theater. An `ArtStage` effect re-asserts the invariant for storage
  written before the feature existed.
- The grid flips in one discrete step; only `transform`/`opacity` are
  animated (`@keyframes settle`). Nothing is re-keyed or reparented, so the
  YouTube iframe never reloads. The settle is skipped on a detach-driven exit
  because a transformed ancestor would become the containing block for the
  `position: fixed` frame the user is dragging.

## Gotchas discovered during the port

**`trackKey()` must stay spotify_id-first.** `_resolve_anchor`
(backend/app.py:924) accepts a spotify_id or the internal numeric `tracks.id`
only — the apple_id path was retired. An apple_id-keyed `/similar` or
`/features` request 404s. This shipped broken once and was caught in the
browser, not the build.

**Two different Spotify flows, one `?code=`.** The in-app *connect* flow is
PKCE and redeems client-side (`SpotifyAuthContext`). The *login* flow is
server-side via `/api/auth/spotify-oauth`, which does its own exchange. Codes
are single-use, so `SpotifyAuthContext.signIn()` sets a `sessionStorage`
marker and the callback handler in `App.jsx` only redeems when it's present.
Removing that guard resurrects an `invalid_grant` bug.

**`/api/users` does not exist.** `docs/mobile-api.md` §7 lists it; the doc is
stale. Auth is email/password + guest + Spotify. `api.listUsers()` is kept only
so nothing breaks at import time — it 404s.

## Known gaps

- **`/callback` hardcodes a redirect to `/`** (backend/app.py:1657), so a
  Spotify *login* started from `/next` lands on the legacy page, which redeems
  the code itself. Needs the origin round-tripped through `state`. The React
  button is otherwise complete.
- **No tests.** Vitest + Testing Library is the obvious next step; `api.js`,
  `lib/vibe.js`, and `queue/dj.js` (pure functions) are the highest-value
  targets.
- **PWA files** (`manifest.json`, `sw.js`) still point at the legacy app, and
  `frontend/icons/` has no actual PNGs, so the app is not installable.
- **The backend drops `dj_mode` and `vibe_source`.** `_normalize_event`
  (backend/app.py) builds its insert row from `type / reason / position_ms /
  duration_ms / vibe / source / client_ts` only; `track_events` has no column
  for either. The client sends them per the contract, but nothing stores them
  today — which means a `vibe` sample still cannot be told apart from the
  recommender's own echo on the server side.
- `source` has no vocabulary for a recent-trail rewind or a mood-grid pick;
  both are logged as `'search'`.
- `duration_ms` is the duration of *what played*, so a 30s preview reports
  30000, not the catalogue track length. Mixing the two clocks would make
  `position_ms / duration_ms` meaningless.
- DJ mode's `natural` end flag is inferred from played ratio rather than the
  media `ended` event. `player.js` exposes a real seam (`setHooks.onEnded`) if
  that's ever worth tightening.
