"""The library-wide score recompute (ingest/score_recompute.py).

It rewrites activation / valence / activation_relative / vibe_score / mood for
EVERY track in the shared catalogue. Before 2026-10-13 any signed-in user could
trigger it over HTTP, every import job ran it on the way out (~3,800 Turso
round trips each time), and it rewrote every row even when nothing changed.
"""
import time

import pytest

import playlist_import as P
import score_recompute
import deps  # backend/deps.py: ADMIN_USER_ID and the session helpers live there
import db as backend_db  # backend/db.py; `db` is the fixture with the test database path

# Three librosa feature banks: loud/fast, quiet/slow, in between.
FIXTURE = [
    dict(tempo=128.0, energy_mean=0.12, energy_std=0.06, brightness=3200.0, tempo_stability=0.9,
         onset_rate=4.0, spectral_contrast=22.0, flatness=0.02, valence_mode=0.6, acousticness=0.1),
    dict(tempo=72.0, energy_mean=0.03, energy_std=0.01, brightness=1200.0, tempo_stability=0.5,
         onset_rate=1.2, spectral_contrast=15.0, flatness=0.005, valence_mode=-0.4, acousticness=0.85),
    dict(tempo=100.0, energy_mean=0.07, energy_std=0.03, brightness=2200.0, tempo_stability=0.7,
         onset_rate=2.5, spectral_contrast=18.0, flatness=0.01, valence_mode=0.1, acousticness=0.4),
]

# Captured from backend/app.py:_recompute_axes_and_zscores on this fixture
# before it moved (2026-10-13): (activation, valence, activation_relative, vibe_score, mood).
EXPECTED = [
    (65.48095238095239, 81.25, 68.51009263422166, 68.51009263422166, "hype"),
    (16.099999999999998, 50.125, 31.770971679096228, 31.770971679096228, "sleep"),
    (40.22380952380953, 66.5, 49.7189356866821, 49.7189356866821, "steady"),
]
SENTINEL = 12.345


def add_featured(sql, user_id=None):
    """Insert the fixture as analysed tracks with sentinel scores. -> ids"""
    ids = []
    for i, f in enumerate(FIXTURE):
        cols = ",".join(f)
        marks = ",".join("?" * len(f))
        cur = sql.execute(
            "INSERT INTO tracks (title, artist, spotify_id, ingestion_status, activation, valence, "
            f"activation_relative, vibe_score, mood, {cols}) "
            f"VALUES (?, 'A', ?, 'done', ?, ?, ?, ?, 'chill', {marks})",
            (f"T{i}", f"FEAT{i}", SENTINEL, SENTINEL, SENTINEL, SENTINEL, *f.values()),
        )
        ids.append(int(cur.lastrowid))
        if user_id is not None:
            sql.execute("INSERT INTO user_tracks (user_id, track_id) VALUES (?, ?)", (user_id, ids[-1]))
    sql.commit()
    return ids


def scores(sql, ids):
    q = ("SELECT activation, valence, activation_relative, vibe_score, mood FROM tracks "
         f"WHERE id IN ({','.join('?' * len(ids))}) ORDER BY id")
    return [tuple(r) for r in sql.execute(q, ids).fetchall()]


def recompute(app_module):
    conn = backend_db.get_conn()
    try:
        return score_recompute.recompute_axes_and_zscores(conn)
    finally:
        conn.close()


# ------------------------------------------------------------------ the maths

def test_same_values_as_the_function_it_replaced(sql, app_module):
    ids = add_featured(sql)
    summary = recompute(app_module)
    assert summary["updated"] == 3
    for got, want in zip(scores(sql, ids), EXPECTED):
        assert got[:4] == pytest.approx(want[:4], abs=1e-9)
        assert got[4] == want[4]
    assert summary["mood_distribution"] == {"hype": 1, "sleep": 1, "steady": 1}
    assert summary["activation_stats"]["mean"] == pytest.approx(40.60158730158731)


