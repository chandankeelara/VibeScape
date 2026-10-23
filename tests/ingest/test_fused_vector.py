"""ingest_pipeline/fused_vector.py: the language block (fused_v2) and the
genre block (fused_v3)."""
import json

import numpy as np
import pytest

from ingest_pipeline import fused_vector as fv

LANG = slice(fv.MERT_DIM + len(fv.SCALAR_COLS), fv.MERT_DIM + len(fv.SCALAR_COLS) + fv.LANG_DIMS)
GENRE = slice(fv.FUSED_V2_DIM, fv.FUSED_DIM)

# The table as first released (2026-10-19). Appending to LANG_CODES is fine;
# changing any of these positions silently changes every stored vector.
RELEASED = ["en", "hi", "kn", "te", "ta", "pa", "ml", "es", "fr", "pt",
            "ar", "sa", "ko", "ur", "ru", "bn", "ja", "mr", "yo", "de",
            "it", "nl", "bm", "ka", "tcy", "tr", "tw", "zh"]


def _fused(language):
    rng = np.random.default_rng(1)
    row = {c: 0.5 for c in fv.SCALAR_COLS}
    row["language"] = language
    return fv._build_fused(row, rng.normal(size=fv.MERT_DIM).astype(np.float32))


def test_the_width():
    assert fv.LANG_BLOCK == fv.LANG_DIMS == 64
    assert fv.FUSED_V2_DIM == 768 + 9 + 64 == 841
    assert fv.GENRE_BLOCK == fv.GENRE_DIMS == 24
    assert fv.FUSED_DIM == fv.FUSED_V2_DIM + 24 == 865
    assert _fused("kn").shape == (fv.FUSED_DIM,)
    assert fv._build_fused_v2({"language": "kn"}, np.ones(fv.MERT_DIM, np.float32)).shape == (841,)
    assert fv.FUSED_MODEL_VERSION == "fused_v3_mert_scalar_lang64_genre24"
    assert fv.FUSED_V2_MODEL_VERSION == "fused_v2_mert_scalar_lang64"


def test_the_slot_table_is_append_only():
    assert fv.LANG_CODES[:len(RELEASED)] == RELEASED
    assert len(set(fv.LANG_CODES)) == len(fv.LANG_CODES) <= fv.OTHER_SLOT


def test_every_language_has_its_own_slot():
    """ur, sa, ar and ko shared 'other' with every rare language in the
    first fused_v2 and scored single-seed precision@10 of 0.09-0.74."""
    for i, lang in enumerate(fv.LANG_CODES):
        v = fv._lang_onehot(lang.upper())
        w = fv.INSTRUMENTAL_WEIGHT if lang == fv.INSTRUMENTAL else 1
        assert v[i] == w and v.sum() == w
    blocks = [_fused(lang)[LANG] for lang in ("ur", "sa", "ar", "ko", "hi")]
    for i in range(len(blocks)):
        for j in range(i + 1, len(blocks)):
            assert float(blocks[i] @ blocks[j]) == 0


def test_a_code_not_in_the_table_goes_to_other():
    for code in ("xx", "km"):           # km: only ever a Whisper mis-tag
        v = fv._lang_onehot(code)
        assert v[fv.OTHER_SLOT] == 1 and v.sum() == 1
    assert "km" not in fv.LANG_CODES


def test_an_untagged_track_has_an_all_zero_language_block():
    """fused_v1 put NULL in 'other', next to every rare tagged language, and
    rare-language seeds leaked 74-94% of their picks into untagged tracks."""
    for lang in (None, "", "  "):
        assert not fv._lang_onehot(lang).any()
        assert not _fused(lang)[LANG].any()


def test_untagged_is_equally_far_from_every_language():
    blank = _fused(None)
    sims = {lang: float(blank @ _fused(lang)) for lang in fv.LANG_CODES + ["xx"]
            if lang != fv.INSTRUMENTAL}
    assert max(sims.values()) - min(sims.values()) < 1e-6


