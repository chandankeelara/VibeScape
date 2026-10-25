"""The admin routes, characterised before backend/app.py is split into
routers: users list, a user's stats, a user's tracks, and delete (which
refuses to delete the admin). Pinned as on main at 535efff.
"""
import pytest

import deps  # backend/deps.py: ADMIN_USER_ID and the session helpers live there


@pytest.fixture
def admin(monkeypatch, app_module, make_user):
    uid, h = make_user("admin")
    monkeypatch.setattr(deps, "ADMIN_USER_ID", uid)
    return uid, h


def add_track(sql, user_id, added_at, **cols):
    cols.setdefault("title", "Song")
    cols.setdefault("artist", "Artist")
    cols.setdefault("vibe_score", 50)
    names, marks = ", ".join(cols), ", ".join("?" for _ in cols)
    tid = int(sql.execute(f"INSERT INTO tracks ({names}) VALUES ({marks})", tuple(cols.values())).lastrowid)
    sql.execute("INSERT INTO user_tracks (user_id, track_id, added_at) VALUES (?, ?, ?)",
                (user_id, tid, added_at))
    sql.commit()
    return tid


ADMIN_GETS = ["/api/admin/users", "/api/admin/users/{uid}/stats", "/api/admin/users/{uid}/tracks"]


@pytest.mark.parametrize("url", ADMIN_GETS + ["DELETE /api/admin/users/{uid}"])
def test_admin_only(client, admin, make_user, url):
    uid, h = make_user()
    method, _, path = url.rpartition(" ")
    call = client.delete if method == "DELETE" else client.get
    r = call(path.format(uid=uid), headers=h)
    assert r.status_code == 403 and r.json()["detail"] == {"error": "admin_only"}
    assert call(path.format(uid=uid)).status_code == 401


def test_users_list(client, sql, admin, make_user):
    admin_id, h = admin
    uid, _ = make_user("Guest")
    sql.execute("UPDATE users SET spotify_user_id='sp', spotify_display_name='SD', spotify_email='e@x', "
                "spotify_country='IN', spotify_product='free', spotify_avatar_url='https://a', "
                "last_login_at='2026-10-01 10:00:00' WHERE id = ?", (uid,))
    add_track(sql, uid, "2026-10-01")
    add_track(sql, uid, "2026-10-02")
    users = client.get("/api/admin/users", headers=h).json()["users"]
    assert [u["user_id"] for u in users] == [admin_id, uid]          # ORDER BY id
    created = users[1].pop("created_at")
    assert created
    assert users[1] == {
        "user_id": uid, "display_name": "Guest", "spotify_user_id": "sp",
        "spotify_display_name": "SD", "spotify_email": "e@x", "spotify_country": "IN",
        "spotify_product": "free", "avatar_url": "https://a", "last_login_at": "2026-10-01 10:00:00",
        "track_count": 2, "is_admin": False, "is_guest": True,
    }
    assert users[0]["is_admin"] is True and users[0]["track_count"] == 0


def test_user_stats(client, sql, admin, make_user):
    _, h = admin
    uid, _ = make_user("listener")
    add_track(sql, uid, "2026-10-01", mood="hype", artist="X", classification_source="mert",
              vibe_score_ml=0.8, activation=70, valence=40)
    add_track(sql, uid, "2026-10-02", mood="hype", artist="X", classification_source="mert",
              vibe_score_ml=0.6, activation=50, valence=60)
    add_track(sql, uid, "2026-10-03", mood=None, artist="Y")
    body = client.get(f"/api/admin/users/{uid}/stats", headers=h).json()
    assert body.pop("created_at")
    assert body == {
        "user_id": uid, "display_name": "listener", "spotify_display_name": None, "track_count": 3,
        "by_mood": [{"mood": "hype", "count": 2}, {"mood": "unknown", "count": 1}],
        "by_source": [{"source": "mert", "count": 2}, {"source": "unknown", "count": 1}],
        "top_artists": [{"artist": "X", "count": 2}, {"artist": "Y", "count": 1}],
        "avg_vibe_ml": pytest.approx(0.7), "avg_activation": 60.0, "avg_valence": 50.0,
    }


def test_user_stats_of_an_empty_library_and_of_nobody(client, admin, make_user):
    _, h = admin
    uid, _ = make_user()
    body = client.get(f"/api/admin/users/{uid}/stats", headers=h).json()
    assert body["track_count"] == 0 and body["by_mood"] == [] and body["avg_vibe_ml"] is None
    r = client.get("/api/admin/users/999999/stats", headers=h)
    assert r.status_code == 404 and r.json()["detail"] == {"error": "user_not_found"}


