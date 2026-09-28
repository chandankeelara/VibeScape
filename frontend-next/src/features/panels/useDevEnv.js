/**
 * Deployment env from /api/client-config ({ env: 'dev' | 'prod', debug }).
 *
 * The legacy app stamped this onto `body[data-env]` and let CSS hide the debug
 * affordances (frontend/app.js:19-37). In React the gate is a conditional
 * render instead — a CSS-only gate still ships the panel's markup, and this
 * panel prints an OAuth token.
 *
 * Defaults to prod-on-unknown: a failed config call must not reveal the panel.
 */

import { useQuery } from '@tanstack/react-query';
import * as api from '../../lib/api';

export function useDevEnv() {
  const { data } = useQuery({
    queryKey: ['client-config'],
    queryFn: api.clientConfig,
    staleTime: Infinity,
  });
  return data?.env === 'dev';
}
