import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import App from './App';
import { ToastProvider } from './state/ToastContext';
import * as player from './media/player';
import { importWithReload, installPreloadErrorReload } from './lib/chunkReload';
import './styles/global.css';

// Media layer boots ONCE here, before React renders — never from a component
// effect. See src/media/README.md for why (StrictMode double-invokes effects,
// and createMediaElementSource is callable once per element for the lifetime
// of the page).
// A tab that outlived a deploy reloads once when a lazy chunk is gone.
installPreloadErrorReload();

function bootApp() {
  player.init();

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: (failureCount, error) => {
        // Don't retry auth failures — the session is gone, retrying just loops.
        if (error?.status === 401 || error?.status === 403) return false;
        return failureCount < 2;
      },
      refetchOnWindowFocus: false,
    },
  },
});

  createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        {/* React is the default UI and owns "/" — legacy lives at /legacy.
            Assets still emit under /next/ (vite base) so they never collide
            with the legacy app's files at the root. */}
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </ToastProvider>
    </QueryClientProvider>
  </React.StrictMode>
  );
}

/*
 * Mascot review page: /next/?bit
 *
 * Mounts BitLab INSTEAD of the app — no auth gate, no router, no
 * PlayerProvider — so a state can be pinned and judged without a track
 * playing. Dynamically imported, like the dock itself (DjLine lazy-loads it),
 * so the ~105KB animation stylesheet stays out of the main bundle.
 *
 * It is where a new animation or prop is judged at ship scale: section 01
 * uses the real DjLine and dock stylesheets.
 */
if (new URLSearchParams(window.location.search).has('bit')) {
  importWithReload(() => import('./features/player/bit/BitLab')).then(({ default: BitLab }) => {
    createRoot(document.getElementById('root')).render(
      <React.StrictMode><BitLab /></React.StrictMode>
    );
  });
} else {
  bootApp();
}
