import { useState } from 'react';
import { Navigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import * as api from '../../lib/api';
import { getToken } from '../../lib/session';
import styles from './AdminPage.module.css';

/* ------------------------------------------------------------------ utils */

function initials(name) {
  const s = (name || '').trim();
  if (!s) return '?';
  const parts = s.split(/\s+/);
  if (parts.length === 1) return parts[0].slice(0, 1).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

const asDate = (v) => (v ? new Date(v).toLocaleDateString() : '—');
const round = (n, d = 2) => (n == null ? '—' : String(Math.round(n * 10 ** d) / 10 ** d));

/* ------------------------------------------------------------- components */

function Chips({ items, labelKey }) {
  if (!items?.length) return <span className={styles.chip}>none</span>;
  return items.map((it) => (
    <span key={it[labelKey]} className={styles.chip}>
      {it[labelKey]}
      <span className={styles.chipCount}>{it.count}</span>
    </span>
  ));
}

function UserRow({ user, onStats, onDelete, deleting }) {
  const badges = [
    user.is_admin && { key: 'admin', label: 'admin', cls: '' },
    user.is_guest && { key: 'guest', label: 'guest', cls: styles.badgeGuest },
    user.spotify_product === 'premium' && { key: 'premium', label: 'premium', cls: styles.badgePremium },
  ].filter(Boolean);

  const spotifyBits = [
    user.spotify_display_name && `Spotify: ${user.spotify_display_name}`,
    user.spotify_email,
    user.spotify_country,
  ].filter(Boolean);

  return (
    <div className={styles.userRow}>
      <div className={styles.avatar}>
        {user.avatar_url ? <img src={user.avatar_url} alt="" /> : <span>{initials(user.display_name)}</span>}
      </div>

      <div className={styles.info}>
        <div className={styles.name}>
          {user.display_name}
          {badges.map((b) => (
            <span key={b.key} className={`${styles.badge} ${b.cls}`}>{b.label}</span>
          ))}
        </div>
        <div className={styles.meta}>
          id {user.user_id} · created {asDate(user.created_at)} · last {asDate(user.last_login_at)}
          {spotifyBits.length > 0 && ` · ${spotifyBits.join(' · ')}`}
        </div>
      </div>

      <div className={styles.count}>
        {user.track_count}
        <small>tracks</small>
      </div>

      <div className={styles.actions}>
        <button className={styles.btn} type="button" onClick={() => onStats(user.user_id)}>
          Stats
        </button>
        <button
          className={`${styles.btn} ${styles.btnDanger}`}
          type="button"
          disabled={user.is_admin || deleting}
          onClick={() => onDelete(user.user_id)}
        >
          {deleting ? 'Deleting…' : 'Delete'}
        </button>
      </div>
    </div>
  );
}

function StatsDetail({ userId, onBack }) {
  const { data, isPending, error } = useQuery({
    queryKey: ['admin', 'stats', userId],
    queryFn: () => api.adminUserStats(userId),
  });

  return (
    <div className={styles.detail}>
      <button className={styles.back} type="button" onClick={onBack}>
        ← Back to users
      </button>

      {isPending && <div className={styles.loading}>Loading stats…</div>}
      {error && <div className={styles.loading}>Failed to load: {error.message}</div>}

      {data && (
        <>
          <h2 className={styles.detailName}>{data.display_name}</h2>
          <p className={styles.detailSub}>
            id {data.user_id}
            {data.spotify_display_name ? ` · Spotify: ${data.spotify_display_name}` : ' · no Spotify link'}
          </p>

          <div className={styles.statGrid}>
            <div className={styles.statCard}>
              <div className={styles.statLabel}>Tracks</div>
              <div className={styles.statValue}>{data.track_count}</div>
            </div>
            <div className={styles.statCard}>
              <div className={styles.statLabel}>Avg vibe (ML)</div>
              <div className={styles.statValue}>{round(data.avg_vibe_ml)}</div>
            </div>
            <div className={styles.statCard}>
              <div className={styles.statLabel}>Avg activation</div>
              <div className={styles.statValue}>{round(data.avg_activation, 1)}</div>
            </div>
          </div>

          <div className={styles.sectionTitle}>Moods</div>
          <div className={styles.chipRow}><Chips items={data.by_mood} labelKey="mood" /></div>

          <div className={styles.sectionTitle}>Classification sources</div>
          <div className={styles.chipRow}><Chips items={data.by_source} labelKey="source" /></div>

          <div className={styles.sectionTitle}>Top artists</div>
          <div className={styles.chipRow}><Chips items={data.top_artists} labelKey="artist" /></div>
        </>
      )}
    </div>
  );
}

/* -------------------------------------------------------------- the page */

export default function AdminPage() {
  const [detailUserId, setDetailUserId] = useState(null);
  const [status, setStatus] = useState(null);
  const queryClient = useQueryClient();

  const hasToken = !!getToken();

  const meQuery = useQuery({
    queryKey: ['auth', 'me'],
    queryFn: api.me,
    enabled: hasToken,
    retry: false,
  });

  const isAdmin = meQuery.data?.is_admin === true;

  const usersQuery = useQuery({
    queryKey: ['admin', 'users'],
    queryFn: api.adminUsers,
    enabled: isAdmin,
  });

  const del = useMutation({
    mutationFn: api.adminDeleteUser,
    onSuccess: (_data, userId) => {
      setStatus({ msg: `User #${userId} deleted.`, kind: 'ok' });
      queryClient.invalidateQueries({ queryKey: ['admin', 'users'] });
    },
    onError: (e) => setStatus({ msg: `Delete failed: ${e.message}`, kind: 'error' }),
  });

  function handleDelete(userId) {
    const ok = window.confirm(
      `Delete user #${userId}?\n\nTheir library link and sessions will be removed. ` +
        `Global tracks stay. This cannot be undone.`
    );
    if (ok) del.mutate(userId);
  }

  // Session died mid-view (AuthGate already covers the no-token case before
  // this ever renders). Declarative redirect rather than a location.replace
  // during render — that is a side effect in the render phase and fires twice
  // under StrictMode. "/" is the React app, which shows the auth card.
  if (!hasToken || meQuery.error) return <Navigate to="/" replace />;

  if (meQuery.isPending) {
    return <main className={styles.main}><div className={styles.loading}>Checking session…</div></main>;
  }

  // Signed in but not admin. Client-side gate is UX only — every /api/admin/*
  // route is server-side gated by require_admin.
  if (!isAdmin) {
    return (
      <main className={styles.main}>
        <div className={styles.gate}>
          <h1>Not authorized</h1>
          <p>Only the admin user can view this page.</p>
          <a className={styles.backLink} href="/">Return to player</a>
        </div>
      </main>
    );
  }

  const users = usersQuery.data?.users ?? [];

  return (
    <>
      <header className={styles.siteNav}>
        <a className={styles.brand} href="/">
          <span className={styles.brandMark} aria-hidden="true" />
          <span className={styles.brandName}>VibeScape</span>
          <span className={styles.brandTag}>/ admin</span>
        </a>
        <nav><a href="/">← Back to player</a></nav>
      </header>

      <main className={styles.main}>
        <header className={styles.pageHead}>
          <div>
            <span className={styles.eyebrow}><span className={styles.eyebrowDot} />admin</span>
            <h1>Users</h1>
            <p className={styles.lede}>
              Every VibeScape identity. Delete removes a user&apos;s library link, keeps the
              global <code>tracks</code> table intact.
            </p>
          </div>
          <button
            className={styles.refresh}
            type="button"
            onClick={() => queryClient.invalidateQueries({ queryKey: ['admin', 'users'] })}
            disabled={usersQuery.isFetching}
          >
            {usersQuery.isFetching ? 'Refreshing…' : 'Refresh'}
          </button>
        </header>

        {detailUserId != null ? (
          <StatsDetail userId={detailUserId} onBack={() => setDetailUserId(null)} />
        ) : (
          <div className={styles.body}>
            {usersQuery.isPending && <div className={styles.loading}>Loading users…</div>}
            {usersQuery.error && (
              <div className={styles.loading}>Failed to load: {usersQuery.error.message}</div>
            )}
            {usersQuery.data &&
              users.map((u) => (
                <UserRow
                  key={u.user_id}
                  user={u}
                  onStats={setDetailUserId}
                  onDelete={handleDelete}
                  deleting={del.isPending && del.variables === u.user_id}
                />
              ))}
          </div>
        )}
      </main>

      {status && (
        <div
          className={`${styles.status} ${status.kind === 'error' ? styles.statusError : ''}`}
          role="status"
          aria-live="polite"
          onAnimationEnd={() => status.kind !== 'error' && setStatus(null)}
        >
          {status.msg}
        </div>
      )}
    </>
  );
}