def test_appending_a_language_moves_no_existing_vector(monkeypatch):
    before = {lang: _fused(lang) for lang in fv.LANG_CODES}
    monkeypatch.setattr(fv, "LANG_CODES", fv.LANG_CODES + ["sw"])
    for lang, v in before.items():
        assert np.array_equal(_fused(lang), v)
    assert fv._lang_onehot("sw")[len(fv.LANG_CODES) - 1] == 1


def test_the_benchmark_overrides_leave_the_recipe_alone():
    rng = np.random.default_rng(2)
    row = {c: 0.5 for c in fv.SCALAR_COLS}
    row["language"] = "kn"
    m = rng.normal(size=fv.MERT_DIM).astype(np.float32)
    assert np.array_equal(fv._build_fused_v2(row, m),
                          fv._build_fused_v2(row, m, weights=(fv.W_MERT_V2, fv.W_SCALAR, fv.W_LANG)))
    assert np.array_equal(fv._build_fused(row, m), fv._build_fused(row, m, weights=fv.v3_weights()))
    assert fv._build_fused_v2(row, m, langs=["en", "kn"]).shape == (768 + 9 + 3,)
    assert fv._build_fused(row, m, langs=["en", "kn"]).shape == (768 + 9 + 3 + 24,)
    assert not fv._build_fused_v2(row, m, weights=(fv.W_MERT_V2, fv.W_SCALAR, 0.0))[LANG].any()


def test_the_slot_check_lists_what_to_append():
    import language_slots
    counts = {"en": 50, None: 3, "sw": 2, "am": 2, "hi": 1}
    assert language_slots.missing(counts) == ["am", "sw"]


def test_the_no_language_verdict_gets_the_instrumental_slot():
    """language NULL with language_status 'done' is tagging's "no language"
    verdict (an instrumental). It gets slot 28 ('zxx') at INSTRUMENTAL_WEIGHT,
    so instrumental sessions hold together; a track with no verdict yet keeps
    the language-neutral all-zero block."""
    assert fv.LANG_CODES[28] == fv.INSTRUMENTAL == "zxx"
    rng = np.random.default_rng(3)
    m = rng.normal(size=fv.MERT_DIM).astype(np.float32)
    row = {c: 0.5 for c in fv.SCALAR_COLS}
    clear = fv._build_fused({**row, "language": None, "language_status": "done"}, m)[LANG]
    nonzero = np.flatnonzero(clear)
    assert list(nonzero) == [28]
    for status in (None, "pending", "no_match"):
        assert not fv._build_fused({**row, "language": None, "language_status": status}, m)[LANG].any()
    # a tagged track is its language, whatever the status says
    tagged = fv._build_fused({**row, "language": "kn", "language_status": "done"}, m)[LANG]
    assert list(np.flatnonzero(tagged)) == [fv.LANG_CODES.index("kn")]


def test_the_instrumental_slot_is_lighter_than_a_language():
    assert 0 < fv.INSTRUMENTAL_WEIGHT < 1
    assert fv._lang_onehot("zxx")[28] == fv.INSTRUMENTAL_WEIGHT


# ------------------------------------------------------------ the genre block

def _row(scores=None, status="done", language="kn"):
    row = {c: 0.5 for c in fv.SCALAR_COLS}
    row.update(language=language, language_status="done", genre_status=status,
               genre_scores=None if scores is None else json.dumps(scores))
    return row


def _mert(seed=4):
    return np.random.default_rng(seed).normal(size=fv.MERT_DIM).astype(np.float32)


def test_the_genre_slots_are_the_taxonomy_in_order():
    """Slot i is genre_taxonomy.GENRES[i]; 7 spare slots stay zero."""
    from ingest_pipeline.genre_taxonomy import GENRES
    assert len(GENRES) <= fv.GENRE_BLOCK
    for i, slug in enumerate(GENRES):
        block = fv._genre_block(_row({slug: 100}))
        assert list(np.flatnonzero(block)) == [i] and block[i] == pytest.approx(1.0)
    assert not fv._genre_block(_row({"pop": 100, "folk": 50}))[len(GENRES):].any()


