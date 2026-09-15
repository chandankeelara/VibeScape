/* ==========================================================================
   VibeScape — landing / login page controller
   Three CTAs: Spotify OAuth, VibeScape profile picker, guest ("just listen").
   All three converge on /app with vibescape_session_token stored locally.
   ========================================================================== */

(function () {
  'use strict';

  // ---------- config ----------

  const API_BASE = ''; // same-origin
  const APP_URL = '/app';
  const SESSION_KEY = 'vibescape_session_token';
  const USER_ID_KEY = 'vibescape_user_id';

  // Match the scope + version the player app expects, so a Spotify login
  // here grants full library-import access without a second consent step.
  const SPOTIFY_SCOPE = 'streaming user-read-email user-read-private user-library-read playlist-read-private playlist-read-collaborative user-top-read playlist-modify-private';
  const SPOTIFY_SCOPE_VERSION = 3;

  // ---------- DOM ----------

  const $ = (sel) => document.querySelector(sel);
  const ctaLogin = $('#ctaLogin');
  const ctaSpotify = $('#ctaSpotify');    // now lives inside the modal
  const ctaGuest = $('#ctaGuest');
  const statusEl = $('#loginStatus');
  const yearEl = $('#year');
  const modal = $('#authModal');
  const modalForm = $('#authEmailForm');

  if (yearEl) yearEl.textContent = String(new Date().getFullYear());

  // ---------- status toast ----------

  let statusTimer = 0;
  function setStatus(msg, kind /* 'info' | 'error' */) {
    if (!statusEl) return;
    if (statusTimer) { clearTimeout(statusTimer); statusTimer = 0; }
    if (!msg) {
      statusEl.hidden = true;
      statusEl.textContent = '';
      statusEl.classList.remove('error');
      return;
    }
    statusEl.textContent = msg;
    statusEl.hidden = false;
    statusEl.classList.toggle('error', kind === 'error');
    if (kind !== 'error') {
      statusTimer = setTimeout(() => setStatus(''), 4000);
    }
  }

  // ---------- session helpers ----------

  function storedToken() {
    try { return localStorage.getItem(SESSION_KEY) || ''; }
    catch { return ''; }
  }

  function persistSession(payload) {
    try {
      localStorage.setItem(SESSION_KEY, payload.session_token);
      if (payload.user_id != null) {
        localStorage.setItem(USER_ID_KEY, String(payload.user_id));
      }
    } catch (e) {
      console.warn('[login] localStorage write failed', e);
    }
  }

  function persistSpotifyTokens(userId, payload) {
    if (!userId || !payload || !payload.spotify_access_token) return;
    const prefix = `spotify_${userId}_`;
    const expiresAt = Date.now() + Math.max(60, (payload.spotify_expires_in || 3600) - 60) * 1000;
    try {
      localStorage.setItem(prefix + 'access_token', payload.spotify_access_token);
      if (payload.spotify_refresh_token) {
        localStorage.setItem(prefix + 'refresh_token', payload.spotify_refresh_token);
      }
      localStorage.setItem(prefix + 'token_expiry', String(expiresAt));
      localStorage.setItem(prefix + 'scope_version', String(SPOTIFY_SCOPE_VERSION));
    } catch (e) {
      console.warn('[login] spotify token persist failed', e);
    }
  }

  async function validateSession(token) {
    try {
      const r = await fetch(API_BASE + '/api/auth/me', {
        headers: { Authorization: 'Bearer ' + token }
      });
      return r.ok;
    } catch {
      return false;
    }
  }

  function gotoApp() {
    window.location.replace(APP_URL);
  }

  // ---------- Spotify OAuth ----------

  async function fetchSpotifyConfig() {
    const r = await fetch(API_BASE + '/api/spotify/config');
    if (!r.ok) throw new Error('spotify config unavailable');
    return r.json();
  }

  function buildRedirectUri() {
    // Must exactly match what /callback returns to; keep it origin-based.
    return window.location.origin + '/callback';
  }

  async function startSpotifyLogin() {
    setStatus('Redirecting to Spotify…');
    ctaSpotify.disabled = true;
    try {
      const cfg = await fetchSpotifyConfig();
      const clientId = cfg.client_id;
      if (!clientId) throw new Error('spotify not configured on this server');
      const params = new URLSearchParams({
        response_type: 'code',
        client_id: clientId,
        redirect_uri: buildRedirectUri(),
        scope: SPOTIFY_SCOPE,
        state: 'vs_landing',
        show_dialog: 'false'
      });
      window.location.href = 'https://accounts.spotify.com/authorize?' + params.toString();
    } catch (e) {
      console.error('[login] spotify start failed', e);
      setStatus('Could not start Spotify sign-in: ' + e.message, 'error');
      ctaSpotify.disabled = false;
    }
  }

  async function completeSpotifyLogin(code) {
    setStatus('Signing you in with Spotify…');
    // Clear the /callback bridge entry before we redeem the code. The
    // /callback HTML writes { code } to localStorage.spotify_pending_auth
    // for the popup/PKCE flow; if we don't clear it, app.js on /app boots
    // and tries to redeem the same (now-consumed) code as PKCE, which
    // Spotify rejects with 400 "Invalid client secret".
    try { localStorage.removeItem('spotify_pending_auth'); } catch (_) {}
    try {
      const r = await fetch(API_BASE + '/api/auth/spotify-oauth', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          code,
          redirect_uri: buildRedirectUri()
        })
      });
      if (!r.ok) {
        let detail = 'sign-in failed';
        try { const j = await r.json(); detail = (j.detail && j.detail.error) || detail; } catch {}
        throw new Error(detail);
      }
      const payload = await r.json();
      persistSession(payload);
      persistSpotifyTokens(payload.user_id, payload);
      setStatus('Welcome back' + (payload.display_name ? ', ' + payload.display_name : '') + ' — loading your library…');
      gotoApp();
    } catch (e) {
      console.error('[login] spotify complete failed', e);
      setStatus('Spotify sign-in failed: ' + e.message, 'error');
      // Clean the URL so a refresh doesn't retry the dead code.
      cleanQueryParams(['spotify_code', 'spotify_error', 'spotify_state']);
      try { localStorage.removeItem('spotify_pending_auth'); } catch (_) {}
    }
  }

  function cleanQueryParams(keys) {
    const url = new URL(window.location.href);
    let changed = false;
    for (const k of keys) {
      if (url.searchParams.has(k)) { url.searchParams.delete(k); changed = true; }
    }
    if (changed) window.history.replaceState({}, '', url.pathname + (url.search ? url.search : ''));
  }

  // ---------- Guest ----------

  async function startGuest() {
    setStatus('Starting demo session…');
    ctaGuest.disabled = true;
    try {
      const r = await fetch(API_BASE + '/api/auth/guest', { method: 'POST' });
      if (!r.ok) throw new Error('guest session failed (' + r.status + ')');
      const payload = await r.json();
      persistSession(payload);
      gotoApp();
    } catch (e) {
      console.error('[login] guest failed', e);
      setStatus('Could not start demo: ' + e.message, 'error');
      ctaGuest.disabled = false;
    }
  }

  // ---------- VibeScape profile ----------

  function startVibescapeLogin() {
    // The player page already renders the profile picker overlay when
    // there is no session. Sending users there is the least-surprising
    // and reuses the existing PIN flow.
    window.location.href = APP_URL;
  }

  // ---------- Boot ----------

  function wireButtons() {
    if (ctaLogin)   ctaLogin.addEventListener('click', () => openAuthModal('login'));
    if (ctaGuest)   ctaGuest.addEventListener('click', startGuest);
    // #ctaSpotify now lives INSIDE the modal — same handler.
    if (ctaSpotify) ctaSpotify.addEventListener('click', startSpotifyLogin);
    wireAuthModal();
  }

  // ---------- Email login/signup modal ----------

  let modalMode = 'login';  // 'login' | 'signup'

  function openAuthModal(mode) {
    if (!modal) return;
    setModalMode(mode || 'login');
    modal.hidden = false;
    // Focus first visible field.
    const first = modal.querySelector('input[name="email"]');
    if (first) setTimeout(() => first.focus(), 40);
  }

  function closeAuthModal() {
    if (!modal) return;
    modal.hidden = true;
    const err = modal.querySelector('[data-auth-error]');
    if (err) { err.hidden = true; err.textContent = ''; }
  }

  function setModalMode(mode) {
    modalMode = mode;
    if (!modal) return;
    modal.querySelectorAll('[data-auth-tab]').forEach((t) => {
      const on = t.getAttribute('data-auth-tab') === mode;
      t.classList.toggle('is-active', on);
      t.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    const submit = modal.querySelector('[data-auth-submit]');
    const pwHint = modal.querySelector('[data-auth-pw-hint]');
    const pwInput = modal.querySelector('input[name="password"]');
    if (mode === 'signup') {
      if (submit) submit.textContent = 'Create account';
      if (pwHint) pwHint.textContent = 'min 6 chars';
      if (pwInput) pwInput.setAttribute('autocomplete', 'new-password');
    } else {
      if (submit) submit.textContent = 'Log in with email';
      if (pwHint) pwHint.textContent = '';
      if (pwInput) pwInput.setAttribute('autocomplete', 'current-password');
    }
  }

  function wireAuthModal() {
    if (!modal) return;
    modal.querySelectorAll('[data-auth-close]').forEach((el) => {
      el.addEventListener('click', (e) => { e.preventDefault(); closeAuthModal(); });
    });
    modal.querySelectorAll('[data-auth-tab]').forEach((t) => {
      t.addEventListener('click', () => setModalMode(t.getAttribute('data-auth-tab')));
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && modal && !modal.hidden) closeAuthModal();
    });
    if (modalForm) modalForm.addEventListener('submit', onEmailSubmit);
  }

  async function onEmailSubmit(ev) {
    ev.preventDefault();
    if (!modal) return;
    const err = modal.querySelector('[data-auth-error]');
    const submit = modal.querySelector('[data-auth-submit]');
    const fd = new FormData(modalForm);
    const email = String(fd.get('email') || '').trim();
    const password = String(fd.get('password') || '');

    if (!email || !password) return;
    if (err) { err.hidden = true; err.textContent = ''; }
    if (submit) { submit.disabled = true; submit.textContent = modalMode === 'signup' ? 'Creating…' : 'Signing in…'; }

    const path = modalMode === 'signup' ? '/api/auth/signup' : '/api/auth/login';
    const body = { email, password };

    try {
      const r = await fetch(API_BASE + path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        let detail = modalMode === 'signup' ? 'Could not create account.' : 'Sign-in failed.';
        try {
          const j = await r.json();
          const code = j.detail && j.detail.error;
          if (code === 'email_taken')       detail = 'That email is already registered — try logging in.';
          else if (code === 'invalid_email')     detail = 'That doesn\'t look like a valid email.';
          else if (code === 'password_too_short') detail = 'Password must be at least 6 characters.';
          else if (code === 'bad_credentials')    detail = 'Wrong email or password.';
        } catch {}
        throw new Error(detail);
      }
      const payload = await r.json();
      persistSession(payload);
      setStatus('Welcome, ' + (payload.display_name || email) + '.');
      gotoApp();
    } catch (e) {
      if (err) { err.textContent = e.message; err.hidden = false; }
      if (submit) {
        submit.disabled = false;
        submit.textContent = modalMode === 'signup' ? 'Create account' : 'Log in with email';
      }
    }
  }

  async function boot() {
    wireButtons();
    animateMoodGrid();

    // 1) If OAuth just redirected us back with a code, exchange it and go.
    const qs = new URLSearchParams(window.location.search);
    const code = qs.get('spotify_code');
    const err = qs.get('spotify_error');
    if (err) {
      setStatus('Spotify sign-in cancelled: ' + err, 'error');
      cleanQueryParams(['spotify_code', 'spotify_error', 'spotify_state']);
      return;
    }
    if (code) {
      cleanQueryParams(['spotify_code', 'spotify_error', 'spotify_state']);
      await completeSpotifyLogin(code);
      return;
    }

    // 2) Already logged in? Fast-path to the player.
    const token = storedToken();
    if (token) {
      const ok = await validateSession(token);
      if (ok) { gotoApp(); return; }
      try { localStorage.removeItem(SESSION_KEY); } catch {}
    }
  }

  // ---------- Live vibe demo ----------
  // The signature moment of the landing page: dragging the hero slider
  // recolors the page (via --accent on <body>), moves the mood-cursor to the
  // matching cell, and swaps a mock "now playing" track. This function name
  // is preserved for boot compatibility; it now drives an interactive demo
  // instead of an autoplay carousel, but still touches .mood-cell.active
  // and .mood-cursor (the contract).

  function animateMoodGrid() {
    const slider = document.querySelector('#heroVibe');
    const cells = Array.from(document.querySelectorAll('.mood-cell'));
    const cursor = document.querySelector('.mood-cursor');
    const vibeNum = document.querySelector('[data-vibe-num]');
    const moodWords = document.querySelectorAll('[data-mood-word]');
    const npTitle = document.querySelector('[data-np-title]');
    const npSub = document.querySelector('[data-np-sub]');
    const npMood = document.querySelector('[data-np-mood]');
    const npVibe = document.querySelector('[data-np-vibe]');
    const sliderTrack = document.querySelector('.vibe-slider-track');
    if (cells.length === 0) return;

    // Sync the rotated slider's pre-rotation length to the .vibe-slider-track
    // wrapper's own height — the room actually available between the
    // "vibe/mood" header and the "drag" hint. If we synced to the mood-grid
    // height instead, the slider would overflow its column and poke into
    // the header / hint boxes above and below. Runs on load, on resize,
    // and via ResizeObserver so font-load reflow is also covered.
    function syncSliderLength() {
      if (!slider || !sliderTrack) return;
      const h = sliderTrack.getBoundingClientRect().height;
      if (h > 40) slider.style.setProperty('--slider-len', h + 'px');
    }
    syncSliderLength();
    window.addEventListener('resize', syncSliderLength, { passive: true });
    if (typeof ResizeObserver === 'function' && sliderTrack) {
      new ResizeObserver(syncSliderLength).observe(sliderTrack);
    }

    // Five bands — mirrors the player's MOODS table in app.js (single energy axis).
    // Each band: [minVibe, maxVibeExclusive, startColor, endColor, moodLabel].
    const BANDS = [
      [ 0, 20,  [ 76,  91, 138], [ 91, 127, 189], 'sleep'],
      [20, 40,  [  0, 180, 216], [ 34, 193, 227], 'chill'],
      [40, 60,  [124,  58, 237], [167, 139, 250], 'steady'],
      [60, 80,  [236,  72, 153], [244,  63,  94], 'hype'],
      [80,101,  [249, 115,  22], [220,  38,  38], 'beast']
    ];

    // Fallback demo tracks per mood — used until /api/demo/moods responds
    // with real tracks from the actual library.
    const DEMOS = {
      sleep:  { title: 'Weightless',        sub: 'Marconi Union · Weightless',                artwork_url: null },
      chill:  { title: 'Late Night Drive',  sub: 'The Midnight · Kids',                       artwork_url: null },
      steady: { title: 'Redbone',           sub: 'Childish Gambino · "Awaken, My Love!"',     artwork_url: null },
      hype:   { title: 'Get Lucky',         sub: 'Daft Punk · Random Access Memories',        artwork_url: null },
      beast:  { title: 'Bulls On Parade',   sub: 'Rage Against the Machine · Evil Empire',    artwork_url: null }
    };

    // Fire-and-forget: swap in real tracks from the library once the
    // endpoint responds. Anything that fails (offline, empty library)
    // silently falls back to the curated stubs above.
    const artImg = document.querySelector('[data-np-art-img]');
    fetch(API_BASE + '/api/demo/moods')
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        if (!data || !Array.isArray(data.moods)) return;
        for (const m of data.moods) {
          if (!m || !m.mood || !DEMOS[m.mood]) continue;
          const title = m.title || DEMOS[m.mood].title;
          const artist = m.artist || 'Unknown';
          const album = m.album ? (' · ' + m.album) : '';
          DEMOS[m.mood] = {
            title, sub: artist + album, artwork_url: m.artwork_url || null,
          };
        }
        // Re-apply current vibe so the swap is visible immediately.
        if (slider) apply(Number(slider.value));
      })
      .catch(() => { /* offline: keep stubs */ });

    function lerp(a, b, t) { return a + (b - a) * t; }
    function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

    function bandFor(vibe) {
      for (const b of BANDS) if (vibe >= b[0] && vibe < b[1]) return b;
      return BANDS[BANDS.length - 1];
    }

    function colorFor(vibe) {
      const b = bandFor(vibe);
      const t = (vibe - b[0]) / (b[1] - b[0]);
      const r = Math.round(lerp(b[2][0], b[3][0], t));
      const g = Math.round(lerp(b[2][1], b[3][1], t));
      const bl = Math.round(lerp(b[2][2], b[3][2], t));
      return `rgb(${r}, ${g}, ${bl})`;
    }

    function moodFor(vibe) { return bandFor(vibe)[4]; }

    function activeCellFor(vibe) {
      // Pick the cell with the HIGHEST data-vibe-min that is still <= vibe.
      // Bug fix: the ladder is laid out in descending-energy DOM order
      // (beast at top → sleep at bottom), so a naive last-wins loop over
      // "min <= vibe" always ended at sleep. Track the max-min explicitly.
      const laddered = cells.filter((c) => c.hasAttribute('data-vibe-min'));
      if (laddered.length === 0) return cells[0];
      let picked = laddered[0];
      let bestMin = -1;
      for (const c of laddered) {
        const min = Number(c.getAttribute('data-vibe-min'));
        if (vibe >= min && min > bestMin) {
          bestMin = min;
          picked = c;
        }
      }
      return picked;
    }

    let raf = 0;
    let lastMood = '';
    function apply(vibe) {
      vibe = clamp(Math.round(vibe), 0, 100);
      const color = colorFor(vibe);
      const mood = moodFor(vibe);
      const demo = DEMOS[mood] || DEMOS.chill;
      const moodChanged = mood !== lastMood;

      document.body.setAttribute('data-vibe', String(vibe));
      document.documentElement.style.setProperty('--accent', color);

      // WebKit slider fill percentage (used by the ::-webkit-slider-runnable-track gradient stop)
      document.documentElement.style.setProperty('--slider-pct', vibe + '%');

      if (vibeNum) vibeNum.textContent = String(vibe);
      moodWords.forEach((el) => {
        if (el.textContent === mood) return;
        el.textContent = mood;
        // Restart the fade-in on the H1 word only (skip other .mood-word usages).
        if (moodChanged && el.classList.contains('serif-em') && el.parentElement && el.parentElement.classList.contains('mood-swap')) {
          el.style.animation = 'none';
          // Force reflow so the next assignment restarts the keyframes.
          void el.offsetWidth;
          el.style.animation = '';
        }
      });
      lastMood = mood;

      if (npTitle) npTitle.textContent = demo.title;
      if (npSub) npSub.textContent = demo.sub;
      if (npMood) npMood.textContent = mood;
      if (npVibe) npVibe.textContent = String(vibe);

      // Swap real album art if we have one; hide the <img> otherwise so
      // the gradient fallback shows through.
      if (artImg) {
        const url = demo.artwork_url || '';
        if (url && artImg.dataset.currentUrl !== url) {
          artImg.dataset.currentUrl = url;
          artImg.classList.remove('is-loaded');
          const preload = new Image();
          preload.onload = () => {
            if (artImg.dataset.currentUrl !== url) return; // superseded
            artImg.src = url;
            requestAnimationFrame(() => artImg.classList.add('is-loaded'));
          };
          preload.onerror = () => {
            if (artImg.dataset.currentUrl !== url) return;
            artImg.removeAttribute('src');
            artImg.classList.remove('is-loaded');
          };
          preload.src = url;
        } else if (!url) {
          artImg.removeAttribute('src');
          artImg.classList.remove('is-loaded');
          delete artImg.dataset.currentUrl;
        }
      }

      // Move .mood-cursor to the active cell (contract compat).
      const active = activeCellFor(vibe);
      cells.forEach((c) => c.classList.toggle('active', c === active));
      if (cursor && active) {
        const parent = active.parentElement.getBoundingClientRect();
        const rect = active.getBoundingClientRect();
        const y = rect.top - parent.top + rect.height / 2 - 11;
        cursor.style.transform = `translateY(${y}px)`;
      }
    }

    function apply_(vibe) {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => apply(vibe));
    }

    // Initial paint.
    apply(slider ? Number(slider.value) || 42 : 42);

    // ---- Interactive: slider drives everything.
    let userTouched = false;
    if (slider) {
      const onInput = () => {
        userTouched = true;
        apply_(Number(slider.value));
      };
      slider.addEventListener('input', onInput);
      slider.addEventListener('change', onInput);
    }

    // ---- Keyboard: ↑/↓ nudges the slider even without focus.
    document.addEventListener('keydown', (e) => {
      if (!slider) return;
      // Skip when typing in a form field.
      const tag = (e.target && e.target.tagName) || '';
      if (tag === 'INPUT' || tag === 'TEXTAREA' || e.target.isContentEditable) return;
      if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
      e.preventDefault();
      userTouched = true;
      const step = e.shiftKey ? 10 : 5;
      const delta = e.key === 'ArrowUp' ? step : -step;
      const next = clamp(Number(slider.value) + delta, 0, 100);
      slider.value = String(next);
      apply_(next);
    });

    // ---- Gentle auto-drift.
    // Until the visitor grabs the slider we drift slowly through the moods —
    // never faster than the eye can follow, never faster than 30fps of work.
    // Killed the moment the user interacts, and paused if they hover the demo.
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const demoCard = document.querySelector('.hero-demo');
    let hovering = false;
    if (demoCard) {
      demoCard.addEventListener('pointerenter', () => { hovering = true; });
      demoCard.addEventListener('pointerleave', () => { hovering = false; });
    }

    if (!reduce) {
      let vibe = slider ? Number(slider.value) || 42 : 42;
      let dir = +1;
      // ~30fps, small delta — total sweep ~30s across the full range.
      setInterval(() => {
        if (userTouched || hovering) return;
        vibe += dir * 0.6;
        if (vibe >= 92) { vibe = 92; dir = -1; }
        if (vibe <= 8)  { vibe = 8;  dir = +1; }
        if (slider) slider.value = String(Math.round(vibe));
        apply_(vibe);
      }, 33);
    }
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
