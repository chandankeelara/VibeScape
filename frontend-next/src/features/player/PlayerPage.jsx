import { useCallback, useEffect, useState } from 'react';
import { usePlayer } from '../../state/PlayerContext';
import { useApplyTheme } from '../../state/ThemeContext';
import { applyAccent } from '../../lib/vibe';
import { importWithReload } from '../../lib/chunkReload';
import useMediaQuery, { PHONE, WIDE } from '../../lib/useMediaQuery';
import { QueueSidebar, QueueStateProvider } from '../queue';
import { SyncJobProvider, SyncPill, useSyncJob } from '../sync';
import { LyricsProvider } from '../lyrics';
import TopBar from './TopBar';
import NowPlaying from './NowPlaying';
import PhoneHome from './PhoneHome';
import PlayerBar from './PlayerBar';
import useKeyboardShortcuts from './useKeyboardShortcuts';
import useFirstTrack from './useFirstTrack';
import useTheaterMode from './useTheaterMode';
import usePictureInPicture from './usePictureInPicture';
import MiniPlayer from './MiniPlayer';
import styles from './PlayerPage.module.css';

/**
 * The player, in the S2 "Familiar" frame (design/premium-player/s2-familiar.html):
 *
 *   desktop  top bar · [now-playing panel | queue panel] · player bar
 *   tablet   top bar · now-playing panel · player bar; queue is a drawer
 *   phone    top bar · home list · floating mini-player; now-playing is a
 *            full-screen view and the queue a bottom sheet
 *
 * The now-playing panel and the phone's now-playing screen are ONE element
 * restyled, and so are the player bar and the mini-player — which is what
 * keeps the YouTube iframe from ever moving in the DOM.
 */
export default function PlayerPage() {
  return (
    <SyncJobProvider>
      <QueueStateProvider>
        <LyricsProvider>
          <PlayerStage />
        </LyricsProvider>
      </QueueStateProvider>
    </SyncJobProvider>
  );
}

const QUEUE_KEY = 'vs.queueOpen';

function readQueuePref() {
  try {
    return localStorage.getItem(QUEUE_KEY) !== '0';
  } catch {
    return true;
  }
}

function PlayerStage() {
  const { vibe, mode } = usePlayer();
  const [metricsOpen, setMetricsOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [syncOpen, setSyncOpen] = useState(false);

  const isPhone = useMediaQuery(PHONE);
  const isWide = useMediaQuery(WIDE);
  const queueVariant = isPhone ? 'sheet' : isWide ? 'panel' : 'drawer';

  // The desktop column remembers whether it was open; the drawer and the
  // sheet always start closed — they cover content.
  const [panelOpen, setPanelOpen] = useState(readQueuePref);
  const [overlayOpen, setOverlayOpen] = useState(false);
  const [npOpen, setNpOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const queueOpen = queueVariant === 'panel' ? panelOpen : overlayOpen;

  useEffect(() => {
    try { localStorage.setItem(QUEUE_KEY, panelOpen ? '1' : '0'); } catch { /* not remembered */ }
  }, [panelOpen]);

  // Crossing a breakpoint must not strand an overlay half-open.
  useEffect(() => { setOverlayOpen(false); }, [queueVariant]);
  useEffect(() => { if (!isPhone) { setNpOpen(false); setSearchOpen(false); } }, [isPhone]);

  const toggleQueue = useCallback(() => {
    if (queueVariant === 'panel') setPanelOpen((v) => !v);
    else setOverlayOpen((v) => !v);
  }, [queueVariant]);
  const closeQueue = useCallback(() => setOverlayOpen(false), []);

  // Phone: the page behind a full-screen view or a sheet must not scroll.
  useEffect(() => {
    if (!isPhone || !(npOpen || overlayOpen || searchOpen)) return undefined;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = prev; };
  }, [isPhone, npOpen, overlayOpen, searchOpen]);

  useApplyTheme(vibe, applyAccent);

  /*
   * Theater: a layout class on the now-playing panel, owned here because
   * the control sits in that panel's header and the preference must survive
   * a trip through audio mode (see useTheaterMode).
   */
  const { theater, canTheater, toggleTheater } = useTheaterMode(mode === 'video');

  /*
   * The miniplayer window is app-level: ONE window, ONE source of truth, and
   * MiniPlayer must render inside the player tree to share PlayerContext.
   * `pipSupported` is false on Firefox, Safari and every mobile browser, and
   * the control is then not rendered at all.
   */
  const {
    supported: pipSupported,
    pipWindow,
    autoEnabled: pipAuto,
    toggle: togglePip,
  } = usePictureInPicture();

  useKeyboardShortcuts({
    onToggleMetrics: () => setMetricsOpen((v) => !v),
    onToggleHelp: () => setHelpOpen((v) => !v),
    onToggleQueue: toggleQueue,
  });

  useFirstTrack();

  useAutoMinimise(syncOpen, () => setSyncOpen(false));

  const appCls = [
    styles.app,
    queueVariant === 'panel' && !panelOpen ? styles.queueClosed : '',
  ].filter(Boolean).join(' ');

  return (
    <div className={appCls}>
      <TopBar
        onOpenSync={() => setSyncOpen(true)}
        onToggleHelp={() => setHelpOpen((v) => !v)}
        searchOpen={searchOpen}
        onSearchOpen={() => setSearchOpen(true)}
        onSearchClose={() => setSearchOpen(false)}
      />

      <div className={styles.content}>
        <PhoneHome bitPaused={!isPhone || npOpen} />
        <NowPlaying
          theater={theater}
          canTheater={canTheater}
          onToggleTheater={toggleTheater}
          phoneOpen={npOpen}
          onPhoneClose={() => setNpOpen(false)}
          onOpenQueue={() => setOverlayOpen(true)}
          onShowMetrics={() => setMetricsOpen(true)}
        />
        <QueueSidebar variant={queueVariant} open={queueOpen} onClose={closeQueue} />
      </div>

      <PlayerBar
        queueOpen={queueOpen}
        onToggleQueue={toggleQueue}
        onOpenNowPlaying={() => { if (isPhone) setNpOpen(true); }}
        pipSupported={pipSupported && !isPhone}
        pipOpen={!!pipWindow}
        pipAuto={pipAuto}
        onTogglePip={togglePip}
      />

      <SyncPill onOpen={() => setSyncOpen(true)} />

      {/* Portals into the PiP window's document. Rendered only while that
          window exists, so closing it unmounts the portal. */}
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
      importWithReload(() => import('../sync'), {}),
      importWithReload(() => import('../panels'), {}),
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
 * there, so there is nothing left for the modal to do. The provider raises
 * the flag once per job and this consumes it, so re-opening the window
 * mid-import does not slam it shut again.
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
