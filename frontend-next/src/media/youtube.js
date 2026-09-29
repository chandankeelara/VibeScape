/**
 * YouTube IFrame player. Ported from frontend/app.js:1964-2160.
 *
 * CRITICAL — read src/media/README.md before touching this:
 *   - `window.onYouTubeIframeAPIReady` is a ONE-SHOT global set by an external
 *     <script>. It fires once and never again, so it is assigned here at
 *     module level, not from a component.
 *   - `new YT.Player(mountId)` replaces a real DOM node with an <iframe>. That
 *     node must be rendered once and never re-created by React reconciliation,
 *     or playback dies mid-song. The React side renders a stable
 *     <div id="ytPlayer"> and never keys or conditionally unmounts it.
 */

export const MOUNT_ID = 'ytPlayer';

// The node YT.Player actually consumes. It is created HERE, imperatively,
// and is never rendered by React.
//
// YT.Player(id) REPLACES the element it is given with an <iframe>. If that
// element is one React rendered, React's fiber still points at a node that
// is no longer in the tree — and the next time a CONDITIONAL SIBLING mounts
// (ArtStage's "Loading video…" / "No video for this track" overlays sit just
// before the mount), React calls insertBefore(newNode, <the replaced div>)
// and the browser throws:
//
//   NotFoundError: Failed to execute 'insertBefore' on 'Node': The node
//   before which the new node is to be inserted is not a child of this node.
//
// So React owns the OUTER div (#ytPlayer, rendered with no children, so
// React never reconciles inside it) and YouTube destroys this inner one.
const TARGET_ID = 'ytPlayerTarget';

const YT_ERROR_MSG = {
  2: 'Invalid video reference',
  5: 'HTML5 player error — try refreshing',
  100: 'Video removed or made private',
  101: 'The uploader disabled embedding',
  150: 'The uploader disabled embedding',
};

const video = {
  apiRequested: false,
  apiReady: false,
  ready: false,
  player: null,
  pendingVideoId: null,
  currentVideoId: null,
  pollTimer: null,
};

// Host callbacks, wired by player.js so this module stays UI-agnostic.
let handlers = { onPlaying: () => {}, onPaused: () => {}, onEnded: () => {}, onError: () => {}, onTime: () => {} };

export function setHandlers(h) {
  handlers = { ...handlers, ...h };
}

export function init() {
  if (video.apiRequested) return;
  video.apiRequested = true;

  // One-shot global. Assign before the script loads.
  window.onYouTubeIframeAPIReady = () => {
    video.apiReady = true;
    createPlayer();
  };

  const s = document.createElement('script');
  s.src = 'https://www.youtube.com/iframe_api';
  s.async = true;
  document.head.appendChild(s);
}

/** Called once the mount node exists. Safe to call repeatedly. */
export function createPlayer() {
  if (video.player || !video.apiReady) return;
  const mount = document.getElementById(MOUNT_ID);
  if (!mount) return;
  // Fresh sacrificial child for YT to replace. React rendered the mount with
  // no children, so it has no fiber for this and will never touch it.
  let target = document.getElementById(TARGET_ID);
  if (!target) {
    target = document.createElement('div');
    target.id = TARGET_ID;
    mount.appendChild(target);
  }
  try {
    video.player = new window.YT.Player(TARGET_ID, {
      width: '100%',
      height: '100%',
      playerVars: { playsinline: 1, rel: 0, modestbranding: 1, iv_load_policy: 3 },
      events: {
        onReady: () => {
          video.ready = true;
          if (video.pendingVideoId) {
            const id = video.pendingVideoId;
            video.pendingVideoId = null;
            cueOrPlay(id);
          }
        },
        onStateChange: onStateChange,
        onError: (ev) => {
          const code = ev?.data;
          console.warn('[VibeScape] YouTube player error:', code);
          handlers.onError({ code, message: YT_ERROR_MSG[code] || 'Video unavailable', videoId: video.currentVideoId });
        },
      },
    });
  } catch (e) {
    console.warn('[VibeScape] YT.Player create failed:', e);
  }
}

function onStateChange(ev) {
  const YTS = window.YT?.PlayerState;
  if (!YTS) return;
  if (ev.data === YTS.PLAYING) {
    handlers.onPlaying();
    startPolling();
  } else if (ev.data === YTS.PAUSED || ev.data === YTS.BUFFERING) {
    handlers.onPaused();
    stopPolling();
  } else if (ev.data === YTS.ENDED) {
    stopPolling();
    handlers.onEnded();
  }
}

function startPolling() {
  stopPolling();
  video.pollTimer = setInterval(() => {
    if (!video.player) return;
    try {
      const duration = video.player.getDuration() || 0;
      const position = video.player.getCurrentTime() || 0;
      handlers.onTime({ position, duration });
    } catch { /* player torn down mid-poll */ }
  }, 250);
}

function stopPolling() {
  if (video.pollTimer) clearInterval(video.pollTimer);
  video.pollTimer = null;
}

export function cueOrPlay(videoId) {
  video.currentVideoId = videoId;
  if (!video.player || !video.ready) {
    video.pendingVideoId = videoId;
    createPlayer();
    return;
  }
  try {
    video.player.loadVideoById(videoId);
  } catch (e) {
    console.warn('[VibeScape] loadVideoById failed:', e);
  }
}

export function play() { try { video.player?.playVideo(); } catch {} }
export function pause() { try { video.player?.pauseVideo(); } catch {} }

export function stop() {
  stopPolling();
  try { video.player?.stopVideo(); } catch {}
  video.currentVideoId = null;
}

export function seekTo(seconds) {
  try { video.player?.seekTo(seconds, true); } catch {}
}

export function getDuration() {
  try { return video.player?.getDuration() || 0; } catch { return 0; }
}

export const getCurrentVideoId = () => video.currentVideoId;
