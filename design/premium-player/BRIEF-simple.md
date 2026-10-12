# Round 2 brief — "simple, premium, for everyone"

Round 1 (a-cinematic, b-editorial, c-generative) was too opinionated: strong
tastes that appeal to some people. The product owner wants the opposite:
**simple but premium, appealing to the mass of people** — the polish of
Apple Music / Spotify / YouTube Music at their best, not a design statement.

## Principles (non-negotiable)
1. **Familiar first.** Layout and controls people already know from the big
   music apps. Nobody should need to learn the UI. Standard icons, standard
   positions (play in the middle, progress above transport, queue on the side
   or in a sheet).
2. **Content is the hero, chrome recedes.** Album art and the song are the
   loudest things on screen. Controls are quiet until needed.
3. **Calm visuals.** Restrained palette (neutral surfaces + one accent),
   generous spacing, clean sans-serif typography with a clear hierarchy.
   No grain, no strobe, no film effects, no dot-matrix, no busy backgrounds.
4. **Exactly one signature element: the vibe control.** It should be
   instantly understandable (0-100, five named bands) and pleasant to use —
   elegant, not a gimmick. The vibe tints the UI's accent subtly.
5. **Motion is subtle and purposeful.** Short, soft transitions (150-300 ms),
   a gentle cover crossfade on track change, light hover states. Nothing that
   loops for its own sake. Respect prefers-reduced-motion.
6. **Accessible and legible.** WCAG AA contrast, 44px touch targets on phone,
   visible focus states, real buttons/inputs, works with keyboard.
7. **Fast and light.** No canvases running full-screen, no per-frame loops
   beyond a tiny progress tick. Should feel instant on a mid-range phone.
8. **Dark mode by default, light mode too** (a toggle) — mass audiences
   expect both.

## Good ideas from round 1 worth keeping (if they fit the principles)
- From A: a soft background tint taken from the album art's colour (static,
  not animated; just a calm gradient), and a gentle cover crossfade.
- From B: the vibe owning the accent colour (cool at sleep → warm at beast),
  with contrast kept safe at every value; band "detents" on the vibe control.
- From C: the DJ explaining itself in one quiet line ("Because you played …"
  / "Steering away after a skip"); pause not snapping but easing.

## Features (all must be present and working with fake data)
1. Top bar: logo, help, Spotify connect state (connected/premium vs "Sign in
   with Spotify"), user avatar menu.
2. Search: results panel with two groups (Your library / Spotify); each row
   playable or "add to queue"; Ctrl+K hint.
3. Hero: album art, OR the YouTube video in video mode. Video has exactly two
   layouts: normal and theater. Placeholder video surface (no real iframe).
4. Recent trail: last ~8 played thumbnails, clickable.
5. Track meta: title, artist · album, genre, VIBE score badge, language
   badge (EN/JA/KN/HI…), mood chip, source chip (FULL via Spotify vs 30s
   PREVIEW), verify and metrics actions — keep secondary items quiet.
6. Progress/seek bar with elapsed/total.
7. Transport: previous, play/pause, next, picture-in-picture, audio/video
   switch, theater toggle (video mode only).
8. Vibe control: number + mood word + control across sleep/chill/steady/
   hype/beast; changing it re-tints the accent live.
9. Queue: "Up next" (reorder, remove, empty state), DJ mode toggle,
   "Recommended for this track" rows (art, title, artist, vibe badge,
   language badge, play, add). Phone: slide-up sheet from a button.
10. "Bit", a small friendly robot mascot that reacts to events (finished =
    happy, skipped = sad) — keep it small and optional, it must not clutter.
11. A subtle "music is playing" cue (e.g. a small animated equalizer on the
    current track) — no full-screen pulsing.
12. Loading state (DJ is picking the next track) and "no video" state.
13. Responsive: desktop 1440 and phone 375. No horizontal scroll on phone.

## Reference
- Current app screenshots: .claude/tmp/mobile_shots/player_1440.png,
  player_375.png, player_375_bottom.png
- Structure: frontend-next/src/features/player/PlayerPage.jsx, ArtStage.jsx,
  TheaterBar.jsx, Transport.jsx, MoodSlider.jsx,
  frontend-next/src/features/queue/QueueSidebar.jsx
- Round 1 files (for ideas only, NOT the style to copy):
  design/premium-player/a-cinematic.html, b-editorial.html, c-generative.html

## Rules for the deliverable
- ONE self-contained HTML file in design/premium-player/ (name given in your
  task). Inline CSS + JS, no external scripts or images; Google Fonts allowed
  (prefer one well-made family, e.g. Inter / Geist / Manrope / Plus Jakarta).
  Album art via CSS gradients / inline SVG.
- Interactive with fake data: play/pause, vibe re-tints, audio/video/theater,
  search, queue sheet on phone, DJ toggle, clicking a rec plays it,
  light/dark toggle.
- URL params to jump to states are welcome (e.g. ?vibe=90&mode=video).
- A small hidden "design notes" toggle explaining the decisions.
- Do NOT edit anything outside design/premium-player/. No dev servers, no app
  source changes. Put any helper scripts in design/premium-player/tmp/<your
  file prefix>/ (shared folders collided in round 1).
- Self-check in headless Chrome (Python Playwright,
  `p.chromium.launch(channel="chrome", headless=True)`, file:/// URL) at
  1440x900 and 375x812, dark and light, vibe ~15 and ~85, audio + theater,
  phone sheet open. scrollWidth must equal viewport width on phone. Save
  screenshots to design/premium-player/shots/<prefix>-*.png.
- Report back concisely: file path, 5 key decisions in one line each, and what
  would be costly in the real React app (CSS Modules; the YouTube iframe must
  never be re-mounted or moved in the DOM).
