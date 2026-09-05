/* ==========================================================================
   VibeScape — /admin page controller
   Standalone admin panel. Auth-guarded: if /api/auth/me returns
   is_admin=false (or no session), the gate view shows and the app is
   never populated. Every fetch under /api/admin/* is server-side gated
   too via require_admin, so this client-side check is UX not security.
   ========================================================================== */

(function () {
  'use strict';

  const API_BASE = '';
  const SESSION_KEY = 'vibescape_session_token';

  const $ = (id) => document.getElementById(id);

  const gate       = $('adminGate');
  const app        = $('adminApp');
  const btnRefresh = $('btnAdminRefresh');
  const body       = $('adminBody');
  const loading    = $('adminLoading');
  const list       = $('adminUsersList');
  const detail     = $('adminDetail');
  const detailBody = $('adminDetailBody');
  const btnBack    = $('btnAdminBack');
  const statusEl   = $('loginStatus');

  let statusTimer = 0;
  function setStatus(msg, kind) {
    if (!statusEl) return;
    if (statusTimer) { clearTimeout(statusTimer); statusTimer = 0; }
    if (!msg) { statusEl.hidden = true; statusEl.textContent = ''; statusEl.classList.remove('error'); return; }
    statusEl.textContent = msg;
    statusEl.hidden = false;
    statusEl.classList.toggle('error', kind === 'error');
    if (kind !== 'error') statusTimer = setTimeout(() => setStatus(''), 3500);
  }

  function token() {
    try { return localStorage.getItem(SESSION_KEY) || ''; } catch { return ''; }
  }

  async function fetchAuthed(path, init) {
    const t = token();
    if (!t) throw new Error('no session');
    const opts = init || {};
    opts.headers = Object.assign({}, opts.headers || {}, { Authorization: 'Bearer ' + t });
    return fetch(API_BASE + path, opts);
  }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function initials(name) {
    const s = (name || '').trim();
    if (!s) return '?';
    const parts = s.split(/\s+/);
    if (parts.length === 1) return parts[0].slice(0, 1).toUpperCase();
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }

  // ---------- auth gate ----------

  async function boot() {
    // No token → straight to landing.
    if (!token()) {
      window.location.replace('/');
      return;
    }
    let me;
    try {
      const r = await fetch(API_BASE + '/api/auth/me', {
        headers: { Authorization: 'Bearer ' + token() }
      });
      if (!r.ok) throw new Error('me ' + r.status);
      me = await r.json();
    } catch {
      window.location.replace('/');
      return;
    }
    if (!me.is_admin) {
      // Signed in but not admin — show the gate, not the panel.
      gate.hidden = false;
      app.hidden = true;
      return;
    }
    gate.hidden = true;
    app.hidden = false;
    wireButtons();
    loadUsers();
  }

  function wireButtons() {
    btnRefresh.addEventListener('click', loadUsers);
    btnBack.addEventListener('click', () => {
      detail.hidden = true;
      body.hidden = false;
    });
  }

  // ---------- users ----------

  async function loadUsers() {
    loading.hidden = false;
    list.hidden = true;
    list.innerHTML = '';
    detail.hidden = true;
    body.hidden = false;
    try {
      const r = await fetchAuthed('/api/admin/users');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const j = await r.json();
      renderUsers(j.users || []);
    } catch (e) {
      list.innerHTML = '<div class="admin-loading">Failed to load: ' + esc(e.message) + '</div>';
      list.hidden = false;
    } finally {
      loading.hidden = true;
    }
  }

  function renderUsers(users) {
    list.innerHTML = '';
    users.forEach((u) => {
      const row = document.createElement('div');
      row.className = 'admin-user-row';

      const avatar = u.avatar_url
        ? `<img src="${esc(u.avatar_url)}" alt="" />`
        : `<span>${esc(initials(u.display_name))}</span>`;

      const badges = [];
      if (u.is_admin) badges.push('<span class="admin-user-badge">admin</span>');
      if (u.is_guest) badges.push('<span class="admin-user-badge badge-guest">guest</span>');
      if (u.spotify_product === 'premium') badges.push('<span class="admin-user-badge badge-premium">premium</span>');

      const spBits = [];
      if (u.spotify_display_name) spBits.push('Spotify: ' + esc(u.spotify_display_name));
      if (u.spotify_email) spBits.push(esc(u.spotify_email));
      if (u.spotify_country) spBits.push(esc(u.spotify_country));
      const spLine = spBits.length ? ' · ' + spBits.join(' · ') : '';
      const created = u.created_at ? new Date(u.created_at).toLocaleDateString() : '—';
      const lastLogin = u.last_login_at ? new Date(u.last_login_at).toLocaleDateString() : '—';

      row.innerHTML = `
        <div class="admin-user-avatar">${avatar}</div>
        <div class="admin-user-info">
          <div class="admin-user-name">${esc(u.display_name)} ${badges.join(' ')}</div>
          <div class="admin-user-meta">id ${u.user_id} · created ${created} · last ${lastLogin}${spLine}</div>
        </div>
        <div class="admin-user-count">${u.track_count}<small>tracks</small></div>
        <div class="admin-actions">
          <button class="admin-btn" data-action="stats" data-uid="${u.user_id}">Stats</button>
          <button class="admin-btn admin-btn-danger" data-action="delete" data-uid="${u.user_id}" ${u.is_admin ? 'disabled' : ''}>Delete</button>
        </div>
      `;
      list.appendChild(row);
    });
    list.hidden = false;

    list.querySelectorAll('[data-action="stats"]').forEach((btn) => {
      btn.addEventListener('click', () => loadStats(parseInt(btn.dataset.uid, 10)));
    });
    list.querySelectorAll('[data-action="delete"]').forEach((btn) => {
      btn.addEventListener('click', () => confirmDelete(parseInt(btn.dataset.uid, 10)));
    });
  }

  // ---------- stats detail ----------

  async function loadStats(userId) {
    body.hidden = true;
    detail.hidden = false;
    detailBody.innerHTML = '<div class="admin-loading">Loading stats…</div>';
    try {
      const r = await fetchAuthed('/api/admin/users/' + userId + '/stats');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const s = await r.json();
      renderStats(s);
    } catch (e) {
      detailBody.innerHTML = '<div class="admin-loading">Failed to load: ' + esc(e.message) + '</div>';
    }
  }

  function renderStats(s) {
    const fmt = (n, d = 2) => (n == null ? '—' : (Math.round(n * Math.pow(10, d)) / Math.pow(10, d)).toString());
    const moods = (s.by_mood || []).map(m =>
      `<span class="admin-chip">${esc(m.mood)}<span class="admin-chip-count">${m.count}</span></span>`
    ).join('');
    const sources = (s.by_source || []).map(m =>
      `<span class="admin-chip">${esc(m.source)}<span class="admin-chip-count">${m.count}</span></span>`
    ).join('');
    const artists = (s.top_artists || []).map(m =>
      `<span class="admin-chip">${esc(m.artist)}<span class="admin-chip-count">${m.count}</span></span>`
    ).join('');

    detailBody.innerHTML = `
      <h2 style="margin:0 0 4px;font-family:var(--sans);font-size:22px;font-weight:600;letter-spacing:-0.02em;">
        ${esc(s.display_name)}
      </h2>
      <p style="margin:0;color:var(--text-3);font-family:var(--mono);font-size:11px;letter-spacing:0.04em;">
        id ${s.user_id}${s.spotify_display_name ? ' · Spotify: ' + esc(s.spotify_display_name) : ' · no Spotify link'}
      </p>
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

  // ---------- delete ----------

  async function confirmDelete(userId) {
    if (!window.confirm('Delete user #' + userId + '?\n\nTheir library link and sessions will be removed. Global tracks stay. This cannot be undone.')) return;
    setStatus('Deleting user #' + userId + '…');
    try {
      const r = await fetchAuthed('/api/admin/users/' + userId, { method: 'DELETE' });
      if (!r.ok && r.status !== 204) {
        let msg = 'HTTP ' + r.status;
        try { const j = await r.json(); if (j && j.detail && j.detail.error) msg = j.detail.error; } catch {}
        throw new Error(msg);
      }
      setStatus('User deleted.');
      loadUsers();
    } catch (e) {
      setStatus('Delete failed: ' + e.message, 'error');
    }
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
