/**
 * Queue sidebar feature.
 *
 * <QueueSidebar variant open onClose /> renders the queue as a panel, drawer
 * or sheet. It and the phone home list read shared state from
 * <QueueStateProvider>, which must wrap both (inside <PlayerProvider> and a
 * React Query provider). Mount the provider ONCE: it owns the only useDj.
 *
 * The DJ helpers are exported for tests and for anything that needs to record a
 * queue signal from outside this panel (a search "+ queue" button, say).
 */

export { default as QueueSidebar } from './QueueSidebar';
export { default } from './QueueSidebar';
export { default as QueueRow } from './QueueRow';
export { QueueStateProvider, useQueueState } from './QueueState';

export { useDj } from './useDj';
export { appendEvent, classifyTransition, excludeIds } from './dj';
