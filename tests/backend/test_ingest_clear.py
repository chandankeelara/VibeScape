"""POST /api/ingest/clear removes the caller's library links and nothing else.

Tracks are a global catalogue. A user clearing their library must not delete
catalogue rows, because on Turso (foreign keys on) that cascades into the
user's track_events, user_track_stats and the track's embeddings. Local
SQLite may not enforce the cascade, so these tests assert the rows directly
rather than observing it.
"""
import numpy as np

ENV = {"session_id": "s-clear", "tz_offset_min": 0, "vibe": 50, "vibe_source": "user", "dj_mode": False}


def count(sql, q, *args):
    return sql.execute(q, args).fetchone()[0]


def test_clear_keeps_catalogue_history_and_embeddings(client, sql, make_user, make_tracks):
    me, h = make_user()
    other, _ = make_user()
    mine, shared = make_tracks(me, [np.ones(788), np.ones(788)])
    sql.execute("INSERT INTO user_tracks (user_id, track_id) VALUES (?, ?)", (other, shared))
    sql.commit()

    r = client.post("/api/events", headers=h, json={"events": [
        {**ENV, "type": "play_start", "track_id": mine},
        {**ENV, "type": "play_end", "track_id": mine, "reason": "completed",
         "position_ms": 200000, "duration_ms": 200000, "listened_ms": 200000},
    ]})
    assert r.status_code == 202 and r.json()["accepted"] == 2, r.text
    events_before = count(sql, "SELECT COUNT(*) FROM track_events WHERE user_id=? AND track_id=?", me, mine)
    assert events_before == 2
    assert count(sql, "SELECT COUNT(*) FROM user_track_stats WHERE user_id=? AND track_id=?", me, mine) == 1

    r = client.post("/api/ingest/clear", headers=h)
    assert r.status_code == 200, r.text

    # The caller's library is empty ...
    assert count(sql, "SELECT COUNT(*) FROM user_tracks WHERE user_id=?", me) == 0
    # ... but the catalogue, its embeddings and the caller's history survive.
    assert count(sql, "SELECT COUNT(*) FROM tracks WHERE id=?", mine) == 1
    assert count(sql, "SELECT COUNT(*) FROM track_embeddings WHERE track_id=?", mine) == 1
    assert count(sql, "SELECT LENGTH(fused_embedding) FROM track_embeddings WHERE track_id=?", mine) == 788 * 4
    assert count(sql, "SELECT COUNT(*) FROM track_events WHERE user_id=? AND track_id=?", me, mine) == events_before
    assert count(sql, "SELECT COUNT(*) FROM user_track_stats WHERE user_id=? AND track_id=?", me, mine) == 1
    # Another user's link to a shared track is untouched.
    assert count(sql, "SELECT COUNT(*) FROM user_tracks WHERE user_id=? AND track_id=?", other, shared) == 1
    assert count(sql, "SELECT COUNT(*) FROM tracks WHERE id=?", shared) == 1
    # The pruning keys stay for old clients (the mobile model parses this body).
    assert r.json() == {"cleared": 2, "tracks_pruned": 0, "audio_files_removed": 0}


def test_clear_does_not_unlink_audio_files(client, sql, make_user, make_tracks, app_module):
    me, h = make_user()
    (tid,) = make_tracks(me, [None])
    # Must sit under PROJECT_ROOT or the old code would refuse to resolve it.
    scratch = app_module.PROJECT_ROOT / "tests" / f".tmp-clear-audio-{tid}-{me}"
    scratch.mkdir(exist_ok=True)
    audio = scratch / "track.mp3"
    audio.write_bytes(b"ID3")
    try:
        sql.execute("UPDATE tracks SET audio_path=? WHERE id=?",
                    (str(audio.relative_to(app_module.PROJECT_ROOT)), tid))
        sql.commit()
        assert client.post("/api/ingest/clear", headers=h).status_code == 200
        assert audio.exists()
    finally:
        audio.unlink(missing_ok=True)
        scratch.rmdir()


def test_clear_requires_a_session(client):
    assert client.post("/api/ingest/clear").status_code == 401
