import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Served from the site root. The /next/ prefix existed only to keep React's
// files from colliding with the legacy app's /app.js, /style.css and
// /login.css at the root — with legacy removed that constraint is gone, and a
// root base is required anyway for the manifest and service worker to sit at
// / with the right scope.
export default defineConfig({
  plugins: [react()],
  base: '/',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: true,
  },
  server: {
    port: 5173,
    // Dev-only: talk to the real FastAPI backend on :8000 so we get live data
    // without CORS. In production both apps are same-origin anyway.
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
});
