/**
 * Recommendations fetch for the queue sidebar.
 *
 * Two data sources behind one hook:
 *   DJ off — GET /similar, plain vibe similarity for the current track. The
 *            result only depends on the anchor, so it is cached forever.
 *   DJ on  — POST /similar with the session weights. The result depends on the
 *            event buffer too, so the buffer signature is part of the key:
 *            every new play/skip/queue event invalidates the list and refetches
 *            with the updated taste vector.
 *
 * The 5s staleTime on the DJ path is the React Query equivalent of the legacy
 * `lastFetchSig` guard, which existed because a song ending fired two refreshes
 * in a row (one for the pick, one for the sidebar) against an identical buffer.
 */

import { useQuery } from '@tanstack/react-query';
import { usePlayer } from '../../state/PlayerContext';
import { apiKey } from '../../lib/vibe';
import { fetchDjPicks, fetchSimilar } from './dj';

export const RECS_LIMIT = 8;

export function useRecs({ djEnabled, events, signature }) {
  const { current, queue, recent } = usePlayer();
  const seedKey = apiKey(current);

  const query = useQuery({
    queryKey: ['queue-recs', seedKey, djEnabled ? 'dj' : 'vibe', djEnabled ? signature : null],
    enabled: Boolean(seedKey),
    staleTime: djEnabled ? 5000 : Infinity,
    retry: false,
    // DJ picks refresh on every event; blanking the list each time would strobe
    // the sidebar, so the previous picks stay up until the new ones land.
    // Vibe mode changes anchor only on a track change, where a loader is the
    // honest thing to show.
    placeholderData: djEnabled ? (prev) => prev : undefined,
    queryFn: () =>
      djEnabled
        ? fetchDjPicks(current, { queue, recent, current, events, limit: RECS_LIMIT })
        : fetchSimilar(current, { limit: RECS_LIMIT }),
  });

  return {
    recs: query.data || [],
    loading: query.isFetching && !(query.data || []).length,
    hasAnchor: Boolean(seedKey),
  };
}
