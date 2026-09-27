import { useEffect, useState } from 'react';

/**
 * Trailing-edge debounce for a value. The search input updates on every
 * keystroke (so the field stays responsive) but the React Query key only
 * changes once typing settles — which is what actually gates the network.
 *
 * 220ms matches the legacy `scheduleSearch()` timer.
 */
export function useDebouncedValue(value, delay = 220) {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(id);
  }, [value, delay]);

  return debounced;
}
