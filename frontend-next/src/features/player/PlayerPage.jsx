import { useEffect, useState } from 'react';
import { usePlayer } from '../../state/PlayerContext';
import { SearchBar } from '../search';
import { QueueSidebar } from '../queue';
import { SyncJobProvider, SyncPill, useSyncJob } from '../sync';
import TopBar from './TopBar';
import ArtStage from './ArtStage';
import TrackMeta from './TrackMeta';
import Transport from './Transport';
import TheaterBar from './TheaterBar';
import MoodSlider from './MoodSlider';
import RecentTrail from './RecentTrail';
import useKeyboardShortcuts from './useKeyboardShortcuts';
import useTheaterMode from './useTheaterMode';
import usePictureInPicture from './usePictureInPicture';
import MiniPlayer from './MiniPlayer';
import BitDock from './bit/BitDock';
import styles from './PlayerPage.module.css';

export default function PlayerPage() {
  return (
    <SyncJobProvider>
      <PlayerStage />
    </SyncJobProvider>
  );
}

function PlayerStage() {
  const { current, fetchForVibe, vibe, mode } = usePlayer();
  const [metricsOpen, setMetricsOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [syncOpen, setSyncOpen] = useState(false);

  /*
   * Theater state lives HERE, not in ArtStage, because the layout it changes
   * belongs to this module: CSS Module class names are hashed, so ArtStage
   * physically cannot reach .shell or .stage, and a :global() selector aimed
   * at them would match nothing. The control is rendered down in ArtStage's
   * video chrome and reaches back up through these props.
   *
   * Passing `mode === 'video'` rather than gating on it here keeps the
   * preference alive across a trip through audio mode — see useTheaterMode.
   */
  const { theater, canTheater, morphing, toggleTheater, exitTheater } =
    useTheaterMode(mode === 'video');

  /*
   * The miniplayer window lives here for the same reason theater does: it is
   * an app-level surface, the control that opens it sits two components down,
   * and the thing it renders (MiniPlayer) has to be a child of the player
   * tree so it shares PlayerContext. Owning it here keeps ONE window and ONE
   * source of truth; a hook instance per button would mean two.
   *
   * `pipSupported` is false on Firefox, Safari and every mobile browser, and
   * the control is then not rendered at all.
   */
  const { supported: pipSupported, pipWindow, toggle: togglePip } = usePictureInPicture();
  const pipProps = { pipSupported, pipOpen: !!pipWindow, onTogglePip: togglePip };

  useKeyboardShortcuts({
    onToggleMetrics: () => setMetricsOpen((v) => !v),
    onToggleHelp: () => setHelpOpen((v) => !v),
  });

  // Seed the first track once, on mount.
  useEffect(() => {
    if (!current) fetchForVibe(vibe);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useAutoMinimise(syncOpen, () => setSyncOpen(false));

  // The shell itself does NOT change in theater: it keeps both columns, so the
  // queue rail stays flanking on the right. Only the stage re-flows.
  // .shellMorphing is transient and drives the settle animation.
  return (
    <div className={`${styles.shell} ${morphing ? styles.shellMorphing : ''}`}>
      {/* Grid slots mirror the legacy body grid (frontend/style.css:56-82):
          topbar and search span both columns, the stage takes column 1, and
          the queue sidebar is a fixed 320px column 2. */}
      <div className={styles.topSlot}>
        <TopBar onOpenSync={() => setSyncOpen(true)} onToggleHelp={() => setHelpOpen((v) => !v)} />
      </div>
      <div className={styles.searchSlot}>
        <SearchBar />
      </div>

      <main className={`${styles.stage} ${theater ? styles.stageTheater : ''}`}>
        {/* Three flow columns, matching legacy at >=1024px (style.css:1891):
            [trail 64px] [art 1.05fr] [meta 1fr]. The trail is right-aligned
            inside its own column so it sits immediately left of the hero card.
            Placement lives here rather than in RecentTrail's CSS Module — its
            class names are hashed, so a :global() selector matches nothing. */}
        <div className={styles.trailSlot}>
          <RecentTrail />
        </div>
        <ArtStage
          theater={theater}
          canTheater={canTheater}
          onToggleTheater={toggleTheater}
          onExitTheater={exitTheater}
        />
        {/*
          * Third child either way, so ArtStage stays at index 1 and React
          * never touches the subtree holding #ytPlayer. Swapping a SIBLING
          * cannot reparent the iframe; re-ordering or keying it would.
          *
          * In theater the whole meta column is replaced — mood slider, vibe
          * meter and chips included — by one strip, and the video takes the
          * height that frees. Normal mode is unchanged.
          */}
        {theater ? (
          <TheaterBar
            className={styles.barSlot}
            theater={theater}
            onToggleTheater={toggleTheater}
            {...pipProps}
          />
        ) : (
          <div className={styles.metaCol}>
            <TrackMeta onShowMetrics={() => setMetricsOpen(true)} />
            <hr className={styles.divider} aria-hidden="true" />
            <Transport {...pipProps} />
            <MoodSlider />
          </div>
        )}
      </main>

      <div className={styles.sidebarSlot}>
        <QueueSidebar />
      </div>

      {/* Fixed to the bottom of the stage column and pointer-events:none, so
          it sits outside the grid and cannot reflow or block anything. */}
      <BitDock theater={theater} />

      <SyncPill onOpen={() => setSyncOpen(true)} />

      {/* Portals into the PiP window's document. Rendered only while that
          window exists, so closing it (by the user, by the browser, or by the
          toggle) unmounts the portal — there is nothing left to dangle. */}
      {pipWindow && <MiniPlayer pipWindow={pipWindow} />}

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

/**
 * Gets the sync window out of the way the moment music can play.
 *
 * The import keeps running in the background and the pill carries it from
 * there, so there is nothing left for the modal to do — and every extra
 * second it stays up is a second the user is watching a progress bar instead
 * of listening. The provider raises the flag once per job and this consumes
 * it, so re-opening the window mid-import does not slam it shut again.
 */
function useAutoMinimise(isOpen, close) {
  const job = useSyncJob();
  const flagged = job ? job.justBecamePlayable : false;
  const clear = job ? job.clearPlayableFlag : null;

  useEffect(() => {
    if (!flagged) return;
    if (isOpen) close();
    clear?.();
  }, [flagged, isOpen, close, clear]);
}
