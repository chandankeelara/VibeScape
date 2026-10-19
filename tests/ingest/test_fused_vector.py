"""ingest_pipeline/fused_vector.py: the fused_v2 language block."""
import numpy as np

from ingest_pipeline import fused_vector as fv

LANG = slice(fv.MERT_DIM + len(fv.SCALAR_COLS), fv.FUSED_DIM)


def _fused(language):
    rng = np.random.default_rng(1)
    row = {c: 0.5 for c in fv.SCALAR_COLS}
    row["language"] = language
    return fv._build_fused(row, rng.normal(size=fv.MERT_DIM).astype(np.float32))


def test_an_untagged_track_has_an_all_zero_language_block():
    """fused_v1 put NULL in 'other', next to every rare tagged language, and
    rare-language seeds leaked 74-94% of their picks into untagged tracks."""
    for lang in (None, "", "  "):
        assert not fv._lang_onehot(lang).any()
        assert not _fused(lang)[LANG].any()


def test_untagged_is_equally_far_from_every_language():
    blank = _fused(None)
    sims = {lang: float(blank @ _fused(lang)) for lang in fv.TOP_LANGS + ["ar"]}
    assert max(sims.values()) - min(sims.values()) < 1e-6


def test_slotted_and_rare_languages():
    for i, lang in enumerate(fv.TOP_LANGS):
        v = fv._lang_onehot(lang.upper())
        assert v[i] == 1 and v.sum() == 1
    for rare in ("ar", "ko", "ur", "km"):
        v = fv._lang_onehot(rare)
        assert v[-1] == 1 and v.sum() == 1


def test_the_slot_list():
    assert "km" not in fv.TOP_LANGS               # only ever Whisper mis-tags
    assert len(set(fv.TOP_LANGS)) == len(fv.TOP_LANGS)
    assert {"ml", "es", "fr"} <= set(fv.TOP_LANGS)
    assert fv.LANG_DIMS == len(fv.TOP_LANGS) + 1
    assert fv.FUSED_DIM == 788                    # production's F32_BLOB(788)
    assert fv.FUSED_MODEL_VERSION.startswith("fused_v2")


def test_the_derivation_reproduces_the_list():
    """scripts/language_slots.py on counts shaped like the 2026-10-19 backup."""
    import language_slots
    counts = {"en": 2857, "hi": 589, "kn": 548, "te": 298, None: 262, "ta": 242,
              "pa": 127, "ml": 94, "es": 85, "fr": 37, "pt": 28, "ar": 24, "sa": 19,
              "ko": 14, "ur": 12}
    assert language_slots.derive(counts, fv.LANG_SLOT_MIN_TRACKS) == fv.TOP_LANGS


def test_the_weights_override_is_only_for_the_benchmark():
    rng = np.random.default_rng(2)
    row = {c: 0.5 for c in fv.SCALAR_COLS}
    row["language"] = "kn"
    m = rng.normal(size=fv.MERT_DIM).astype(np.float32)
    assert np.array_equal(fv._build_fused(row, m),
                          fv._build_fused(row, m, langs=fv.TOP_LANGS,
                                          weights=(fv.W_MERT, fv.W_SCALAR, fv.W_LANG)))
    assert not fv._build_fused(row, m, weights=(fv.W_MERT, fv.W_SCALAR, 0.0))[LANG].any()
