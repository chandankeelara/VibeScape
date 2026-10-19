"""ingest_pipeline/fused_vector.py: the fused_v2 language block."""
import numpy as np

from ingest_pipeline import fused_vector as fv

LANG = slice(fv.MERT_DIM + len(fv.SCALAR_COLS), fv.FUSED_DIM)

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
    assert fv.FUSED_DIM == 768 + 9 + 64 == 841
    assert _fused("kn").shape == (841,)
    assert fv.FUSED_MODEL_VERSION == "fused_v2_mert_scalar_lang64"


def test_the_slot_table_is_append_only():
    assert fv.LANG_CODES[:len(RELEASED)] == RELEASED
    assert len(set(fv.LANG_CODES)) == len(fv.LANG_CODES) <= fv.OTHER_SLOT


def test_every_language_has_its_own_slot():
    """ur, sa, ar and ko shared 'other' with every rare language in the
    first fused_v2 and scored single-seed precision@10 of 0.09-0.74."""
    for i, lang in enumerate(fv.LANG_CODES):
        v = fv._lang_onehot(lang.upper())
        assert v[i] == 1 and v.sum() == 1
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
    sims = {lang: float(blank @ _fused(lang)) for lang in fv.LANG_CODES + ["xx"]}
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
    assert np.array_equal(fv._build_fused(row, m),
                          fv._build_fused(row, m, weights=(fv.W_MERT, fv.W_SCALAR, fv.W_LANG)))
    assert fv._build_fused(row, m, langs=["en", "kn"]).shape == (768 + 9 + 3,)
    assert not fv._build_fused(row, m, weights=(fv.W_MERT, fv.W_SCALAR, 0.0))[LANG].any()


def test_the_slot_check_lists_what_to_append():
    import language_slots
    counts = {"en": 50, None: 3, "sw": 2, "am": 2, "hi": 1}
    assert language_slots.missing(counts) == ["am", "sw"]