def test_a_track_with_no_features_is_left_alone(sql, app_module, make_user, make_tracks):
    uid, _ = make_user()
    (bare,) = make_tracks(uid, [None])          # vibe_score 50, no librosa features
    add_featured(sql)
    recompute(app_module)
    row = sql.execute("SELECT vibe_score, activation FROM tracks WHERE id=?", (bare,)).fetchone()
    assert tuple(row) == (50, None)


def test_a_second_run_writes_nothing(sql, app_module):
    ids = add_featured(sql)
    first = recompute(app_module)
    second = recompute(app_module)
    assert (first["written"], first["unchanged"]) == (3, 0)
    assert (second["updated"], second["written"], second["unchanged"]) == (3, 0, 3)
    assert scores(sql, ids)[0][2] == pytest.approx(EXPECTED[0][2])


def test_only_the_rows_that_drifted_are_rewritten(sql, app_module):
    ids = add_featured(sql)
    recompute(app_module)
    sql.execute("UPDATE tracks SET mood = 'beast' WHERE id = ?", (ids[1],))
    sql.commit()
    again = recompute(app_module)
    assert (again["written"], again["unchanged"]) == (1, 2)
    assert scores(sql, ids)[1][4] == "sleep"


def test_the_writes_go_out_in_batches_not_one_round_trip_each(sql, app_module, batches,
                                                             monkeypatch):
    # On Turso each execute() is an HTTPS round trip; each execute_batch()
    # is one for the whole chunk.
    ids = add_featured(sql)
    monkeypatch.setattr(score_recompute, "WRITE_CHUNK", 2)
    assert recompute(app_module)["written"] == 3
    assert [len(b) for b in batches] == [2, 1]
    assert [row[2] for row in scores(sql, ids)] == pytest.approx([e[2] for e in EXPECTED])


# --------------------------------------------------------------- the endpoint

def test_a_non_admin_gets_403_and_nothing_is_written(client, sql, make_user, app_module, monkeypatch):
    admin_id, _ = make_user("admin")
    uid, h = make_user("someone")
    monkeypatch.setattr(deps, "ADMIN_USER_ID", admin_id)
    ids = add_featured(sql, uid)

    r = client.post("/api/recompute-scores", headers=h)

    assert r.status_code == 403
    assert r.json()["detail"] == {"error": "admin_only"}
    assert all(row[2] == SENTINEL for row in scores(sql, ids))


def test_the_admin_recomputes_the_whole_catalogue(client, sql, make_user, app_module, monkeypatch):
    admin_id, admin_h = make_user("admin")
    other, _ = make_user("someone")
    monkeypatch.setattr(deps, "ADMIN_USER_ID", admin_id)
    ids = add_featured(sql, other)               # not in the admin's library: it is global

    r = client.post("/api/recompute-scores", headers=admin_h)

    assert r.status_code == 200
    assert (r.json()["updated"], r.json()["written"]) == (3, 3)
    assert [row[2] for row in scores(sql, ids)] == pytest.approx([e[2] for e in EXPECTED])


# ---------------------------------------------------------------- import jobs

def test_an_import_job_leaves_catalogue_scores_alone(client, sql, make_user, app_module, monkeypatch):
    uid, h = make_user()
    ids = add_featured(sql)
    playlist = P.Playlist("List", [P.Item(title="New", artist="Ann", spotify_id="NEWSID")])
    monkeypatch.setattr(P, "fetch", lambda source, get, ytdlp_extract=None: playlist)
    monkeypatch.setattr(app_module, "_get_app_token", lambda force_refresh=False: "app-token")

    r = client.post("/api/ingest/playlist-url",
                    json={"url": "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"}, headers=h)
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    deadline = time.time() + 20
    while True:
        s = client.get(f"/api/ingest/status/{job_id}", headers=h).json()
        if s["status"] in ("complete", "error", "cancelled"):
            break
        assert time.time() < deadline, "job did not finish"
        time.sleep(0.05)

    assert s["status"] == "complete" and s["queued_for_analysis"] == 1
    assert scores(sql, ids) == [(SENTINEL, SENTINEL, SENTINEL, SENTINEL, "chill")] * 3