def test_scores_are_divided_by_100_and_the_block_is_unit_length():
    block = fv._genre_block(_row({"film_melody": 100, "folk": 50}))
    from ingest_pipeline.genre_taxonomy import GENRES
    i, j = GENRES.index("film_melody"), GENRES.index("folk")
    assert block[i] == pytest.approx(2 * block[j])
    assert float(np.linalg.norm(block)) == pytest.approx(1.0)
    # one genre or three, the block weighs the same
    many = fv._genre_block(_row({"pop": 100, "folk": 100, "dance_edm": 50}))
    assert float(np.linalg.norm(many)) == pytest.approx(1.0)


@pytest.mark.parametrize("scores,status", [
    (None, "done"), ({}, "done"), ({"pop": 100}, "pending"), ({"pop": 100}, None),
    ({"not_a_genre_yet": 100}, "done"),
])
def test_an_untagged_track_has_an_all_zero_genre_block(scores, status):
    v = fv._build_fused(_row(scores, status), _mert())
    assert v.shape == (fv.FUSED_DIM,) and not v[GENRE].any()


def test_unreadable_scores_are_neutral_not_a_crash():
    row = _row()
    row["genre_scores"] = "{not json"
    assert not fv._build_fused(row, _mert())[GENRE].any()


def test_w_genre_zero_is_fused_v2_bit_for_bit():
    """W_GENRE = 0 (MERT keeps all its weight) must reproduce fused_v2
    exactly, padded with zeros, for tagged and untagged rows: the
    replay_bench grid's G0 row is compared against fused_v2 on that."""
    for row in (_row({"film_mass": 100, "folk": 50}), _row(None, None), _row({"pop": 50}, "done", None)):
        for seed in range(5):
            m = _mert(seed)
            v3 = fv._build_fused(row, m, weights=fv.v3_weights(0.0))
            assert np.array_equal(v3[:fv.FUSED_V2_DIM], fv._build_fused_v2(row, m))
            assert not v3[GENRE].any()


def test_the_genre_weight_comes_out_of_mert():
    assert fv.v3_weights(0.0) == (fv.W_MERT_V2, fv.W_SCALAR, fv.W_LANG, 0.0)
    w_mert, w_scalar, w_lang, w_genre = fv.v3_weights()
    assert (w_scalar, w_lang, w_genre) == (fv.W_SCALAR, fv.W_LANG, fv.W_GENRE)
    assert w_mert == pytest.approx(fv.W_MERT_V2 - fv.W_GENRE) == pytest.approx(fv.W_MERT)
    assert sum(fv.v3_weights()) == pytest.approx(1.0)


def test_a_shared_genre_brings_two_tracks_closer_by_the_genre_weight():
    m1, m2 = _mert(1), _mert(2)
    seed = fv._build_fused(_row({"film_mass": 100}), m1)
    same = float(seed @ fv._build_fused(_row({"film_mass": 100}), m2))
    other = float(seed @ fv._build_fused(_row({"rock_alt_metal": 100}), m2))
    assert same > other
    # every block is unit length, so the gap is W_GENRE^2 over the sum of
    # the squared weights (the whole vector's norm before the L2)
    assert same - other == pytest.approx(fv.W_GENRE ** 2 / sum(w * w for w in fv.v3_weights()),
                                         rel=1e-4)


def test_an_untagged_track_is_equally_far_from_every_genre():
    blank = fv._build_fused(_row(None, None), _mert(2))
    from ingest_pipeline.genre_taxonomy import GENRES
    sims = [float(fv._build_fused(_row({g: 100}), _mert(1)) @ blank) for g in GENRES]
    assert max(sims) - min(sims) < 1e-6


def test_appending_a_genre_moves_no_existing_vector(monkeypatch):
    from ingest_pipeline import genre_taxonomy as tax
    rows = [_row({slug: 100}) for slug in tax.GENRES]
    before = [fv._build_fused(r, _mert()) for r in rows]
    monkeypatch.setattr(fv, "GENRES", tax.GENRES + ("bhangra",))
    for r, v in zip(rows, before):
        assert np.array_equal(fv._build_fused(r, _mert()), v)
    assert fv._genre_block(_row({"bhangra": 100}))[len(tax.GENRES)] == pytest.approx(1.0)
