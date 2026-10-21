/**
 * Recommendations fetch for the queue sidebar.
 *
 * Two data sources behind one hook:
 *   DJ off — GET /similar, plain vibe similarity for the current track. The
 *            result only depends on the anchor, so it is cached forever.
 *   DJ on  — POST /similar with the event log. The result depends on the log
 *            too, so its signature is part of the key: every new play / skip /
 *            queue / pick event invalidates the list and refetches.
 *
 * Discovery widens the pool both fetchers draw from (whole catalogue, not the
 * library), so it is part of the key too: flipping it must never serve the
 * other pool's cached list.
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

/**
 * The one place the recs query key is built. QueueState's DJ autoplay picker
 * fetches through React Query under the same key so the sidebar attaches to
 * its request instead of firing a second POST; two hand-built copies of this
 * array would drift the first time either gained a field.
 */
export const recsQueryKey = ({ seedKey, djEnabled, discover, signature }) => [
  'queue-recs',
  seedKey,
  djEnabled ? 'dj' : 'vibe',
  discover ? 'discover' : 'library',
  djEnabled ? signature : null,
];

export function useRecs({ djEnabled, discover = false, events, signature }) {
  const { current, queue, getSeenIds } = usePlayer();
  const seedKey = apiKey(current);

  const query = useQuery({
    queryKey: recsQueryKey({ seedKey, djEnabled, discover, signature }),
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
        // getSeenIds() is read at fetch time, so the set is always current
        // even though it lives in a ref and never triggers a re-render.
        ? fetchDjPicks(current, { events, seen: getSeenIds(), queue, limit: RECS_LIMIT, discover })
        : fetchSimilar(current, { limit: RECS_LIMIT, discover }),
  });

  return {
    recs: query.data || [],
    loading: query.isFetching && !(query.data || []).length,
    hasAnchor: Boolean(seedKey),
  };
}
