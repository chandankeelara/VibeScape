"""GET /api/tracks/{key}/lyrics and backend/lyrics.py.

LRCLIB is never reached: conftest.py's _no_lrclib refuses lyrics._http, and
every test here that needs answers installs a FakeLrclib in its place.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import requests

import lyrics

_REAL_HTTP = lyrics._http   # taken before conftest.py replaces it


# ---------------------------------------------------------------------------
# LRC parser
# ---------------------------------------------------------------------------

def test_lrc_two_and_three_digit_fractions():
    lines = lyrics.parse_lrc("[00:12.40]first\n[01:02.345]second\n[02:03]third")
    assert lines == [{"t_ms": 12400, "text": "first"},
                     {"t_ms": 62345, "text": "second"},
                     {"t_ms": 123000, "text": "third"}]


def test_lrc_several_timestamps_on_one_line_are_sorted():
    lines = lyrics.parse_lrc("[00:10.00][00:30.00]chorus\n[00:20.00]verse")
    assert lines == [{"t_ms": 10000, "text": "chorus"},
                     {"t_ms": 20000, "text": "verse"},
                     {"t_ms": 30000, "text": "chorus"}]


def test_lrc_offset_tag_applies_and_other_metadata_is_ignored():
    text = "[ar:Someone]\n[ti:Song]\n[offset:+500]\n[length: 03:20]\n[00:10.00]a\n[00:00.20]b"
    assert lyrics.parse_lrc(text) == [{"t_ms": 0, "text": "b"}, {"t_ms": 9500, "text": "a"}]
    assert lyrics.parse_lrc("[offset:-250]\n[00:01.00]x") == [{"t_ms": 1250, "text": "x"}]


def test_lrc_blank_lines_are_kept_as_gaps_and_untimed_lines_dropped():
    text = "[00:01.00]one\n[00:05.00]\n[00:09.00]  two  \nno timestamp here\n\n"
    assert lyrics.parse_lrc(text) == [{"t_ms": 1000, "text": "one"},
                                      {"t_ms": 5000, "text": ""},
                                      {"t_ms": 9000, "text": "two"}]


def test_lrc_word_stamps_are_stripped():
    assert lyrics.parse_lrc("[00:01.00]<00:01.00>hello <00:01.50>world") == \
        [{"t_ms": 1000, "text": "hello world"}]


# ---------------------------------------------------------------------------
# A fake LRCLIB
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class Seq(list):
    """Answers served in order, the last one repeated."""


class FakeLrclib:
    """get / search: a body (200), an int (that status), an exception (raised),
    or a Seq of them served in order. Records every call as (endpoint, params)."""

    def __init__(self, get=404, search=()):
        self.answers = {"get": get, "search": list(search)}
        self.calls = []

    def __call__(self, url, params):
        name = url.rsplit("/", 1)[-1]
        self.calls.append((name, dict(params)))
        a = self.answers[name]
        if isinstance(a, Seq):
            a = a.pop(0) if len(a) > 1 else a[0]
        if isinstance(a, BaseException):
            raise a
        if isinstance(a, int):
            return _Resp(a, {"statusCode": a})
        return _Resp(200, a)


def entry(id=1, title="Song", artist="Artist", duration=200.0, synced=True, plain=True,
          instrumental=False):
    return {"id": id, "trackName": title, "artistName": artist, "albumName": "Album",
            "duration": duration, "instrumental": instrumental,
            "syncedLyrics": f"[00:01.00]sung {id}\n[00:03.00]\n[00:05.00]end {id}" if synced else None,
            "plainLyrics": f"sung {id}\n\nend {id}" if plain else None}


@pytest.fixture
def fake(monkeypatch):
    def install(**kw):
        f = FakeLrclib(**kw)
        monkeypatch.setattr(lyrics, "_http", f)
        return f
    return install


# ---------------------------------------------------------------------------
# Choosing the entry
# ---------------------------------------------------------------------------

def test_get_within_three_seconds_is_used_without_a_search(fake):
    f = fake(get=entry(id=7, duration=202.0))
    out = lyrics.lookup("Song", "Artist", "Album", 200_000)
    assert out["status"] == "synced" and out["source_id"] == "7"
    assert out["matched_duration_ms"] == 202_000
    assert [c[0] for c in f.calls] == ["get"]
    assert f.calls[0][1] == {"track_name": "Song", "artist_name": "Artist",
                             "album_name": "Album", "duration": 200}


def test_search_picks_the_synced_entry_closest_to_our_duration(fake):
    fake(get=404, search=[entry(id=1, duration=170.0), entry(id=2, duration=202.5),
                          entry(id=3, duration=199.0), entry(id=4, duration=200.0, synced=False)])
    out = lyrics.lookup("Song", "Artist", None, 200_000)
    assert out["status"] == "synced" and out["source_id"] == "3"


def test_a_synced_entry_ten_seconds_off_is_not_synced(fake):
    """The get answer is 10 s off, so it falls through to search, where the
    only entry is the same one: its words are right, its timing is not."""
    f = fake(get=entry(id=9, duration=210.0), search=[entry(id=9, duration=210.0)])
    out = lyrics.lookup("Song", "Artist", "Album", 200_000)
    assert [c[0] for c in f.calls] == ["get", "search"]
    assert out["status"] == "plain" and out["synced_lrc"] is None
    assert out["plain"] == "sung 9\n\nend 9"
    assert lyrics.response({**out, "fetched_at": "x"}, 200_000)["lines"] == []


def test_instrumental_within_tolerance(fake):
    fake(get=entry(id=5, duration=200.0, synced=False, plain=False, instrumental=True))
    out = lyrics.lookup("Song", "Artist", None, 200_000)
    assert out["status"] == "instrumental" and out["plain"] is None


@pytest.mark.parametrize("title,artist", [
    ("Another Song", "Artist"),        # same artist, different song
    ("Song", "Somebody Else"),         # same name, different artist
    ("Song (Live)", "Artist"),         # a different recording
    ("Song - Karaoke Version", "Artist"),
])
def test_a_different_song_is_never_returned(fake, title, artist):
    wrong = entry(id=1, title=title, artist=artist, duration=200.0)
    f = fake(get=wrong, search=[wrong])
    assert lyrics.lookup("Song", "Artist", None, 200_000)["status"] == "none"
    assert [c[0] for c in f.calls] == ["get", "search"]


def test_title_and_artist_variants_still_match():
    assert lyrics.titles_match("Thrift Shop (feat. Wanz)", "Thrift Shop")
    assert lyrics.titles_match("Song - Remastered 2011", "Song")
    assert lyrics.artists_match("Tyler, The Creator", "Tyler, The Creator")
    assert lyrics.artists_match("Macklemore", "Macklemore & Ryan Lewis")


# ---------------------------------------------------------------------------
# Upstream failures
# ---------------------------------------------------------------------------

def test_one_503_is_retried(fake):
    f = fake(get=Seq([503, entry(id=2, duration=200.0)]))
    assert lyrics.lookup("Song", "Artist", None, 200_000)["status"] == "synced"
    assert [c[0] for c in f.calls] == ["get", "get"]


def test_two_503s_are_an_upstream_error_and_no_search(fake):
    f = fake(get=Seq([503]), search=[entry()])
    out = lyrics.lookup("Song", "Artist", None, 200_000)
    assert out["status"] == "none" and out["error"] == "upstream"
    assert [c[0] for c in f.calls] == ["get", "get"]


def test_a_timeout_on_search_is_an_upstream_error(fake):
    f = fake(get=404)
    f.answers["search"] = requests.Timeout("slow")
    assert lyrics.lookup("Song", "Artist", None, 200_000)["error"] == "upstream"
    assert [c[0] for c in f.calls] == ["get", "search", "search"]


def test_user_agent_and_timeout_are_sent(monkeypatch):
    seen = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        seen.update(url=url, headers=headers, timeout=timeout)
        return _Resp(404, {})
    monkeypatch.setattr(lyrics.requests, "get", fake_get)
    _REAL_HTTP("https://lrclib.net/api/get", {"track_name": "x"})
    assert seen["headers"] == {"User-Agent": "VibeScape (https://github.com/chandankeelara/VibeScape)"}
    assert seen["timeout"] == 5.0 and seen["url"] == "https://lrclib.net/api/get"


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------

@pytest.fixture
def track(make_user, make_tracks):
    uid, headers = make_user()
    (tid,) = make_tracks(uid, [None], titles={0: "Song"}, duration_ms=200_000)
    return tid, headers


def _get(client, tid, headers):
    return client.get(f"/api/tracks/{tid}/lyrics", headers=headers)


def test_requires_auth(client, track, fake):
    f = fake(get=entry())
    tid, _ = track
    assert _get(client, tid, {}).status_code == 401
    assert f.calls == []


def test_unknown_track_is_404(client, track, fake):
    _, headers = track
    assert _get(client, 999_999, headers).status_code == 404


def test_contract_for_synced(client, track, fake):
    fake(get=entry(id=4, duration=201.0))
    tid, headers = track
    r = _get(client, tid, headers)
    assert r.status_code == 200
    assert r.json() == {
        "status": "synced", "source": "lrclib",
        "lines": [{"t_ms": 1000, "text": "sung 4"}, {"t_ms": 3000, "text": ""},
                  {"t_ms": 5000, "text": "end 4"}],
        "plain": "sung 4\n\nend 4",
        "matched_duration_ms": 201_000, "track_duration_ms": 200_000,
        "credit": "Lyrics from LRCLIB",
    }


def test_cache_hit_makes_no_network_call(client, track, fake, sql):
    fake(get=entry(id=4, duration=201.0))
    tid, headers = track
    first = _get(client, tid, headers).json()
    f = fake(get=AssertionError("network"))   # a second request must not reach this
    assert _get(client, tid, headers).json() == first
    assert f.calls == []
    assert sql.execute("SELECT COUNT(*) FROM track_lyrics").fetchone()[0] == 1


def test_the_cache_write_is_one_execute_batch(client, track, fake, batches):
    fake(get=entry(id=4, duration=201.0))
    tid, headers = track
    _get(client, tid, headers)
    lyric_batches = [b for b in batches if "track_lyrics" in b[0][0]]
    assert len(lyric_batches) == 1 and len(lyric_batches[0]) == 1


def _age(sql, tid, days=0, minutes=0):
    at = datetime.now(timezone.utc) - timedelta(days=days, minutes=minutes)
    sql.execute("UPDATE track_lyrics SET fetched_at = ? WHERE track_id = ?",
                (lyrics.now_ts(at), tid))
    sql.commit()


def test_none_is_cached_then_asked_again_after_30_days(client, track, fake, sql):
    f = fake(get=404, search=[])
    tid, headers = track
    assert _get(client, tid, headers).json()["status"] == "none"
    assert len(f.calls) == 2
    _age(sql, tid, days=29)
    assert _get(client, tid, headers).json()["status"] == "none"
    assert len(f.calls) == 2                         # still fresh
    f.answers = {"get": entry(id=8, duration=200.0), "search": []}
    _age(sql, tid, days=31)
    body = _get(client, tid, headers).json()
    assert body["status"] == "synced" and len(f.calls) == 3
    assert sql.execute("SELECT status, source_id FROM track_lyrics WHERE track_id = ?",
                       (tid,)).fetchone()[:] == ("synced", "8")


def test_upstream_503_is_none_with_error_not_500_and_retried_soon(client, track, fake, sql):
    f = fake(get=503)
    tid, headers = track
    r = _get(client, tid, headers)
    assert r.status_code == 200
    assert r.json() == {"status": "none", "source": None, "lines": [], "plain": None,
                        "matched_duration_ms": None, "track_duration_ms": 200_000,
                        "credit": None, "error": "upstream"}
    assert sql.execute("SELECT status, error FROM track_lyrics WHERE track_id = ?",
                       (tid,)).fetchone()[:] == ("none", "upstream")
    n = len(f.calls)
    assert _get(client, tid, headers).json()["error"] == "upstream"
    assert len(f.calls) == n                         # within 10 minutes: cached
    _age(sql, tid, minutes=11)
    f.answers = {"get": entry(id=2, duration=200.0), "search": []}
    body = _get(client, tid, headers).json()
    assert body["status"] == "synced" and "error" not in body


def test_works_without_the_table(client, track, fake, app_module, monkeypatch):
    """Turso before scripts/_turso_create_track_lyrics.py: served, not cached.
    Dropping the table would not do: get_conn() re-runs ensure_db() locally."""
    import sqlite3
    real = app_module.get_conn

    class NoTable:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, q, params=()):
            if "track_lyrics" in q:
                raise sqlite3.OperationalError("no such table: track_lyrics")
            return self._conn.execute(q, params)

        def execute_batch(self, stmts):
            raise AssertionError("wrote to a table that is not there")

        def __getattr__(self, name):
            return getattr(self._conn, name)
    monkeypatch.setattr(app_module, "get_conn", lambda: NoTable(real()))
    f = fake(get=entry(id=4, duration=201.0))
    tid, headers = track
    r = _get(client, tid, headers)
    assert r.status_code == 200 and r.json()["status"] == "synced"
    _get(client, tid, headers)
    assert len(f.calls) == 2


# ---------------------------------------------------------------------------
# The one-shot table script
# ---------------------------------------------------------------------------

def test_create_script_reads_schema_and_is_dry_run_by_default(db, sql, capsys):
    import _turso_create_track_lyrics as script
    assert "CREATE TABLE IF NOT EXISTS track_lyrics" in script.statement()
    sql.execute("DROP TABLE track_lyrics")
    sql.commit()
    assert script.main([]) == 0
    assert "dry-run" in capsys.readouterr().out
    assert not sql.execute("SELECT 1 FROM sqlite_master WHERE name = 'track_lyrics'").fetchone()
    assert script.main(["--apply"]) == 0
    assert sql.execute("SELECT 1 FROM sqlite_master WHERE name = 'track_lyrics'").fetchone()
    assert script.main(["--apply"]) == 0
    assert "exists" in capsys.readouterr().out


def test_create_script_prod_exits_without_env_prod(db):
    import _turso_create_track_lyrics as script
    with pytest.raises(SystemExit):
        script.main(["--target", "prod"])
