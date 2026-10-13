"""The iTunes preview matcher must return the track it was asked for, or
nothing. Before 2026-10-11 it fell back to "first result with a preview",
which gave one album track's audio to 111 different songs."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ingest_pipeline.preview_providers import (  # noqa: E402
    ItunesPreview, artists_match, durations_match, titles_match,
)

pick = ItunesPreview._pick_best


def res(name, artist, ms=None, url=None):
    return {"trackName": name, "artistName": artist, "trackTimeMillis": ms,
            "previewUrl": url or f"https://audio/{name}.m4a"}


# What iTunes returned for "FINA Bad Bunny": the album, not the song.
DEBI_TIRAR = [res("EoO", "Bad Bunny"), res("BAILE INoLVIDABLE", "Bad Bunny"),
              res("NUEVAYoL", "Bad Bunny")]


def test_album_neighbour_is_not_the_song():
    assert pick(DEBI_TIRAR, "FINA", "Bad Bunny") is None


def test_the_song_is_found_among_its_album():
    hit = pick(DEBI_TIRAR + [res("FINA", "Bad Bunny")], "FINA", "Bad Bunny")
    assert hit["trackName"] == "FINA"


def test_no_result_with_a_preview():
    assert pick([{"trackName": "FINA", "artistName": "Bad Bunny"}], "FINA", "Bad Bunny") is None


def test_accents_and_case():
    assert titles_match("EL CLúB", "EL CLÚB")
    assert titles_match("El Club", "EL CLUB")


def test_suffixes_that_name_the_same_recording():
    assert titles_match("Hukum - Thalaivar Alappara", 'Hukum (From "Jailer")')
    assert titles_match("Havana (feat. Young Thug)", "Havana")
    assert titles_match("Bohemian Rhapsody - Remastered 2011", "Bohemian Rhapsody")
    assert titles_match("Secret Of Succes", "Secret Of Success")


def test_different_songs_sharing_a_film_name():
    assert not titles_match("Dhurandhar The Revenge - Aari Aari (From \"Dhurandhar The Revenge\")",
                            "Phir Se (From \"Dhurandhar The Revenge\")")


def test_collisions_found_on_2026_10_11():
    for a, b in [("Nights", "White Ferrari"), ("We Rollin", "Still Rollin"),
                 ("Pink + White", "Pink Matter"), ("Icey", "Memo"),
                 ("Act II: Can't Go Back", "Never Go Back"), ("STATS", "family ties")]:
        assert not titles_match(a, b), (a, b)


def test_a_different_version_is_a_different_recording():
    assert not titles_match("Marali Manasaagide 8D Audio Song", "Marali Manasaagide")
    assert not titles_match("NO BATIDÃO - Super Slowed", "NO BATIDÃO")
    assert not titles_match("Sanam Teri Kasam - Reprise", "Sanam Teri Kasam")
    assert titles_match("Ultra Instinct (Trap Remix)", "Ultra Instinct - Trap Remix")


def test_artists():
    assert artists_match("A.R. Rahman", "A. R. Rahman")
    assert artists_match("Jorja Smith", "Jorja Smith X Preditah")
    assert artists_match("Sonu Nigam", "V. Harikrishna & Sonu Nigam")
    assert not artists_match("Majestic", "Boney M.")
    assert not artists_match("Naisha", "Sean Paul")


def test_durations():
    assert durations_match(None, 200_000)
    assert durations_match(200_000, 210_000)
    assert not durations_match(200_000, 260_000)


def test_wrong_length_rejected():
    assert pick([res("Levitating", "Dua Lipa", ms=290_000)], "Levitating", "Dua Lipa", 203_000) is None


def test_literal_title_preferred():
    hit = pick([res('Hukum (From "Jailer")', "Anirudh Ravichander"),
                res("Hukum", "Anirudh Ravichander")], "Hukum", "Anirudh Ravichander")
    assert hit["trackName"] == "Hukum"
