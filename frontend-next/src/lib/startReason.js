/**
 * Classify why a track started, for the DJ line. Pure; see startInfo in
 * state/PlayerContext.jsx. UI only — never sent as telemetry.
 *   ended  — the previous track finished and the DJ / autoplay followed
 *   skip   — the user moved on and the DJ / autoplay chose the replacement
 *   queue  — came off Up next (by hand, or after a track finished)
 *   search — played from search
 *   pick   — chosen off a list (a recommendation)
 *   vibe   — a vibe-band pull after the slider moved (and the first track)
 */
export function startKind({ source, trigger, ended }) {
  // Only the automatic followers honour `ended`: if the follow-up fetch
  // failed, a stale flag must not relabel the user's next search or pick.
  if (ended && (source === 'dj' || source === 'autoplay')) return 'ended';
  if (source === 'queue') return 'queue';
  if (source === 'search') return 'search';
  if (source === 'dj' || source === 'autoplay') return trigger === 'vibe_change' ? 'vibe' : 'skip';
  return 'pick';
}
