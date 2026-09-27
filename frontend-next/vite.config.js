import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The React app is served from /next by FastAPI (see backend/app.py), so every
// emitted asset URL must be prefixed accordingly. The legacy vanilla app keeps
// serving from / until this one reaches parity.
export default defineConfig({
  plugins: [react()],
  base: '/next/',
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
