"""POST /api/tracks/{id}/youtube writes the GLOBAL tracks.youtube_id, so only
the admin may call it. Before 2026-10-13 any session could, including the
shared guest minted by POST /api/auth/guest with no credentials at all."""

VID = "dQw4w9WgXcQ"


def youtube_id_of(sql, track_id):
    return sql.execute("SELECT youtube_id FROM tracks WHERE id = ?", (track_id,)).fetchone()[0]


def test_non_admin_gets_403_and_nothing_is_written(client, sql, make_user, make_tracks, app_module, monkeypatch):
    admin_id, _ = make_user("admin")
    user_id, user_h = make_user("someone")
    monkeypatch.setattr(app_module, "ADMIN_USER_ID", admin_id)
    (tid,) = make_tracks(user_id, [None])

    r = client.post(f"/api/tracks/{tid}/youtube", json={"youtube_id": VID}, headers=user_h)

    assert r.status_code == 403
    assert r.json()["detail"] == {"error": "admin_only"}
    assert youtube_id_of(sql, tid) is None


def test_guest_session_gets_403(client, sql, make_user, make_tracks, app_module, monkeypatch):
    admin_id, _ = make_user("admin")
    monkeypatch.setattr(app_module, "ADMIN_USER_ID", admin_id)
    (tid,) = make_tracks(admin_id, [None])
    guest = client.post("/api/auth/guest").json()
    assert guest["is_guest"] is True

    r = client.post(f"/api/tracks/{tid}/youtube", json={"youtube_id": VID},
                    headers={"Authorization": f"Bearer {guest['session_token']}"})

    assert r.status_code == 403
    assert youtube_id_of(sql, tid) is None


def test_admin_sets_it(client, sql, make_user, make_tracks, app_module, monkeypatch):
    admin_id, admin_h = make_user("admin")
    monkeypatch.setattr(app_module, "ADMIN_USER_ID", admin_id)
    (tid,) = make_tracks(admin_id, [None])

    r = client.post(f"/api/tracks/{tid}/youtube", json={"youtube_id": VID}, headers=admin_h)

    assert r.status_code == 200
    assert r.json() == {"youtube_id": VID, "cached": True}
    assert youtube_id_of(sql, tid) == VID


def test_no_session_is_401(client):
    r = client.post("/api/tracks/1/youtube", json={"youtube_id": VID})
    assert r.status_code == 401
