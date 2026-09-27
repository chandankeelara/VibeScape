import { useEffect, useState } from 'react';
import { usePlayer } from '../../state/PlayerContext';
import { SearchBar } from '../search';
import { QueueSidebar } from '../queue';
import TopBar from './TopBar';
import ArtStage from './ArtStage';
import TrackMeta from './TrackMeta';
import Transport from './Transport';
import MoodSlider from './MoodSlider';
import RecentTrail from './RecentTrail';
import useKeyboardShortcuts from './useKeyboardShortcuts';
import styles from './PlayerPage.module.css';

export default function PlayerPage() {
  const { current, fetchForVibe, vibe } = usePlayer();
  const [metricsOpen, setMetricsOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [syncOpen, setSyncOpen] = useState(false);

  useKeyboardShortcuts({
    onToggleMetrics: () => setMetricsOpen((v) => !v),
    onToggleHelp: () => setHelpOpen((v) => !v),
  });

  // Seed the first track once, on mount.
  useEffect(() => {
    if (!current) fetchForVibe(vibe);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className={styles.shell}>
      {/* Grid slots mirror the legacy body grid (frontend/style.css:56-82):
          topbar and search span both columns, the stage takes column 1, and
          the queue sidebar is a fixed 320px column 2. */}
      <div className={styles.topSlot}>
        <TopBar onOpenSync={() => setSyncOpen(true)} onToggleHelp={() => setHelpOpen((v) => !v)} />
      </div>
      <div className={styles.searchSlot}>
        <SearchBar />
      </div>

      <main className={styles.stage}>
        {/* Three flow columns, matching legacy at >=1024px (style.css:1891):
            [trail 64px] [art 1.05fr] [meta 1fr]. The trail is right-aligned
            inside its own column so it sits immediately left of the hero card.
            Placement lives here rather than in RecentTrail's CSS Module — its
            class names are hashed, so a :global() selector matches nothing. */}
        <div className={styles.trailSlot}>
          <RecentTrail />
        </div>
        <ArtStage />
        <div className={styles.metaCol}>
          <TrackMeta onShowMetrics={() => setMetricsOpen(true)} />
          <hr className={styles.divider} aria-hidden="true" />
          <Transport />
          <MoodSlider />
        </div>
      </main>

      <div className={styles.sidebarSlot}>
        <QueueSidebar />
      </div>

      <LazyPanels
        metricsOpen={metricsOpen}
        onCloseMetrics={() => setMetricsOpen(false)}
        helpOpen={helpOpen}
        onCloseHelp={() => setHelpOpen(false)}
        syncOpen={syncOpen}
        onCloseSync={() => setSyncOpen(false)}
      />
    </div>
  );
}

/**
 * The sync modal and the metrics/help panels are mounted only when opened.
 * They're the least-used surfaces and pull in their own queries, so keeping
 * them out of the initial tree keeps first paint cheap.
 */
function LazyPanels({ metricsOpen, onCloseMetrics, helpOpen, onCloseHelp, syncOpen, onCloseSync }) {
  const [mods, setMods] = useState(null);
  const anyOpen = metricsOpen || helpOpen || syncOpen;

  useEffect(() => {
    if (!anyOpen || mods) return;
    Promise.all([
      import('../sync').catch(() => ({})),
      import('../panels').catch(() => ({})),
    ]).then(([sync, panels]) => setMods({ ...sync, ...panels }));
  }, [anyOpen, mods]);

  if (!anyOpen || !mods) return null;
  const { SyncModal, MetricsPanel, HelpPopover } = mods;

  return (
    <>
      {syncOpen && SyncModal && <SyncModal open onClose={onCloseSync} />}
      {metricsOpen && MetricsPanel && <MetricsPanel open onClose={onCloseMetrics} />}
      {helpOpen && HelpPopover && <HelpPopover open onClose={onCloseHelp} />}
    </>
  );
}
