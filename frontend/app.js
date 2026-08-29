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
    titleVibe: $('titleVibe'),
    titleLang: $('titleLang'),
    titleSdkOnly: $('titleSdkOnly'),
    queueSidebar: $('queueSidebar'),
    queueList: $('queueList'),
    queueEmpty: $('queueEmpty'),
    queueCount: $('queueCount'),
    queueClear: $('queueClear'),
    queueRecsList: $('queueRecsList'),
    queueRecsEmpty: $('queueRecsEmpty'),
    queueRecsLoading: $('queueRecsLoading'),
    searchBar: $('searchBar'),
    searchInputWrap: $('searchInputWrap'),
    searchInput: $('searchInput'),
    searchClear: $('btnSearchClear'),
    searchDropdown: $('searchDropdown'),
    searchEmpty: $('searchEmpty'),
    searchLoading: $('searchLoading'),
    searchResults: $('searchResults'),
    searchNone: $('searchNone'),
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
    // Auth overlay + user menu
    authOverlay: $('authOverlay'),
    authViewPicker: $('authViewPicker'),
    authViewPin: $('authViewPin'),
    authViewCreate: $('authViewCreate'),
    authPickerLoading: $('authPickerLoading'),
    authProfilesGrid: $('authProfilesGrid'),
    authPickerEmpty: $('authPickerEmpty'),
    btnAuthCreate: $('btnAuthCreate'),
    btnAuthPinBack: $('btnAuthPinBack'),
    btnAuthCreateBack: $('btnAuthCreateBack'),
    authPinAvatar: $('authPinAvatar'),
    authPinName: $('authPinName'),
    authPinForm: $('authPinForm'),
    authPinInput: $('authPinInput'),
    authCreateForm: $('authCreateForm'),
    authCreateName: $('authCreateName'),
    authCreatePin: $('authCreatePin'),
    authCreatePinConfirm: $('authCreatePinConfirm'),
    authCreatePinConfirmField: $('authCreatePinConfirmField'),
    userMenu: $('userMenu'),
    btnUserMenu: $('btnUserMenu'),
    userMenuPopover: $('userMenuPopover'),
    userAvatar: $('userAvatar'),
    userName: $('userName'),
    userMenuName: $('userMenuName'),
    btnUserSignOut: $('btnUserSignOut'),
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
    syncTabs: $('syncTabs'),
    syncTabLibrary: $('syncTabLibrary'),
    syncTabUrl: $('syncTabUrl'),
    syncViewLoading: $('syncViewLoading'),
    syncViewError: $('syncViewError'),
    syncViewSelect: $('syncViewSelect'),
    syncViewUrl: $('syncViewUrl'),
    syncViewProgress: $('syncViewProgress'),
    syncViewComplete: $('syncViewComplete'),
    syncUrlInput: $('syncUrlInput'),
    syncUrlHint: $('syncUrlHint'),
    syncUrlSignedIn: $('syncUrlSignedIn'),
    syncUrlSignedOut: $('syncUrlSignedOut'),
    btnUrlSpotifySignIn: $('btnUrlSpotifySignIn'),
    btnUserAddPlaylist: $('btnUserAddPlaylist'),
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
    syncStatNew: $('syncStatNew'),
    syncStatLinked: $('syncStatLinked'),
    syncStatAlready: $('syncStatAlready'),
    syncStatNoPreview: $('syncStatNoPreview'),
    syncCompleteSummary: $('syncCompleteSummary'),
    syncFooterMeta: $('syncFooterMeta'),
    syncModalFooter: $('syncModalFooter'),
    videoStage: $('videoStage'),
    videoFrame: document.querySelector('.video-frame'),
    videoSkeleton: $('videoSkeleton'),
    videoEmpty: $('videoEmpty'),
    videoEmptyMsg: $('videoEmptyMsg'),
    videoEmptyCode: $('videoEmptyCode'),
    videoOpenYt: $('videoOpenYt'),
    ytPlayerMount: $('ytPlayer'),
    videoSearchFromEmpty: $('videoSearchFromEmpty'),
    videoEditBtn: $('videoEditBtn'),
    videoSearchPanel: $('videoSearchPanel'),
    videoSearchClose: $('videoSearchClose'),
    videoSearchForm: $('videoSearchForm'),
    videoSearchInput: $('videoSearchInput'),
    videoSearchSubmit: $('videoSearchSubmit'),
    videoSearchResults: $('videoSearchResults'),
    modeToggle: $('modeToggle'),
    modeAudio: $('modeAudio'),
    modeVideo: $('modeVideo'),
    modeVideoTip: $('modeVideoTip')
  };

  // v3: added `playlist-modify-private` — required to silently follow public
  // playlists server-side before fetching their tracks (Spotify's `/playlists/{id}/tracks`
  // returns 403 for third-party playlists otherwise, even if marked Public).
  const SPOTIFY_SCOPE = 'streaming user-read-email user-read-private user-library-read playlist-read-private playlist-read-collaborative user-top-read playlist-modify-private';
  const SPOTIFY_SCOPE_VERSION = 3;

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
    // User-managed play queue. Populated only via search "+ queue" and the
    // recommendations panel — never auto-filled. Advance-to-next consumes
    // queue[0] when non-empty; otherwise falls back to /api/tracks/random.
    queue: [],
    // Cached recommendations for the currently playing track. Rendered in
    // the right sidebar. Refreshes on each loadTrack; anchor identity is
    // tracked to avoid duplicate fetches.
    recs: { anchorKey: null, list: [], loading: false, reqToken: 0 },
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
    spotifyUserId: '',
    isPremium: false,
    player: null,
    deviceId: '',
    sdkReady: false,
    sdkLoaded: false,
    lastState: null,
    positionMs: 0,
    durationMs: 0,
    positionAt: 0,
    pollTimer: null
  };

  const video = {
    mode: 'audio',
    modeKey: 'vibescape.playbackMode',
    player: null,
    ready: false,
    apiRequested: false,
    apiReady: false,
    pendingVideoId: null,
    currentVideoId: null,
    pollTimer: null,
    lookupInFlight: null
  };

  // Per-VibeScape-user Spotify token key convention. Namespaced by user_id so
  // multiple profiles on the same browser don't stomp each other's Spotify
  // session. If auth.user is null (edge case: pre-login) we fall back to a
  // shared '_anon' namespace; those entries get cleared when a user logs in.
  function spotifyKeys() {
    const uid = (auth.user && auth.user.user_id) ? auth.user.user_id : '_anon';
    return {
      verifierKey:      `spotify_${uid}_pkce_verifier`,
      tokenKey:         `spotify_${uid}_access_token`,
      refreshKey:       `spotify_${uid}_refresh_token`,
      expiryKey:        `spotify_${uid}_token_expiry`,
      profileKey:       `spotify_${uid}_profile`,
      scopeVersionKey:  `spotify_${uid}_scope_version`
    };
  }

  // ===== VibeScape session auth =====
  const auth = {
    token: '',           // vibescape_session_token
    user: null,          // { user_id, display_name, has_pin, spotify_connected, created_at }
    booted: false,       // set once auth flow has completed (either login OR guest? — currently login required)
    tokenKey: 'vibescape_session_token',
    userIdKey: 'vibescape_user_id',
    // Picker UI state
    picker: {
      users: [],
      selected: null,    // user object being PIN-prompted
      view: 'picker'     // picker | pin | create
    }
  };

  const sync = {
    open: false,
    library: null,
    fetching: false,
    jobId: null,
    pollTimer: null,
    stage: 'idle', // idle | loading | error | select | url | progress | complete
    tab: 'library', // 'library' | 'url'
    urlValid: false,
    // The last successfully-added URL, used to short-circuit re-submits
    lastUrl: '',
    // Track whether we've surfaced the backend's `note` field yet — the note
    // can arrive on the 202 response OR mid-flight in a status poll; either
    // way we only want to show it once per job.
    noteShown: false,
    // Remember auth mode from status poll (`user_oauth` | `app_token`) for
    // the debug panel section.
    authMode: ''
  };
  // Extracts a 22-char base62 Spotify playlist ID from either the full open.spotify.com
  // URL or the spotify: URI scheme, tolerating query strings and `intl-*` locale segments.
  const SPOTIFY_PLAYLIST_URL_RE = /(?:^|\/|:)playlist(?::|\/)([A-Za-z0-9]{22})(?:$|[/?#])/;
  const SPOTIFY_BARE_ID_RE = /^[A-Za-z0-9]{22}$/;

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

  // ============== Auth wrapper + 401 interceptor ==============
  // Wraps fetch() for all authenticated backend calls. Adds Bearer header,
  // intercepts 401 by clearing session + showing the picker + toasting.
  // Endpoints that should NOT go through this: /api/health, /api/spotify/config,
  // /callback, /api/users, /api/auth/*. Those pass through raw fetch().
  // <audio> elements cannot send Authorization headers with their src fetch.
  // Append the session token as a query param so backend can authorize.
  // Session-backend needs to accept `?token=<vibescape_session>` on /api/stream/*.
  function authedStreamUrl(applePath) {
    const base = API_BASE + applePath;
    if (!auth.token) return base;
    return base + (base.indexOf('?') >= 0 ? '&' : '?') + 'token=' + encodeURIComponent(auth.token);
  }

  async function fetchWithAuth(path, options) {
    options = options || {};
    const headers = new Headers(options.headers || {});
    if (auth.token) headers.set('Authorization', 'Bearer ' + auth.token);
    const url = path.startsWith('http') ? path : (API_BASE + path);
    const r = await fetch(url, Object.assign({}, options, { headers }));
    if (r.status === 401 && auth.token) {
      // Session expired or revoked — force re-auth
      console.warn('[VibeScape] 401 from', path, '— clearing session');
      handleSessionExpired();
    }
    return r;
  }

  function handleSessionExpired() {
    // Called on any 401 from an authenticated endpoint. Wipes session,
    // stops playback, shows picker. Idempotent — safe to call multiple times.
    if (!auth.token) return;
    auth.token = '';
    auth.user = null;
    auth.booted = false;
    localStorage.removeItem(auth.tokenKey);
    localStorage.removeItem(auth.userIdKey);
    try { stopPlayback(); } catch (_) {}
    updateUserMenu();
    toast('Session expired. Sign in again.', 'error');
    showAuthOverlay();
  }

  // ============== Auth flow ==============
  function initialsFor(name) {
    if (!name) return '?';
    const parts = String(name).trim().split(/\s+/);
    if (!parts.length) return '?';
    if (parts.length === 1) return parts[0].slice(0, 1).toUpperCase();
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }

  function updateUserMenu() {
    if (!el.userMenu) return;
    if (auth.user) {
      el.userMenu.hidden = false;
      const name = auth.user.display_name || 'user';
      el.userName.textContent = name;
      el.userMenuName.textContent = name;
      el.userAvatar.textContent = initialsFor(name);
      // Admin button visibility (chandan-only).
      const btnAdmin = document.getElementById('btnUserAdmin');
      if (btnAdmin) btnAdmin.hidden = !auth.user.is_admin;
    } else {
      el.userMenu.hidden = true;
    }
  }

  function setAuthView(name) {
    auth.picker.view = name;
    if (el.authViewPicker) el.authViewPicker.hidden = name !== 'picker';
    if (el.authViewPin) el.authViewPin.hidden = name !== 'pin';
    if (el.authViewCreate) el.authViewCreate.hidden = name !== 'create';
  }

  function showAuthOverlay() {
    if (!el.authOverlay) return;
    el.authOverlay.hidden = false;
    document.body.classList.add('auth-locked');
    setAuthView('picker');
    fetchProfileList();
  }

  function hideAuthOverlay() {
    if (!el.authOverlay) return;
    el.authOverlay.hidden = true;
    document.body.classList.remove('auth-locked');
  }

  async function fetchProfileList() {
    if (!el.authProfilesGrid) return;
    if (el.authPickerLoading) el.authPickerLoading.hidden = false;
    if (el.authProfilesGrid) el.authProfilesGrid.hidden = true;
    if (el.authPickerEmpty) el.authPickerEmpty.hidden = true;
    try {
      const r = await fetch(API_BASE + '/api/users');
      if (!r.ok) throw new Error('users list failed ' + r.status);
      const users = await r.json();
      auth.picker.users = Array.isArray(users) ? users : [];
      renderProfileList();
    } catch (e) {
      console.warn('[VibeScape] fetch users failed:', e);
      if (el.authPickerLoading) el.authPickerLoading.hidden = true;
      if (el.authPickerEmpty) {
        el.authPickerEmpty.hidden = false;
        el.authPickerEmpty.querySelector('.modal-hint').textContent = 'Could not load profiles. Is the backend running?';
      }
    }
  }

  function renderProfileList() {
    if (!el.authProfilesGrid) return;
    el.authProfilesGrid.innerHTML = '';
    if (el.authPickerLoading) el.authPickerLoading.hidden = true;
    if (!auth.picker.users.length) {
      if (el.authPickerEmpty) el.authPickerEmpty.hidden = false;
      el.authProfilesGrid.hidden = true;
      return;
    }
    if (el.authPickerEmpty) el.authPickerEmpty.hidden = true;
    el.authProfilesGrid.hidden = false;
    auth.picker.users.forEach((u) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'auth-profile-card';
      const av = document.createElement('span');
      av.className = 'auth-profile-avatar';
      av.textContent = initialsFor(u.display_name);
      btn.appendChild(av);
      const nm = document.createElement('span');
      nm.className = 'auth-profile-name';
      nm.textContent = u.display_name || 'unnamed';
      btn.appendChild(nm);
      if (u.has_pin) {
        const lock = document.createElement('span');
        lock.className = 'auth-profile-lock';
        lock.innerHTML = '<svg viewBox="0 0 24 24" width="10" height="10" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg><span>PIN</span>';
        btn.appendChild(lock);
        btn.setAttribute('aria-label', 'Sign in as ' + u.display_name + ' (PIN required)');
      } else {
        btn.setAttribute('aria-label', 'Sign in as ' + u.display_name);
      }
      btn.addEventListener('click', () => selectProfile(u));
      el.authProfilesGrid.appendChild(btn);
    });
  }

  function selectProfile(u) {
    auth.picker.selected = u;
    if (u.has_pin) {
      setAuthView('pin');
      el.authPinName.textContent = u.display_name || 'unnamed';
      el.authPinAvatar.textContent = initialsFor(u.display_name);
      el.authPinInput.value = '';
      el.authPinInput.classList.remove('auth-pin-shake');
      setTimeout(() => { try { el.authPinInput.focus(); } catch (_) {} }, 60);
    } else {
      loginUser(u.user_id, null);
    }
  }

  async function loginUser(userId, pin) {
    const submitBtn = document.getElementById('btnAuthPinSubmit');
    if (submitBtn) submitBtn.disabled = true;
    try {
      const body = { user_id: userId };
      if (pin) body.pin = pin;
      const r = await fetch(API_BASE + '/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      if (r.status === 401) {
        // Wrong PIN — shake, clear, refocus
        el.authPinInput.value = '';
        el.authPinInput.classList.remove('auth-pin-shake');
        // force reflow so animation restarts
        void el.authPinInput.offsetWidth;
        el.authPinInput.classList.add('auth-pin-shake');
        toast('Wrong PIN. Try again.', 'error');
        setTimeout(() => { try { el.authPinInput.focus(); } catch (_) {} }, 40);
        return;
      }
      if (r.status === 404) {
        toast('Profile not found. Refreshing list.', 'error');
        setAuthView('picker');
        fetchProfileList();
        return;
      }
      if (!r.ok) throw new Error('login failed ' + r.status);
      const j = await r.json();
      completeLogin(j);
    } catch (e) {
      console.warn('[VibeScape] login error:', e);
      toast('Could not sign in. Check the backend.', 'error');
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  }

  function completeLogin(payload) {
    // payload: { user_id, display_name, session_token, has_pin, is_admin, ... }
    auth.token = payload.session_token || '';
    auth.user = {
      user_id: payload.user_id,
      display_name: payload.display_name,
      has_pin: !!payload.has_pin,
      spotify_connected: !!payload.spotify_connected,
      is_admin: !!payload.is_admin
    };
    localStorage.setItem(auth.tokenKey, auth.token);
    localStorage.setItem(auth.userIdKey, String(auth.user.user_id));
    updateUserMenu();
    hideAuthOverlay();
    // Boot the rest of the app now that we have a session
    bootAuthenticatedApp();
  }

  async function submitCreate(ev) {
    ev.preventDefault();
    const name = (el.authCreateName.value || '').trim();
    const pin = (el.authCreatePin.value || '').trim();
    const pin2 = (el.authCreatePinConfirm.value || '').trim();
    if (!name) {
      el.authCreateName.classList.add('auth-input-error');
      return;
    }
    el.authCreateName.classList.remove('auth-input-error');
    if (pin) {
      if (!/^\d{4}$/.test(pin)) {
        el.authCreatePin.classList.add('auth-input-error');
        toast('PIN must be exactly 4 digits.', 'error');
        return;
      }
      if (pin !== pin2) {
        el.authCreatePinConfirm.classList.add('auth-input-error');
        toast('PINs do not match.', 'error');
        return;
      }
    }
    el.authCreatePin.classList.remove('auth-input-error');
    el.authCreatePinConfirm.classList.remove('auth-input-error');

    const btn = document.getElementById('btnAuthCreateSubmit');
    if (btn) btn.disabled = true;
    try {
      const body = { display_name: name };
      if (pin) body.pin = pin;
      const r = await fetch(API_BASE + '/api/auth/signup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      if (r.status === 409) {
        el.authCreateName.classList.add('auth-input-error');
        toast('That name is taken — try another.', 'error');
        return;
      }
      if (!r.ok) throw new Error('signup failed ' + r.status);
      const j = await r.json();
      completeLogin(j);
    } catch (e) {
      console.warn('[VibeScape] signup error:', e);
      toast('Could not create profile. Try again.', 'error');
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function signOutOfVibeScape() {
    const wasSignedIn = !!auth.token;
    // Best-effort logout — even if it fails, wipe local state
    if (auth.token) {
      try {
        await fetch(API_BASE + '/api/auth/logout', {
          method: 'POST',
          headers: { 'Authorization': 'Bearer ' + auth.token }
        });
      } catch (_) {}
    }
    // Also stop main + Spotify playback and clear in-memory state
    try { stopPlayback(); } catch (_) {}
    try {
      if (spotify.player) { spotify.player.disconnect(); spotify.player = null; }
    } catch (_) {}
    spotify.accessToken = '';
    spotify.refreshToken = '';
    spotify.expiresAt = 0;
    spotify.displayName = '';
    spotify.spotifyUserId = '';
    spotify.isPremium = false;
    spotify.deviceId = '';
    spotify.lastState = null;
    updateSpotifyUI();
    // Clear session
    auth.token = '';
    auth.user = null;
    auth.booted = false; // allow re-boot on next login
    localStorage.removeItem(auth.tokenKey);
    localStorage.removeItem(auth.userIdKey);
    // Reset in-memory library state so the next user doesn't see stale data
    state.current = null;
    state.recent = [];
    state.featureCache = {};
    state.queue = [];
    state.recs = { anchorKey: null, list: [], loading: false, reqToken: 0 };
    try { renderQueue(); } catch (_) {}
    try { renderRecs(); } catch (_) {}
    if (el.recentTrail) { el.recentTrail.hidden = true; el.recentTrail.innerHTML = ''; }
    el.title.textContent = 'Move the slider to begin';
    el.artist.textContent = '—';
    if (el.album) el.album.textContent = '';
    if (el.artistSep) el.artistSep.hidden = true;
    if (el.genreLine) el.genreLine.hidden = true;
    clearTitleMeta();
    if (el.artImg) el.artImg.removeAttribute('src');
    if (el.art) el.art.classList.add('empty');
    setSourcePill(null);
    updateVerifyChip(null);
    updateMetricsChip(null);
    updateUserMenu();
    closeUserMenu();
    if (wasSignedIn) toast('Signed out of VibeScape.', 'success');
    showAuthOverlay();
  }

  // Try to hydrate from a stored session token. Returns true if we're auth'd.
  async function hydrateSessionFromStorage() {
    const tok = localStorage.getItem(auth.tokenKey) || '';
    if (!tok) return false;
    try {
      const r = await fetch(API_BASE + '/api/auth/me', {
        headers: { 'Authorization': 'Bearer ' + tok }
      });
      if (r.status === 401 || r.status === 404) {
        localStorage.removeItem(auth.tokenKey);
        localStorage.removeItem(auth.userIdKey);
        return false;
      }
      if (!r.ok) throw new Error('me failed ' + r.status);
      const j = await r.json();
      auth.token = tok;
      auth.user = {
        user_id: j.user_id,
        display_name: j.display_name,
        has_pin: !!j.has_pin,
        spotify_connected: !!j.spotify_connected,
        created_at: j.created_at,
        is_admin: !!j.is_admin
      };
      localStorage.setItem(auth.userIdKey, String(auth.user.user_id));
      updateUserMenu();
      return true;
    } catch (e) {
      console.warn('[VibeScape] hydrate session failed:', e);
      return false;
    }
  }

  // User-menu dropdown
  const userMenuState = { open: false };
  function openUserMenu() {
    if (!el.userMenuPopover) return;
    userMenuState.open = true;
    el.userMenuPopover.hidden = false;
    el.userMenuPopover.setAttribute('aria-hidden', 'false');
    el.btnUserMenu.setAttribute('aria-expanded', 'true');
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        el.userMenuPopover.classList.add('user-menu-open');
      });
    });
  }
  function closeUserMenu() {
    if (!el.userMenuPopover || !userMenuState.open) return;
    userMenuState.open = false;
    el.userMenuPopover.classList.remove('user-menu-open');
    el.userMenuPopover.setAttribute('aria-hidden', 'true');
    el.btnUserMenu.setAttribute('aria-expanded', 'false');
    setTimeout(() => {
      if (!userMenuState.open) el.userMenuPopover.hidden = true;
    }, 200);
  }
  function toggleUserMenu() {
    if (userMenuState.open) closeUserMenu(); else openUserMenu();
  }

  // Wire auth UI events (fire even before hydration because elements exist)
  if (el.btnAuthCreate) el.btnAuthCreate.addEventListener('click', () => {
    setAuthView('create');
    setTimeout(() => { try { el.authCreateName.focus(); } catch (_) {} }, 60);
  });
  if (el.btnAuthPinBack) el.btnAuthPinBack.addEventListener('click', () => setAuthView('picker'));
  if (el.btnAuthCreateBack) el.btnAuthCreateBack.addEventListener('click', () => setAuthView('picker'));
  if (el.authPinForm) el.authPinForm.addEventListener('submit', (ev) => {
    ev.preventDefault();
    const pin = (el.authPinInput.value || '').trim();
    if (!/^\d{4}$/.test(pin)) {
      el.authPinInput.classList.remove('auth-pin-shake');
      void el.authPinInput.offsetWidth;
      el.authPinInput.classList.add('auth-pin-shake');
      return;
    }
    if (auth.picker.selected) loginUser(auth.picker.selected.user_id, pin);
  });
  if (el.authCreatePin) el.authCreatePin.addEventListener('input', () => {
    // Show confirm field only when user starts typing a PIN
    const has = (el.authCreatePin.value || '').length > 0;
    if (el.authCreatePinConfirmField) el.authCreatePinConfirmField.hidden = !has;
  });
  if (el.authCreateForm) el.authCreateForm.addEventListener('submit', submitCreate);
  if (el.btnUserMenu) el.btnUserMenu.addEventListener('click', toggleUserMenu);
  if (el.btnUserSignOut) el.btnUserSignOut.addEventListener('click', () => {
    closeUserMenu();
    signOutOfVibeScape();
  });
  // Click-outside closes user menu
  document.addEventListener('mousedown', (ev) => {
    if (!userMenuState.open) return;
    const t = ev.target;
    if (el.userMenuPopover && el.userMenuPopover.contains(t)) return;
    if (el.btnUserMenu && el.btnUserMenu.contains(t)) return;
    closeUserMenu();
  });

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
      const r = await fetchWithAuth('/api/tracks/random?' + params.toString());
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
        clearTitleMeta();
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
    if (videoSearch.open) closeVideoSearchPanel();
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

    // Inline predicted vibe + mood next to the title. Mirrors the search
    // dropdown row so users see the same "feel at a glance" info in both
    // surfaces. Hidden when the track has no ML/formula vibe yet.
    updateTitleMeta(t);

    // Refresh sidebar recommendations for the new anchor. Fire-and-forget;
    // guarded by request-token inside so stale results are dropped.
    try { loadRecommendationsFor(t); } catch (_) {}

    updateMediaSessionMetadata(t);

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

    updateModeToggleDisabled();

    if (isVideoMode()) {
      try { el.player.pause(); } catch (_) {}
      el.player.removeAttribute('src');
      el.player.load();
      if (spotify.player) { try { spotify.player.pause(); } catch (_) {} }
      setSourcePill(null);
      el.tCur.textContent = '0:00';
      el.progressFill.style.width = '0%';
      el.progressThumb.style.left = '0%';
      loadVideoForCurrentTrack();
      return;
    }

    const useSpotify = sdkActive() && !!t.spotify_id;

    // Metadata-only tracks have no local audio (audio_path is NULL). The
    // <audio> stream would 404. Refuse up-front unless SDK is active — a
    // clear toast is friendlier than a silent-failure network error.
    if (!useSpotify && isMetadataOnly(t)) {
      try { el.player.pause(); } catch (_) {}
      el.player.removeAttribute('src');
      el.player.load();
      document.body.classList.remove('playing');
      const total = t.duration_ms ? t.duration_ms / 1000 : 30;
      el.tTot.textContent = fmtTime(total);
      el.tCur.textContent = '0:00';
      el.progressFill.style.width = '0%';
      el.progressThumb.style.left = '0%';
      setSourcePill(null);
      toast('This track requires Spotify Premium to play (no preview available).', 'warning');
      return;
    }

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

      // Prefer apple_id (present for iTunes-sourced tracks) but fall back to
      // spotify_id when apple_id is null — happens for search-added tracks
      // processed via the ML-only path (no iTunes term-search).
      const streamKey = (t.apple_id != null ? t.apple_id : t.spotify_id) || '';
      el.player.src = authedStreamUrl('/api/stream/' + encodeURIComponent(streamKey));
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
    stopYouTube();
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
    if (isVideoMode()) {
      if (!video.player || !video.ready) {
        loadVideoForCurrentTrack();
        return;
      }
      try {
        const YTS = window.YT && YT.PlayerState;
        const s = video.player.getPlayerState();
        if (YTS && s === YTS.PLAYING) video.player.pauseVideo();
        else video.player.playVideo();
      } catch (_) {}
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
    if (isVideoMode()) { try { el.player.pause(); } catch (_) {} return; }
    document.body.classList.add('playing');
    startGlowAnalyser();
    setMediaSessionState(true);
    applyMediaSessionHandlers();
    setMediaSessionPosition(el.player.duration, el.player.currentTime);
  });
  el.player.addEventListener('playing', () => {
    if (isVideoMode()) { try { el.player.pause(); } catch (_) {} return; }
    document.body.classList.add('playing');
    startGlowAnalyser();
    setMediaSessionState(true);
    applyMediaSessionHandlers();
    setMediaSessionPosition(el.player.duration, el.player.currentTime);
  });
  el.player.addEventListener('pause', () => {
    if (isVideoMode()) return;
    if (!sdkActive()) document.body.classList.remove('playing');
    stopGlowAnalyser();
    if (!sdkActive()) setMediaSessionState(false);
  });
  el.player.addEventListener('ended', () => {
    if (isVideoMode()) return;
    document.body.classList.remove('playing');
    stopGlowAnalyser();
    setGlowAlpha(0.65);
    setMediaSessionState(false);
    advanceToNext();
  });
  el.player.addEventListener('loadedmetadata', () => {
    if (isFinite(el.player.duration) && el.player.duration > 0) {
      el.tTot.textContent = fmtTime(el.player.duration);
    }
  });
  el.player.addEventListener('timeupdate', () => {
    if (state.isSeeking) return;
    if (isVideoMode()) return;
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
      setMediaSessionPosition(dur, cur);
    }
  });

  el.btnPlay.addEventListener('click', () => {
    state.firstInteraction = true;
    togglePlay();
  });
  el.btnNext.addEventListener('click', () => {
    advanceToNext();
  });
  el.btnPrev.addEventListener('click', () => {
    advanceToNext();
  });

  // ===== Video mode (YouTube IFrame API) =====
  function isVideoMode() { return video.mode === 'video'; }

  function loadYouTubeApi() {
    if (video.apiRequested) return;
    video.apiRequested = true;
    window.onYouTubeIframeAPIReady = () => {
      video.apiReady = true;
      createYouTubePlayer();
    };
    const s = document.createElement('script');
    s.src = 'https://www.youtube.com/iframe_api';
    s.async = true;
    document.head.appendChild(s);
  }

  function createYouTubePlayer() {
    if (video.player || !video.apiReady || !el.ytPlayerMount) return;
    try {
      video.player = new YT.Player('ytPlayer', {
        width: '100%',
        height: '100%',
        playerVars: { playsinline: 1, rel: 0, modestbranding: 1, iv_load_policy: 3 },
        events: {
          onReady: onYouTubeReady,
          onStateChange: onYouTubeStateChange,
          onError: onYouTubeError
        }
      });
    } catch (e) {
      console.warn('[VibeScape] YT.Player create failed:', e);
    }
  }

  function onYouTubeReady() {
    video.ready = true;
    if (video.pendingVideoId) {
      const vid = video.pendingVideoId;
      video.pendingVideoId = null;
      cueOrPlayVideoId(vid);
    }
  }

  function onYouTubeStateChange(ev) {
    const s = ev.data;
    const YTS = window.YT && YT.PlayerState;
    if (!YTS) return;
    if (s === YTS.PLAYING) {
      if (isVideoMode()) {
        document.body.classList.add('playing');
        setMediaSessionState(true);
        applyMediaSessionHandlers();
        startYouTubePositionPolling();
        try {
          const dur = video.player.getDuration() || 0;
          const cur = video.player.getCurrentTime() || 0;
          if (dur > 0) el.tTot.textContent = fmtTime(dur);
          el.tCur.textContent = fmtTime(cur);
          setMediaSessionPosition(dur, cur);
        } catch (_) {}
      }
    } else if (s === YTS.PAUSED || s === YTS.BUFFERING) {
      if (isVideoMode()) {
        document.body.classList.remove('playing');
        setMediaSessionState(false);
        stopYouTubePositionPolling();
      }
    } else if (s === YTS.ENDED) {
      if (isVideoMode()) {
        document.body.classList.remove('playing');
        setMediaSessionState(false);
        stopYouTubePositionPolling();
        advanceToNext();
      }
    }
  }

  const YT_ERROR_MSG = {
    2:   'Invalid video reference',
    5:   'HTML5 player error — try refreshing',
    100: 'Video removed or made private',
    101: 'The uploader disabled embedding',
    150: 'The uploader disabled embedding',
  };

  function onYouTubeError(ev) {
    const code = ev && ev.data;
    console.warn('[VibeScape] YouTube player error:', code);
    const msg = YT_ERROR_MSG[code] || 'Video unavailable';
    showVideoUnavailable({ message: msg, code, videoId: video.currentVideoId });
  }

  function startYouTubePositionPolling() {
    stopYouTubePositionPolling();
    video.pollTimer = setInterval(() => {
      if (!video.player || !isVideoMode()) return;
      try {
        const dur = video.player.getDuration() || 0;
        const cur = video.player.getCurrentTime() || 0;
        if (dur > 0) {
          const pct = Math.min(100, (cur / dur) * 100);
          el.progressFill.style.width = pct + '%';
          el.progressThumb.style.left = pct + '%';
          el.tCur.textContent = fmtTime(cur);
          el.tTot.textContent = fmtTime(dur);
          el.progress.setAttribute('aria-valuenow', String(Math.round(pct)));
          el.progress.setAttribute('aria-valuetext', fmtTime(cur) + ' of ' + fmtTime(dur));
          setMediaSessionPosition(dur, cur);
        }
      } catch (_) {}
    }, 400);
  }

  function stopYouTubePositionPolling() {
    if (video.pollTimer) {
      clearInterval(video.pollTimer);
      video.pollTimer = null;
    }
  }

  function cueOrPlayVideoId(id) {
    if (!video.player || !video.ready) {
      video.pendingVideoId = id;
      return;
    }
    video.currentVideoId = id;
    showVideoReady();
    try {
      if (state.firstInteraction) {
        video.player.loadVideoById(id);
      } else {
        video.player.cueVideoById(id);
      }
    } catch (e) {
      console.warn('[VibeScape] loadVideoById failed:', e);
    }
  }

  function pauseYouTube() {
    stopYouTubePositionPolling();
    if (!video.player) return;
    try { video.player.pauseVideo(); } catch (_) {}
  }

  function stopYouTube() {
    stopYouTubePositionPolling();
    if (!video.player) return;
    try { video.player.stopVideo(); } catch (_) {}
    video.currentVideoId = null;
  }

  function showVideoLoading() {
    if (el.videoFrame) el.videoFrame.classList.remove('is-ready');
    if (el.videoSkeleton) el.videoSkeleton.hidden = false;
    if (el.videoEmpty) el.videoEmpty.hidden = true;
    if (el.videoEditBtn) el.videoEditBtn.hidden = true;
  }
  function showVideoReady() {
    if (el.videoFrame) el.videoFrame.classList.add('is-ready');
    if (el.videoSkeleton) el.videoSkeleton.hidden = true;
    if (el.videoEmpty) el.videoEmpty.hidden = true;
    if (el.videoEditBtn) el.videoEditBtn.hidden = false;
  }
  function showVideoUnavailable(opts) {
    opts = opts || {};
    if (el.videoFrame) el.videoFrame.classList.remove('is-ready');
    if (el.videoSkeleton) el.videoSkeleton.hidden = true;
    if (el.videoEmpty) el.videoEmpty.hidden = false;
    if (el.videoEditBtn) el.videoEditBtn.hidden = true;
    if (el.videoEmptyMsg) el.videoEmptyMsg.textContent = opts.message || 'No video for this track';
    if (el.videoEmptyCode) {
      if (opts.code != null) {
        el.videoEmptyCode.textContent = 'YouTube error ' + opts.code;
        el.videoEmptyCode.hidden = false;
      } else {
        el.videoEmptyCode.textContent = '';
        el.videoEmptyCode.hidden = true;
      }
    }
    if (el.videoOpenYt) {
      if (opts.videoId) {
        el.videoOpenYt.href = 'https://www.youtube.com/watch?v=' + encodeURIComponent(opts.videoId);
        el.videoOpenYt.hidden = false;
      } else {
        el.videoOpenYt.removeAttribute('href');
        el.videoOpenYt.hidden = true;
      }
    }
    stopYouTube();
  }

  async function resolveYouTubeIdForTrack(t) {
    if (!t) return null;
    if (t.youtube_id) return t.youtube_id;
    if (t.youtube_id === null && t.youtube_queried_at) return null;
    if (!t.id) return null;
    if (video.lookupInFlight && video.lookupInFlight.trackId === t.id) {
      return video.lookupInFlight.promise;
    }
    const promise = (async () => {
      try {
        const r = await fetchWithAuth('/api/tracks/' + encodeURIComponent(t.id) + '/youtube');
        if (!r.ok) return null;
        const body = await r.json();
        const yid = body && body.youtube_id ? body.youtube_id : null;
        if (state.current && state.current.id === t.id) {
          state.current.youtube_id = yid;
          state.current.youtube_queried_at = Date.now();
        }
        return yid;
      } catch (e) {
        console.warn('[VibeScape] youtube lookup failed:', e);
        return null;
      }
    })();
    video.lookupInFlight = { trackId: t.id, promise };
    try { return await promise; }
    finally { if (video.lookupInFlight && video.lookupInFlight.trackId === t.id) video.lookupInFlight = null; }
  }

  async function loadVideoForCurrentTrack() {
    const t = state.current;
    if (!t) { showVideoUnavailable(); return; }
    showVideoLoading();
    const yid = await resolveYouTubeIdForTrack(t);
    if (state.current !== t) return;
    updateModeToggleDisabled();
    if (!yid) {
      showVideoUnavailable();
      return;
    }
    if (!video.apiRequested) loadYouTubeApi();
    if (!video.player) {
      video.pendingVideoId = yid;
      return;
    }
    cueOrPlayVideoId(yid);
  }

  const videoSearch = {
    open: false,
    forTrackId: null,
    fetchToken: 0,
    lastQuery: '',
    submitting: false
  };

  function defaultVideoSearchQuery(t) {
    if (!t) return '';
    const title = (t.title || '').trim();
    const artist = (t.artist || '').trim();
    return [title, artist].filter(Boolean).join(' ');
  }

  function openVideoSearchPanel(prefillQuery) {
    if (!el.videoSearchPanel || !state.current) return;
    videoSearch.open = true;
    videoSearch.forTrackId = state.current.id || null;
    el.videoSearchPanel.hidden = false;
    requestAnimationFrame(() => el.videoSearchPanel.classList.add('is-open'));
    const q = prefillQuery != null ? prefillQuery : defaultVideoSearchQuery(state.current);
    if (el.videoSearchInput) {
      el.videoSearchInput.value = q;
      setTimeout(() => {
        try { el.videoSearchInput.focus(); el.videoSearchInput.select(); } catch (_) {}
      }, 30);
    }
    renderVideoSearchState('idle');
    if (q && q.trim()) runVideoSearch(q.trim());
  }

  function closeVideoSearchPanel() {
    if (!el.videoSearchPanel) return;
    videoSearch.open = false;
    videoSearch.fetchToken++;
    el.videoSearchPanel.classList.remove('is-open');
    el.videoSearchPanel.hidden = true;
  }

  function renderVideoSearchState(kind, extra) {
    if (!el.videoSearchResults) return;
    el.videoSearchResults.innerHTML = '';
    if (kind === 'idle') return;
    const wrap = document.createElement('div');
    wrap.className = 'video-search-state';
    if (kind === 'loading') {
      const sp = document.createElement('div');
      sp.className = 'modal-spinner';
      sp.setAttribute('aria-hidden', 'true');
      const msg = document.createElement('p');
      msg.className = 'modal-hint';
      msg.textContent = 'Searching…';
      wrap.appendChild(sp);
      wrap.appendChild(msg);
    } else if (kind === 'empty') {
      const msg = document.createElement('p');
      msg.className = 'modal-hint';
      msg.textContent = 'No results. Try different keywords.';
      wrap.appendChild(msg);
    } else if (kind === 'error') {
      const msg = document.createElement('p');
      msg.className = 'modal-hint';
      msg.textContent = (extra && extra.message) || 'Search failed. Try again.';
      wrap.appendChild(msg);
    }
    el.videoSearchResults.appendChild(wrap);
  }

  function renderVideoSearchResults(results) {
    if (!el.videoSearchResults) return;
    el.videoSearchResults.innerHTML = '';
    results.forEach((r) => {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'video-search-result';
      row.setAttribute('role', 'option');
      row.dataset.youtubeId = r.youtube_id || '';

      const thumb = document.createElement('div');
      thumb.className = 'video-search-thumb';
      if (r.thumbnail_url) {
        const img = document.createElement('img');
        img.src = r.thumbnail_url;
        img.alt = '';
        img.loading = 'lazy';
        img.referrerPolicy = 'no-referrer';
        thumb.appendChild(img);
      } else {
        const ph = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        ph.setAttribute('viewBox', '0 0 24 24');
        ph.setAttribute('width', '20');
        ph.setAttribute('height', '20');
        ph.setAttribute('fill', 'none');
        ph.setAttribute('stroke', 'currentColor');
        ph.setAttribute('stroke-width', '1.5');
        ph.innerHTML = '<rect x="2" y="6" width="20" height="12" rx="2"/><polygon points="10 9 15 12 10 15 10 9"/>';
        thumb.appendChild(ph);
      }
      if (r.duration != null && isFinite(r.duration)) {
        const dur = document.createElement('span');
        dur.className = 'video-search-thumb-duration';
        dur.textContent = fmtTime(r.duration);
        thumb.appendChild(dur);
      }

      const meta = document.createElement('div');
      meta.className = 'video-search-meta';
      const title = document.createElement('p');
      title.className = 'video-search-result-title';
      title.textContent = r.title || '—';
      const chan = document.createElement('p');
      chan.className = 'video-search-result-channel';
      chan.textContent = r.channel || '—';
      meta.appendChild(title);
      meta.appendChild(chan);

      row.appendChild(thumb);
      row.appendChild(meta);
      row.addEventListener('click', () => selectVideoSearchResult(r, row));
      el.videoSearchResults.appendChild(row);
    });
  }

  async function runVideoSearch(query) {
    const t = state.current;
    if (!t || !t.id) return;
    videoSearch.lastQuery = query;
    const token = ++videoSearch.fetchToken;
    renderVideoSearchState('loading');
    try {
      const url = '/api/tracks/' + encodeURIComponent(t.id) + '/youtube/search'
        + '?q=' + encodeURIComponent(query) + '&limit=5';
      const r = await fetchWithAuth(url);
      if (token !== videoSearch.fetchToken || !videoSearch.open) return;
      if (!r.ok) {
        renderVideoSearchState('error');
        return;
      }
      const body = await r.json();
      const results = (body && Array.isArray(body.results)) ? body.results : [];
      if (!results.length) renderVideoSearchState('empty');
      else renderVideoSearchResults(results);
    } catch (e) {
      if (token !== videoSearch.fetchToken || !videoSearch.open) return;
      renderVideoSearchState('error');
    }
  }

  async function selectVideoSearchResult(result, rowEl) {
    if (videoSearch.submitting) return;
    const t = state.current;
    if (!t || !t.id || !result || !result.youtube_id) return;
    videoSearch.submitting = true;
    if (rowEl) rowEl.setAttribute('aria-busy', 'true');
    try {
      const r = await fetchWithAuth('/api/tracks/' + encodeURIComponent(t.id) + '/youtube', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ youtube_id: result.youtube_id })
      });
      if (!r.ok) {
        if (window.toast) toast('Could not save video override.', 'error');
        return;
      }
      const body = await r.json().catch(() => ({}));
      const newId = (body && body.youtube_id) || result.youtube_id;
      if (state.current && state.current.id === t.id) {
        state.current.youtube_id = newId;
      }
      closeVideoSearchPanel();
      if (isVideoMode()) {
        showVideoLoading();
        if (!video.apiRequested) loadYouTubeApi();
        if (!video.player) {
          video.pendingVideoId = newId;
        } else {
          cueOrPlayVideoId(newId);
        }
      }
      updateModeToggleDisabled();
    } catch (e) {
      if (window.toast) toast('Could not save video override.', 'error');
    } finally {
      videoSearch.submitting = false;
      if (rowEl) rowEl.removeAttribute('aria-busy');
    }
  }

  function pauseAllAudioSources() {
    try { el.player.pause(); } catch (_) {}
    if (spotify.player) {
      try { spotify.player.pause(); } catch (_) {}
    }
    stopGlowAnalyser();
  }

  function setPlaybackMode(next, opts) {
    opts = opts || {};
    const prev = video.mode;
    if (next !== 'audio' && next !== 'video') return;
    if (next === prev && !opts.force) { updateModeToggleUi(); return; }
    video.mode = next;
    try { localStorage.setItem(video.modeKey, next); } catch (_) {}
    document.body.classList.toggle('mode-video', next === 'video');
    if (el.videoStage) {
      el.videoStage.hidden = next !== 'video';
      el.videoStage.setAttribute('aria-hidden', next === 'video' ? 'false' : 'true');
    }
    if (next !== 'video' && videoSearch.open) closeVideoSearchPanel();
    updateModeToggleUi();
    if (next === 'video') {
      pauseAllAudioSources();
      loadVideoForCurrentTrack();
    } else {
      pauseYouTube();
      document.body.classList.remove('playing');
      if (state.current && opts.resumeAudio !== false && state.firstInteraction) {
        const t = state.current;
        const useSpotify = sdkActive() && !!t.spotify_id;
        if (useSpotify) {
          try { spotifyPlayTrack(t.spotify_id); } catch (_) {}
        } else {
          try {
            if (!el.player.src) {
              const streamKey = (t.apple_id != null ? t.apple_id : t.spotify_id) || '';
              el.player.src = authedStreamUrl('/api/stream/' + encodeURIComponent(streamKey));
            }
            const p = el.player.play();
            if (p && typeof p.catch === 'function') p.catch(() => {});
          } catch (_) {}
        }
      }
    }
  }

  function updateModeToggleUi() {
    if (!el.modeAudio || !el.modeVideo) return;
    const isVideo = isVideoMode();
    el.modeAudio.classList.toggle('is-active', !isVideo);
    el.modeVideo.classList.toggle('is-active', isVideo);
    el.modeAudio.setAttribute('aria-checked', String(!isVideo));
    el.modeVideo.setAttribute('aria-checked', String(isVideo));
  }

  function updateModeToggleDisabled() {
    if (!el.modeVideo) return;
    const t = state.current;
    const knownNoVideo = !!(t && t.youtube_id === null && t.youtube_queried_at);
    if (knownNoVideo) {
      el.modeVideo.setAttribute('aria-disabled', 'true');
      if (isVideoMode()) setPlaybackMode('audio');
    } else {
      el.modeVideo.removeAttribute('aria-disabled');
    }
  }

  if (el.modeAudio) {
    el.modeAudio.addEventListener('click', () => {
      state.firstInteraction = true;
      setPlaybackMode('audio');
    });
  }
  if (el.modeVideo) {
    el.modeVideo.addEventListener('click', () => {
      if (el.modeVideo.getAttribute('aria-disabled') === 'true') return;
      state.firstInteraction = true;
      setPlaybackMode('video');
    });
  }

  if (el.videoEditBtn) {
    el.videoEditBtn.addEventListener('click', (ev) => {
      ev.stopPropagation();
      openVideoSearchPanel();
    });
  }
  if (el.videoSearchFromEmpty) {
    el.videoSearchFromEmpty.addEventListener('click', (ev) => {
      ev.stopPropagation();
      openVideoSearchPanel();
    });
  }
  if (el.videoSearchClose) {
    el.videoSearchClose.addEventListener('click', () => closeVideoSearchPanel());
  }
  if (el.videoSearchForm) {
    el.videoSearchForm.addEventListener('submit', (ev) => {
      ev.preventDefault();
      const q = (el.videoSearchInput && el.videoSearchInput.value || '').trim();
      if (!q) return;
      runVideoSearch(q);
    });
  }
  document.addEventListener('mousedown', (ev) => {
    if (!videoSearch.open) return;
    if (!el.videoSearchPanel) return;
    const t = ev.target;
    if (el.videoSearchPanel.contains(t)) return;
    if (el.videoEditBtn && el.videoEditBtn.contains(t)) return;
    if (el.videoSearchFromEmpty && el.videoSearchFromEmpty.contains(t)) return;
    closeVideoSearchPanel();
  });

  (function initPlaybackMode() {
    let saved = 'audio';
    try { saved = localStorage.getItem(video.modeKey) || 'audio'; } catch (_) {}
    video.mode = saved === 'video' ? 'video' : 'audio';
    document.body.classList.toggle('mode-video', video.mode === 'video');
    if (el.videoStage) {
      el.videoStage.hidden = video.mode !== 'video';
      el.videoStage.setAttribute('aria-hidden', video.mode === 'video' ? 'false' : 'true');
    }
    updateModeToggleUi();
    if (video.mode === 'video') loadYouTubeApi();
  })();

  function updateMediaSessionMetadata(t) {
    if (!('mediaSession' in navigator) || !t) return;
    const artwork = t.artwork_url
      ? [{ src: t.artwork_url, sizes: '300x300', type: 'image/jpeg' }]
      : [];
    try {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: t.title || 'Untitled',
        artist: t.artist || 'Unknown artist',
        album: t.album || '',
        artwork,
      });
    } catch (e) {}
  }

  function setMediaSessionState(playing) {
    if (!('mediaSession' in navigator)) return;
    try {
      navigator.mediaSession.playbackState = playing ? 'playing' : 'paused';
    } catch (e) {}
  }

  function setMediaSessionPosition(durationSec, positionSec) {
    if (!('mediaSession' in navigator) || !('setPositionState' in navigator.mediaSession)) return;
    if (!isFinite(durationSec) || durationSec <= 0) return;
    const pos = Math.max(0, Math.min(positionSec || 0, durationSec));
    try {
      navigator.mediaSession.setPositionState({ duration: durationSec, playbackRate: 1, position: pos });
    } catch (e) {}
  }

  function mediaPlay() {
    state.firstInteraction = true;
    if (!state.current) { fetchTrack(state.vibe); return; }
    if (isVideoMode()) {
      if (video.player && video.ready) {
        try { video.player.playVideo(); } catch (_) {}
      } else {
        loadVideoForCurrentTrack();
      }
      return;
    }
    if (sdkActive() && spotify.player) {
      try { spotify.player.resume(); } catch (e) {}
      return;
    }
    if (el.player && el.player.paused) {
      const p = el.player.play();
      if (p && typeof p.catch === 'function') p.catch(() => {});
    }
  }

  function mediaPause() {
    state.firstInteraction = true;
    if (isVideoMode()) {
      pauseYouTube();
      return;
    }
    if (sdkActive() && spotify.player) {
      try { spotify.player.pause(); } catch (e) {}
      return;
    }
    if (el.player && !el.player.paused) {
      el.player.pause();
    }
  }

  function applyMediaSessionHandlers() {
    if (!('mediaSession' in navigator)) return;
    const handlers = {
      play: mediaPlay,
      pause: mediaPause,
      nexttrack: () => { advanceToNext(); },
      previoustrack: () => { advanceToNext(); },
    };
    for (const [action, fn] of Object.entries(handlers)) {
      try { navigator.mediaSession.setActionHandler(action, fn); } catch (e) {}
    }
  }
  applyMediaSessionHandlers();

  function progressPosFromEvent(e) {
    const rect = el.progress.getBoundingClientRect();
    const x = ('touches' in e && e.touches[0]) ? e.touches[0].clientX : e.clientX;
    const frac = Math.min(1, Math.max(0, (x - rect.left) / rect.width));
    return frac;
  }
  function seekTo(frac) {
    if (isVideoMode() && video.player && video.ready) {
      let dur = 0;
      try { dur = video.player.getDuration() || 0; } catch (_) {}
      if (dur > 0) {
        try { video.player.seekTo(frac * dur, true); } catch (_) {}
      }
      el.progressFill.style.width = (frac * 100) + '%';
      el.progressThumb.style.left = (frac * 100) + '%';
      el.tCur.textContent = fmtTime(dur * frac);
      return;
    }
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
    if (isVideoMode() && video.player && video.ready) {
      try { const d = video.player.getDuration() || 0; if (d > 0) return d; } catch (_) {}
    }
    if (sdkActive() && spotify.lastState) return (spotify.durationMs || 0) / 1000;
    const d = el.player.duration;
    return isFinite(d) && d > 0 ? d : 0;
  }
  function currentPlaybackPositionSec() {
    if (isVideoMode() && video.player && video.ready) {
      try { return video.player.getCurrentTime() || 0; } catch (_) {}
    }
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
      advanceToNext();
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault();
      advanceToNext();
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
    const k = spotifyKeys();
    const tok = localStorage.getItem(k.tokenKey);
    const exp = parseInt(localStorage.getItem(k.expiryKey) || '0', 10);
    const refresh = localStorage.getItem(k.refreshKey) || '';
    const profileRaw = localStorage.getItem(k.profileKey) || '';
    if (!tok || !exp) return false;
    // Force re-signin if scope version is missing or older than current
    const storedScopeVersion = parseInt(localStorage.getItem(k.scopeVersionKey) || '0', 10);
    if (!storedScopeVersion || storedScopeVersion < SPOTIFY_SCOPE_VERSION) {
      clearSpotifySession();
      toast('Spotify sign-in refresh needed to enable playlist link import.', 'info', {
        action: { label: 'Sign in', onClick: () => beginSpotifyLogin() }
      });
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
        spotify.spotifyUserId = p.spotify_user_id || '';
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
    // If the sync modal's URL tab is currently visible, keep its Spotify-gated
    // state fresh (input vs sign-in prompt).
    if (sync.open && sync.stage === 'url' && typeof refreshUrlViewState === 'function') {
      refreshUrlViewState();
    }
  }

  function clearSpotifySession() {
    const k = spotifyKeys();
    localStorage.removeItem(k.tokenKey);
    localStorage.removeItem(k.refreshKey);
    localStorage.removeItem(k.expiryKey);
    localStorage.removeItem(k.profileKey);
    localStorage.removeItem(k.scopeVersionKey);
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
    localStorage.setItem(spotifyKeys().verifierKey, verifier);
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

    // Same-window redirect is the primary flow — it survives modern browser
    // storage partitioning (Firefox strict, Brave shields, Safari ITP,
    // installed PWAs) that severs the localStorage bridge between a popup
    // and its opener. Return here after Spotify redirects back to
    // /?spotify_code=... which handleRedirectReturn picks up.
    //
    // The old popup path (window.open + watchPopupForCode + storage-event
    // bridge) is left in place below `return` so future work can re-enable
    // it behind an env flag if we want the "modal on desktop" UX back.
    try {
      sessionStorage.setItem('spotify_return_to', window.location.pathname + window.location.search + window.location.hash);
    } catch (_) {}
    toast('Redirecting to Spotify…', 'info');
    window.location.assign(url);
    return;

    // ---- legacy popup path (unreachable — kept for reference) ----
    // eslint-disable-next-line no-unreachable
    const w = 520, h = 720;
    const left = window.screenX + (window.outerWidth - w) / 2;
    const top = window.screenY + (window.outerHeight - h) / 2;
    const features = `width=${w},height=${h},left=${left},top=${top},resizable=yes,scrollbars=yes`;
    let popup = null;
    try { popup = window.open(url, OAUTH_POPUP_NAME, features); } catch (_) { popup = null; }

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
    const k = spotifyKeys();
    const verifier = localStorage.getItem(k.verifierKey) || '';
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
        let detail = '';
        try {
          const body = await r.clone().text();
          detail = ' [' + r.status + '] ' + body.slice(0, 200);
        } catch (_) {}
        console.error('[VibeScape] Spotify token exchange failed', r.status, detail, 'redirect_uri sent:', spotify.redirectUri);
        toast('Spotify token exchange failed.' + detail, 'error');
        return;
      }
      const j = await r.json();
      spotify.accessToken = j.access_token || '';
      spotify.refreshToken = j.refresh_token || '';
      spotify.expiresAt = Date.now() + ((j.expires_in || 3600) * 1000);
      localStorage.setItem(k.tokenKey, spotify.accessToken);
      if (spotify.refreshToken) localStorage.setItem(k.refreshKey, spotify.refreshToken);
      localStorage.setItem(k.expiryKey, String(spotify.expiresAt));
      localStorage.setItem(k.scopeVersionKey, String(SPOTIFY_SCOPE_VERSION));
      localStorage.removeItem(k.verifierKey);
      await fetchSpotifyProfile();
      // Link Spotify identity to VibeScape user server-side
      linkSpotifyToVibeScape().catch(() => {});
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
      const k = spotifyKeys();
      localStorage.setItem(k.tokenKey, spotify.accessToken);
      localStorage.setItem(k.refreshKey, spotify.refreshToken);
      localStorage.setItem(k.expiryKey, String(spotify.expiresAt));
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
      spotify.spotifyUserId = j.id || '';
      localStorage.setItem(spotifyKeys().profileKey, JSON.stringify({
        display_name: spotify.displayName,
        product: j.product || '',
        spotify_user_id: spotify.spotifyUserId
      }));
    } catch (e) { console.warn('Spotify /v1/me error:', e); }
  }

  async function linkSpotifyToVibeScape() {
    if (!auth.token) return;
    if (!spotify.spotifyUserId && !spotify.displayName) return;
    try {
      await fetchWithAuth('/api/auth/spotify-link', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          spotify_user_id: spotify.spotifyUserId || '',
          spotify_display_name: spotify.displayName || ''
        })
      });
    } catch (e) {
      console.warn('[VibeScape] spotify-link failed:', e);
    }
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
      // Immediately transfer playback to our SDK device so /me/player/play
      // has a valid active target. Without this, Spotify treats another
      // client (phone/desktop/other tab) as active and returns 404.
      transferPlayback(device_id);
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
        setMediaSessionState(false);
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
      if (!isVideoMode()) {
        setMediaSessionState(!playerState.paused);
        applyMediaSessionHandlers();
        setMediaSessionPosition((spotify.durationMs || 0) / 1000, (spotify.positionMs || 0) / 1000);
        renderSpotifyProgress();
      }

      // detect end-of-track: paused, position 0, and a previous track exists
      const prevTracks = (playerState.track_window && playerState.track_window.previous_tracks) || [];
      if (playerState.paused && playerState.position === 0 && prevTracks.length > 0) {
        setTimeout(() => {
          if (spotify.lastState && spotify.lastState.paused && spotify.lastState.position === 0) {
            advanceToNext();
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
      setMediaSessionPosition(spotify.durationMs / 1000, pos / 1000);
    }, 250);
  }

  function stopPositionPolling() {
    if (spotify.pollTimer) {
      clearInterval(spotify.pollTimer);
      spotify.pollTimer = null;
    }
  }

  // PUT /me/player to declare our SDK device as the active playback target.
  // Called once when SDK 'ready' fires and defensively on 404 from /player/play
  // (another Spotify client may have stolen the active-device slot).
  async function transferPlayback(deviceId) {
    if (!deviceId || !spotify.accessToken) return false;
    try {
      const r = await fetch('https://api.spotify.com/v1/me/player', {
        method: 'PUT',
        headers: {
          'Authorization': 'Bearer ' + spotify.accessToken,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ device_ids: [deviceId], play: false })
      });
      // 204 = transferred, 202 = accepted (queued), both fine
      if (r.ok || r.status === 204 || r.status === 202) return true;
      console.warn('[VibeScape] transferPlayback failed:', r.status);
      return false;
    } catch (e) {
      console.warn('[VibeScape] transferPlayback error:', e);
      return false;
    }
  }

  function fallbackToPreview(reason) {
    if (!state.current) return;
    setSourcePill('preview');
    const streamKey = (state.current.apple_id != null ? state.current.apple_id : state.current.spotify_id) || '';
    el.player.src = authedStreamUrl('/api/stream/' + encodeURIComponent(streamKey));
    const p = el.player.play();
    if (p && typeof p.catch === 'function') p.catch(() => {});
    if (reason) console.warn('[VibeScape] Spotify → preview fallback:', reason);
  }

  // Single PUT /me/player/play call — pulled out so the retry-after-transfer
  // path doesn't have to duplicate the fetch body.
  async function _doSpotifyPlay(spotifyId) {
    return fetch('https://api.spotify.com/v1/me/player/play?device_id=' + encodeURIComponent(spotify.deviceId), {
      method: 'PUT',
      headers: {
        'Authorization': 'Bearer ' + spotify.accessToken,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ uris: ['spotify:track:' + spotifyId] })
    });
  }

  async function spotifyPlayTrack(spotifyId) {
    if (!spotify.deviceId || !spotify.accessToken) return;
    if (Date.now() >= spotify.expiresAt - 60_000) {
      const ok = await refreshSpotifyToken();
      if (!ok) return;
    }
    // Defensive re-transfer before each play — cheap (single PUT) and
    // guarantees our device is active even if another client stole context
    // since the last play.
    await transferPlayback(spotify.deviceId);
    try {
      let r = await _doSpotifyPlay(spotifyId);
      if (r.status === 401) {
        // Token expired mid-flight; refresh + retry once
        const ok = await refreshSpotifyToken();
        if (ok) r = await _doSpotifyPlay(spotifyId);
      }
      if (r.status === 404) {
        // Device gone — try one more transfer + retry
        console.warn('[VibeScape] play 404 — retrying after transfer');
        const transferred = await transferPlayback(spotify.deviceId);
        if (transferred) {
          // Small delay lets Spotify propagate the transfer server-side before we
          // hit /play again. Empirically 250-400ms is enough on most networks.
          await new Promise((res) => setTimeout(res, 300));
          r = await _doSpotifyPlay(spotifyId);
        }
        if (r.status === 404) {
          // Still 404 after re-transfer — device permanently lost or backend
          // refuses. Fall back to preview and surface an actionable toast.
          fallbackToPreview('play 404 twice');
          toast("Spotify playback couldn't take over. Pause Spotify on other devices, then try again.", 'warning', {
            action: {
              label: 'Retry',
              onClick: async () => {
                await transferPlayback(spotify.deviceId);
                if (state.current && state.current.spotify_id) {
                  setSourcePill('spotify');
                  spotifyPlayTrack(state.current.spotify_id);
                }
              }
            }
          });
          return;
        }
      }
      if (r.status === 403) {
        // 403 is a policy/entitlement error (non-Premium, region-restricted,
        // etc.) — genuine "can't play" case, drop to preview permanently for
        // this session.
        toast('Spotify playback unavailable — falling back to preview.', 'warning');
        spotify.isPremium = false;
        fallbackToPreview('403');
        return;
      }
      if (!r.ok && r.status !== 204) {
        console.warn('[VibeScape] unexpected play status:', r.status);
      }
    } catch (e) {
      console.warn('[VibeScape] spotifyPlayTrack error:', e);
      toast('Spotify play error — falling back to preview.', 'warning');
      fallbackToPreview('exception');
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
    // Append last-known ingest auth_mode (user_oauth | app_token) if we have
    // one from a recent job status poll — useful for debugging why a public
    // playlist ingest picked one path or the other.
    const authModeSuffix = sync.authMode ? ` · ingest: ${sync.authMode}` : '';
    el.debugUser.textContent = spotify.displayName
      ? `${spotify.displayName}${spotify.isPremium ? ' · premium' : ' · free'}${authModeSuffix}`
      : ('(not signed in)' + authModeSuffix);
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

    // ML PREDICTIONS (only shown when the batch predictor has run for this track)
    const ePred = get('energy_pred');
    const dPred = get('danceability_pred');
    const vPred = get('valence_pred');
    const vibeML = get('vibe_score_ml');
    const modelV = get('model_version');
    if (ePred !== null || dPred !== null || vPred !== null) {
      const ml = makeGroup('ML predictions' + (modelV ? ' (' + modelV + ')' : ''));
      if (ePred !== null) ml.appendChild(makeRow('energy_pred', ePred, { bar: true, barValue: ePred, decimals: 3 }));
      if (dPred !== null) ml.appendChild(makeRow('danceability_pred', dPred, { bar: true, barValue: dPred, decimals: 3 }));
      if (vPred !== null) ml.appendChild(makeRow('valence_pred', vPred, { bar: true, barValue: vPred, decimals: 3 }));
      if (vibeML !== null) ml.appendChild(makeRow('vibe_score_ml', vibeML, { bar: true, barValue: vibeML, decimals: 3, annot: '0.55E + 0.45D' }));
      el.metricsContent.appendChild(ml);
    }

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
      const r = await fetchWithAuth('/api/tracks/' + encodeURIComponent(key) + '/features');
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

  // ===== Persistent search bar (library + Spotify catalog) =====
  // `dropdownOpen` tracks whether the results dropdown is visible. The input
  // itself is always mounted above the stage; the dropdown floats over it.
  const search = {
    dropdownOpen: false,
    query: '',
    reqToken: 0,
    debounceTimer: null,
    lastResults: { library: [], spotify: [], spotifyErr: null }
  };

  function escapeHtml(str) {
    return String(str == null ? '' : str)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // Compute the predicted vibe (0-100 int) for a track row. Prefers the ML
  // prediction (stored 0-1) and falls back to the formula-based vibe_score
  // (already 0-100). Returns null if neither is populated.
  function trackVibe(t) {
    if (!t) return null;
    // Metadata-only tracks have a placeholder vibe_score (50) so the NOT NULL
    // constraint is satisfied. That number isn't a real prediction — suppress
    // the pill so users don't see misleading "vibe 50" chips on unanalyzed songs.
    if (t.classification_source === 'metadata_only') return null;
    const ml = t.vibe_score_ml;
    if (ml != null && !isNaN(ml)) return Math.round(Math.max(0, Math.min(100, ml * 100)));
    const raw = t.vibe_score;
    if (raw != null && !isNaN(raw)) return Math.round(Math.max(0, Math.min(100, raw)));
    return null;
  }

  // Snap the app's global vibe slider to the given track's vibe. Used when
  // the user picks a specific track from search — we want the ambient state
  // (colour accent, slider position, subsequent random-track vibe range) to
  // follow the just-played song, not stay wherever the user left it.
  function setVibeFromTrack(t) {
    const v = trackVibe(t);
    if (v == null) return;
    state.vibe = v;
    if (el.slider) el.slider.value = String(v);
    try { updateSliderVisual(v); } catch (_) {}
    try { applyAccent(v); } catch (_) {}
  }

  // Human-readable name for ISO 639-1 codes Whisper returns. Anything not
  // in the map falls back to the uppercased code (e.g. "SW", "AF").
  const LANG_NAMES = {
    en: 'English', es: 'Spanish', fr: 'French', de: 'German', it: 'Italian',
    pt: 'Portuguese', ru: 'Russian', ja: 'Japanese', ko: 'Korean', zh: 'Chinese',
    ar: 'Arabic', hi: 'Hindi', bn: 'Bengali', pa: 'Punjabi', ta: 'Tamil',
    te: 'Telugu', ml: 'Malayalam', kn: 'Kannada', mr: 'Marathi', gu: 'Gujarati',
    ur: 'Urdu', tr: 'Turkish', vi: 'Vietnamese', th: 'Thai', id: 'Indonesian',
    ms: 'Malay', tl: 'Filipino', nl: 'Dutch', sv: 'Swedish', no: 'Norwegian',
    da: 'Danish', fi: 'Finnish', pl: 'Polish', cs: 'Czech', el: 'Greek',
    he: 'Hebrew', fa: 'Persian', uk: 'Ukrainian', ro: 'Romanian', hu: 'Hungarian'
  };
  function languageLabel(code) {
    if (!code) return '';
    const key = String(code).toLowerCase().trim();
    return LANG_NAMES[key] || key.toUpperCase();
  }

  // Metadata-only tracks have title/artist/artwork but no local audio and no
  // ML analysis. Playable only through the Spotify Web Playback SDK.
  function isMetadataOnly(t) {
    return !!(t && t.classification_source === 'metadata_only');
  }
  // HTML for the small sdk-only badge — reused by search rows, queue rows,
  // and recs rows. Empty string when the track has a normal audio path.
  function sdkOnlyBadgeHtml(t) {
    if (!isMetadataOnly(t)) return '';
    return '<span class="sdk-only-badge" title="No local audio — playable only via Spotify Premium">sdk-only</span>';
  }

  function updateTitleMeta(t) {
    if (!el.titleVibe && !el.titleLang && !el.titleSdkOnly) return;
    const v = trackVibe(t);
    if (el.titleVibe) {
      if (v != null) {
        el.titleVibe.textContent = 'vibe ' + v;
        el.titleVibe.hidden = false;
        el.titleVibe.setAttribute('data-vibe', String(v));
      } else {
        el.titleVibe.hidden = true;
      }
    }
    if (el.titleLang) {
      const raw = t && t.language ? String(t.language) : '';
      const label = languageLabel(raw);
      if (label) {
        el.titleLang.textContent = label;
        el.titleLang.hidden = false;
        el.titleLang.setAttribute('data-lang', String(raw).toLowerCase());
        const conf = t && t.language_confidence;
        if (conf != null && !isNaN(conf)) {
          el.titleLang.title = 'predicted language: ' + label +
            ' (confidence ' + Math.round(Math.max(0, Math.min(1, conf)) * 100) + '%)';
        } else {
          el.titleLang.title = 'predicted language: ' + label;
        }
      } else {
        el.titleLang.hidden = true;
      }
    }
    if (el.titleSdkOnly) {
      el.titleSdkOnly.hidden = !isMetadataOnly(t);
    }
  }
  function clearTitleMeta() {
    if (el.titleVibe) el.titleVibe.hidden = true;
    if (el.titleLang) el.titleLang.hidden = true;
    if (el.titleSdkOnly) el.titleSdkOnly.hidden = true;
  }

  function setSearchStage(name) {
    if (el.searchEmpty) el.searchEmpty.hidden = name !== 'empty';
    if (el.searchLoading) el.searchLoading.hidden = name !== 'loading';
    if (el.searchResults) el.searchResults.hidden = name !== 'results';
    if (el.searchNone) el.searchNone.hidden = name !== 'none';
  }

  function showSearchDropdown() {
    if (!el.searchDropdown) return;
    if (!search.dropdownOpen) {
      el.searchDropdown.hidden = false;
      search.dropdownOpen = true;
      if (el.searchInput) el.searchInput.setAttribute('aria-expanded', 'true');
    }
  }

  function hideSearchDropdown() {
    if (!el.searchDropdown) return;
    if (search.dropdownOpen) {
      el.searchDropdown.hidden = true;
      search.dropdownOpen = false;
      if (el.searchInput) el.searchInput.setAttribute('aria-expanded', 'false');
    }
    if (search.debounceTimer) { clearTimeout(search.debounceTimer); search.debounceTimer = null; }
  }

  function clearSearchInput({ blur } = { blur: false }) {
    if (el.searchInput) {
      el.searchInput.value = '';
      if (blur) { try { el.searchInput.blur(); } catch (_) {} }
    }
    if (el.searchInputWrap) el.searchInputWrap.classList.remove('has-query');
    if (el.searchClear) el.searchClear.hidden = true;
    search.query = '';
    search.lastResults = { library: [], spotify: [], spotifyErr: null };
    hideSearchDropdown();
  }

  function focusSearchInput() {
    if (!el.searchInput) return;
    try { el.searchInput.focus(); el.searchInput.select(); } catch (_) {}
    // If there's an existing query, re-show the dropdown so results are visible.
    if ((el.searchInput.value || '').trim()) showSearchDropdown();
  }

  function renderSearchResults() {
    if (!el.searchResults) return;
    const lib = search.lastResults.library || [];
    const sp = search.lastResults.spotify || [];
    const spotifySignedIn = !!(spotify && spotify.accessToken);
    const spErr = search.lastResults.spotifyErr;

    if (!lib.length && !sp.length && !spErr) {
      setSearchStage('none');
      return;
    }
    setSearchStage('results');

    const parts = [];

    // Library section
    parts.push('<div class="search-section">');
    parts.push('<div class="search-section-title"><span>Your library</span>' +
      (lib.length ? ('<span class="search-section-hint">' + lib.length + ' match' + (lib.length === 1 ? '' : 'es') + '</span>') : '') +
      '</div>');
    if (lib.length) {
      lib.forEach((t) => {
        const key = t.spotify_id || t.apple_id || '';
        const title = escapeHtml(t.title || '(unknown)');
        const artist = escapeHtml(t.artist || '');
        const album = escapeHtml(t.album || '');
        const art = t.artwork_url ? '<img class="search-item-art" src="' + escapeHtml(t.artwork_url) + '" alt="" loading="lazy" />' : '<div class="search-item-art"></div>';
        const sub = artist + (album && artist ? ' &middot; ' : '') + album;
        const vibe = trackVibe(t);
        const vibeChip = vibe != null
          ? '<span class="search-item-vibe" data-vibe="' + vibe + '" title="predicted vibe">vibe ' + vibe + '</span>'
          : '';
        const sdkBadge = sdkOnlyBadgeHtml(t);
        const keyEsc = escapeHtml(String(key));
        parts.push(
          '<div class="search-item" data-action="play-library" data-key="' + keyEsc + '" tabindex="0" role="button">' +
          art +
          '<div class="search-item-body">' +
            '<div class="search-item-title-row">' +
              '<span class="search-item-title">' + title + '</span>' +
              vibeChip +
              sdkBadge +
            '</div>' +
            '<div class="search-item-sub">' + sub + '</div>' +
          '</div>' +
          '<button class="search-item-queue" type="button" data-action="queue-library" data-key="' + keyEsc + '" aria-label="Add to queue" title="Add to queue">' +
            '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>' +
          '</button>' +
          '</div>'
        );
      });
    } else {
      parts.push('<div class="search-item-status">No matches in your library.</div>');
    }
    parts.push('</div>');

    // Spotify section
    parts.push('<div class="search-section">');
    parts.push('<div class="search-section-title"><span>From Spotify</span>' +
      (spotifySignedIn ? '' : '<span class="search-section-hint">Sign in with Spotify to enable</span>') +
      '</div>');
    if (!spotifySignedIn) {
      parts.push('<div class="search-item-status">Spotify catalog search is disabled until you sign in.</div>');
    } else if (spErr) {
      parts.push('<div class="search-item-status is-error">' + escapeHtml(spErr) + '</div>');
    } else if (!sp.length) {
      parts.push('<div class="search-item-status">No matches on Spotify.</div>');
    } else {
      sp.forEach((t) => {
        const sid = escapeHtml(String(t.spotify_id || ''));
        const title = escapeHtml(t.title || '(unknown)');
        const artist = escapeHtml(t.artist || '');
        const album = escapeHtml(t.album || '');
        const art = t.artwork_url ? '<img class="search-item-art" src="' + escapeHtml(t.artwork_url) + '" alt="" loading="lazy" />' : '<div class="search-item-art"></div>';
        const sub = artist + (album && artist ? ' &middot; ' : '') + album;
        const inLib = !!t.in_library;
        const vibe = trackVibe(t);
        const vibeChip = vibe != null
          ? '<span class="search-item-vibe" data-vibe="' + vibe + '" title="predicted vibe">vibe ' + vibe + '</span>'
          : '';
        const sdkBadge = sdkOnlyBadgeHtml(t);
        const badge = inLib
          ? '<span class="search-item-badge is-lib">in library</span>'
          : '<button class="search-item-add" type="button" data-action="add-spotify" data-spotify-id="' + sid + '">' +
              '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>' +
              '<span>Add</span>' +
            '</button>';
        // Queue button — shown for every Spotify result. For not-in-library
        // tracks the click ingests silently and adds to queue without
        // auto-playing (distinct from the "Add" button which ingests + plays).
        const queueBtn = '<button class="search-item-queue" type="button" data-action="queue-spotify" data-spotify-id="' + sid + '" aria-label="Add to queue" title="Add to queue">' +
          '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>' +
          '</button>';
        const clickAction = inLib ? 'play-spotify' : '';
        const itemAttrs = clickAction
          ? ' data-action="' + clickAction + '" data-spotify-id="' + sid + '"'
          : ' data-action="noop"';
        parts.push(
          '<div class="search-item"' + itemAttrs + '>' +
          art +
          '<div class="search-item-body">' +
            '<div class="search-item-title-row">' +
              '<span class="search-item-title">' + title + '</span>' +
              vibeChip +
              sdkBadge +
            '</div>' +
            '<div class="search-item-sub">' + sub + '</div>' +
          '</div>' +
          queueBtn +
          badge +
          '</div>'
        );
      });
    }
    parts.push('</div>');

    el.searchResults.innerHTML = parts.join('');
  }

  async function runSearch(query) {
    const q = (query || '').trim();
    search.query = q;
    if (!q) {
      hideSearchDropdown();
      return;
    }
    showSearchDropdown();
    setSearchStage('loading');
    const reqToken = ++search.reqToken;

    // Library search (always). Spotify search only if signed in.
    const libP = fetchWithAuth('/api/tracks/search?q=' + encodeURIComponent(q) + '&limit=15')
      .then(async (r) => {
        if (!r.ok) return { tracks: [] };
        try { return await r.json(); } catch (_) { return { tracks: [] }; }
      })
      .catch(() => ({ tracks: [] }));

    let spP;
    if (spotify && spotify.accessToken) {
      spP = fetchWithAuth('/api/spotify/search?q=' + encodeURIComponent(q) + '&limit=10', {
        headers: { 'X-Spotify-Authorization': 'Bearer ' + spotify.accessToken }
      }).then(async (r) => {
        if (r.status === 401) return { _err: 'Spotify session expired — reconnect Spotify.' };
        if (!r.ok) {
          let msg = 'Spotify search failed.';
          try {
            const j = await r.json();
            if (j && j.detail && j.detail.message) msg = 'Spotify: ' + j.detail.message;
          } catch (_) {}
          return { _err: msg };
        }
        try { return await r.json(); } catch (_) { return { tracks: [] }; }
      }).catch(() => ({ _err: 'Spotify search failed.' }));
    } else {
      spP = Promise.resolve({ tracks: [] });
    }

    const [libR, spR] = await Promise.all([libP, spP]);
    if (reqToken !== search.reqToken) return;

    search.lastResults.library = (libR && libR.tracks) || [];
    search.lastResults.spotify = (spR && spR.tracks) || [];
    search.lastResults.spotifyErr = (spR && spR._err) || null;
    renderSearchResults();
  }

  function scheduleSearch(query) {
    if (search.debounceTimer) clearTimeout(search.debounceTimer);
    search.debounceTimer = setTimeout(() => runSearch(query), 220);
  }

  function playLibraryByKey(key) {
    if (!key) return;
    // Library results carry the full track shape from _row_to_dict; find it
    // in the cached list rather than refetching.
    const lib = search.lastResults.library || [];
    const found = lib.find((t) => String(t.spotify_id || '') === String(key) || String(t.apple_id || '') === String(key));
    if (!found) {
      toast('Could not open that track.', 'error');
      return;
    }
    clearSearchInput({ blur: true });
    setVibeFromTrack(found);
    loadTrack(found);
    // Autoplay: mirror the pattern used elsewhere when a track is user-picked
    try { state.firstInteraction = true; } catch (_) {}
    try {
      if (el.player && typeof el.player.play === 'function') {
        // loadTrack already wires the audio src via its own path
      }
    } catch (_) {}
  }

  async function playSpotifyIdInLibrary(sid) {
    if (!sid) return;
    // Use the idempotent ingest/single endpoint. It handles three cases:
    //   1. Track already linked to user → returns the row instantly.
    //   2. Track exists globally but not linked → links user_tracks + returns row.
    //   3. Track doesn't exist → runs the ingest pipeline + returns row.
    // Case (2) is what fires when the "in library" badge was wrong (e.g., the
    // track sits in the global tracks table because another user has it, but
    // this user's user_tracks link is missing). Case (3) is safe fallback.
    try {
      const body = { spotify_id: sid };
      if (spotify && spotify.accessToken) body.access_token = spotify.accessToken;
      const r = await fetchWithAuth('/api/ingest/single', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      if (r.ok) {
        const j = await r.json();
        if (j && j.track) {
          clearSearchInput({ blur: true });
          setVibeFromTrack(j.track);
          loadTrack(j.track);
          return;
        }
      }
    } catch (_) {}
    toast('Could not open that track.', 'error');
  }

  async function addSpotifyTrack(sid, btn) {
    if (!sid) return;
    if (btn) {
      btn.classList.add('is-busy');
      btn.disabled = true;
      const span = btn.querySelector('span');
      if (span) span.textContent = 'Adding…';
    }
    try {
      const body = { spotify_id: sid };
      if (spotify && spotify.accessToken) body.access_token = spotify.accessToken;
      const r = await fetchWithAuth('/api/ingest/single', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      if (!r.ok) {
        let msg = 'Could not add track.';
        try {
          const j = await r.json();
          if (j && j.detail) {
            if (j.detail.error === 'no_preview_available') msg = 'No preview available for that track.';
            else if (j.detail.error === 'spotify_token_expired') msg = 'Spotify session expired.';
            else if (j.detail.message) msg = j.detail.message;
            else if (typeof j.detail === 'string') msg = j.detail;
          }
        } catch (_) {}
        toast(msg, 'error');
        if (btn) {
          btn.classList.remove('is-busy');
          btn.disabled = false;
          const span = btn.querySelector('span');
          if (span) span.textContent = 'Add';
        }
        return;
      }
      const j = await r.json();
      toast('Added to your library.', 'success');
      // Update local state: mark in_library on the cached result and re-render.
      const spList = search.lastResults.spotify || [];
      spList.forEach((t) => { if (t.spotify_id === sid) t.in_library = true; });
      // Merge the new track into library results too, at the top.
      if (j && j.track) {
        const lib = search.lastResults.library || [];
        if (!lib.find((t) => t.spotify_id === sid)) {
          search.lastResults.library = [j.track, ...lib];
        }
      }
      renderSearchResults();
      // Auto-play the just-added track.
      if (j && j.track) {
        clearSearchInput({ blur: true });
        setVibeFromTrack(j.track);
        loadTrack(j.track);
      }
    } catch (e) {
      toast('Could not add track: ' + (e && e.message ? e.message : String(e)), 'error');
      if (btn) {
        btn.classList.remove('is-busy');
        btn.disabled = false;
        const span = btn.querySelector('span');
        if (span) span.textContent = 'Add';
      }
    }
  }

  if (el.searchClear) el.searchClear.addEventListener('click', () => {
    clearSearchInput({ blur: false });
    // Keep focus on the input after clearing so user can immediately type again.
    if (el.searchInput) { try { el.searchInput.focus(); } catch (_) {} }
  });
  if (el.searchInput) {
    el.searchInput.addEventListener('input', (ev) => {
      const q = ev.target.value || '';
      if (el.searchInputWrap) el.searchInputWrap.classList.toggle('has-query', q.length > 0);
      if (el.searchClear) el.searchClear.hidden = q.length === 0;
      if (q.length === 0) {
        hideSearchDropdown();
      } else {
        showSearchDropdown();
        setSearchStage('loading');
      }
      scheduleSearch(q);
    });
    el.searchInput.addEventListener('focus', () => {
      const q = (el.searchInput.value || '').trim();
      if (q) showSearchDropdown();
    });
    el.searchInput.addEventListener('keydown', (ev) => {
      if (ev.key === 'Escape') {
        ev.preventDefault();
        clearSearchInput({ blur: true });
      }
    });
  }
  // Click-outside closes the dropdown (input stays where it is; just hides
  // the results panel). Keep it open on any click inside .search-bar.
  document.addEventListener('mousedown', (ev) => {
    if (!search.dropdownOpen) return;
    const t = ev.target;
    if (el.searchBar && el.searchBar.contains(t)) return;
    hideSearchDropdown();
  });
  if (el.searchResults) {
    el.searchResults.addEventListener('click', (ev) => {
      // Nested buttons first — stop propagation so the outer row click doesn't
      // also fire.
      const addBtn = ev.target.closest && ev.target.closest('[data-action="add-spotify"]');
      if (addBtn) {
        ev.preventDefault();
        ev.stopPropagation();
        addSpotifyTrack(addBtn.getAttribute('data-spotify-id') || '', addBtn);
        return;
      }
      const queueLibBtn = ev.target.closest && ev.target.closest('[data-action="queue-library"]');
      if (queueLibBtn) {
        ev.preventDefault();
        ev.stopPropagation();
        queueLibraryByKey(queueLibBtn.getAttribute('data-key') || '');
        return;
      }
      const queueSpBtn = ev.target.closest && ev.target.closest('[data-action="queue-spotify"]');
      if (queueSpBtn) {
        ev.preventDefault();
        ev.stopPropagation();
        queueSpotifyId(queueSpBtn.getAttribute('data-spotify-id') || '', queueSpBtn);
        return;
      }
      const item = ev.target.closest && ev.target.closest('.search-item[data-action]');
      if (!item) return;
      const action = item.getAttribute('data-action');
      if (action === 'play-library') {
        playLibraryByKey(item.getAttribute('data-key') || '');
      } else if (action === 'play-spotify') {
        playSpotifyIdInLibrary(item.getAttribute('data-spotify-id') || '');
      }
    });
  }

  // Queue-add handlers used by the search dropdown "+ queue" buttons.
  function queueLibraryByKey(key) {
    if (!key) return;
    const lib = search.lastResults.library || [];
    const t = lib.find((x) => String(x.spotify_id || '') === String(key) || String(x.apple_id || '') === String(key));
    if (!t) { toast('Could not queue that track.', 'error'); return; }
    if (addToQueue(t)) toast('Added to queue.', 'success');
  }

  async function queueSpotifyId(sid, btn) {
    if (!sid) return;
    // If the track is already in library (either in the cached Spotify search
    // result marked in_library, or already surfaced in the library section),
    // we can add it to the queue directly using the cached row.
    const spList = search.lastResults.spotify || [];
    const spCached = spList.find((x) => x.spotify_id === sid);
    const libCached = (search.lastResults.library || []).find((x) => x.spotify_id === sid);
    if (libCached) { if (addToQueue(libCached)) toast('Added to queue.', 'success'); return; }
    if (spCached && spCached.in_library) {
      // We only have the Spotify-shaped row (title/artist/vibe) — enough
      // to render in the queue. loadTrack will fetch/play when it advances.
      if (addToQueue(spCached)) toast('Added to queue.', 'success');
      return;
    }
    // Not in library — silently ingest and then queue the returned row.
    if (btn) { btn.classList.add('is-busy'); btn.disabled = true; }
    try {
      const body = { spotify_id: sid };
      if (spotify && spotify.accessToken) body.access_token = spotify.accessToken;
      const r = await fetchWithAuth('/api/ingest/single', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      if (!r.ok) {
        let msg = 'Could not queue track.';
        try {
          const j = await r.json();
          if (j && j.detail) {
            if (j.detail.error === 'no_preview_available') msg = 'No preview available for that track.';
            else if (j.detail.message) msg = j.detail.message;
          }
        } catch (_) {}
        toast(msg, 'error');
        return;
      }
      const j = await r.json();
      if (j && j.track) {
        if (addToQueue(j.track)) toast('Added to queue.', 'success');
        // Mark cached Spotify result as in_library so subsequent renders
        // are consistent with the DB.
        spList.forEach((x) => { if (x.spotify_id === sid) x.in_library = true; });
        renderSearchResults();
      }
    } catch (e) {
      toast('Could not queue track.', 'error');
    } finally {
      if (btn) { btn.classList.remove('is-busy'); btn.disabled = false; }
    }
  }

  // Escape + Ctrl+Shift+D shortcut (+ ? for help, Ctrl+Shift+M for metrics)
  document.addEventListener('keydown', (ev) => {
    // Ctrl/Cmd+K focuses the persistent search input from anywhere.
    if ((ev.ctrlKey || ev.metaKey) && (ev.key === 'k' || ev.key === 'K')) {
      ev.preventDefault();
      focusSearchInput();
      return;
    }
    if (ev.key === 'Escape') {
      // Search dropdown handled by the input's own keydown when focused;
      // this catches the case where dropdown is open but focus is elsewhere.
      if (search.dropdownOpen) { ev.preventDefault(); hideSearchDropdown(); return; }
      if (videoSearch.open) { ev.preventDefault(); closeVideoSearchPanel(); return; }
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

  // ===== Play queue + recommendations sidebar =====
  // Queue is user-managed only — never auto-fills. Added to via search
  // "+ queue" buttons and the recommendations panel. Advance-to-next
  // consumes queue[0]; when empty, falls back to random pick at the
  // current vibe (existing behaviour).

  function trackKeyOf(t) {
    if (!t) return '';
    return String(t.spotify_id || t.apple_id || '');
  }

  function queueContains(key) {
    if (!key) return false;
    return state.queue.some((t) => trackKeyOf(t) === key);
  }

  function addToQueue(t) {
    if (!t) return false;
    const key = trackKeyOf(t);
    if (!key) return false;
    if (queueContains(key)) {
      toast('Already in your queue.', 'info');
      return false;
    }
    state.queue.push(t);
    renderQueue();
    return true;
  }

  function removeFromQueue(idx) {
    if (idx < 0 || idx >= state.queue.length) return;
    state.queue.splice(idx, 1);
    renderQueue();
  }

  function clearQueue() {
    if (!state.queue.length) return;
    state.queue = [];
    renderQueue();
  }

  function jumpToQueueItem(idx) {
    if (idx < 0 || idx >= state.queue.length) return;
    // Drop everything above idx, then advance-to-next consumes idx.
    state.queue.splice(0, idx);
    advanceToNext();
  }

  function advanceToNext() {
    state.firstInteraction = true;
    if (state.queue.length > 0) {
      const next = state.queue.shift();
      renderQueue();
      setVibeFromTrack(next);
      loadTrack(next);
      return;
    }
    fetchTrack(state.vibe);
  }

  function renderQueue() {
    if (!el.queueList || !el.queueEmpty) return;
    const q = state.queue;
    if (!q.length) {
      el.queueList.hidden = true;
      el.queueList.innerHTML = '';
      el.queueEmpty.hidden = false;
      if (el.queueCount) { el.queueCount.hidden = true; el.queueCount.textContent = '0'; }
      if (el.queueClear) el.queueClear.hidden = true;
      return;
    }
    el.queueEmpty.hidden = true;
    el.queueList.hidden = false;
    if (el.queueCount) { el.queueCount.hidden = false; el.queueCount.textContent = String(q.length); }
    if (el.queueClear) el.queueClear.hidden = false;
    el.queueList.innerHTML = q.map((t, i) => renderQueueRow(t, i, 'queue')).join('');
  }

  function renderQueueRow(t, idx, kind) {
    const title = escapeHtml(t.title || '(unknown)');
    const artist = escapeHtml(t.artist || '');
    const album = escapeHtml(t.album || '');
    const sub = artist + (album && artist ? ' &middot; ' : '') + album;
    const art = t.artwork_url
      ? '<img class="queue-item-art" src="' + escapeHtml(t.artwork_url) + '" alt="" loading="lazy" />'
      : '<div class="queue-item-art"></div>';
    const vibe = trackVibe(t);
    const vibeHtml = vibe != null
      ? '<span class="queue-item-vibe">vibe ' + vibe + '</span>'
      : '';
    const lang = languageLabel(t.language || '');
    const langHtml = lang ? '<span class="queue-item-lang">' + escapeHtml(lang) + '</span>' : '';
    const sdkHtml = sdkOnlyBadgeHtml(t);
    const meta = (vibeHtml || langHtml || sdkHtml) ? '<div class="queue-item-meta">' + vibeHtml + langHtml + sdkHtml + '</div>' : '';

    const dataKey = escapeHtml(trackKeyOf(t));
    const actionAttrs = kind === 'queue'
      ? ' data-action="jump" data-idx="' + idx + '"'
      : ' data-action="play-rec" data-key="' + dataKey + '"';
    // Right-side button: remove-from-queue OR add-to-queue depending on kind.
    const rightBtn = kind === 'queue'
      ? '<button class="queue-item-btn" type="button" data-action="remove" data-idx="' + idx + '" aria-label="Remove from queue" title="Remove">' +
          '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>' +
        '</button>'
      : '<button class="queue-item-btn is-add" type="button" data-action="add-rec" data-key="' + dataKey + '" aria-label="Add to queue" title="Add to queue">' +
          '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>' +
        '</button>';
    return (
      '<li class="queue-item"' + actionAttrs + ' tabindex="0">' +
      art +
      '<div class="queue-item-body">' +
        '<div class="queue-item-title">' + title + '</div>' +
        '<div class="queue-item-sub">' + sub + '</div>' +
        meta +
      '</div>' +
      '<div class="queue-item-actions">' + rightBtn + '</div>' +
      '</li>'
    );
  }

  // ---- Recommendations ----

  async function loadRecommendationsFor(t) {
    if (!el.queueRecsList) return;
    const key = trackKeyOf(t);
    if (!key) {
      // No anchor — show idle state.
      state.recs.anchorKey = null;
      state.recs.list = [];
      if (el.queueRecsList) { el.queueRecsList.hidden = true; el.queueRecsList.innerHTML = ''; }
      if (el.queueRecsLoading) el.queueRecsLoading.hidden = true;
      if (el.queueRecsEmpty) el.queueRecsEmpty.hidden = false;
      return;
    }
    if (state.recs.anchorKey === key && state.recs.list.length) {
      // Same anchor, already cached — nothing to do.
      return;
    }
    state.recs.anchorKey = key;
    state.recs.loading = true;
    if (el.queueRecsEmpty) el.queueRecsEmpty.hidden = true;
    if (el.queueRecsList) { el.queueRecsList.hidden = true; el.queueRecsList.innerHTML = ''; }
    if (el.queueRecsLoading) el.queueRecsLoading.hidden = false;
    const reqToken = ++state.recs.reqToken;

    try {
      const r = await fetchWithAuth('/api/tracks/' + encodeURIComponent(key) + '/similar?limit=8');
      if (reqToken !== state.recs.reqToken) return;
      if (!r.ok) throw new Error('similar_fetch_failed');
      const j = await r.json();
      state.recs.list = (j && j.tracks) || [];
    } catch (_) {
      if (reqToken !== state.recs.reqToken) return;
      state.recs.list = [];
    } finally {
      if (reqToken === state.recs.reqToken) {
        state.recs.loading = false;
        if (el.queueRecsLoading) el.queueRecsLoading.hidden = true;
        renderRecs();
      }
    }
  }

  function renderRecs() {
    if (!el.queueRecsList) return;
    const list = state.recs.list;
    if (!list.length) {
      el.queueRecsList.hidden = true;
      el.queueRecsList.innerHTML = '';
      if (el.queueRecsEmpty) {
        const empty = el.queueRecsEmpty;
        empty.hidden = false;
        // Swap in a "nothing similar" message if there was an anchor.
        const hint = empty.querySelector('.queue-empty-hint');
        if (hint) {
          hint.textContent = state.recs.anchorKey
            ? 'No similar tracks in your library yet.'
            : 'Play a track to see similar songs from your library.';
        }
      }
      return;
    }
    if (el.queueRecsEmpty) el.queueRecsEmpty.hidden = true;
    el.queueRecsList.hidden = false;
    el.queueRecsList.innerHTML = list.map((t, i) => renderQueueRow(t, i, 'rec')).join('');
  }

  // Delegated click handling for both lists.
  function onQueueSidebarClick(ev) {
    const btn = ev.target.closest && ev.target.closest('button[data-action]');
    if (btn) {
      const action = btn.getAttribute('data-action');
      if (action === 'remove') {
        ev.preventDefault();
        ev.stopPropagation();
        removeFromQueue(parseInt(btn.getAttribute('data-idx'), 10));
        return;
      }
      if (action === 'add-rec') {
        ev.preventDefault();
        ev.stopPropagation();
        const key = btn.getAttribute('data-key') || '';
        const t = (state.recs.list || []).find((x) => trackKeyOf(x) === key);
        if (t) {
          if (addToQueue(t)) toast('Added to queue.', 'success');
        }
        return;
      }
    }
    const row = ev.target.closest && ev.target.closest('.queue-item[data-action]');
    if (!row) return;
    const action = row.getAttribute('data-action');
    if (action === 'jump') {
      jumpToQueueItem(parseInt(row.getAttribute('data-idx'), 10));
    } else if (action === 'play-rec') {
      const key = row.getAttribute('data-key') || '';
      const t = (state.recs.list || []).find((x) => trackKeyOf(x) === key);
      if (t) {
        setVibeFromTrack(t);
        loadTrack(t);
      }
    }
  }

  if (el.queueSidebar) el.queueSidebar.addEventListener('click', onQueueSidebarClick);
  if (el.queueClear) el.queueClear.addEventListener('click', clearQueue);

  // Initial paint
  renderQueue();
  renderRecs();

  // ===== Library sync modal =====

  function setSyncView(name) {
    sync.stage = name;
    const map = {
      loading: el.syncViewLoading,
      error: el.syncViewError,
      select: el.syncViewSelect,
      url: el.syncViewUrl,
      progress: el.syncViewProgress,
      complete: el.syncViewComplete
    };
    Object.values(map).forEach((v) => { if (v) v.hidden = true; });
    if (map[name]) map[name].hidden = false;

    // Tabs are visible only on the two "picker" stages (select + url) — hidden
    // once we're in progress / complete / error / loading. Keeps the modal
    // focused during ingest.
    const showTabs = (name === 'select' || name === 'url' || name === 'loading');
    if (el.syncTabs) el.syncTabs.hidden = !showTabs;

    // Footer variants
    el.syncModalFooter.hidden = false;
    if (name === 'select') {
      el.btnSyncStart.hidden = false;
      el.btnSyncStart.textContent = 'Sync selected';
      el.btnSyncCancel.textContent = 'Cancel';
      el.syncFooterMeta.hidden = false;
    } else if (name === 'url') {
      el.btnSyncStart.hidden = false;
      el.btnSyncStart.textContent = 'Add playlist';
      el.btnSyncStart.disabled = !sync.urlValid;
      el.btnSyncCancel.textContent = 'Cancel';
      el.syncFooterMeta.hidden = false;
      el.syncFooterMeta.textContent = sync.urlValid ? 'Ready to add' : 'Paste a playlist link';
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

  // Extract a playlist ID from a URL, URI, or bare ID string. Returns null if invalid.
  function parsePlaylistId(raw) {
    if (!raw) return null;
    const s = String(raw).trim();
    if (!s) return null;
    if (SPOTIFY_BARE_ID_RE.test(s)) return s;
    const m = s.match(SPOTIFY_PLAYLIST_URL_RE);
    return m ? m[1] : null;
  }

  // Swap the URL tab body between the signed-in (paste input) and signed-out
  // (Spotify sign-in prompt) states. Called on tab open + whenever Spotify
  // session changes (login / logout / expiry).
  function refreshUrlViewState() {
    if (!el.syncUrlSignedIn || !el.syncUrlSignedOut) return;
    const signedIn = !!spotify.accessToken;
    el.syncUrlSignedIn.hidden = !signedIn;
    el.syncUrlSignedOut.hidden = signedIn;
    // When switching TO signed-out, force Add button off + hint text neutral
    if (!signedIn && sync.stage === 'url') {
      sync.urlValid = false;
      if (el.btnSyncStart) {
        el.btnSyncStart.hidden = true; // no primary action available without Spotify
      }
      if (el.syncFooterMeta) el.syncFooterMeta.textContent = 'Spotify sign-in required';
    } else if (signedIn && sync.stage === 'url') {
      if (el.btnSyncStart) el.btnSyncStart.hidden = false;
      updateUrlHint();
    }
  }

  function updateUrlHint() {
    if (!el.syncUrlInput || !el.syncUrlHint) return;
    const raw = el.syncUrlInput.value || '';
    const wrap = el.syncUrlInput.closest('.url-input-wrap');
    if (!raw.trim()) {
      sync.urlValid = false;
      el.syncUrlHint.textContent = 'Paste a playlist link to continue';
      el.syncUrlHint.dataset.state = 'empty';
      if (wrap) wrap.dataset.state = 'empty';
    } else {
      const id = parsePlaylistId(raw);
      if (id) {
        sync.urlValid = true;
        el.syncUrlHint.textContent = 'Looks good — playlist ' + id;
        el.syncUrlHint.dataset.state = 'valid';
        if (wrap) wrap.dataset.state = 'valid';
      } else {
        sync.urlValid = false;
        el.syncUrlHint.textContent = 'Not a Spotify playlist URL';
        el.syncUrlHint.dataset.state = 'invalid';
        if (wrap) wrap.dataset.state = 'invalid';
      }
    }
    // Reflect valid-state on the footer Add button + meta when URL tab active
    if (sync.stage === 'url') {
      if (el.btnSyncStart) el.btnSyncStart.disabled = !sync.urlValid;
      if (el.syncFooterMeta) el.syncFooterMeta.textContent = sync.urlValid ? 'Ready to add' : 'Paste a playlist link';
    }
  }

  function setSyncTab(tab) {
    sync.tab = tab;
    if (el.syncTabLibrary) el.syncTabLibrary.setAttribute('aria-selected', tab === 'library' ? 'true' : 'false');
    if (el.syncTabUrl) el.syncTabUrl.setAttribute('aria-selected', tab === 'url' ? 'true' : 'false');
    if (tab === 'library') {
      // Show library view — either fetch or already-fetched
      if (sync.library) {
        renderLibrary(sync.library);
        setSyncView('select');
        updateSyncFooter();
      } else {
        // Only fetch if Spotify is connected; otherwise show a friendly hint
        if (spotify.accessToken) {
          setSyncView('loading');
          fetchLibrary();
        } else {
          setSyncView('error');
          if (el.syncErrorMsg) el.syncErrorMsg.textContent = 'Sign in with Spotify to sync your library. Or use "Add public playlist" instead.';
        }
      }
    } else {
      setSyncView('url');
      refreshUrlViewState();
      updateUrlHint();
      // Focus the input on tab switch (only if signed in — otherwise the
      // input isn't visible)
      if (spotify.accessToken) {
        setTimeout(() => { try { el.syncUrlInput && el.syncUrlInput.focus(); } catch (_) {} }, 60);
      }
    }
  }

  // opts: { defaultTab: 'library' | 'url' } — overrides the auto-pick
  function openSyncModal(opts) {
    // Requires a VibeScape session but NOT Spotify — the URL-paste tab works
    // for non-Spotify users too.
    if (!auth.token) return;
    opts = opts || {};
    sync.open = true;
    el.syncModal.hidden = false;
    el.syncModal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('modal-open');
    // Reset URL input on every open so stale state doesn't confuse next use
    if (el.syncUrlInput) el.syncUrlInput.value = '';
    sync.urlValid = false;
    // Smart default: if Spotify-connected, land on library sync;
    // otherwise land on URL paste (their only option).
    const defaultTab = opts.defaultTab || (spotify.accessToken ? 'library' : 'url');
    setSyncTab(defaultTab);
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
    sync.tab = 'library'; // next open re-runs the smart default picker
    sync.urlValid = false;
    sync.noteShown = false;
    sync.authMode = '';
    if (el.syncPlaylistList) el.syncPlaylistList.innerHTML = '';
    if (el.syncUrlInput) el.syncUrlInput.value = '';
  }

  async function fetchLibrary() {
    if (sync.fetching) return;
    sync.fetching = true;
    setSyncView('loading');
    try {
      // /api/spotify/library needs both: VibeScape session (fetchWithAuth adds
      // it as Authorization: Bearer) AND the Spotify access token (passed via
      // X-Spotify-Authorization: Bearer <token> — session-backend contract).
      const r = await fetchWithAuth('/api/spotify/library', {
        headers: { 'X-Spotify-Authorization': 'Bearer ' + spotify.accessToken }
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
    updateSyncProgress({ processed: 0, total: sel.total || 0, newly_analyzed: 0, linked_from_global: 0, already_in_library: 0, no_preview: 0, current_track: 'Starting…' });

    try {
      const r = await fetchWithAuth('/api/ingest/spotify', {
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

  async function startPublicPlaylistIngest() {
    const raw = (el.syncUrlInput && el.syncUrlInput.value || '').trim();
    const id = parsePlaylistId(raw);
    if (!id) {
      // Should not happen — button is disabled — but guard anyway
      updateUrlHint();
      return;
    }
    // Payload: send playlist_url + playlist_id (backend accepts either).
    // Also forward the user's Spotify OAuth token when available — Spotify
    // killed app-level access to public playlists in Nov 2024, so the backend
    // needs a user token to actually read the playlist. Absence is allowed
    // (backend will 403 on most real playlists, which is the honest outcome).
    const body = {};
    if (/^https?:|^spotify:/.test(raw)) body.playlist_url = raw;
    body.playlist_id = id;
    if (spotify.accessToken) body.access_token = spotify.accessToken;

    setSyncView('progress');
    updateSyncProgress({ processed: 0, total: 0, newly_analyzed: 0, linked_from_global: 0, already_in_library: 0, no_preview: 0, current_track: 'Starting…' });

    try {
      const r = await fetchWithAuth('/api/ingest/spotify-public', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      // Domain-specific error codes per backend contract
      if (r.status === 400) {
        const j = await r.json().catch(() => ({}));
        el.syncErrorMsg.textContent = 'Invalid playlist URL. Double-check the link.';
        console.warn('[VibeScape] invalid_playlist_url:', j);
        setSyncView('url');
        // reflect an inline error
        const wrap = el.syncUrlInput && el.syncUrlInput.closest('.url-input-wrap');
        if (wrap) wrap.dataset.state = 'invalid';
        if (el.syncUrlHint) { el.syncUrlHint.textContent = 'Invalid playlist URL. Double-check the link.'; el.syncUrlHint.dataset.state = 'invalid'; }
        return;
      }
      if (r.status === 401) {
        // Backend emits two distinct 401 codes:
        //   spotify_token_expired         — user's OAuth token is stale/revoked
        //   spotify_scope_upgrade_required — token valid but missing v3 scope
        //                                    (needs consent screen for playlist-modify-private)
        const j = await r.json().catch(() => ({}));
        const errCode = (j && (j.detail && j.detail.error || j.error)) || '';
        console.warn('[VibeScape] 401 from public-ingest:', errCode || '(no code)', j);
        // Clear the stale/insufficient Spotify session so the UI reflects reality
        try { clearSpotifySession(); } catch (_) {}
        if (errCode === 'spotify_scope_upgrade_required') {
          toast('Please sign in with Spotify again to grant the new permission (needed to add playlists by link).', 'error', {
            action: { label: 'Sign in', onClick: () => beginSpotifyLogin() }
          });
        } else {
          // spotify_token_expired OR unknown/legacy 401 — treat as expired
          toast('Your Spotify session expired. Sign in with Spotify again.', 'error', {
            action: { label: 'Sign in', onClick: () => beginSpotifyLogin() }
          });
        }
        setSyncView('url');
        return;
      }
      if (r.status === 404) {
        toast('Playlist not found. Is the ID correct?', 'error');
        setSyncView('url');
        return;
      }
      if (r.status === 403) {
        toast("This playlist isn't public. Only public playlists can be added this way.", 'error');
        setSyncView('url');
        return;
      }
      if (!r.ok) throw new Error('public ingest start failed ' + r.status);
      const j = await r.json();
      sync.jobId = j.job_id || '';
      if (!sync.jobId) throw new Error('no job id');
      sync.lastUrl = raw;
      // Backend may include a `note` explaining preflight actions (e.g., the
      // silent follow to unlock playlist track access). Surface it once.
      if (j.note) {
        toast(j.note, 'info');
        sync.noteShown = true;
      } else {
        sync.noteShown = false;
      }
      startSyncPolling();
    } catch (e) {
      console.warn('[VibeScape] public playlist ingest error:', e);
      toast('Could not add playlist. Try again.', 'error');
      setSyncView('url');
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
      const r = await fetchWithAuth('/api/ingest/status/' + encodeURIComponent(sync.jobId));
      if (!r.ok) return;
      const s = await r.json();
      updateSyncProgress(s);
      // Surface a job-level note the first time it appears (handles the case
      // where the silent-follow happened INSIDE the job runner rather than at
      // preflight — 202 wouldn't have carried it).
      if (s.note && !sync.noteShown) {
        toast(s.note, 'info');
        sync.noteShown = true;
      }
      // Remember auth_mode for the debug panel section
      if (s.auth_mode && s.auth_mode !== sync.authMode) {
        sync.authMode = s.auth_mode;
        if (debugPanel && debugPanel.open) renderDebugPanel();
      }
      if (s.status === 'complete') {
        stopSyncPolling();
        // Four-bucket summary: new / linked / already yours / no preview.
        const nNew     = s.newly_analyzed     || 0;
        const nLinked  = s.linked_from_global || 0;
        const nAlready = s.already_in_library || 0;
        const nDropped = s.no_preview         || 0;
        const total    = s.total              || 0;
        const landed   = nNew + nLinked + nAlready;
        const summary =
          `${landed} of ${total} in your library — ` +
          `${nNew} new, ${nLinked} linked from global, ${nAlready} already yours, ${nDropped} no preview.`;
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
    if (el.syncStatNew)       el.syncStatNew.textContent       = String(s.newly_analyzed     || 0);
    if (el.syncStatLinked)    el.syncStatLinked.textContent    = String(s.linked_from_global || 0);
    if (el.syncStatAlready)   el.syncStatAlready.textContent   = String(s.already_in_library || 0);
    if (el.syncStatNoPreview) el.syncStatNoPreview.textContent = String(s.no_preview         || 0);
  }

  async function cancelSyncJob() {
    if (sync.jobId) {
      try {
        await fetchWithAuth('/api/ingest/status/' + encodeURIComponent(sync.jobId), {
          method: 'DELETE'
        });
      } catch (e) { /* backend handles cleanup even if this fails */ }
    }
    stopSyncPolling();
  }

  // Wire modal events
  if (el.btnSpotifySync) {
    el.btnSpotifySync.addEventListener('click', () => openSyncModal());
  }
  if (el.btnUserAddPlaylist) {
    el.btnUserAddPlaylist.addEventListener('click', () => {
      closeUserMenu();
      openSyncModal({ defaultTab: 'url' });
    });
  }
  if (el.syncTabLibrary) {
    el.syncTabLibrary.addEventListener('click', () => setSyncTab('library'));
  }
  if (el.syncTabUrl) {
    el.syncTabUrl.addEventListener('click', () => setSyncTab('url'));
  }
  if (el.btnUrlSpotifySignIn) {
    el.btnUrlSpotifySignIn.addEventListener('click', () => {
      // Same flow as the header sign-in button; the modal stays open and
      // updateSpotifyUI's hook will swap the URL view to the input state
      // once the OAuth token bridge fires.
      beginSpotifyLogin();
    });
  }
  if (el.syncUrlInput) {
    el.syncUrlInput.addEventListener('input', updateUrlHint);
    el.syncUrlInput.addEventListener('paste', () => {
      // paste event fires before .value updates; defer one tick
      setTimeout(updateUrlHint, 0);
    });
    el.syncUrlInput.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' && sync.urlValid && sync.stage === 'url') {
        ev.preventDefault();
        startPublicPlaylistIngest();
      }
    });
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
    el.btnSyncRetry.addEventListener('click', () => {
      // Retry maps to whichever tab is active
      if (sync.tab === 'url') setSyncTab('url');
      else fetchLibrary();
    });
  }
  if (el.btnSyncStart) {
    el.btnSyncStart.addEventListener('click', () => {
      if (sync.stage === 'complete') {
        closeSyncModal();
        fetchTrack(state.vibe);
      } else if (sync.stage === 'url') {
        startPublicPlaylistIngest();
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
  // Also read (but do NOT consume yet) any bridge entry written BEFORE we
  // started listening. We defer the exchange to bootAuthenticatedApp because
  // the mobile same-window flow returns to a fresh page load, and firing
  // consumeOAuthPayload before loadSpotifyConfig completes means client_id
  // is empty → Spotify returns invalid_client.
  const pendingAtBoot = readAndClearBridge();

  // Pre-auth shell setup — safe to run even without a session, only touches DOM
  updateSliderVisual(state.vibe);
  applyAccent(state.vibe);
  el.art.classList.add('empty');
  updateVerifyChip(null);
  updateMetricsChip(null);
  updateUserMenu();
  checkHealth(); // unauthenticated, safe

  // Split boot: run Spotify + track loads ONLY after we have a session
  async function bootAuthenticatedApp() {
    if (auth.booted) return;
    auth.booted = true;
    // Spotify config is public; safe to call unauthenticated but do it here
    // so the OAuth-return path also gets set up post-login.
    await loadSpotifyConfig();
    // Consume any pre-boot bridge entry now that spotify.clientId is loaded.
    if (pendingAtBoot) {
      await consumeOAuthPayload(pendingAtBoot);
    }
    // If we returned from Spotify via same-window redirect, exchange the code
    // BEFORE trying to restore a stale session.
    const handled = await handleRedirectReturn();
    if (!handled) restoreSpotifySession();
  }

  // ============ Admin panel (chandan-only) ============
  const adminEl = {
    overlay: document.getElementById('adminOverlay'),
    backdrop: document.getElementById('adminBackdrop'),
    close: document.getElementById('btnAdminClose'),
    loading: document.getElementById('adminLoading'),
    list: document.getElementById('adminUsersList'),
    body: document.getElementById('adminBody'),
    detail: document.getElementById('adminDetail'),
    detailBody: document.getElementById('adminDetailBody'),
    back: document.getElementById('btnAdminBack'),
  };

  function openAdmin() {
    if (!adminEl.overlay) return;
    if (!auth.user || !auth.user.is_admin) {
      toast('Admin panel is restricted.', 'error');
      return;
    }
    adminEl.overlay.hidden = false;
    adminEl.detail.hidden = true;
    adminEl.body.hidden = false;
    closeUserMenu();
    loadAdminUsers();
  }

  function closeAdmin() {
    if (adminEl.overlay) adminEl.overlay.hidden = true;
  }

  async function loadAdminUsers() {
    adminEl.loading.hidden = false;
    adminEl.list.hidden = true;
    adminEl.list.innerHTML = '';
    try {
      const r = await fetchWithAuth('/api/admin/users');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const j = await r.json();
      renderAdminUsers(j.users || []);
    } catch (e) {
      adminEl.list.innerHTML = '<div class="admin-loading">Failed to load users: ' + escapeHtml(String(e)) + '</div>';
      adminEl.list.hidden = false;
    } finally {
      adminEl.loading.hidden = true;
    }
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function renderAdminUsers(users) {
    adminEl.list.innerHTML = '';
    users.forEach((u) => {
      const row = document.createElement('div');
      row.className = 'admin-user-row';
      const badge = u.is_admin ? '<span class="admin-user-badge">admin</span>' : '';
      const spName = u.spotify_display_name ? ' · Spotify: ' + escapeHtml(u.spotify_display_name) : '';
      const created = u.created_at ? new Date(u.created_at).toLocaleDateString() : '—';
      row.innerHTML = `
        <div class="admin-user-info">
          <div class="admin-user-name">${escapeHtml(u.display_name)} ${badge}</div>
          <div class="admin-user-meta">id ${u.user_id} · created ${created}${spName}</div>
        </div>
        <div class="admin-user-count">${u.track_count} tracks</div>
        <div class="admin-actions">
          <button class="admin-btn" data-action="stats" data-uid="${u.user_id}">Stats</button>
          <button class="admin-btn admin-btn-danger" data-action="delete" data-uid="${u.user_id}" ${u.is_admin ? 'disabled' : ''}>Delete</button>
        </div>
      `;
      adminEl.list.appendChild(row);
    });
    adminEl.list.hidden = false;

    adminEl.list.querySelectorAll('[data-action="stats"]').forEach((btn) => {
      btn.addEventListener('click', () => loadAdminStats(parseInt(btn.dataset.uid, 10)));
    });
    adminEl.list.querySelectorAll('[data-action="delete"]').forEach((btn) => {
      btn.addEventListener('click', () => confirmDeleteUser(parseInt(btn.dataset.uid, 10)));
    });
  }

  async function loadAdminStats(userId) {
    adminEl.body.hidden = true;
    adminEl.detail.hidden = false;
    adminEl.detailBody.innerHTML = '<div class="admin-loading">Loading stats…</div>';
    try {
      const r = await fetchWithAuth('/api/admin/users/' + userId + '/stats');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const s = await r.json();
      renderAdminStats(s);
    } catch (e) {
      adminEl.detailBody.innerHTML = '<div class="admin-loading">Failed to load: ' + escapeHtml(String(e)) + '</div>';
    }
  }

  function renderAdminStats(s) {
    const fmt = (n, d = 2) => (n == null ? '—' : (Math.round(n * Math.pow(10, d)) / Math.pow(10, d)).toString());
    const moods = (s.by_mood || []).map(m =>
      `<span class="admin-chip">${escapeHtml(m.mood)}<span class="admin-chip-count">${m.count}</span></span>`
    ).join('');
    const sources = (s.by_source || []).map(m =>
      `<span class="admin-chip">${escapeHtml(m.source)}<span class="admin-chip-count">${m.count}</span></span>`
    ).join('');
    const artists = (s.top_artists || []).map(m =>
      `<span class="admin-chip">${escapeHtml(m.artist)}<span class="admin-chip-count">${m.count}</span></span>`
    ).join('');

    adminEl.detailBody.innerHTML = `
      <h3 style="margin:0 0 4px;font-size:16px;">${escapeHtml(s.display_name)}</h3>
      <p style="margin:0;color:rgba(255,255,255,0.5);font-size:12px;">id ${s.user_id} · ${s.spotify_display_name ? 'Spotify: ' + escapeHtml(s.spotify_display_name) : 'no Spotify link'}</p>
      <div class="admin-stat-grid">
        <div class="admin-stat-card"><div class="admin-stat-label">Tracks</div><div class="admin-stat-value">${s.track_count}</div></div>
        <div class="admin-stat-card"><div class="admin-stat-label">Avg vibe (ML)</div><div class="admin-stat-value">${fmt(s.avg_vibe_ml)}</div></div>
        <div class="admin-stat-card"><div class="admin-stat-label">Avg activation</div><div class="admin-stat-value">${fmt(s.avg_activation, 1)}</div></div>
      </div>
      <div class="admin-section-title">Moods</div>
      <div class="admin-chip-row">${moods || '<span class="admin-chip">none</span>'}</div>
      <div class="admin-section-title">Classification sources</div>
      <div class="admin-chip-row">${sources || '<span class="admin-chip">none</span>'}</div>
      <div class="admin-section-title">Top artists</div>
      <div class="admin-chip-row">${artists || '<span class="admin-chip">none</span>'}</div>
    `;
  }

  async function confirmDeleteUser(userId) {
    if (!window.confirm('Delete user #' + userId + '? Their PIN + library link will be removed. Global tracks stay. This cannot be undone.')) return;
    try {
      const r = await fetchWithAuth('/api/admin/users/' + userId, { method: 'DELETE' });
      if (!r.ok && r.status !== 204) {
        let msg = 'HTTP ' + r.status;
        try { const j = await r.json(); if (j && j.detail && j.detail.error) msg = j.detail.error; } catch (_) {}
        throw new Error(msg);
      }
      toast('User deleted.', 'success');
      loadAdminUsers();
    } catch (e) {
      toast('Delete failed: ' + e.message, 'error');
    }
  }

  if (adminEl.overlay) {
    const btnAdmin = document.getElementById('btnUserAdmin');
    if (btnAdmin) btnAdmin.addEventListener('click', openAdmin);
    if (adminEl.close) adminEl.close.addEventListener('click', closeAdmin);
    if (adminEl.backdrop) adminEl.backdrop.addEventListener('click', closeAdmin);
    if (adminEl.back) adminEl.back.addEventListener('click', () => {
      adminEl.detail.hidden = true;
      adminEl.body.hidden = false;
    });
  }

  // Try to hydrate an existing session from localStorage; if success, boot
  // the app. Otherwise show the profile picker.
  (async () => {
    const authed = await hydrateSessionFromStorage();
    if (authed) {
      hideAuthOverlay();
      bootAuthenticatedApp();
    } else {
      showAuthOverlay();
    }
  })();
})();
