"""
The genre taxonomy: which genres a track can be scored on, in a fixed order.

Read by ingest_pipeline/genre_tagging.py (which validates every score
against it) and by whoever tags (scripts/genre_tags.py, ingest_pipeline/
README.md § Tagging genres). The definitions describe what a track SOUNDS
like, not where it comes from: an Indian film song is scored by its sound
(film_melody, film_mass) and may score on other genres too.

THE ORDER IS APPEND-ONLY. A later DJ-vector block will give each genre a
slot, indexed by its position in GENRES and sized with spare slots, the way
fused_vector.LANG_CODES gives each language one. Reordering or removing a
genre would silently move every stored vector's genre block onto the wrong
genres. A new genre goes at the END, and bumps GENRE_TAXONOMY_VERSION so a
tag's provenance says which list it was scored against. Renaming a slug is
removing one and appending another. tests/ingest/test_genre_tags.py pins
the current list as a prefix.
"""
from __future__ import annotations

# Stamped into tracks.genre_model_version as `...+taxonomy_<version>`. Bump
# it whenever a genre is appended: a track tagged against v1 was never asked
# about the new one, which a re-tag can find by this value.
GENRE_TAXONOMY_VERSION = "v1"

# (slug, one-line definition by sound). APPEND ONLY -- see the module
# docstring.
GENRE_TAXONOMY: tuple[tuple[str, str], ...] = (
    ("pop", "Hook-led verse-chorus songs with polished mainstream production and a lead vocal up front."),
    ("hiphop_rap", "Rapped or rhythmic spoken vocals over programmed beats: hip-hop, trap, drill, boom-bap."),
    ("dance_edm", "Electronic club music built on a steady kick or a build-and-drop: house, techno, EDM, trance."),
    ("ambient_chill", "Low-intensity, atmospheric, mellow textures: lo-fi, chillout, downtempo, ambient."),
    ("rock_alt_metal", "Band music driven by electric guitars and live drums: rock, indie, alternative, punk, metal."),
    ("rnb_soul", "Smooth groove-led vocals with melismatic singing: R&B, soul, funk, neo-soul."),
    ("film_melody", "Melodic, emotional film songs led by the singer over strings, piano or acoustic backing."),
    ("film_mass", "Film songs built for spectacle: heavy percussion, chant-like hooks, hero-entry and item numbers."),
    ("folk", "Traditional or acoustic roots music, regional folk idioms and singer-songwriter."),
    ("devotional", "Religious or spiritual music: bhajans, kirtans, qawwali, gospel, chants."),
    ("classical", "Art-music traditions: Western classical, Carnatic, Hindustani."),
    ("instrumental_score", "Soundtrack and score with no lead vocal: background score, cinematic themes."),
    ("latin_afro", "Latin and African popular rhythms: reggaeton, salsa, bachata, Afrobeats, amapiano."),
    ("reggae_dancehall", "Off-beat skank and riddim-based music: reggae, dub, dancehall."),
    ("country", "Twang, steel and acoustic guitars and storytelling vocals: country, Americana, bluegrass."),
    ("jazz_blues", "Swing, improvisation and blue notes: jazz, blues, bossa nova, lounge."),
    ("other", "A sound none of the genres above describes."),
)

GENRES: tuple[str, ...] = tuple(slug for slug, _ in GENRE_TAXONOMY)
DEFINITIONS: dict[str, str] = dict(GENRE_TAXONOMY)

# The only values a score may take. Zero is the absence of a genre and is
# OMITTED from a track's scores, never written; scores are independent per
# genre and do not sum to anything.
SCORE_VALUES: tuple[int, ...] = (50, 100)
