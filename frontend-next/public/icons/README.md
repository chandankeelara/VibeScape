# App icons

The `manifest.json` and `index.html` reference these files:

- `icon-192.png` — 192×192, PWA "any" purpose
- `icon-512.png` — 512×512, PWA "any" purpose
- `icon-maskable-512.png` — 512×512, PWA "maskable" purpose (safe zone in inner 80%)
- `apple-touch-icon.png` — 180×180, iOS home-screen icon (no transparency, no rounded corners — iOS handles the mask)

## Generating

Fastest path: use a source SVG or 1024×1024 PNG of the VibeScape wordmark dot and export the sizes above. Any of these tools work:

- https://realfavicongenerator.net/ (upload once, download a bundle)
- https://maskable.app/editor (for the maskable variant specifically)
- `npx pwa-asset-generator source.png . --icon-only --favicon`

Drop the generated files directly into this folder. Until then, iOS will fall back to a screenshot of the page as the home-screen icon, which looks bad but isn't blocking for testing installability.