def test_user_tracks_newest_first_with_stats_and_paging(client, sql, admin, make_user):
    _, h = admin
    uid, _ = make_user()
    old = add_track(sql, uid, "2026-10-01 00:00:00", title="old")
    mid = add_track(sql, uid, "2026-10-02 00:00:00", title="mid", mood="chill", vibe_score_ml=0.3)
    new = add_track(sql, uid, "2026-10-03 00:00:00", title="new")
    sql.execute("INSERT INTO user_track_stats (user_id, track_id, play_count, last_played, last_skipped_at) "
                "VALUES (?, ?, 4, '2026-10-05 00:00:00', '2026-10-09 00:00:00')", (uid, mid))
    sql.execute("INSERT INTO user_track_stats (user_id, track_id, play_count, last_played) "
                "VALUES (?, ?, 1, '2026-10-06 00:00:00')", (uid, old))
    sql.commit()
    tracks = client.get(f"/api/admin/users/{uid}/tracks", headers=h).json()["tracks"]
    assert [t["id"] for t in tracks] == [new, mid, old]
    assert tracks[1] == {"id": mid, "title": "mid", "artist": "Artist", "album": None, "mood": "chill",
                         "vibe_score_ml": 0.3, "classification_source": None,
                         "added_at": "2026-10-02 00:00:00", "play_count": 4,
                         # MAX(last_played, last_skipped_at), under the old name
                         "last_played": "2026-10-09 00:00:00"}
    assert tracks[0]["play_count"] == 0 and tracks[0]["last_played"] is None
    assert tracks[2]["last_played"] == "2026-10-06 00:00:00"
    page = client.get(f"/api/admin/users/{uid}/tracks?limit=1&offset=1", headers=h).json()["tracks"]
    assert [t["id"] for t in page] == [mid]
    for bad in ("limit=0", "limit=501", "offset=-1"):
        assert client.get(f"/api/admin/users/{uid}/tracks?{bad}", headers=h).status_code == 422


def test_delete_removes_the_user_their_links_and_sessions(client, sql, admin, make_user):
    _, h = admin
    uid, uh = make_user()
    tid = add_track(sql, uid, "2026-10-01")
    r = client.delete(f"/api/admin/users/{uid}", headers=h)
    assert r.status_code == 204 and r.content == b""
    assert sql.execute("SELECT COUNT(*) FROM users WHERE id = ?", (uid,)).fetchone()[0] == 0
    assert sql.execute("SELECT COUNT(*) FROM user_tracks WHERE user_id = ?", (uid,)).fetchone()[0] == 0
    assert sql.execute("SELECT COUNT(*) FROM sessions WHERE user_id = ?", (uid,)).fetchone()[0] == 0
    assert sql.execute("SELECT COUNT(*) FROM tracks WHERE id = ?", (tid,)).fetchone()[0] == 1
    assert client.get("/api/auth/me", headers=uh).status_code == 401


def test_delete_leaves_the_users_stats_behind_on_sqlite(client, sql, admin, make_user):
    """ODD, pinned: the manual cascade covers user_tracks and sessions only.
    SQLite connections here do not enable foreign keys, so the user's
    user_track_stats (and track_events, user_events) are orphaned locally;
    on Turso the schema's ON DELETE CASCADE would remove them."""
    _, h = admin
    uid, _ = make_user()
    tid = add_track(sql, uid, "2026-10-01")
    sql.execute("INSERT INTO user_track_stats (user_id, track_id, play_count) VALUES (?, ?, 1)", (uid, tid))
    sql.commit()
    assert client.delete(f"/api/admin/users/{uid}", headers=h).status_code == 204
    assert sql.execute("SELECT COUNT(*) FROM user_track_stats WHERE user_id = ?", (uid,)).fetchone()[0] == 1


def test_delete_refuses_the_admin_and_unknown_users(client, sql, admin):
    admin_id, h = admin
    r = client.delete(f"/api/admin/users/{admin_id}", headers=h)
    assert r.status_code == 400 and r.json()["detail"] == {"error": "cannot_delete_admin"}
    assert sql.execute("SELECT COUNT(*) FROM users WHERE id = ?", (admin_id,)).fetchone()[0] == 1
    r = client.delete("/api/admin/users/999999", headers=h)
    assert r.status_code == 404 and r.json()["detail"] == {"error": "user_not_found"}
