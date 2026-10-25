"""Library search: fuzzy over title, artist, album and language (backend/library_search.py)."""
import secrets

import pytest

import library_search as L
import track_rows

LIB = [
    dict(id=1, title="Bekhayali", artist="Arijit Singh", album="Kabir Singh", language="hi"),
    dict(id=2, title="Tum Hi Ho", artist="Arijit Singh", album="Aashiqui 2", language="hi"),
    dict(id=3, title="Belageddu", artist="Vijay Prakash", album="Kirik Party", language="kn"),
    dict(id=4, title="Hoovina Baanadanthe", artist="Shreya Ghoshal", album="Bachchan", language="kn"),
    dict(id=5, title="After Hours", artist="The Weeknd", album="After Hours", language="en"),
    dict(id=6, title="Over Now (with The Weeknd)", artist="Calvin Harris", album="Over Now", language="en"),
    dict(id=7, title="Silver Lining", artist="Jackson Homer", album="Lines", language="en"),
    dict(id=8, title="Aathi", artist="Anirudh Ravichander", album="Kaththi", language="ta"),
    dict(id=9, title="Instrumental One", artist="Nobody", album="Quiet", language=None),
]


def ids(q, rows=LIB, limit=15):
    return [r["id"] for r in L.search(rows, q, limit)]


def test_typos_and_partial_words_match():
    assert set(ids("arjit")) == {1, 2}
    assert ids("shreya goshal") == [4]
    assert ids("bekhay") == [1]


def test_language_by_name_including_a_typo():
    assert set(ids("kannada")) == {3, 4}
    assert set(ids("kanada")) == {3, 4}
    assert ids("tamil") == [8]


def test_every_word_must_match_across_fields():
    assert ids("kannada vijay") == [3]          # language + artist
    assert ids("hindi kabir") == [1]           # language + album
    assert ids("kannada arijit") == []


def test_own_songs_outrank_features():
    assert ids("weeknd") == [5, 6]


def test_title_phrase_outranks_scattered_words():
    rows = LIB + [dict(id=10, title="Ho Tum", artist="Hi", album="", language="hi")]
    assert ids("tum hi ho", rows)[0] == 2


def test_a_different_word_is_not_a_typo():
    assert 7 not in ids("blinding")             # 'lining': wrong first letter
    assert ids("zzzz") == []


def test_an_exact_word_outranks_a_prefix():
    # 'hi' is the word in 'Tum Hi Ho' and the start of 'hindi' (typing
    # incrementally); the exact word ranks first.
    assert ids("hi")[0] == 2


def test_accents_and_case_are_ignored():
    rows = [dict(id=1, title="Café Del Mar", artist="Energy 52", album="", language="es")]
    assert ids("cafe del", rows) == [1] and ids("CAFÉ", rows) == [1]


def test_empty_query_and_limit():
    assert ids("   ") == []
    assert len(L.search(LIB, "a", 3)) <= 3


# ----------------------------------------------------------------- endpoint

@pytest.fixture
def library(sql, make_user):
    uid, headers = make_user()
    other, _ = make_user()

    def add(user, title, artist, album, lang, status="done"):
        cur = sql.execute(
            "INSERT INTO tracks (title, artist, album, language, spotify_id, ingestion_status, vibe_score) "
            "VALUES (?, ?, ?, ?, ?, ?, 50)",
            (title, artist, album, lang, secrets.token_hex(11), status))
        sql.execute("INSERT INTO user_tracks (user_id, track_id) VALUES (?, ?)", (user, cur.lastrowid))
        return cur.lastrowid
    a = add(uid, "Belageddu", "Vijay Prakash", "Kirik Party", "kn")
    b = add(uid, "Bekhayali", "Arijit Singh", "Kabir Singh", "hi")
    add(uid, "Pending Song", "Vijay Prakash", "", "kn", status="pending")
    add(other, "Someone Else's", "Vijay Prakash", "", "kn")
    sql.commit()
    return headers, a, b


def test_endpoint_returns_full_rows_best_first(client, library, app_module):
    track_rows._search_rows_cache.clear()
    headers, a, b = library
    r = client.get("/api/tracks/search", params={"q": "kanada vijay"}, headers=headers)
    assert r.status_code == 200
    tracks = r.json()["tracks"]
    assert [t["id"] for t in tracks] == [a]                 # not pending, not another user's
    assert tracks[0]["album"] == "Kirik Party" and "vibe_score" in tracks[0]
    assert [t["id"] for t in client.get("/api/tracks/search", params={"q": "arjit"},
                                        headers=headers).json()["tracks"]] == [b]


def test_endpoint_requires_a_session(client):
    assert client.get("/api/tracks/search", params={"q": "x"}).status_code == 401
