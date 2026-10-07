/**
 * Queue sidebar feature.
 *
 * <QueueSidebar /> renders the desktop sidebar, the mobile slide-up sheet, and
 * the mobile FAB/scrim that open it — mount it once inside the player page and
 * let its own media queries decide which of those is visible. It must be inside
 * <PlayerProvider> and a React Query provider.
 *
 * The DJ helpers are exported for tests and for anything that needs to record a
 * queue signal from outside this panel (a search "+ queue" button, say).
 */

export { default as QueueSidebar } from './QueueSidebar';
export { default } from './QueueSidebar';

export { useDj } from './useDj';
export { buildWeights, classifyTransition, excludeIds, upsertTrack } from './dj';
