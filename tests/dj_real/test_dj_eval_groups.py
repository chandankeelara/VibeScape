"""The DJ eval's group keys and its copy of the frontend's thresholds. Pure:
no server, no data."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from dj_eval.client import Track, classify  # noqa: E402
from dj_eval.groups import album_group_key, album_key, derive, primary_artist  # noqa: E402


def test_primary_artist():
    assert primary_artist("Tyler, The Creator") == "tyler, the creator"
    assert primary_artist("Yuvan Shankar Raja,Sid Sriram") == "yuvan shankar raja"
    assert primary_artist("Earth, Wind & Fire") == "earth, wind & fire"
    assert primary_artist("Sidhu Moose Wala,Amrit Maan") == "sidhu moose wala"


def test_album_key_names_the_film():
    assert album_key('High On Love (From "Pyaar Prema Kaadhal" Original Motion Picture Soundtrack)') \
        == "pyaar prema kaadhal"
    assert album_key("Kaala (Tamil) (Original Motion Picture Soundtrack)") == "kaala"
    assert album_key("Red (Taylor's Version)") == "red"


def test_films_group_across_artists_albums_do_not():
    film = [Track("a", album='Song (From "Kantara")', artist="B. Ajaneesh Loknath"),
            Track("b", album="Kantara (Original Motion Picture Soundtrack)", artist="Sai Vignesh")]
    assert album_group_key(film[0]) == album_group_key(film[1]) == "kantara"
    hits = [Track("c", album="Greatest Hits", artist="Queen"),
            Track("d", album="Greatest Hits", artist="ABBA")]
    assert album_group_key(hits[0]) != album_group_key(hits[1])


def test_energy_bands_split_a_language_in_three():
    cat = {f"t{i}": Track(f"t{i}", language="kn", vibe=i / 30) for i in range(30)}
    bands = {g.name: g.members for g in derive(cat) if g.family == "lang_band"}
    assert [len(bands[f"lang_band:kn:{b}"]) for b in ("low", "mid", "high")] == [10, 10, 10]
    assert max(cat[s].vibe for s in bands["lang_band:kn:low"]) < \
        min(cat[s].vibe for s in bands["lang_band:kn:mid"])


def test_classify_matches_the_frontend():
    # frontend-next/src/features/queue/dj.js classifyTransition
    assert classify(0.85) == "completed"
    assert classify(0.84) == "next"
    assert classify(0.45) == "next"
    assert classify(0.44) == "skipped"
