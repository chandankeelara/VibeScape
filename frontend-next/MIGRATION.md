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
- DJ mode's `natural` end flag is inferred from played ratio rather than the
  media `ended` event. `player.js` exposes a real seam (`setHooks.onEnded`) if
  that's ever worth tightening.
