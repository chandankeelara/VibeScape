(function () {
  const API_BASE = (function () {
    const host = window.location.hostname;
    if (window.location.protocol === 'file:') return 'http://localhost:8000';
    if (host === '' || host === 'localhost' || host === '127.0.0.1') {
      if (window.location.port && window.location.port !== '8000') {
        return `http://${host}:8000`;
      }
    }
    return '';
  })();

  const $ = (id) => document.getElementById(id);

  const el = {
    art: $('art'),
    artImg: $('artImg'),
    artGlow: $('artGlow'),
    title: $('title'),
    artist: $('artist'),
    artistSep: $('artistSep'),
    album: $('album'),
    genreLine: $('genreLine'),
    genreEm: $('genreEm'),
    recentTrail: $('recentTrail'),
    chipMood: $('chipMood'),
    chipGenre: $('chipGenre'),
    chipVibe: $('chipVibe'),
    meta: document.querySelector('.meta'),
    btnHelp: $('btnHelp'),
    helpPopover: $('helpPopover'),
    progress: $('progress'),
    progressFill: $('progressFill'),
    progressThumb: $('progressThumb'),
    progressTimeChip: $('progressTimeChip'),
    tCur: $('tCur'),
    tTot: $('tTot'),
    btnPlay: $('btnPlay'),
    btnPrev: $('btnPrev'),
    btnNext: $('btnNext'),
    slider: $('slider'),
    sliderFill: $('sliderFill'),
    sliderThumb: $('sliderThumb'),
    vibeNum: $('vibeNum'),
    vibeMood: $('vibeMood'),
    ticks: document.querySelectorAll('.ticks span'),
    toastContainer: $('toastContainer'),
    player: $('player'),
    btnSpotifySignIn: $('btnSpotifySignIn'),
    btnSpotifySignOut: $('btnSpotifySignOut'),
    btnSpotifySync: $('btnSpotifySync'),
    btnSpotifyDebug: $('btnSpotifyDebug'),
    spSigned: $('spSigned'),
    spName: $('spName'),
    chipSource: $('chipSource'),
    btnVerify: $('btnVerify'),
    verifyTip: $('verifyTip'),
    verifyTipLabel: $('verifyTipLabel'),
    verifyTipUrl: $('verifyTipUrl'),
    verifyOverlay: $('verifyOverlay'),
    verifyOverlaySub: $('verifyOverlaySub'),
    verifyAudio: $('verifyAudio'),
    btnMetrics: $('btnMetrics'),
    metricsPanel: $('metricsPanel'),
    metricsPanelBody: $('metricsPanelBody'),
    metricsLoading: $('metricsLoading'),
    metricsEmpty: $('metricsEmpty'),
    metricsEmptyMsg: $('metricsEmptyMsg'),
    metricsContent: $('metricsContent'),
    btnMetricsClose: $('btnMetricsClose'),
    debugFeatActivation: $('debugFeatActivation'),
    debugFeatValence: $('debugFeatValence'),
    debugFeatAcousticness: $('debugFeatAcousticness'),
    debugFeatTempo: $('debugFeatTempo'),
    debugFeatEnergy: $('debugFeatEnergy'),
    debugPanel: $('debugPanel'),
    debugUser: $('debugUser'),
    debugExpiry: $('debugExpiry'),
    debugScopes: $('debugScopes'),
    debugToken: $('debugToken'),
    debugOutput: $('debugOutput'),
    debugClassSource: $('debugClassSource'),
    debugClassUrl: $('debugClassUrl'),
    btnDebugClose: $('btnDebugClose'),
    btnDebugCopy: $('btnDebugCopy'),
    btnDebugCopyUrl: $('btnDebugCopyUrl'),
    btnDebugTestMe: $('btnDebugTestMe'),
    syncModal: $('syncModal'),
    syncModalBackdrop: $('syncModalBackdrop'),
    btnSyncClose: $('btnSyncClose'),
    btnSyncCancel: $('btnSyncCancel'),
    btnSyncStart: $('btnSyncStart'),
    btnSyncRetry: $('btnSyncRetry'),
    syncViewLoading: $('syncViewLoading'),
    syncViewError: $('syncViewError'),
    syncViewSelect: $('syncViewSelect'),
    syncViewProgress: $('syncViewProgress'),
    syncViewComplete: $('syncViewComplete'),
    syncErrorMsg: $('syncErrorMsg'),
    syncLiked: $('syncLiked'),
    syncTop: $('syncTop'),
    syncLikedCount: $('syncLikedCount'),
    syncTopCount: $('syncTopCount'),
    syncPlaylistList: $('syncPlaylistList'),
    syncBarFill: $('syncBarFill'),
    syncProgressPct: $('syncProgressPct'),
    syncProgressCounts: $('syncProgressCounts'),
    syncCurrentTrack: $('syncCurrentTrack'),
    syncStatMatched: $('syncStatMatched'),
    syncStatPreview: $('syncStatPreview'),
    syncStatNoPreview: $('syncStatNoPreview'),
    syncStatSkipped: $('syncStatSkipped'),
    syncCompleteSummary: $('syncCompleteSummary'),
    syncFooterMeta: $('syncFooterMeta'),
    syncModalFooter: $('syncModalFooter')
  };

  const SPOTIFY_SCOPE = 'streaming user-read-email user-read-private user-library-read playlist-read-private playlist-read-collaborative user-top-read';
  const SPOTIFY_SCOPE_VERSION = 2;

  const MOODS = [
    { name: 'sleep',  min: 0,  max: 20,  a: [76, 91, 138],  b: [91, 127, 189] },
    { name: 'chill',  min: 20, max: 40,  a: [0, 180, 216],  b: [34, 193, 227] },
    { name: 'steady', min: 40, max: 60,  a: [124, 58, 237], b: [167, 139, 250] },
    { name: 'hype',   min: 60, max: 80,  a: [236, 72, 153], b: [244, 63, 94] },
    { name: 'beast',  min: 80, max: 100, a: [249, 115, 22], b: [220, 38, 38] }
  ];

  const state = {
    vibe: 50,
    current: null,
    // recent: [{ apple_id, spotify_id, artwork_url, title, artist, ...full track }, ...]
    // Newest last. Cap at RECENT_MAX. Used both to exclude re-fetches and to render the trail.
    recent: [],
    fetchToken: 0,
    isSeeking: false,
    firstInteraction: false,
    // Feature blobs keyed by apple_id (fallback: spotify_id). Populated lazily
    // when the metrics panel opens for a track. Never cleared during a session.
    featureCache: {},
    // Filter model — wide-open defaults; only appended to /api/tracks/random
    // when non-default. Backend ignores unknown params, safe to send early.
    filters: {
      vibe_min: 0,
      vibe_max: 100,
      valence_min: 0,
      valence_max: 100,
      acousticness_min: 0,
      acousticness_max: 100,
      mood: null
    }
  };
  const RECENT_MAX = 5;

  const FILTER_DEFAULTS = {
    vibe_min: 0, vibe_max: 100,
    valence_min: 0, valence_max: 100,
    acousticness_min: 0, acousticness_max: 100,
    mood: null
  };

  const spotify = {
    clientId: '',
    redirectUri: '',
    accessToken: '',
    refreshToken: '',
    expiresAt: 0,
    displayName: '',
    isPremium: false,
    player: null,
    deviceId: '',
    sdkReady: false,
    sdkLoaded: false,
    lastState: null,
    positionMs: 0,
    durationMs: 0,
    positionAt: 0,
    pollTimer: null,
    verifierKey: 'spotify_pkce_verifier',
    tokenKey: 'spotify_access_token',
    refreshKey: 'spotify_refresh_token',
    expiryKey: 'spotify_token_expiry',
    profileKey: 'spotify_profile',
    scopeVersionKey: 'spotify_scope_version'
  };

  const sync = {
    open: false,
    library: null,
    fetching: false,
    jobId: null,
    pollTimer: null,
    stage: 'idle' // idle | loading | error | select | progress | complete
  };

  function moodFor(vibe) {
    for (const m of MOODS) if (vibe >= m.min && vibe < m.max) return m;
    return MOODS[MOODS.length - 1];
  }

  function lerp(a, b, t) { return a + (b - a) * t; }
  function rgb([r,g,b]) { return `rgb(${Math.round(r)}, ${Math.round(g)}, ${Math.round(b)})`; }

  function accentFor(vibe) {
    const m = moodFor(vibe);
    const span = m.max - m.min;
    const t = span > 0 ? Math.min(1, Math.max(0, (vibe - m.min) / span)) : 0;
    const a = m.a.map((c, i) => lerp(c, m.b[i], t));
    const b = m.b;
    return { a: rgb(a), b: rgb(b), mood: m.name };
  }

  function applyAccent(vibe) {
    const { a, b, mood } = accentFor(vibe);
    document.documentElement.style.setProperty('--vibe-accent', a);
    document.documentElement.style.setProperty('--vibe-accent-2', b);
    el.vibeMood.textContent = mood;
    el.chipMood.textContent = mood;
    el.ticks.forEach((t) => {
      t.classList.toggle('active', t.dataset.mood === mood);
    });
  }

  function fmtTime(sec) {
    if (!isFinite(sec) || sec < 0) sec = 0;
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${s < 10 ? '0' : ''}${s}`;
  }

  function setSourcePill(source) {
    // source: 'spotify' | 'preview' | null
    if (!el.chipSource) return;
    if (!source) {
      el.chipSource.hidden = true;
      el.chipSource.dataset.source = 'none';
      el.chipSource.textContent = '';
      return;
    }
    el.chipSource.hidden = false;
    el.chipSource.dataset.source = source;
    el.chipSource.textContent = source === 'spotify' ? 'SPOTIFY' : 'PREVIEW';
  }

  function updateSliderVisual(vibe) {
    const pct = vibe;
    el.sliderFill.style.width = pct + '%';
    el.sliderThumb.style.left = pct + '%';
    el.vibeNum.textContent = vibe;
    el.chipVibe.textContent = 'vibe ' + vibe;
    el.slider.setAttribute('aria-valuenow', String(vibe));
  }

  // ===== Toast notifications =====
  const TOAST_MAX_VISIBLE = 3;
  const TOAST_DEFAULT_DURATION = 5000;
  const TOAST_VARIANTS = { info: 1, success: 1, error: 1, warning: 1 };
  const activeToasts = [];

  function toast(message, variant, options) {
    if (!message) return { dismiss: () => {} };
    variant = TOAST_VARIANTS[variant] ? variant : 'info';
    options = options || {};

    const container = el.toastContainer;
    if (!container) return { dismiss: () => {} };

    // Cap at TOAST_MAX_VISIBLE — dismiss oldest if we'd exceed
    while (activeToasts.length >= TOAST_MAX_VISIBLE) {
      const oldest = activeToasts[0];
      oldest.dismiss();
    }

    const persistent = variant === 'error'
      ? (options.duration === undefined ? true : options.duration === 0)
      : (options.duration === 0);
    const duration = options.duration !== undefined
      ? options.duration
      : (variant === 'error' ? 0 : TOAST_DEFAULT_DURATION);

    const isAlert = (variant === 'error' || variant === 'warning');

    const node = document.createElement('div');
    node.className = 'toast';
    node.dataset.variant = variant;
    node.setAttribute('role', isAlert ? 'alert' : 'status');
    node.setAttribute('aria-live', isAlert ? 'assertive' : 'polite');

    const msg = document.createElement('div');
    msg.className = 'toast-message';
    msg.textContent = message;
    node.appendChild(msg);

    // Optional action button (item #10). Sits between message and close X.
    let actionBtn = null;
    if (options.action && options.action.label) {
      actionBtn = document.createElement('button');
      actionBtn.type = 'button';
      actionBtn.className = 'toast-action';
      actionBtn.textContent = options.action.label;
      actionBtn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        try {
          if (typeof options.action.onClick === 'function') options.action.onClick();
        } finally {
          // Dismiss after action fires unless keepOpen was set explicitly
          if (!options.action.keepOpen) dismiss();
        }
      });
      node.appendChild(actionBtn);
    }

    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'toast-close';
    closeBtn.setAttribute('aria-label', 'Dismiss notification');
    closeBtn.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
    node.appendChild(closeBtn);

    container.appendChild(node);

    let dismissed = false;
    let autoTimer = null;

    function dismiss() {
      if (dismissed) return;
      dismissed = true;
      if (autoTimer) { clearTimeout(autoTimer); autoTimer = null; }
      const idx = activeToasts.indexOf(entry);
      if (idx >= 0) activeToasts.splice(idx, 1);
      node.classList.remove('toast-enter');
      node.classList.add('toast-exit');
      const remove = () => { if (node.parentNode) node.parentNode.removeChild(node); };
      const reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      const removeDelay = reduced ? 150 : 200;
      setTimeout(remove, removeDelay + 20);
    }

    closeBtn.addEventListener('click', dismiss);

    const entry = { node, dismiss };
    activeToasts.push(entry);

    // Trigger enter animation on next frame
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        node.classList.add('toast-enter');
      });
    });

    if (!persistent && duration > 0) {
      autoTimer = setTimeout(dismiss, duration);
    }

    return { dismiss };
  }

  // Expose globally so other scripts / console can use it
  window.toast = toast;

  async function checkHealth() {
    try {
      const r = await fetch(API_BASE + '/api/health');
      if (!r.ok) throw new Error('bad');
      const j = await r.json();
      if (!j.track_count || j.track_count === 0) {
        toast('Backend online, but no tracks loaded yet.', 'info');
      }
    } catch (e) {
      toast('Backend unreachable. Start it at ' + (API_BASE || 'this origin') + '.', 'error');
    }
  }

  function pushRecent(track) {
    if (!track || !track.apple_id) return;
    // Dedup: if same apple_id already present, remove old entry so this becomes freshest
    state.recent = state.recent.filter((r) => r.apple_id !== track.apple_id);
    state.recent.push({
      apple_id: track.apple_id,
      spotify_id: track.spotify_id || '',
      artwork_url: track.artwork_url || '',
      title: track.title || '',
      artist: track.artist || '',
      album: track.album || '',
      genre: track.genre || '',
      duration_ms: track.duration_ms || 0
    });
    while (state.recent.length > RECENT_MAX) state.recent.shift();
    renderRecentTrail();
  }

  function recentExcludeIds() {
    // Newest first is convention for exclude; backend just needs IDs, order agnostic.
    // We include the currently-playing track's id too so the next fetch never repeats it.
    return state.recent.map((r) => r.apple_id);
  }

  function renderRecentTrail() {
    if (!el.recentTrail) return;
    // Build the display order: currently-playing track at the TOP with an
    // accent ring (persistent breadcrumb), followed by prior tracks newest-first.
    const currentId = state.current && state.current.apple_id;
    const prior = state.recent.filter((r) => r.apple_id !== currentId).slice().reverse();
    const ordered = [];
    if (state.current) ordered.push({ track: state.current, isCurrent: true });
    prior.slice(0, 4).forEach((track) => ordered.push({ track, isCurrent: false }));
    if (!ordered.length) {
      el.recentTrail.hidden = true;
      el.recentTrail.innerHTML = '';
      return;
    }
    el.recentTrail.hidden = false;
    el.recentTrail.innerHTML = '';
    ordered.forEach(({ track, isCurrent }) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'recent-trail-item' + (isCurrent ? ' recent-trail-current' : '');
      if (isCurrent) {
        btn.title = 'Restart current track (' + (track.title || 'Untitled') + ')';
        btn.setAttribute('aria-label', 'Restart current track');
        btn.setAttribute('aria-current', 'true');
      } else {
        btn.title = (track.title || 'Untitled') + (track.artist ? ' — ' + track.artist : '');
        btn.setAttribute('aria-label', 'Replay ' + (track.title || 'track') + (track.artist ? ' by ' + track.artist : ''));
      }
      const img = document.createElement('img');
      img.alt = '';
      img.src = track.artwork_url || '';
      btn.appendChild(img);
      btn.addEventListener('click', () => {
        state.firstInteraction = true;
        if (isCurrent) {
          // Different action for the current-track affordance: seek to 0
          // instead of re-loading (which would refetch/reset the SDK state).
          seekTo(0);
          // If paused, kick playback back on
          if (sdkActive() && spotify.lastState && spotify.lastState.paused) {
            try { spotify.player.resume(); } catch (_) {}
          } else if (el.player.paused && el.player.src) {
            const p = el.player.play();
            if (p && typeof p.catch === 'function') p.catch(() => {});
          }
        } else {
          loadTrack(track);
        }
      });
      el.recentTrail.appendChild(btn);
    });
  }

  function sdkActive() {
    return !!(spotify.accessToken && spotify.isPremium && spotify.player && spotify.deviceId);
  }

  async function fetchTrack(vibe) {
    const token = ++state.fetchToken;
    const params = new URLSearchParams({
      vibe: String(vibe),
      tolerance: '12'
    });
    const excl = recentExcludeIds();
    if (excl.length) params.set('exclude_ids', excl.join(','));
    // Attach any non-default filters. Backend currently ignores unknown params,
    // so this is safe to send even before backend implements filter routing.
    Object.keys(FILTER_DEFAULTS).forEach((k) => {
      const v = state.filters[k];
      if (v === null || v === undefined) return;
      if (v === FILTER_DEFAULTS[k]) return;
      params.set(k, String(v));
    });

    el.art.classList.add('loading');
    el.meta.classList.add('loading');

    try {
      const r = await fetch(API_BASE + '/api/tracks/random?' + params.toString());
      if (token !== state.fetchToken) return;

      if (r.status === 404) {
        el.art.classList.remove('loading');
        el.meta.classList.remove('loading');
        el.art.classList.add('empty');
        el.artImg.removeAttribute('src');
        el.title.textContent = 'No tracks in this vibe range';
        el.artist.textContent = 'Try a different setting';
        if (el.album) el.album.textContent = '';
        if (el.artistSep) el.artistSep.hidden = true;
        if (el.genreLine) el.genreLine.hidden = true;
        if (el.chipGenre) el.chipGenre.hidden = true;
        state.current = null;
        setSourcePill(null);
        stopPlayback();
        updateVerifyChip(null);
        updateMetricsChip(null);
        if (debugPanel && debugPanel.open) renderDebugPanel();
        if (metricsPanel && metricsPanel.open) loadMetricsForCurrent();
        return;
      }
      if (!r.ok) throw new Error('fetch failed');

      const t = await r.json();
      if (token !== state.fetchToken) return;
      loadTrack(t);
    } catch (e) {
      if (token !== state.fetchToken) return;
      el.art.classList.remove('loading');
      el.meta.classList.remove('loading');
      toast('Could not load track. Check the backend.', 'error');
    }
  }

  function loadTrack(t) {
    // Loading a new track always cancels any classification-audio verify session
    if (verify.active) stopVerify({ resume: false });
    state.current = t;
    pushRecent(t);
    updateVerifyChip(t);
    updateMetricsChip(t);
    if (debugPanel && debugPanel.open) renderDebugPanel();
    // If the metrics panel is open, refresh it for the new track
    if (metricsPanel && metricsPanel.open) {
      metricsPanel.forTrackId = trackKey(t);
      loadMetricsForCurrent();
    }

    el.art.classList.remove('empty');
    el.art.classList.remove('loading');
    el.meta.classList.remove('loading');

    const img = new Image();
    img.onload = () => {
      el.artImg.src = img.src;
    };
    img.onerror = () => {
      el.art.classList.add('empty');
      el.artImg.removeAttribute('src');
    };
    img.src = t.artwork_url || '';

    el.title.textContent = t.title || 'Untitled';
    el.artist.textContent = t.artist || 'Unknown artist';
    // Merged Artist · Album inline (item #11)
    if (el.album) el.album.textContent = t.album || '';
    if (el.artistSep) el.artistSep.hidden = !t.album;

    // Genre whisper line (item #14) — replaces the chip when present.
    if (t.genre) {
      if (el.genreEm) el.genreEm.textContent = t.genre;
      if (el.genreLine) el.genreLine.hidden = false;
      if (el.chipGenre) el.chipGenre.hidden = true;
    } else {
      if (el.genreLine) el.genreLine.hidden = true;
      if (el.chipGenre) el.chipGenre.hidden = true;
    }

    // Rerender recent trail so the currently-playing track drops out of it
    renderRecentTrail();

    const useSpotify = sdkActive() && !!t.spotify_id;

    if (useSpotify) {
      try { el.player.pause(); } catch (e) {}
      el.player.removeAttribute('src');
      el.player.load();
      spotify.positionMs = 0;
      spotify.durationMs = (t.duration_ms || 30000);
      el.tTot.textContent = fmtTime(spotify.durationMs / 1000);
      el.tCur.textContent = '0:00';
      el.progressFill.style.width = '0%';
      el.progressThumb.style.left = '0%';
      setSourcePill('spotify');
      // SDK stream can't be tapped by AudioContext — glow reverts to static (0.65)
      stopGlowAnalyser();
      spotifyPlayTrack(t.spotify_id);
    } else {
      const total = t.duration_ms ? t.duration_ms / 1000 : 30;
      el.tTot.textContent = fmtTime(total);
      el.tCur.textContent = '0:00';
      el.progressFill.style.width = '0%';
      el.progressThumb.style.left = '0%';
      setSourcePill('preview');

      el.player.src = API_BASE + '/api/stream/' + encodeURIComponent(t.apple_id);
      el.player.volume = 0.8;

      const p = el.player.play();
      if (p && typeof p.catch === 'function') {
        p.catch(() => {
          document.body.classList.remove('playing');
        });
      }
    }
  }

  function stopPlayback() {
    try { el.player.pause(); } catch (e) {}
    el.player.removeAttribute('src');
    el.player.load();
    if (spotify.player) {
      try { spotify.player.pause(); } catch (e) {}
    }
    document.body.classList.remove('playing');
    el.tCur.textContent = '0:00';
    el.progressFill.style.width = '0%';
    el.progressThumb.style.left = '0%';
    stopGlowAnalyser();
    setGlowAlpha(0.65);
  }

  // ===== Verify affordance (classification-audio playback) =====
  // Plays the exact 30-sec clip that was classified for the current track,
  // in an isolated <audio id="verifyAudio"> element, without disturbing the
  // main playback state. Main playback is paused and resumed on completion.
  const verify = {
    active: false,
    prevSource: null,       // 'spotify' | 'preview' | null
    prevAudioTime: 0,       // where preview <audio> was
    prevAudioSrc: '',       // preview <audio> src (in case we need to reload)
    prevPlaying: false,     // was anything playing pre-verify
    timerId: null,          // countdown display
    toastHandle: null       // active toast for stop-and-resume action
  };
  const VERIFY_MAX_MS = 30_000;

  const CLASSIFICATION_LABELS = {
    'spotify_preview': 'Spotify preview',
    'itunes_isrc': 'iTunes ISRC lookup',
    'itunes_term_search': 'iTunes term search',
    'none': 'No classification audio'
  };
  function classificationLabel(src) {
    if (!src) return 'Unknown';
    return CLASSIFICATION_LABELS[src] || src;
  }
  function canVerify(track) {
    if (!track) return false;
    const src = track.classification_source;
    if (src === 'none') return false;
    return !!track.preview_url;
  }

  function updateVerifyChip(track) {
    if (!el.btnVerify) return;
    if (!track) {
      el.btnVerify.hidden = true;
      el.btnVerify.removeAttribute('aria-disabled');
      el.btnVerify.removeAttribute('data-verifying');
      return;
    }
    // Show the chip whenever we have a track — but disable if no verify audio
    el.btnVerify.hidden = false;
    const usable = canVerify(track);
    if (usable) {
      el.btnVerify.removeAttribute('aria-disabled');
      el.btnVerify.title = 'Play the 30-sec clip used to classify this track';
    } else {
      el.btnVerify.setAttribute('aria-disabled', 'true');
      el.btnVerify.title = 'No classification audio available for this track';
    }
    // Tooltip content
    const label = classificationLabel(track.classification_source);
    if (el.verifyTipLabel) el.verifyTipLabel.textContent = label;
    if (el.verifyTipUrl) el.verifyTipUrl.textContent = track.preview_url || '(no url)';
    if (!verify.active) el.btnVerify.removeAttribute('data-verifying');
  }

  function snapshotMainPlayback() {
    const usingSdk = sdkActive() && !!(state.current && state.current.spotify_id);
    if (usingSdk) {
      verify.prevSource = 'spotify';
      verify.prevPlaying = !!(spotify.lastState && !spotify.lastState.paused);
    } else {
      verify.prevSource = 'preview';
      verify.prevAudioTime = isFinite(el.player.currentTime) ? el.player.currentTime : 0;
      verify.prevAudioSrc = el.player.src || '';
      verify.prevPlaying = !el.player.paused;
    }
  }

  function pauseMainPlayback() {
    if (verify.prevSource === 'spotify' && spotify.player) {
      try { spotify.player.pause(); } catch (_) {}
    } else {
      try { el.player.pause(); } catch (_) {}
    }
    stopGlowAnalyser();
  }

  function resumeMainPlayback() {
    if (!verify.prevPlaying) return;
    if (verify.prevSource === 'spotify' && spotify.player) {
      try { spotify.player.resume(); } catch (_) {}
    } else {
      // Preview <audio> may have kept src; if not, restore.
      if (!el.player.src && verify.prevAudioSrc) el.player.src = verify.prevAudioSrc;
      try {
        if (isFinite(verify.prevAudioTime) && verify.prevAudioTime > 0) {
          el.player.currentTime = verify.prevAudioTime;
        }
      } catch (_) {}
      const p = el.player.play();
      if (p && typeof p.catch === 'function') p.catch(() => {});
    }
  }

  function updateVerifyOverlaySub(remainingMs) {
    if (!el.verifyOverlaySub) return;
    const t = state.current;
    const src = t && t.classification_source;
    const label = classificationLabel(src);
    const sec = Math.max(0, Math.ceil(remainingMs / 1000));
    el.verifyOverlaySub.textContent = `${label} · ${sec}s`;
  }

  function startVerify() {
    const t = state.current;
    if (!t || !canVerify(t)) {
      toast('No classification audio for this track.', 'info');
      return;
    }
    if (verify.active) return;

    snapshotMainPlayback();
    pauseMainPlayback();

    verify.active = true;
    document.body.classList.add('verifying');
    if (el.btnVerify) el.btnVerify.setAttribute('data-verifying', 'true');
    if (el.verifyOverlay) el.verifyOverlay.setAttribute('aria-hidden', 'false');

    try {
      el.verifyAudio.crossOrigin = 'anonymous';
      el.verifyAudio.src = t.preview_url;
      el.verifyAudio.currentTime = 0;
      el.verifyAudio.volume = 0.85;
    } catch (e) {
      console.warn('[VibeScape] verify audio src failed:', e);
    }

    // Timer for the overlay countdown + safety hard-stop at 30s
    const startedAt = performance.now();
    if (verify.timerId) clearInterval(verify.timerId);
    verify.timerId = setInterval(() => {
      const elapsed = performance.now() - startedAt;
      const remaining = VERIFY_MAX_MS - elapsed;
      updateVerifyOverlaySub(remaining);
      if (remaining <= 0) stopVerify({ resume: true });
    }, 250);
    updateVerifyOverlaySub(VERIFY_MAX_MS);

    const p = el.verifyAudio.play();
    if (p && typeof p.catch === 'function') {
      p.catch((err) => {
        console.warn('[VibeScape] verify audio play error:', err);
        toast('Could not play classification audio — link may be broken.', 'error');
        stopVerify({ resume: true });
      });
    }

    // Toast with a stop-and-resume action
    if (verify.toastHandle) { try { verify.toastHandle.dismiss(); } catch (_) {} }
    verify.toastHandle = toast('Classification audio playing.', 'info', {
      duration: 0, // persistent while verify runs
      action: {
        label: 'Stop & resume',
        onClick: () => stopVerify({ resume: true }),
        keepOpen: false
      }
    });
  }

  function stopVerify(opts) {
    opts = opts || { resume: true };
    if (!verify.active) return;
    verify.active = false;
    document.body.classList.remove('verifying');
    if (el.btnVerify) el.btnVerify.removeAttribute('data-verifying');
    if (el.verifyOverlay) el.verifyOverlay.setAttribute('aria-hidden', 'true');
    if (verify.timerId) { clearInterval(verify.timerId); verify.timerId = null; }
    try { el.verifyAudio.pause(); } catch (_) {}
    try { el.verifyAudio.removeAttribute('src'); el.verifyAudio.load(); } catch (_) {}
    if (verify.toastHandle) { try { verify.toastHandle.dismiss(); } catch (_) {} verify.toastHandle = null; }
    if (opts.resume) resumeMainPlayback();
    verify.prevSource = null;
    verify.prevPlaying = false;
  }

  // Wire the chip. Click starts verify; clicking again while active stops it.
  if (el.btnVerify) {
    el.btnVerify.addEventListener('click', (ev) => {
      // ignore if disabled
      if (el.btnVerify.getAttribute('aria-disabled') === 'true') return;
      // Don't let a click on the tooltip descendants trigger the button
      // (aria-hidden tooltip is inside; but click bubbles through)
      ev.preventDefault();
      state.firstInteraction = true;
      if (verify.active) stopVerify({ resume: true });
      else startVerify();
    });
  }
  if (el.verifyAudio) {
    el.verifyAudio.addEventListener('ended', () => {
      if (verify.active) stopVerify({ resume: true });
    });
    el.verifyAudio.addEventListener('error', () => {
      if (!verify.active) return;
      console.warn('[VibeScape] verifyAudio error event');
      toast('Classification audio failed to load.', 'error');
      stopVerify({ resume: true });
    });
  }

  // ===== Art-glow RMS analyser (replaces the 3-bar equalizer) =====
  // Attaches an AudioContext.AnalyserNode to the preview <audio> and drives
  // --art-glow-alpha from smoothed RMS. Preview-only; Spotify SDK stream cannot
  // be tapped, so it falls back to a static 0.65 opacity.
  const glow = {
    ctx: null,
    source: null,
    analyser: null,
    buffer: null,
    rafId: null,
    smoothed: 0.65,
    lastRmsAt: 0
  };
  const GLOW_MIN = 0.5;
  const GLOW_MAX = 0.9;
  const GLOW_LERP = 0.15;
  const REDUCED_MOTION = () => window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function setGlowAlpha(a) {
    document.documentElement.style.setProperty('--art-glow-alpha', String(a));
  }

  function ensureGlowAnalyser() {
    if (REDUCED_MOTION()) return false;
    if (glow.analyser && glow.source) return true;
    try {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return false;
      if (!glow.ctx) glow.ctx = new AC();
      // MediaElementSource can only be created ONCE per element. Cache it.
      if (!glow.source) {
        glow.source = glow.ctx.createMediaElementSource(el.player);
      }
      if (!glow.analyser) {
        glow.analyser = glow.ctx.createAnalyser();
        glow.analyser.fftSize = 512;
        glow.buffer = new Uint8Array(glow.analyser.fftSize);
        glow.source.connect(glow.analyser);
        // Also connect analyser to destination so audio still plays
        glow.analyser.connect(glow.ctx.destination);
      }
      return true;
    } catch (e) {
      console.warn('[VibeScape] glow analyser init failed:', e);
      return false;
    }
  }

  function startGlowAnalyser() {
    if (!ensureGlowAnalyser()) return;
    // Resume context if suspended (autoplay policy)
    if (glow.ctx.state === 'suspended') {
      glow.ctx.resume().catch(() => {});
    }
    if (glow.rafId) return; // already running
    let lastFrame = 0;
    const FRAME_INTERVAL = 33; // ~30Hz
    const step = (ts) => {
      glow.rafId = requestAnimationFrame(step);
      if (ts - lastFrame < FRAME_INTERVAL) return;
      lastFrame = ts;
      if (!glow.analyser) return;
      glow.analyser.getByteTimeDomainData(glow.buffer);
      // RMS of centered signal ([-128..127] domain-shifted from unsigned 0..255)
      let sum = 0;
      for (let i = 0; i < glow.buffer.length; i++) {
        const v = (glow.buffer[i] - 128) / 128;
        sum += v * v;
      }
      const rms = Math.sqrt(sum / glow.buffer.length); // 0..~1
      // Map to alpha range; RMS around 0.15-0.4 typical for music
      const mapped = GLOW_MIN + Math.min(1, rms * 2.4) * (GLOW_MAX - GLOW_MIN);
      glow.smoothed = glow.smoothed + (mapped - glow.smoothed) * GLOW_LERP;
      setGlowAlpha(glow.smoothed.toFixed(3));
    };
    glow.rafId = requestAnimationFrame(step);
  }

  function stopGlowAnalyser() {
    if (glow.rafId) {
      cancelAnimationFrame(glow.rafId);
      glow.rafId = null;
    }
  }

  function togglePlay() {
    if (!state.current) {
      fetchTrack(state.vibe);
      return;
    }
    if (sdkActive() && spotify.lastState !== null) {
      try { spotify.player.togglePlay(); } catch (e) {}
      return;
    }
    if (el.player.paused) {
      const p = el.player.play();
      if (p && typeof p.catch === 'function') p.catch(() => {});
    } else {
      el.player.pause();
    }
  }

  el.player.addEventListener('play', () => {
    document.body.classList.add('playing');
    startGlowAnalyser();
  });
  el.player.addEventListener('playing', () => {
    document.body.classList.add('playing');
    startGlowAnalyser();
  });
  el.player.addEventListener('pause', () => {
    if (!sdkActive()) document.body.classList.remove('playing');
    stopGlowAnalyser();
  });
  el.player.addEventListener('ended', () => {
    document.body.classList.remove('playing');
    stopGlowAnalyser();
    setGlowAlpha(0.65);
    fetchTrack(state.vibe);
  });
  el.player.addEventListener('loadedmetadata', () => {
    if (isFinite(el.player.duration) && el.player.duration > 0) {
      el.tTot.textContent = fmtTime(el.player.duration);
    }
  });
  el.player.addEventListener('timeupdate', () => {
    if (state.isSeeking) return;
    if (sdkActive() && spotify.lastState) return;
    const dur = el.player.duration;
    const cur = el.player.currentTime;
    el.tCur.textContent = fmtTime(cur);
    if (isFinite(dur) && dur > 0) {
      const pct = Math.min(100, (cur / dur) * 100);
      el.progressFill.style.width = pct + '%';
      el.progressThumb.style.left = pct + '%';
      el.progress.setAttribute('aria-valuenow', String(Math.round(pct)));
      el.progress.setAttribute('aria-valuetext', fmtTime(cur) + ' of ' + fmtTime(dur));
    }
  });

  el.btnPlay.addEventListener('click', () => {
    state.firstInteraction = true;
    togglePlay();
  });
  el.btnNext.addEventListener('click', () => {
    state.firstInteraction = true;
    fetchTrack(state.vibe);
  });
  el.btnPrev.addEventListener('click', () => {
    state.firstInteraction = true;
    fetchTrack(state.vibe);
  });

  function progressPosFromEvent(e) {
    const rect = el.progress.getBoundingClientRect();
    const x = ('touches' in e && e.touches[0]) ? e.touches[0].clientX : e.clientX;
    const frac = Math.min(1, Math.max(0, (x - rect.left) / rect.width));
    return frac;
  }
  function seekTo(frac) {
    if (sdkActive() && spotify.lastState) {
      const dur = spotify.durationMs || 0;
      if (dur > 0) {
        const ms = Math.floor(frac * dur);
        try { spotify.player.seek(ms); } catch (e) {}
        spotify.positionMs = ms;
        spotify.positionAt = performance.now();
      }
      el.progressFill.style.width = (frac * 100) + '%';
      el.progressThumb.style.left = (frac * 100) + '%';
      el.tCur.textContent = fmtTime((dur / 1000) * frac);
      return;
    }
    const dur = el.player.duration;
    if (isFinite(dur) && dur > 0) {
      el.player.currentTime = frac * dur;
    }
    el.progressFill.style.width = (frac * 100) + '%';
    el.progressThumb.style.left = (frac * 100) + '%';
    el.tCur.textContent = fmtTime((dur || 0) * frac);
  }
  function updateProgressHoverChip(frac) {
    if (!el.progressTimeChip) return;
    const dur = currentPlaybackDurationSec ? currentPlaybackDurationSec() : (el.player.duration || 30);
    const t = (dur || 30) * frac;
    el.progressTimeChip.textContent = fmtTime(t);
    el.progressTimeChip.style.left = (frac * 100) + '%';
  }
  el.progress.addEventListener('pointerdown', (e) => {
    if (!state.current) return;
    state.isSeeking = true;
    el.progress.classList.add('dragging');
    el.progress.setPointerCapture(e.pointerId);
    const frac = progressPosFromEvent(e);
    seekTo(frac);
    updateProgressHoverChip(frac);
  });
  el.progress.addEventListener('pointermove', (e) => {
    const frac = progressPosFromEvent(e);
    // Always update the hover chip position (even when not seeking) — that's
    // the whole point of the hover preview.
    updateProgressHoverChip(frac);
    if (!state.isSeeking) return;
    seekTo(frac);
  });
  const endSeek = (e) => {
    if (!state.isSeeking) return;
    state.isSeeking = false;
    el.progress.classList.remove('dragging');
    if (e && e.pointerId != null) {
      try { el.progress.releasePointerCapture(e.pointerId); } catch (_) {}
    }
  };
  el.progress.addEventListener('pointerup', endSeek);
  el.progress.addEventListener('pointercancel', endSeek);

  // Progress keyboard seek (item #8) — handles ← → ±5s, Home/End 0/100%, PgUp/PgDn ±15s
  function currentPlaybackDurationSec() {
    if (sdkActive() && spotify.lastState) return (spotify.durationMs || 0) / 1000;
    const d = el.player.duration;
    return isFinite(d) && d > 0 ? d : 0;
  }
  function currentPlaybackPositionSec() {
    if (sdkActive() && spotify.lastState) return spotify.positionMs / 1000;
    return el.player.currentTime || 0;
  }
  function updateProgressAria(posSec, durSec) {
    const pct = durSec > 0 ? Math.round((posSec / durSec) * 100) : 0;
    el.progress.setAttribute('aria-valuenow', String(pct));
    el.progress.setAttribute('aria-valuetext', fmtTime(posSec) + ' of ' + fmtTime(durSec));
  }
  el.progress.addEventListener('keydown', (ev) => {
    if (!state.current) return;
    const dur = currentPlaybackDurationSec();
    if (!(dur > 0)) return;
    let targetSec = null;
    if (ev.key === 'ArrowRight') targetSec = currentPlaybackPositionSec() + 5;
    else if (ev.key === 'ArrowLeft') targetSec = currentPlaybackPositionSec() - 5;
    else if (ev.key === 'PageUp') targetSec = currentPlaybackPositionSec() + 15;
    else if (ev.key === 'PageDown') targetSec = currentPlaybackPositionSec() - 15;
    else if (ev.key === 'Home') targetSec = 0;
    else if (ev.key === 'End') targetSec = Math.max(0, dur - 0.5);
    if (targetSec === null) return;
    ev.preventDefault();
    ev.stopPropagation(); // prevent document-level ArrowRight/Left from advancing track
    targetSec = Math.max(0, Math.min(dur, targetSec));
    const frac = dur > 0 ? targetSec / dur : 0;
    seekTo(frac);
    updateProgressAria(targetSec, dur);
  });

  let sliderDebounce = null;
  el.slider.addEventListener('input', () => {
    state.firstInteraction = true;
    const v = parseInt(el.slider.value, 10);
    state.vibe = v;
    updateSliderVisual(v);
    applyAccent(v);
    if (sliderDebounce) clearTimeout(sliderDebounce);
    sliderDebounce = setTimeout(() => fetchTrack(v), 400);
  });

  function shiftVibe(delta) {
    const v = Math.min(100, Math.max(0, state.vibe + delta));
    if (v === state.vibe) return;
    state.vibe = v;
    el.slider.value = String(v);
    updateSliderVisual(v);
    applyAccent(v);
    if (sliderDebounce) clearTimeout(sliderDebounce);
    sliderDebounce = setTimeout(() => fetchTrack(v), 400);
  }

  document.addEventListener('keydown', (e) => {
    if (e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA')) {
      if (e.target !== el.slider) return;
    }
    if (e.key === ' ' || e.code === 'Space') {
      e.preventDefault();
      state.firstInteraction = true;
      togglePlay();
    } else if (e.key === 'ArrowRight') {
      e.preventDefault();
      state.firstInteraction = true;
      fetchTrack(state.vibe);
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault();
      state.firstInteraction = true;
      fetchTrack(state.vibe);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      shiftVibe(+5);
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      shiftVibe(-5);
    }
  });

  // ===== Spotify PKCE + Web Playback SDK =====

  function base64UrlEncode(bytes) {
    let str = '';
    const arr = new Uint8Array(bytes);
    for (let i = 0; i < arr.length; i++) str += String.fromCharCode(arr[i]);
    return btoa(str).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }

  function randomString(len) {
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~';
    const buf = new Uint8Array(len);
    crypto.getRandomValues(buf);
    let out = '';
    for (let i = 0; i < len; i++) out += chars[buf[i] % chars.length];
    return out;
  }

  async function sha256(input) {
    const enc = new TextEncoder().encode(input);
    return await crypto.subtle.digest('SHA-256', enc);
  }

  async function loadSpotifyConfig() {
    try {
      const r = await fetch(API_BASE + '/api/spotify/config');
      if (!r.ok) return;
      const j = await r.json();
      spotify.clientId = j.client_id || '';
      spotify.redirectUri = j.redirect_uri || '';
      if (!spotify.clientId) {
        toast('Spotify not configured — paste Client ID in config.py', 'warning');
        el.btnSpotifySignIn.disabled = true;
        el.btnSpotifySignIn.style.opacity = '0.5';
        el.btnSpotifySignIn.style.cursor = 'not-allowed';
      }
    } catch (e) {
      // silent — connection issues handled by checkHealth
    }
  }

  function restoreSpotifySession() {
    const tok = localStorage.getItem(spotify.tokenKey);
    const exp = parseInt(localStorage.getItem(spotify.expiryKey) || '0', 10);
    const refresh = localStorage.getItem(spotify.refreshKey) || '';
    const profileRaw = localStorage.getItem(spotify.profileKey) || '';
    if (!tok || !exp) return false;
    // Force re-signin if scope version is missing or older than current
    const storedScopeVersion = parseInt(localStorage.getItem(spotify.scopeVersionKey) || '0', 10);
    if (!storedScopeVersion || storedScopeVersion < SPOTIFY_SCOPE_VERSION) {
      clearSpotifySession();
      toast('Spotify scopes updated — please sign in again to enable library sync.', 'info');
      return false;
    }
    if (Date.now() >= exp - 60_000) {
      if (refresh) {
        // async refresh
        refreshSpotifyToken().then((ok) => {
          if (ok) initSpotifyAfterAuth();
        });
        return true;
      }
      clearSpotifySession();
      return false;
    }
    spotify.accessToken = tok;
    spotify.refreshToken = refresh;
    spotify.expiresAt = exp;
    if (profileRaw) {
      try {
        const p = JSON.parse(profileRaw);
        spotify.displayName = p.display_name || 'Connected';
        spotify.isPremium = p.product === 'premium';
      } catch (e) {}
    }
    updateSpotifyUI();
    initSpotifyPlayerWhenReady();
    fetchSpotifyProfile().then(() => updateSpotifyUI());
    return true;
  }

  function updateSpotifyUI() {
    if (spotify.accessToken) {
      el.btnSpotifySignIn.hidden = true;
      el.spSigned.hidden = false;
      el.spName.textContent = spotify.displayName || 'Connected';
      el.spName.title = spotify.isPremium
        ? 'Spotify Premium — full tracks'
        : 'Free account — preview only';
    } else {
      el.btnSpotifySignIn.hidden = false;
      el.spSigned.hidden = true;
      closeDebugPanel();
    }
  }

  function clearSpotifySession() {
    localStorage.removeItem(spotify.tokenKey);
    localStorage.removeItem(spotify.refreshKey);
    localStorage.removeItem(spotify.expiryKey);
    localStorage.removeItem(spotify.profileKey);
    localStorage.removeItem(spotify.scopeVersionKey);
    spotify.accessToken = '';
    spotify.refreshToken = '';
    spotify.expiresAt = 0;
    spotify.displayName = '';
    spotify.isPremium = false;
    spotify.deviceId = '';
    spotify.lastState = null;
    if (spotify.player) {
      try { spotify.player.disconnect(); } catch (e) {}
      spotify.player = null;
    }
    updateSpotifyUI();
  }

  async function buildSpotifyAuthUrl() {
    const verifier = randomString(64);
    localStorage.setItem(spotify.verifierKey, verifier);
    const challengeBytes = await sha256(verifier);
    const challenge = base64UrlEncode(challengeBytes);
    const params = new URLSearchParams({
      response_type: 'code',
      client_id: spotify.clientId,
      code_challenge_method: 'S256',
      code_challenge: challenge,
      redirect_uri: spotify.redirectUri,
      scope: SPOTIFY_SCOPE
    });
    return 'https://accounts.spotify.com/authorize?' + params.toString();
  }

  // ===== localStorage bridge for OAuth popup → parent =====
  // Popup writes { code, state, ts, error? } to this key; parent listens via
  // the 'storage' event (fires on OTHER same-origin windows/tabs). Reliable
  // across COOP boundaries and PWA-style detached popup instances where
  // window.opener is null and the URL poller may lose the popup's context.
  const OAUTH_BRIDGE_KEY = 'spotify_pending_auth';
  const OAUTH_POPUP_NAME = 'vibescape-oauth-popup';
  let oauthBridgeListening = false;
  let oauthCodeConsumed = false; // single-fire guard shared by all three paths

  function consumeOAuthPayload(payload) {
    if (oauthCodeConsumed) return;
    if (!payload || (!payload.code && !payload.error)) return;
    oauthCodeConsumed = true;
    if (payload.error) {
      toast('Spotify sign-in error: ' + payload.error, 'error');
      return;
    }
    if (payload.code) {
      try { window.focus(); } catch (_) {}
      exchangeCodeForToken(payload.code);
    }
  }

  function readAndClearBridge() {
    try {
      const raw = localStorage.getItem(OAUTH_BRIDGE_KEY);
      if (!raw) return null;
      localStorage.removeItem(OAUTH_BRIDGE_KEY);
      return JSON.parse(raw);
    } catch (e) {
      console.warn('[VibeScape] bridge parse failed:', e);
      try { localStorage.removeItem(OAUTH_BRIDGE_KEY); } catch (_) {}
      return null;
    }
  }

  function ensureOAuthBridgeListener() {
    if (oauthBridgeListening) return;
    oauthBridgeListening = true;
    window.addEventListener('storage', (ev) => {
      // 'storage' events fire on OTHER windows — perfect for popup → parent
      if (ev.key !== OAUTH_BRIDGE_KEY) return;
      if (!ev.newValue) return; // ignore the clear that follows
      let payload = null;
      try { payload = JSON.parse(ev.newValue); } catch (_) { return; }
      // Ignore stale entries (>5 min old)
      if (payload && payload.ts && Date.now() - payload.ts > 5 * 60 * 1000) {
        try { localStorage.removeItem(OAUTH_BRIDGE_KEY); } catch (_) {}
        return;
      }
      // Clear it so a refresh doesn't re-consume
      try { localStorage.removeItem(OAUTH_BRIDGE_KEY); } catch (_) {}
      consumeOAuthPayload(payload);
    });
  }

  // If we ARE the popup (window.name is our tag), or we landed on /callback or
  // /?spotify_code=… inside a popup, hand the code back to the parent via
  // localStorage and self-close. This runs BEFORE the normal boot so the
  // popup lifecycle terminates before app.js paints anything.
  function handlePopupCallback() {
    const isPopup = (function () {
      try {
        if (window.name === OAUTH_POPUP_NAME) return true;
        // Fallback: window.opener present + we're on /callback or /?spotify_code
        if (window.opener && window.opener !== window) return true;
      } catch (_) {}
      return false;
    })();
    if (!isPopup) return false;

    const path = window.location.pathname || '';
    const params = new URLSearchParams(window.location.search || '');
    const code = params.get('code') || params.get('spotify_code');
    const error = params.get('error') || params.get('spotify_error');
    const state = params.get('state');
    // Only act if this window's URL actually carries an OAuth response
    if (!code && !error) return false;
    if (!path.endsWith('/callback') && !params.has('spotify_code') && !params.has('code')) return false;

    const payload = { code: code || null, error: error || null, state: state || null, ts: Date.now() };
    try {
      localStorage.setItem(OAUTH_BRIDGE_KEY, JSON.stringify(payload));
    } catch (e) {
      console.warn('[VibeScape] popup: bridge write failed:', e);
    }
    try {
      // Best-effort: focus and close the parent window before we go.
      if (window.opener && !window.opener.closed) {
        try { window.opener.focus(); } catch (_) {}
      }
    } catch (_) {}
    // Give the storage event a moment to propagate before self-close
    setTimeout(() => {
      try { window.close(); } catch (_) {}
    }, 60);
    return true;
  }

  // Poll the popup we opened for its URL. Once it lands on our own /callback
  // (same-origin as the opener), we can read `popup.location.search` directly —
  // no postMessage, no window.opener dependency. Works around COOP severing the
  // opener link when navigating cross-origin (Spotify) and back.
  function watchPopupForCode(popup) {
    let done = false;
    const timeoutAt = Date.now() + 5 * 60 * 1000; // 5 min max
    const timer = setInterval(() => {
      try {
        if (done) { clearInterval(timer); return; }
        if (!popup || popup.closed) {
          clearInterval(timer);
          if (!done) {
            // User closed the popup before completing auth
            console.warn('[VibeScape] Spotify popup closed before completion');
          }
          return;
        }
        if (Date.now() > timeoutAt) {
          clearInterval(timer);
          try { popup.close(); } catch (_) {}
          toast('Spotify sign-in timed out. Try again.', 'error');
          return;
        }
        // Same-origin access — throws while popup is on accounts.spotify.com,
        // succeeds the moment it lands on our /callback.
        let href;
        try { href = popup.location.href; } catch (_) { return; }
        if (!href) return;
        // Must be OUR redirect_uri path — filter out about:blank / spotify pages
        // that briefly resolve same-origin.
        if (href.indexOf(spotify.redirectUri) !== 0 && href.indexOf('/callback') < 0) return;
        const qs = popup.location.search || '';
        const params = new URLSearchParams(qs);
        const code = params.get('code');
        const error = params.get('error');
        if (!code && !error) return;
        done = true;
        clearInterval(timer);
        try { popup.close(); } catch (_) {}
        // Route through the shared consumer so the storage-event, postMessage,
        // and poller paths are all mutually idempotent — whichever fires first
        // wins; the others no-op.
        consumeOAuthPayload({ code: code || null, error: error || null, ts: Date.now() });
      } catch (e) {
        console.warn('[VibeScape] popup poll error:', e);
      }
    }, 400);
  }

  async function beginSpotifyLogin() {
    if (!spotify.clientId) return;
    console.log('[VibeScape] beginSpotifyLogin — redirect_uri:', spotify.redirectUri, 'origin:', window.location.origin);

    // Reset the single-fire guard so a retry after a failed attempt still works
    oauthCodeConsumed = false;

    // Ensure the storage-event bridge is armed BEFORE the popup opens — this
    // is the primary signaling channel (immune to COOP + PWA-popup isolation).
    ensureOAuthBridgeListener();
    // Also clear any stale bridge entry from a prior aborted attempt.
    try { localStorage.removeItem(OAUTH_BRIDGE_KEY); } catch (_) {}

    const url = await buildSpotifyAuthUrl();

    // Try popup first. Sized-window request is more likely to open as a real
    // popup with a handle we can poll. Named so the callback HTML (either
    // ours or the future backend redirect) can recognize us via window.name.
    const w = 520, h = 720;
    const left = window.screenX + (window.outerWidth - w) / 2;
    const top = window.screenY + (window.outerHeight - h) / 2;
    const features = `width=${w},height=${h},left=${left},top=${top},resizable=yes,scrollbars=yes`;
    let popup = null;
    try { popup = window.open(url, OAUTH_POPUP_NAME, features); } catch (_) { popup = null; }

    // Popup blocked OR opened as a background tab we can't poll — fall back to
    // same-window redirect. Saves current path so we can restore on return.
    if (!popup || popup.closed || typeof popup.closed === 'undefined') {
      console.warn('[VibeScape] popup unavailable — falling back to same-window redirect');
      try {
        sessionStorage.setItem('spotify_return_to', window.location.pathname + window.location.search + window.location.hash);
      } catch (_) {}
      toast('Redirecting to Spotify…', 'info');
      window.location.assign(url);
      return;
    }

    // We have a popup handle. Three racing signaling channels — first one wins,
    // others are no-ops via consumeOAuthPayload's single-fire guard:
    //   (1) storage-event bridge     (primary, COOP-safe)
    //   (2) same-origin URL polling  (secondary)
    //   (3) legacy postMessage       (tertiary, in case backend callback HTML uses it)
    try { popup.focus(); } catch (_) {}
    watchPopupForCode(popup);

    const onMessage = (ev) => {
      const data = ev.data || {};
      if (!data || data.source !== 'vibescape-spotify-callback') return;
      window.removeEventListener('message', onMessage);
      try { popup.close(); } catch (_) {}
      consumeOAuthPayload({ code: data.code || null, error: data.error || null, ts: Date.now() });
    };
    window.addEventListener('message', onMessage);
  }

  // Handle same-window redirect return: if we land back on the app with a code
  // in the URL, exchange it and strip the query string. This is the future-
  // proof path once backend /callback 302-redirects to `/?spotify_code=...`.
  async function handleRedirectReturn() {
    const p = new URLSearchParams(window.location.search);
    const code = p.get('spotify_code') || p.get('code');
    const error = p.get('spotify_error') || p.get('error');
    if (!code && !error) return false;
    // Strip params from history immediately so a refresh doesn't re-run the flow
    const clean = new URL(window.location.href);
    clean.searchParams.delete('spotify_code');
    clean.searchParams.delete('spotify_error');
    clean.searchParams.delete('code');
    clean.searchParams.delete('error');
    clean.searchParams.delete('state');
    window.history.replaceState({}, document.title, clean.pathname + (clean.search || '') + clean.hash);
    if (error) {
      toast('Spotify sign-in error: ' + error, 'error');
      return true;
    }
    if (code) {
      // Route through the shared consumer (single-fire guard). If handlePopupCallback
      // already ran (i.e., we're a popup that also happens to have the same-window
      // URL), this is a no-op.
      consumeOAuthPayload({ code, error: null, ts: Date.now() });
      // Restore prior scroll/path if we saved one
      try {
        const returnTo = sessionStorage.getItem('spotify_return_to');
        if (returnTo) {
          sessionStorage.removeItem('spotify_return_to');
          // Only restore path if it's within the same app (safety)
          if (returnTo.charAt(0) === '/') window.history.replaceState({}, document.title, returnTo);
        }
      } catch (_) {}
      return true;
    }
    return false;
  }

  async function exchangeCodeForToken(code) {
    const verifier = localStorage.getItem(spotify.verifierKey) || '';
    const body = new URLSearchParams({
      grant_type: 'authorization_code',
      code: code,
      redirect_uri: spotify.redirectUri,
      client_id: spotify.clientId,
      code_verifier: verifier
    });
    try {
      const r = await fetch('https://accounts.spotify.com/api/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: body.toString()
      });
      if (!r.ok) {
        toast('Spotify token exchange failed.', 'error');
        return;
      }
      const j = await r.json();
      spotify.accessToken = j.access_token || '';
      spotify.refreshToken = j.refresh_token || '';
      spotify.expiresAt = Date.now() + ((j.expires_in || 3600) * 1000);
      localStorage.setItem(spotify.tokenKey, spotify.accessToken);
      if (spotify.refreshToken) localStorage.setItem(spotify.refreshKey, spotify.refreshToken);
      localStorage.setItem(spotify.expiryKey, String(spotify.expiresAt));
      localStorage.setItem(spotify.scopeVersionKey, String(SPOTIFY_SCOPE_VERSION));
      localStorage.removeItem(spotify.verifierKey);
      await fetchSpotifyProfile();
      initSpotifyAfterAuth();
    } catch (e) {
      toast('Spotify token exchange error.', 'error');
    }
  }

  async function refreshSpotifyToken() {
    if (!spotify.refreshToken || !spotify.clientId) return false;
    const body = new URLSearchParams({
      grant_type: 'refresh_token',
      refresh_token: spotify.refreshToken,
      client_id: spotify.clientId
    });
    try {
      const r = await fetch('https://accounts.spotify.com/api/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: body.toString()
      });
      if (!r.ok) { clearSpotifySession(); return false; }
      const j = await r.json();
      spotify.accessToken = j.access_token || '';
      if (j.refresh_token) spotify.refreshToken = j.refresh_token;
      spotify.expiresAt = Date.now() + ((j.expires_in || 3600) * 1000);
      localStorage.setItem(spotify.tokenKey, spotify.accessToken);
      localStorage.setItem(spotify.refreshKey, spotify.refreshToken);
      localStorage.setItem(spotify.expiryKey, String(spotify.expiresAt));
      await fetchSpotifyProfile();
      return true;
    } catch (e) {
      return false;
    }
  }

  async function fetchSpotifyProfile() {
    try {
      const r = await fetch('https://api.spotify.com/v1/me', {
        headers: { 'Authorization': 'Bearer ' + spotify.accessToken }
      });
      if (!r.ok) {
        console.warn('Spotify /v1/me failed:', r.status, await r.text().catch(() => ''));
        return;
      }
      const j = await r.json();
      spotify.displayName = j.display_name || j.id || 'Connected';
      spotify.isPremium = j.product === 'premium';
      localStorage.setItem(spotify.profileKey, JSON.stringify({
        display_name: spotify.displayName,
        product: j.product || ''
      }));
    } catch (e) { console.warn('Spotify /v1/me error:', e); }
  }

  function initSpotifyAfterAuth() {
    updateSpotifyUI();
    if (!spotify.isPremium) {
      toast('Spotify Premium required for full-track playback. Preview mode is active.', 'info');
      return;
    }
    initSpotifyPlayerWhenReady();
  }

  function initSpotifyPlayerWhenReady() {
    if (!spotify.accessToken || !spotify.isPremium) return;
    if (spotify.player) return;
    if (spotify.sdkReady) {
      createSpotifyPlayer();
    }
    // else onSpotifyWebPlaybackSDKReady will trigger it
  }

  function createSpotifyPlayer() {
    if (spotify.player) return;
    if (!window.Spotify || !window.Spotify.Player) return;
    const player = new window.Spotify.Player({
      name: 'VibeScape',
      getOAuthToken: (cb) => {
        if (Date.now() >= spotify.expiresAt - 60_000) {
          refreshSpotifyToken().then((ok) => {
            cb(ok ? spotify.accessToken : '');
          });
        } else {
          cb(spotify.accessToken);
        }
      },
      volume: 0.8
    });

    player.addListener('ready', ({ device_id }) => {
      spotify.deviceId = device_id;
    });
    player.addListener('not_ready', () => {
      spotify.deviceId = '';
    });
    player.addListener('initialization_error', ({ message }) => {
      toast('Spotify SDK init error: ' + message, 'error');
    });
    player.addListener('authentication_error', () => {
      clearSpotifySession();
      toast('Spotify auth expired. Sign in again.', 'error', {
        action: { label: 'Sign in', onClick: () => beginSpotifyLogin() }
      });
    });
    player.addListener('account_error', () => {
      toast('Spotify Premium required for full-track playback.', 'warning');
      spotify.isPremium = false;
    });
    player.addListener('player_state_changed', (playerState) => {
      if (!playerState) {
        spotify.lastState = null;
        document.body.classList.remove('playing');
        stopPositionPolling();
        return;
      }
      spotify.lastState = playerState;
      spotify.positionMs = playerState.position || 0;
      spotify.durationMs = playerState.duration || spotify.durationMs;
      spotify.positionAt = performance.now();
      if (playerState.paused) {
        document.body.classList.remove('playing');
        stopPositionPolling();
      } else {
        document.body.classList.add('playing');
        startPositionPolling();
      }
      renderSpotifyProgress();

      // detect end-of-track: paused, position 0, and a previous track exists
      const prevTracks = (playerState.track_window && playerState.track_window.previous_tracks) || [];
      if (playerState.paused && playerState.position === 0 && prevTracks.length > 0) {
        setTimeout(() => {
          if (spotify.lastState && spotify.lastState.paused && spotify.lastState.position === 0) {
            fetchTrack(state.vibe);
          }
        }, 500);
      }
    });

    player.connect();
    spotify.player = player;
  }

  function renderSpotifyProgress() {
    if (!spotify.durationMs) return;
    const pct = Math.min(100, (spotify.positionMs / spotify.durationMs) * 100);
    el.progressFill.style.width = pct + '%';
    el.progressThumb.style.left = pct + '%';
    el.tCur.textContent = fmtTime(spotify.positionMs / 1000);
    el.tTot.textContent = fmtTime(spotify.durationMs / 1000);
    el.progress.setAttribute('aria-valuenow', String(Math.round(pct)));
    el.progress.setAttribute('aria-valuetext', fmtTime(spotify.positionMs / 1000) + ' of ' + fmtTime(spotify.durationMs / 1000));
  }

  function startPositionPolling() {
    stopPositionPolling();
    spotify.pollTimer = setInterval(() => {
      if (state.isSeeking) return;
      if (!spotify.lastState || spotify.lastState.paused) return;
      const elapsed = performance.now() - spotify.positionAt;
      const pos = Math.min(spotify.durationMs, (spotify.lastState.position || 0) + elapsed);
      spotify.positionMs = pos;
      renderSpotifyProgress();
    }, 250);
  }

  function stopPositionPolling() {
    if (spotify.pollTimer) {
      clearInterval(spotify.pollTimer);
      spotify.pollTimer = null;
    }
  }

  async function spotifyPlayTrack(spotifyId) {
    if (!spotify.deviceId || !spotify.accessToken) return;
    if (Date.now() >= spotify.expiresAt - 60_000) {
      const ok = await refreshSpotifyToken();
      if (!ok) return;
    }
    try {
      const r = await fetch('https://api.spotify.com/v1/me/player/play?device_id=' + encodeURIComponent(spotify.deviceId), {
        method: 'PUT',
        headers: {
          'Authorization': 'Bearer ' + spotify.accessToken,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ uris: ['spotify:track:' + spotifyId] })
      });
      if (r.status === 401) {
        const ok = await refreshSpotifyToken();
        if (ok) {
          await fetch('https://api.spotify.com/v1/me/player/play?device_id=' + encodeURIComponent(spotify.deviceId), {
            method: 'PUT',
            headers: {
              'Authorization': 'Bearer ' + spotify.accessToken,
              'Content-Type': 'application/json'
            },
            body: JSON.stringify({ uris: ['spotify:track:' + spotifyId] })
          });
        }
      } else if (r.status === 403 || r.status === 404) {
        toast('Spotify playback unavailable — falling back to preview.', 'warning');
        spotify.isPremium = false;
        setSourcePill('preview');
        el.player.src = API_BASE + '/api/stream/' + encodeURIComponent(state.current.apple_id);
        const p = el.player.play();
        if (p && typeof p.catch === 'function') p.catch(() => {});
      }
    } catch (e) {
      toast('Spotify play error — falling back to preview.', 'warning');
      if (state.current) {
        setSourcePill('preview');
        el.player.src = API_BASE + '/api/stream/' + encodeURIComponent(state.current.apple_id);
        const p = el.player.play();
        if (p && typeof p.catch === 'function') p.catch(() => {});
      }
    }
  }

  window.onSpotifyWebPlaybackSDKReady = function () {
    spotify.sdkReady = true;
    if (spotify.accessToken && spotify.isPremium) {
      createSpotifyPlayer();
    }
  };

  el.btnSpotifySignIn.addEventListener('click', () => {
    state.firstInteraction = true;
    beginSpotifyLogin();
  });
  el.btnSpotifySignOut.addEventListener('click', () => {
    console.log('[VibeScape] sign-out clicked; accessToken present:', !!spotify.accessToken);
    const wasSignedIn = !!spotify.accessToken;
    try {
      stopPlayback();
      clearSpotifySession();
      stopPositionPolling();
      setSourcePill(null);
      if (wasSignedIn) toast('Signed out of Spotify.', 'success');
    } catch (err) {
      console.error('[VibeScape] sign-out error:', err);
      toast('Sign-out error — see console.', 'error');
    }
  });

  // ===== Debug panel (personal token debug view) =====

  const debugPanel = { open: false };

  function fmtRelativeExpiry(ms) {
    if (!ms) return '—';
    const delta = ms - Date.now();
    if (delta <= 0) return 'expired';
    const totalSec = Math.floor(delta / 1000);
    const h = Math.floor(totalSec / 3600);
    const m = Math.floor((totalSec % 3600) / 60);
    const s = totalSec % 60;
    const abs = new Date(ms).toLocaleTimeString();
    if (h > 0) return `in ${h}h ${m}m (${abs})`;
    if (m > 0) return `in ${m}m ${s}s (${abs})`;
    return `in ${s}s (${abs})`;
  }

  function renderDebugPanel() {
    if (!el.debugPanel) return;
    el.debugUser.textContent = spotify.displayName
      ? `${spotify.displayName}${spotify.isPremium ? ' · premium' : ' · free'}`
      : '(not signed in)';
    el.debugExpiry.textContent = fmtRelativeExpiry(spotify.expiresAt);
    el.debugScopes.textContent = SPOTIFY_SCOPE.split(/\s+/).join('\n');
    el.debugToken.textContent = spotify.accessToken || '(no token)';
    // Classification rows
    const t = state.current;
    if (el.debugClassSource) {
      if (!t) el.debugClassSource.textContent = '(no track loaded)';
      else el.debugClassSource.textContent = `${t.classification_source || 'unknown'} — ${classificationLabel(t.classification_source)}`;
    }
    if (el.debugClassUrl) {
      el.debugClassUrl.textContent = (t && t.preview_url) || '(no url)';
    }
    renderDebugFeatures();
  }

  function openDebugPanel() {
    if (!el.debugPanel || !spotify.accessToken) return;
    debugPanel.open = true;
    el.debugPanel.hidden = false;
    el.debugPanel.setAttribute('aria-hidden', 'false');
    if (el.btnSpotifyDebug) el.btnSpotifyDebug.setAttribute('aria-expanded', 'true');
    renderDebugPanel();
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        el.debugPanel.classList.add('debug-panel-open');
      });
    });
  }

  function closeDebugPanel() {
    if (!el.debugPanel || !debugPanel.open) return;
    debugPanel.open = false;
    el.debugPanel.classList.remove('debug-panel-open');
    el.debugPanel.setAttribute('aria-hidden', 'true');
    if (el.btnSpotifyDebug) el.btnSpotifyDebug.setAttribute('aria-expanded', 'false');
    const reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    setTimeout(() => {
      if (!debugPanel.open) el.debugPanel.hidden = true;
    }, reduced ? 140 : 200);
  }

  function toggleDebugPanel() {
    if (debugPanel.open) closeDebugPanel(); else openDebugPanel();
  }

  async function copyTextToClipboard(text, sourceNode, successMsg) {
    if (!text) return;
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
      } else if (sourceNode) {
        const range = document.createRange();
        range.selectNodeContents(sourceNode);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        document.execCommand('copy');
      }
      toast(successMsg || 'Copied', 'success');
    } catch (e) {
      console.warn('[VibeScape] copy failed:', e);
      toast('Copy failed — select manually.', 'warning');
    }
  }
  async function copyTokenToClipboard() {
    return copyTextToClipboard(spotify.accessToken, el.debugToken, 'Token copied');
  }
  async function copyClassUrlToClipboard() {
    const t = state.current;
    const url = t && t.preview_url;
    if (!url) { toast('No classification URL to copy.', 'info'); return; }
    return copyTextToClipboard(url, el.debugClassUrl, 'Classification URL copied');
  }

  async function debugTestMe() {
    if (!spotify.accessToken) return;
    el.debugOutput.textContent = 'Fetching…';
    try {
      const r = await fetch('https://api.spotify.com/v1/me', {
        headers: { 'Authorization': 'Bearer ' + spotify.accessToken }
      });
      const text = await r.text();
      let pretty = text;
      try { pretty = JSON.stringify(JSON.parse(text), null, 2); } catch (_) {}
      el.debugOutput.textContent = `HTTP ${r.status}\n\n${pretty}`;
    } catch (e) {
      el.debugOutput.textContent = 'Request error: ' + (e && e.message ? e.message : String(e));
    }
  }

  // ===== Help popover (keyboard shortcuts, item #7) =====
  const helpPop = { open: false };
  function openHelpPopover() {
    if (!el.helpPopover) return;
    helpPop.open = true;
    el.helpPopover.hidden = false;
    el.helpPopover.setAttribute('aria-hidden', 'false');
    if (el.btnHelp) el.btnHelp.setAttribute('aria-expanded', 'true');
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        el.helpPopover.classList.add('help-popover-open');
      });
    });
  }
  function closeHelpPopover() {
    if (!el.helpPopover || !helpPop.open) return;
    helpPop.open = false;
    el.helpPopover.classList.remove('help-popover-open');
    el.helpPopover.setAttribute('aria-hidden', 'true');
    if (el.btnHelp) el.btnHelp.setAttribute('aria-expanded', 'false');
    const reduced = REDUCED_MOTION();
    setTimeout(() => {
      if (!helpPop.open) el.helpPopover.hidden = true;
    }, reduced ? 140 : 200);
  }
  function toggleHelpPopover() {
    if (helpPop.open) closeHelpPopover(); else openHelpPopover();
  }
  if (el.btnHelp) el.btnHelp.addEventListener('click', toggleHelpPopover);
  document.addEventListener('mousedown', (ev) => {
    if (!helpPop.open) return;
    const t = ev.target;
    if (el.helpPopover && el.helpPopover.contains(t)) return;
    if (el.btnHelp && el.btnHelp.contains(t)) return;
    closeHelpPopover();
  });

  if (el.btnSpotifyDebug) {
    el.btnSpotifyDebug.addEventListener('click', toggleDebugPanel);
  }
  if (el.btnDebugClose) {
    el.btnDebugClose.addEventListener('click', closeDebugPanel);
  }
  if (el.btnDebugCopy) {
    el.btnDebugCopy.addEventListener('click', copyTokenToClipboard);
  }
  if (el.btnDebugCopyUrl) {
    el.btnDebugCopyUrl.addEventListener('click', copyClassUrlToClipboard);
  }
  if (el.btnDebugTestMe) {
    el.btnDebugTestMe.addEventListener('click', debugTestMe);
  }
  // Click-outside to close
  document.addEventListener('mousedown', (ev) => {
    if (!debugPanel.open) return;
    const t = ev.target;
    if (el.debugPanel.contains(t)) return;
    if (el.btnSpotifyDebug && el.btnSpotifyDebug.contains(t)) return;
    closeDebugPanel();
  });
  // ===== Metrics panel (per-track features) =====
  const metricsPanel = { open: false, forTrackId: null };

  function trackKey(t) {
    if (!t) return null;
    return t.apple_id || t.spotify_id || null;
  }

  function fmtMetricValue(v, opts) {
    opts = opts || {};
    if (v === null || v === undefined || (typeof v === 'number' && !isFinite(v))) return null;
    if (typeof v === 'number') {
      const d = opts.decimals !== undefined ? opts.decimals : 2;
      return v.toFixed(d);
    }
    return String(v);
  }

  function pct01(v) {
    // Accept 0-1 or 0-100; normalize to 0-100 for the bar fill
    if (v === null || v === undefined || !isFinite(v)) return null;
    return v > 1.5 ? Math.max(0, Math.min(100, v)) : Math.max(0, Math.min(100, v * 100));
  }

  // Map a raw value to a 0-100 bar percent given an explicit natural-scale
  // ceiling. Used for fields like bandwidth (~5000 Hz max) that don't fit
  // the 0-1 / 0-100 auto-detect in pct01. `signed`=true maps abs(v)/max.
  function pctMax(v, max, opts) {
    if (v === null || v === undefined || !isFinite(v) || !isFinite(max) || max <= 0) return null;
    opts = opts || {};
    const raw = opts.signed ? Math.abs(v) : v;
    return Math.max(0, Math.min(100, (raw / max) * 100));
  }

  function makeRow(key, value, opts) {
    opts = opts || {};
    const row = document.createElement('div');
    row.className = opts.bar ? 'metrics-row' : 'metrics-row metrics-row-simple';
    const kEl = document.createElement('span');
    kEl.className = 'metrics-key';
    kEl.textContent = key;
    row.appendChild(kEl);
    if (opts.bar) {
      const bar = document.createElement('div');
      bar.className = 'metrics-bar';
      const fill = document.createElement('div');
      fill.className = 'metrics-bar-fill';
      const rawForBar = opts.barValue !== undefined ? opts.barValue : (typeof value === 'number' ? value : 0);
      let p;
      if (opts.barMax !== undefined) {
        p = pctMax(rawForBar, opts.barMax, { signed: !!opts.barSigned });
      } else {
        p = pct01(rawForBar);
      }
      fill.style.width = (p === null ? 0 : p) + '%';
      bar.appendChild(fill);
      row.appendChild(bar);
    }
    const vEl = document.createElement('span');
    vEl.className = 'metrics-value';
    const shown = fmtMetricValue(value, { decimals: opts.decimals !== undefined ? opts.decimals : 2 });
    if (shown === null) { vEl.textContent = '—'; vEl.classList.add('metrics-value-null'); }
    else vEl.textContent = opts.unit ? `${shown} ${opts.unit}` : shown;
    row.appendChild(vEl);
    if (opts.annot) {
      const annot = document.createElement('span');
      annot.className = 'metrics-annot';
      annot.textContent = opts.annot;
      row.appendChild(annot);
    }
    return row;
  }

  function makeGroup(title) {
    const wrap = document.createElement('div');
    wrap.className = 'metrics-group';
    const h = document.createElement('div');
    h.className = 'metrics-group-title';
    h.textContent = title;
    wrap.appendChild(h);
    return wrap;
  }

  function makeMiniBarChart(values, opts) {
    opts = opts || {};
    if (!Array.isArray(values) || !values.length) return null;
    const w = 280, h = 60;
    const n = values.length;
    const gap = 2;
    const barW = Math.max(1, (w - gap * (n - 1)) / n);
    // Normalize: signed values center around 0; MFCC has negatives, chroma is 0..1
    const hasNeg = values.some((v) => typeof v === 'number' && v < 0);
    let maxAbs = 1;
    values.forEach((v) => { if (typeof v === 'number' && isFinite(v)) maxAbs = Math.max(maxAbs, Math.abs(v)); });
    const midY = hasNeg ? h / 2 : h;
    const svgNS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(svgNS, 'svg');
    svg.setAttribute('class', 'metrics-mini-chart');
    svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
    svg.setAttribute('preserveAspectRatio', 'none');
    svg.setAttribute('role', 'img');
    if (opts.ariaLabel) svg.setAttribute('aria-label', opts.ariaLabel);
    // Zero-line for signed data
    if (hasNeg) {
      const line = document.createElementNS(svgNS, 'line');
      line.setAttribute('x1', '0'); line.setAttribute('x2', String(w));
      line.setAttribute('y1', String(midY)); line.setAttribute('y2', String(midY));
      line.setAttribute('stroke', 'rgba(255,255,255,0.08)');
      line.setAttribute('stroke-width', '1');
      svg.appendChild(line);
    } else if (opts.midTick) {
      // Subtle center Y-axis tick for unsigned charts (e.g., chroma) — a
      // horizontal reference line at the half-value mark so we can visually
      // read "which pitch classes are above vs below average intensity".
      const midY2 = h * 0.5;
      const line = document.createElementNS(svgNS, 'line');
      line.setAttribute('x1', '0'); line.setAttribute('x2', String(w));
      line.setAttribute('y1', String(midY2)); line.setAttribute('y2', String(midY2));
      line.setAttribute('stroke', 'rgba(255,255,255,0.06)');
      line.setAttribute('stroke-width', '1');
      line.setAttribute('stroke-dasharray', '2 3');
      svg.appendChild(line);
    }
    values.forEach((v, i) => {
      if (typeof v !== 'number' || !isFinite(v)) return;
      const scaled = (v / maxAbs) * (hasNeg ? (h / 2 - 2) : (h - 2));
      const rect = document.createElementNS(svgNS, 'rect');
      const x = i * (barW + gap);
      let y, barH;
      if (hasNeg) {
        if (scaled >= 0) { y = midY - scaled; barH = scaled; }
        else { y = midY; barH = -scaled; }
      } else {
        y = h - scaled; barH = scaled;
      }
      rect.setAttribute('x', String(x));
      rect.setAttribute('y', String(y));
      rect.setAttribute('width', String(barW));
      rect.setAttribute('height', String(Math.max(0.5, barH)));
      rect.setAttribute('rx', '1');
      rect.setAttribute('fill', 'var(--vibe-accent)');
      svg.appendChild(rect);
    });
    return svg;
  }

  function renderMetricsPanel(track, features) {
    if (!el.metricsContent) return;
    el.metricsContent.innerHTML = '';

    // Merge: features endpoint values win, fall back to track dict fields
    const f = features || {};
    const get = (k) => {
      if (f && f[k] !== undefined && f[k] !== null) return f[k];
      if (track && track[k] !== undefined && track[k] !== null) return track[k];
      return null;
    };

    // DERIVED (all on 0-100 or 0-1; pct01 auto-detects)
    const derived = makeGroup('Derived axes');
    derived.appendChild(makeRow('activation', get('activation'), { bar: true, barValue: get('activation'), decimals: 1 }));
    derived.appendChild(makeRow('valence', get('valence'), { bar: true, barValue: get('valence'), decimals: 1 }));
    const acVal = get('acousticness');
    derived.appendChild(makeRow('acousticness', acVal, { bar: true, barValue: acVal, decimals: 2 }));
    const arel = get('activation_relative');
    if (arel !== null) derived.appendChild(makeRow('activation_rel', arel, { bar: true, barValue: arel, decimals: 1 }));
    el.metricsContent.appendChild(derived);

    // RHYTHM (natural scales)
    const rhythm = makeGroup('Rhythm');
    rhythm.appendChild(makeRow('tempo', get('tempo'), { bar: true, barValue: get('tempo'), barMax: 200, decimals: 0, unit: 'BPM' }));
    rhythm.appendChild(makeRow('tempo_stability', get('tempo_stability'), { bar: true, barValue: get('tempo_stability'), barMax: 10, decimals: 2 }));
    rhythm.appendChild(makeRow('onset_rate', get('onset_rate'), { bar: true, barValue: get('onset_rate'), barMax: 10, decimals: 2 }));
    el.metricsContent.appendChild(rhythm);

    // ENERGY (typical range 0-0.5)
    const energy = makeGroup('Energy');
    energy.appendChild(makeRow('energy_mean', get('energy_mean'), { bar: true, barValue: get('energy_mean'), barMax: 1, decimals: 3, annot: 'typical loudness' }));
    energy.appendChild(makeRow('energy_std', get('energy_std'), { bar: true, barValue: get('energy_std'), barMax: 1, decimals: 3, annot: 'dynamic range' }));
    el.metricsContent.appendChild(energy);

    // TIMBRE (Hz values ~5000 max; contrast ~40; flatness/zcr 0-1; timbre_var ~50)
    const timbre = makeGroup('Timbre');
    timbre.appendChild(makeRow('brightness', get('brightness') || get('spectral_centroid'), { bar: true, barValue: get('brightness') || get('spectral_centroid'), barMax: 5000, decimals: 0, unit: 'Hz' }));
    timbre.appendChild(makeRow('bandwidth', get('bandwidth'), { bar: true, barValue: get('bandwidth'), barMax: 5000, decimals: 0, unit: 'Hz' }));
    timbre.appendChild(makeRow('rolloff', get('rolloff'), { bar: true, barValue: get('rolloff'), barMax: 8000, decimals: 0, unit: 'Hz' }));
    timbre.appendChild(makeRow('spectral_contrast', get('spectral_contrast'), { bar: true, barValue: get('spectral_contrast'), barMax: 40, decimals: 2 }));
    timbre.appendChild(makeRow('flatness', get('flatness'), { bar: true, barValue: get('flatness'), decimals: 3 }));
    timbre.appendChild(makeRow('zcr', get('zcr'), { bar: true, barValue: get('zcr'), decimals: 3 }));
    timbre.appendChild(makeRow('timbre_variability', get('timbre_variability'), { bar: true, barValue: get('timbre_variability'), barMax: 50, decimals: 2 }));
    el.metricsContent.appendChild(timbre);

    // HARMONY (valence_mode is signed -1..+1; tonnetz_std 0-1)
    const harmony = makeGroup('Harmony');
    const vm = get('valence_mode');
    const vmAnnot = (typeof vm === 'number' && isFinite(vm))
      ? (vm > 0 ? 'major-leaning' : (vm < 0 ? 'minor-leaning' : 'neutral'))
      : '';
    harmony.appendChild(makeRow('valence_mode', vm, { bar: true, barValue: vm, barMax: 1, barSigned: true, decimals: 2, annot: vmAnnot }));
    harmony.appendChild(makeRow('tonnetz_std', get('tonnetz_std'), { bar: true, barValue: get('tonnetz_std'), decimals: 3 }));
    el.metricsContent.appendChild(harmony);

    // MFCC (13)
    const mfcc = get('mfcc_mean');
    if (Array.isArray(mfcc) && mfcc.length) {
      const g = makeGroup('MFCC (13 coeffs)');
      const chart = makeMiniBarChart(mfcc, { ariaLabel: 'MFCC coefficients' });
      if (chart) g.appendChild(chart);
      el.metricsContent.appendChild(g);
    }

    // CHROMA (12)
    const chroma = get('chroma_mean');
    if (Array.isArray(chroma) && chroma.length) {
      const g = makeGroup('Chroma (C..B)');
      const chart = makeMiniBarChart(chroma, { ariaLabel: 'Chroma pitch classes C through B', midTick: true });
      if (chart) g.appendChild(chart);
      const labels = document.createElement('div');
      labels.className = 'metrics-mini-chart-labels';
      ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B'].forEach((n) => {
        const s = document.createElement('span'); s.textContent = n; labels.appendChild(s);
      });
      g.appendChild(labels);
      el.metricsContent.appendChild(g);
    }
  }

  // Backend returns { features: {...}, axes: {...}, ...topLevel }.
  // Flatten to a single object so renderMetricsPanel's `get(k)` can find
  // fields without walking a nested structure. Top-level fields (mood,
  // classification_source, title, etc.) win over features/axes when they
  // collide (unlikely, but explicit).
  function flattenFeaturesPayload(raw) {
    if (!raw || typeof raw !== 'object') return {};
    const flat = {};
    if (raw.features && typeof raw.features === 'object') Object.assign(flat, raw.features);
    if (raw.axes && typeof raw.axes === 'object') Object.assign(flat, raw.axes);
    // Copy top-level scalars/arrays (excluding the nested containers themselves)
    Object.keys(raw).forEach((k) => {
      if (k === 'features' || k === 'axes') return;
      flat[k] = raw[k];
    });
    return flat;
  }

  async function fetchTrackFeatures(track) {
    if (!track) return null;
    const key = trackKey(track);
    if (!key) return null;
    if (state.featureCache[key]) return state.featureCache[key];
    try {
      const r = await fetch(API_BASE + '/api/tracks/' + encodeURIComponent(key) + '/features');
      if (r.status === 404) {
        state.featureCache[key] = { __missing: true };
        return state.featureCache[key];
      }
      if (!r.ok) throw new Error('features fetch failed ' + r.status);
      const j = await r.json();
      state.featureCache[key] = flattenFeaturesPayload(j);
      return state.featureCache[key];
    } catch (e) {
      console.warn('[VibeScape] features fetch error:', e);
      return { __error: true };
    }
  }

  function setMetricsView(name) {
    if (!el.metricsLoading || !el.metricsEmpty || !el.metricsContent) return;
    el.metricsLoading.hidden = name !== 'loading';
    el.metricsEmpty.hidden = name !== 'empty';
    el.metricsContent.hidden = name !== 'content';
  }

  async function loadMetricsForCurrent() {
    const t = state.current;
    if (!t) {
      setMetricsView('empty');
      if (el.metricsEmptyMsg) el.metricsEmptyMsg.textContent = 'No track loaded yet.';
      return;
    }
    const key = trackKey(t);
    // If track dict already carries derived axes, render immediately; still
    // fetch full blob in background to fill in mfcc/chroma etc.
    const hasInlineDerived = (t.activation !== null && t.activation !== undefined) ||
                             (t.valence !== null && t.valence !== undefined);
    if (state.featureCache[key] && !state.featureCache[key].__error) {
      const f = state.featureCache[key];
      if (f.__missing) {
        setMetricsView('empty');
        if (el.metricsEmptyMsg) el.metricsEmptyMsg.textContent = 'Features not computed for this track.';
        return;
      }
      renderMetricsPanel(t, f);
      setMetricsView('content');
      return;
    }
    if (hasInlineDerived) {
      renderMetricsPanel(t, null);
      setMetricsView('content');
      // Kick off full fetch to enrich mfcc/chroma
      fetchTrackFeatures(t).then((f) => {
        if (!metricsPanel.open || state.current !== t) return;
        if (!f || f.__missing || f.__error) return;
        renderMetricsPanel(t, f);
      });
      return;
    }
    // Nothing inline — go to loading state then fetch
    setMetricsView('loading');
    const f = await fetchTrackFeatures(t);
    if (!metricsPanel.open || state.current !== t) return;
    if (!f || f.__error) {
      setMetricsView('empty');
      if (el.metricsEmptyMsg) el.metricsEmptyMsg.textContent = 'Could not load features. Try again after the backend is running.';
      return;
    }
    if (f.__missing) {
      setMetricsView('empty');
      if (el.metricsEmptyMsg) el.metricsEmptyMsg.textContent = 'Features not computed for this track.';
      return;
    }
    renderMetricsPanel(t, f);
    setMetricsView('content');
  }

  function openMetricsPanel() {
    if (!el.metricsPanel) return;
    // Close debug and help if they're open — one right-anchored panel at a time
    if (typeof closeDebugPanel === 'function') closeDebugPanel();
    if (typeof closeHelpPopover === 'function') closeHelpPopover();
    metricsPanel.open = true;
    metricsPanel.forTrackId = trackKey(state.current);
    el.metricsPanel.hidden = false;
    el.metricsPanel.setAttribute('aria-hidden', 'false');
    if (el.btnMetrics) el.btnMetrics.setAttribute('aria-expanded', 'true');
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        el.metricsPanel.classList.add('metrics-panel-open');
      });
    });
    loadMetricsForCurrent();
  }

  function closeMetricsPanel() {
    if (!el.metricsPanel || !metricsPanel.open) return;
    metricsPanel.open = false;
    el.metricsPanel.classList.remove('metrics-panel-open');
    el.metricsPanel.setAttribute('aria-hidden', 'true');
    if (el.btnMetrics) el.btnMetrics.setAttribute('aria-expanded', 'false');
    const reduced = REDUCED_MOTION();
    setTimeout(() => {
      if (!metricsPanel.open) el.metricsPanel.hidden = true;
    }, reduced ? 140 : 200);
  }

  function toggleMetricsPanel() {
    if (metricsPanel.open) closeMetricsPanel(); else openMetricsPanel();
  }

  function updateMetricsChip(track) {
    if (!el.btnMetrics) return;
    // Always show the chip when we have a track; graceful-degrade for missing features
    el.btnMetrics.hidden = !track;
  }

  function renderDebugFeatures() {
    const t = state.current;
    const key = t ? trackKey(t) : null;
    const cached = key ? state.featureCache[key] : null;
    const src = (cached && !cached.__missing && !cached.__error) ? cached : (t || {});
    const write = (node, val, decimals, unit) => {
      if (!node) return;
      if (val === null || val === undefined || !isFinite(val)) { node.textContent = '—'; node.classList.add('metrics-value-null'); return; }
      const d = decimals !== undefined ? decimals : 2;
      node.textContent = (typeof val === 'number' ? val.toFixed(d) : String(val)) + (unit ? ' ' + unit : '');
      node.classList.remove('metrics-value-null');
    };
    if (!t) {
      [el.debugFeatActivation, el.debugFeatValence, el.debugFeatAcousticness, el.debugFeatTempo, el.debugFeatEnergy].forEach((n) => {
        if (n) { n.textContent = '—'; n.classList.add('metrics-value-null'); }
      });
      return;
    }
    write(el.debugFeatActivation, src.activation, 1);
    write(el.debugFeatValence, src.valence, 1);
    write(el.debugFeatAcousticness, src.acousticness, 1);
    write(el.debugFeatTempo, src.tempo, 0, 'BPM');
    write(el.debugFeatEnergy, src.energy_mean, 3);
  }

  // Wiring
  if (el.btnMetrics) el.btnMetrics.addEventListener('click', toggleMetricsPanel);
  if (el.btnMetricsClose) el.btnMetricsClose.addEventListener('click', closeMetricsPanel);
  // Click-outside to close
  document.addEventListener('mousedown', (ev) => {
    if (!metricsPanel.open) return;
    const t = ev.target;
    if (el.metricsPanel && el.metricsPanel.contains(t)) return;
    if (el.btnMetrics && el.btnMetrics.contains(t)) return;
    closeMetricsPanel();
  });

  // Escape + Ctrl+Shift+D shortcut (+ ? for help, Ctrl+Shift+M for metrics)
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') {
      if (metricsPanel.open) { ev.preventDefault(); closeMetricsPanel(); return; }
      if (debugPanel.open) { ev.preventDefault(); closeDebugPanel(); return; }
      if (helpPop.open) { ev.preventDefault(); closeHelpPopover(); return; }
    }
    if ((ev.ctrlKey || ev.metaKey) && ev.shiftKey && (ev.key === 'D' || ev.key === 'd')) {
      ev.preventDefault();
      toggleDebugPanel();
      return;
    }
    if ((ev.ctrlKey || ev.metaKey) && ev.shiftKey && (ev.key === 'M' || ev.key === 'm')) {
      ev.preventDefault();
      toggleMetricsPanel();
      return;
    }
    // `?` opens help; ignore when typing in an input/textarea
    if (ev.key === '?' && !ev.ctrlKey && !ev.metaKey && !ev.altKey) {
      const t = ev.target;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA')) return;
      ev.preventDefault();
      toggleHelpPopover();
    }
  });

  // ===== Library sync modal =====

  function setSyncView(name) {
    sync.stage = name;
    const map = {
      loading: el.syncViewLoading,
      error: el.syncViewError,
      select: el.syncViewSelect,
      progress: el.syncViewProgress,
      complete: el.syncViewComplete
    };
    Object.values(map).forEach((v) => { if (v) v.hidden = true; });
    if (map[name]) map[name].hidden = false;

    // Footer variants
    el.syncModalFooter.hidden = false;
    if (name === 'select') {
      el.btnSyncStart.hidden = false;
      el.btnSyncStart.textContent = 'Sync selected';
      el.btnSyncCancel.textContent = 'Cancel';
      el.syncFooterMeta.hidden = false;
    } else if (name === 'progress') {
      el.btnSyncStart.hidden = true;
      el.btnSyncCancel.textContent = 'Cancel';
      el.syncFooterMeta.hidden = true;
    } else if (name === 'complete') {
      el.btnSyncStart.hidden = false;
      el.btnSyncStart.disabled = false;
      el.btnSyncStart.textContent = 'Play now';
      el.btnSyncCancel.textContent = 'Close';
      el.syncFooterMeta.hidden = true;
    } else if (name === 'error') {
      el.btnSyncStart.hidden = true;
      el.btnSyncCancel.textContent = 'Close';
      el.syncFooterMeta.hidden = true;
    } else {
      el.btnSyncStart.hidden = true;
      el.btnSyncCancel.textContent = 'Cancel';
      el.syncFooterMeta.hidden = true;
    }
  }

  function openSyncModal() {
    if (!spotify.accessToken) return;
    sync.open = true;
    el.syncModal.hidden = false;
    el.syncModal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('modal-open');
    setSyncView('loading');
    fetchLibrary();
  }

  function closeSyncModal() {
    sync.open = false;
    el.syncModal.hidden = true;
    el.syncModal.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('modal-open');
    stopSyncPolling();
    // reset library so next open refetches
    sync.library = null;
    sync.jobId = null;
    if (el.syncPlaylistList) el.syncPlaylistList.innerHTML = '';
  }

  async function fetchLibrary() {
    if (sync.fetching) return;
    sync.fetching = true;
    setSyncView('loading');
    try {
      const r = await fetch(API_BASE + '/api/spotify/library', {
        headers: { 'Authorization': 'Bearer ' + spotify.accessToken }
      });
      if (!r.ok) throw new Error('bad status ' + r.status);
      const j = await r.json();
      sync.library = j;
      renderLibrary(j);
      setSyncView('select');
      updateSyncFooter();
    } catch (e) {
      el.syncErrorMsg.textContent = 'Could not load your library. Check that you are signed in and the backend is running.';
      setSyncView('error');
    } finally {
      sync.fetching = false;
    }
  }

  function renderLibrary(lib) {
    el.syncLikedCount.textContent = String(lib.liked_count || 0);
    el.syncTopCount.textContent = String(lib.top_tracks_count || 0);
    el.syncPlaylistList.innerHTML = '';
    const playlists = Array.isArray(lib.playlists) ? lib.playlists : [];
    if (!playlists.length) {
      const empty = document.createElement('div');
      empty.className = 'modal-hint';
      empty.style.padding = '8px 4px';
      empty.textContent = 'No playlists found.';
      el.syncPlaylistList.appendChild(empty);
      return;
    }
    playlists.forEach((p) => {
      const row = document.createElement('label');
      row.className = 'sync-row';
      row.htmlFor = 'sync-pl-' + p.id;
      row.innerHTML = `
        <input class="sync-check" type="checkbox" id="sync-pl-${p.id}" data-kind="playlist" data-id="${p.id}" data-count="${p.track_count || 0}" />
        <div class="sync-row-body">
          <div class="sync-row-title"></div>
          <div class="sync-row-meta">
            <span class="count"></span>
            <span class="dot">·</span>
            <span class="owner"></span>
          </div>
        </div>
      `;
      row.querySelector('.sync-row-title').textContent = p.name || 'Untitled';
      row.querySelector('.count').textContent = (p.track_count || 0) + ' tracks';
      row.querySelector('.owner').textContent = p.owner || '';
      el.syncPlaylistList.appendChild(row);
    });
  }

  function collectSyncSelection() {
    const liked = !!el.syncLiked.checked;
    const top = !!el.syncTop.checked;
    const playlistBoxes = el.syncPlaylistList.querySelectorAll('input[data-kind="playlist"]');
    const playlist_ids = [];
    let playlistTracks = 0;
    playlistBoxes.forEach((box) => {
      if (box.checked) {
        playlist_ids.push(box.dataset.id);
        playlistTracks += parseInt(box.dataset.count || '0', 10);
      }
    });
    let total = 0;
    if (liked && sync.library) total += (sync.library.liked_count || 0);
    if (top && sync.library) total += (sync.library.top_tracks_count || 0);
    total += playlistTracks;
    return { liked, top, playlist_ids, total };
  }

  function updateSyncFooter() {
    const sel = collectSyncSelection();
    const anySelected = sel.liked || sel.top || sel.playlist_ids.length > 0;
    el.btnSyncStart.disabled = !anySelected;
    el.syncFooterMeta.textContent = anySelected
      ? `${sel.total} track${sel.total === 1 ? '' : 's'} selected`
      : 'Nothing selected';
  }

  async function startSync() {
    const sel = collectSyncSelection();
    if (!sel.liked && !sel.top && sel.playlist_ids.length === 0) return;
    setSyncView('progress');
    updateSyncProgress({ processed: 0, total: sel.total || 0, matched_spotify: 0, preview_only: 0, skipped: 0, current_track: 'Starting…' });

    try {
      const r = await fetch(API_BASE + '/api/ingest/spotify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          access_token: spotify.accessToken,
          sources: {
            liked: sel.liked,
            top_tracks: sel.top,
            playlist_ids: sel.playlist_ids
          }
        })
      });
      if (!r.ok) throw new Error('ingest start failed ' + r.status);
      const j = await r.json();
      sync.jobId = j.job_id || '';
      if (!sync.jobId) throw new Error('no job id');
      startSyncPolling();
    } catch (e) {
      el.syncErrorMsg.textContent = 'Could not start sync. Try again.';
      setSyncView('error');
    }
  }

  function startSyncPolling() {
    stopSyncPolling();
    sync.pollTimer = setInterval(pollSyncStatus, 1000);
    // fire once immediately
    pollSyncStatus();
  }

  function stopSyncPolling() {
    if (sync.pollTimer) {
      clearInterval(sync.pollTimer);
      sync.pollTimer = null;
    }
  }

  async function pollSyncStatus() {
    if (!sync.jobId) return;
    try {
      const r = await fetch(API_BASE + '/api/ingest/status/' + encodeURIComponent(sync.jobId));
      if (!r.ok) return;
      const s = await r.json();
      updateSyncProgress(s);
      if (s.status === 'complete') {
        stopSyncPolling();
        const summary = `Spotify ${s.matched_spotify || 0} · iTunes ${s.preview_only || 0} · no source ${s.no_preview || 0} · skipped ${s.skipped || 0} of ${s.total || 0}.`;
        el.syncCompleteSummary.textContent = summary;
        setSyncView('complete');
      } else if (s.status === 'error') {
        stopSyncPolling();
        el.syncErrorMsg.textContent = s.error_message || 'Sync failed.';
        setSyncView('error');
      }
    } catch (e) {
      // swallow — will retry on next tick
    }
  }

  function updateSyncProgress(s) {
    const total = s.total || 0;
    const processed = s.processed || 0;
    const pct = total > 0 ? Math.min(100, Math.round((processed / total) * 100)) : 0;
    el.syncBarFill.style.width = pct + '%';
    el.syncProgressPct.textContent = pct + '%';
    el.syncProgressCounts.textContent = `${processed} / ${total}`;
    el.syncCurrentTrack.textContent = s.current_track || '—';
    el.syncStatMatched.textContent = String(s.matched_spotify || 0);
    el.syncStatPreview.textContent = String(s.preview_only || 0);
    if (el.syncStatNoPreview) el.syncStatNoPreview.textContent = String(s.no_preview || 0);
    el.syncStatSkipped.textContent = String(s.skipped || 0);
  }

  async function cancelSyncJob() {
    if (sync.jobId) {
      try {
        await fetch(API_BASE + '/api/ingest/status/' + encodeURIComponent(sync.jobId), {
          method: 'DELETE'
        });
      } catch (e) { /* backend handles cleanup even if this fails */ }
    }
    stopSyncPolling();
  }

  // Wire modal events
  if (el.btnSpotifySync) {
    el.btnSpotifySync.addEventListener('click', openSyncModal);
  }
  if (el.btnSyncClose) {
    el.btnSyncClose.addEventListener('click', () => {
      if (sync.stage === 'progress') cancelSyncJob();
      closeSyncModal();
    });
  }
  if (el.syncModalBackdrop) {
    el.syncModalBackdrop.addEventListener('click', () => {
      if (sync.stage === 'progress') cancelSyncJob();
      closeSyncModal();
    });
  }
  if (el.btnSyncCancel) {
    el.btnSyncCancel.addEventListener('click', () => {
      if (sync.stage === 'progress') cancelSyncJob();
      closeSyncModal();
    });
  }
  if (el.btnSyncRetry) {
    el.btnSyncRetry.addEventListener('click', () => fetchLibrary());
  }
  if (el.btnSyncStart) {
    el.btnSyncStart.addEventListener('click', () => {
      if (sync.stage === 'complete') {
        closeSyncModal();
        fetchTrack(state.vibe);
      } else {
        startSync();
      }
    });
  }
  // Delegated change handler for selection updates
  document.addEventListener('change', (ev) => {
    if (!sync.open || sync.stage !== 'select') return;
    const t = ev.target;
    if (!t || !t.classList || !t.classList.contains('sync-check')) return;
    updateSyncFooter();
  });
  document.addEventListener('keydown', (ev) => {
    if (!sync.open) return;
    if (ev.key === 'Escape') {
      ev.preventDefault();
      if (sync.stage === 'progress') cancelSyncJob();
      closeSyncModal();
    }
  });

  // ===== Popup self-handoff — must run BEFORE anything else =====
  // If this window IS the OAuth popup (named tag, or opener present with a
  // callback URL), write the code to the localStorage bridge and self-close.
  // Return early so the popup never boots the full app UI.
  if (handlePopupCallback()) {
    // Popup mode — bridge write is queued, close in 60ms. Skip the rest of boot.
    return;
  }

  // Parent-window boot: arm the storage bridge listener immediately so we
  // never miss a popup write, even if the user is quick.
  ensureOAuthBridgeListener();
  // Also consume any bridge entry that was written BEFORE we started listening
  // (e.g., popup wrote and closed while parent app.js was still parsing).
  const pendingAtBoot = readAndClearBridge();
  if (pendingAtBoot) {
    // Slight defer so exchangeCodeForToken doesn't race with loadSpotifyConfig
    setTimeout(() => consumeOAuthPayload(pendingAtBoot), 0);
  }

  updateSliderVisual(state.vibe);
  applyAccent(state.vibe);
  el.art.classList.add('empty');
  updateVerifyChip(null);
  updateMetricsChip(null);
  checkHealth();
  loadSpotifyConfig().then(async () => {
    // If we returned from Spotify via same-window redirect, exchange the code
    // BEFORE trying to restore a stale session.
    const handled = await handleRedirectReturn();
    if (!handled) restoreSpotifySession();
  });
})();
