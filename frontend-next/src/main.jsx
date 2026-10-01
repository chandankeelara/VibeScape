import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import App from './App';
import { ToastProvider } from './state/ToastContext';
import * as player from './media/player';
import './styles/global.css';

// Media layer boots ONCE here, before React renders — never from a component
// effect. See src/media/README.md for why (StrictMode double-invokes effects,
// and createMediaElementSource is callable once per element for the lifetime
// of the page).
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
 * PlayerProvider — because the rig has to be reviewable before it is wired
 * into anything. Dynamically imported so the 60KB animation stylesheet stays
 * out of the main bundle until the mascot actually ships.
 *
 * Remove this branch along with BitLab.* when Bit goes live.
 */
if (new URLSearchParams(window.location.search).has('bit')) {
  import('./features/player/bit/BitLab').then(({ default: BitLab }) => {
    createRoot(document.getElementById('root')).render(
      <React.StrictMode><BitLab /></React.StrictMode>
    );
  });
} else {
  bootApp();
}
