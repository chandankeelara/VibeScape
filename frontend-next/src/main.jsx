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
        {/* basename matches the FastAPI mount point in backend/app.py */}
        <BrowserRouter basename="/next">
          <App />
        </BrowserRouter>
      </ToastProvider>
    </QueryClientProvider>
  </React.StrictMode>
);
