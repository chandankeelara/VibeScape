"""score_dj.py: the vibe-test maths, session validation, the scoreboard's
best and provisional flag, and label idempotence. Pure: no server, no data;
sessions are synthetic listener.py state files."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from dj_eval import score_dj  # noqa: E402
from dj_eval.score_dj import ScoreError, add_entry, score_round, validate, vibe_metrics  # noqa: E402

CFG = score_dj.load_scenarios()
SCS = {sc["id"]: sc for sc in CFG["scenarios"]}
VIBE = SCS["vibe-kn-melody"]


def _pick(fit, outcome=None, how="dj"):
    return {"step": 0, "how": how, "spotify_id": "x", "title": "song", "artist": "a",
            "outcome": outcome or ("finished" if fit >= 3 else "skipped"), "fit": fit}


def _search(sc):
    s = CFG["songs"][sc["song"]]
    return {"step": 0, "how": "search", "spotify_id": s["spotify_ids"][0], "title": s["title"],
            "artist": s["artist"], "outcome": "finished", "fit": 4}


def _session(sc, picks, sid="s"):
    """sc's search, then the given picks (fits, or ready-made play dicts)."""
    plays = [_search(sc)] + [p if isinstance(p, dict) else _pick(p) for p in picks]
    for i, p in enumerate(plays, 1):
        p["step"] = i
    return {"id": sid, "persona": sc["account"], "plays": plays}


def _round(fits_by_run):
    """Every scenario: one session per run, with that run's fits."""
    return {sc["id"]: [_session(sc, f, f"{sc['id']}-{i}") for i, f in enumerate(fits_by_run)]
            for sc in CFG["scenarios"]}


# ----------------------------------------------------------------- maths
def test_a_perfect_run_scores_100_without_a_recovery():
    m = vibe_metrics([_pick(5) for _ in range(15)])
    assert m == {"hold": 1.0, "skip_rate": 0.0, "skip_recovery": None, "early": 1.0,
                 "score": 100.0}


def test_hold_skip_rate_and_early():
    fits = [2, 4, 5] + [4] * 6 + [1] * 6                 # 8 good, 7 skipped (fit < 3)
    m = vibe_metrics([_pick(f) for f in fits])
    assert m["hold"] == pytest.approx(8 / 15)
    assert m["skip_rate"] == pytest.approx(7 / 15)
    assert m["early"] == pytest.approx(2 / 3)


def test_skip_recovery_counts_the_pick_right_after_each_skip():
    # skips at 1, 2 and 5; after them: 2 (skipped), 3 (finished), 6 (finished).
    # The skip at 15 has no pick after it.
    outs = ["s", "s", "f", "f", "s", "f"] + ["f"] * 8 + ["s"]
    picks = [_pick(4, "finished" if o == "f" else "skipped") for o in outs]
    assert vibe_metrics(picks)["skip_recovery"] == pytest.approx(2 / 3)


def test_score_weights_and_a_missing_recovery_is_neutral():
    picks = [_pick(4, "skipped")] + [_pick(4)] * 14      # recovery 1, skip 1/15
    m = vibe_metrics(picks)
    assert m["score"] == pytest.approx(50 * 1 + 25 * (14 / 15) + 15 * 1 + 10 * 1)
    # No skip at all: recovery is left out, not scored as 0 or as 1.
    assert vibe_metrics([_pick(3)] * 15)["score"] == pytest.approx(100 * 25 / 85)


def test_weights_sum_to_100():
    assert sum(score_dj.VIBE_WEIGHTS.values()) == 100


# ----------------------------------------------------------- validation
def test_a_clean_session_is_valid():
    assert validate(_session(VIBE, [5] * 15), VIBE, CFG) == ([], [])


def test_a_run_of_18_is_kept_and_scored_on_its_first_15():
    st = _session(VIBE, [5] * 15 + [1, 1, 1])
    st["plays"][-1]["fit"] = None                        # an unrated overshoot does not matter
    bad, warn = validate(st, VIBE, CFG)
    assert bad == [] and warn == ["3 plays after play 16 ignored (dj x3)"]
    assert score_dj.score_run(st, VIBE)["hold"] == 1.0


def test_an_up_next_play_after_the_last_step_is_ignored():
    st = _session(VIBE, [5] * 15 + [_pick(1, how="picked")])
    bad, warn = validate(st, VIBE, CFG)
    assert bad == [] and warn == ["1 plays after play 16 ignored (picked x1)"]


def test_an_up_next_play_inside_the_window_is_rejected():
    st = _session(VIBE, [5] * 9 + [_pick(5, how="picked")] + [5] * 6)
    bad, _ = validate(st, VIBE, CFG)
    assert bad == ["play 11 is a 'picked' play, want 'dj'"]


def test_an_extra_search_is_rejected():
    st = _session(VIBE, [5] * 15)
    st["plays"].insert(5, _search(VIBE))
    bad, _ = validate(st, VIBE, CFG)
    assert bad[0].startswith("play 6 is a 'search' play, want 'dj'")


def test_too_few_plays_are_rejected():
    bad, _ = validate(_session(VIBE, [5] * 14), VIBE, CFG)
    assert bad == ["15 plays, want 16 (1 search + 15 DJ)"]


def test_the_wrong_start_song_is_rejected():
    st = _session(VIBE, [5] * 15)
    st["plays"][0]["spotify_id"] = "somethingelse"
    bad, _ = validate(st, VIBE, CFG)
    assert len(bad) == 1 and "want 'Anisuthide'" in bad[0]


def test_a_skipped_start_song_is_rejected():
    st = _session(VIBE, [5] * 15)
    st["plays"][0]["outcome"] = "skipped"
    assert validate(st, VIBE, CFG)[0] == ["start song at play 1 was skipped, want finished"]


def test_an_unrated_pick_and_the_wrong_account_are_rejected():
    st = _session(VIBE, [5] * 15)
    st["plays"][5]["fit"] = None
    assert validate(st, VIBE, CFG)[0] == ["play 6 has no fit 1-5"]
    st = _session(VIBE, [5] * 15)
    st["persona"] = "kannada-mass"
    assert any("account" in b for b in validate(st, VIBE, CFG)[0])


def test_invalid_sessions_are_excluded_from_the_score():
    runs = _round([[5] * 15, [5] * 15])
    st = runs["vibe-kn-melody"][1]
    st["plays"][3]["how"] = "picked"
    st["plays"][3]["fit"] = 1
    res = score_round(CFG, runs)
    assert [x["session"] for x in res["excluded"]] == [st["id"]]
    assert len(res["scenarios"]["vibe-kn-melody"]["runs"]) == 1
    assert res["scenarios"]["vibe-kn-melody"]["score"] == 100.0
    assert any("vibe-kn-melody" in p for p in res["provisional"])


# ---------------------------------------------------- the round and best
LOW = 100 * 25 / 85                                      # all finished, none good, no skips


def test_overall_and_spread():
    res = score_round(CFG, _round([[5] * 15, [3] * 15]))
    for v in res["scenarios"].values():
        assert v["spread"] == [pytest.approx(LOW), 100.0]
    assert res["overall"] == pytest.approx((100 + LOW) / 2)
    assert res["overall_band"] == pytest.approx(100 - LOW)
    assert res["provisional"] == []


def test_overall_is_the_mean_over_scenarios():
    runs = _round([[5] * 15, [5] * 15])
    runs["vibe-en-workout"] = [_session(SCS["vibe-en-workout"], [3] * 15, f"w{i}")
                               for i in range(2)]
    assert score_round(CFG, runs)["overall"] == pytest.approx((100 + 100 + LOW) / 3)


def _entry(label, fits_by_run):
    res = score_round(CFG, _round(fits_by_run))
    return score_dj.make_entry(CFG, res, label, "abc", None, {}, "2026-10-19")


def test_a_single_run_entry_is_provisional_and_cannot_be_best():
    board = {"best": None, "entries": []}
    board, lines = add_entry(board, _entry("one-run", [[5] * 15]))
    assert board["entries"][0]["provisional"] is True
    assert board["best"] is None
    assert "NEW BEST" not in lines[0] and "provisional" in lines[0]


def test_best_is_the_highest_two_run_entry_and_new_best_is_announced():
    board = {"best": None, "entries": []}
    board, lines = add_entry(board, _entry("a", [[5] * 15, [3] * 15]))
    assert lines[0].startswith("NEW BEST") and board["best"]["label"] == "a"
    board, lines = add_entry(board, _entry("b", [[5] * 15, [5] * 15]))
    assert lines[0].startswith("NEW BEST") and board["best"]["label"] == "b"
    c = _entry("c", [[5] * 15, [3] * 15])
    board, lines = add_entry(board, c)
    assert board["best"]["label"] == "b"
    assert lines[0].startswith(f"Δ vs best (b, 100.0): −{100 - c['overall']:.1f}")
    assert f"within the run-to-run spread ({c['overall_band']:.1f})" in lines[0]
    board, lines = add_entry(board, _entry("d", [[5] * 15]))        # a provisional 100
    assert board["best"]["label"] == "b" and "provisional" in lines[0]


def test_a_gap_outside_the_spread_is_called_out():
    board = {"best": None, "entries": []}
    board, _ = add_entry(board, _entry("a", [[5] * 15, [5] * 15]))  # band 0
    _, lines = add_entry(board, _entry("b", [[3] * 15, [3] * 15]))  # band 0
    assert "outside the run-to-run spread (0.0)" in lines[0]


def test_the_best_is_kept_per_scenario_version():
    board = {"best": None, "entries": []}
    board, _ = add_entry(board, {**_entry("v1", [[5] * 15, [5] * 15]), "scenarios_version": 1})
    board, lines = add_entry(board, _entry("v2", [[3] * 15, [3] * 15]))
    assert lines[0].startswith("NEW BEST") and board["best"]["label"] == "v2"


# --------------------------------------------------------- idempotence
def test_the_same_label_twice_is_rejected_unless_replace():
    board = {"best": None, "entries": []}
    board, _ = add_entry(board, _entry("a", [[5] * 15, [5] * 15]))
    with pytest.raises(ScoreError):
        add_entry(board, _entry("a", [[3] * 15, [3] * 15]))
    low = _entry("a", [[3] * 15, [3] * 15])
    board, _ = add_entry(board, low, replace=True)
    assert [e["label"] for e in board["entries"]] == ["a"]
    assert board["best"]["overall"] == low["overall"]   # recomputed, not the replaced 100


def _write_round(tmp_path, fits_by_run):
    ldir = tmp_path / "listener"
    ldir.mkdir()
    sessions = {}
    for sid, sts in _round(fits_by_run).items():
        for st in sts:
            (ldir / f"{st['id']}.json").write_text(json.dumps(st), encoding="utf-8")
            sessions.setdefault(sid, []).append(st["id"])
    return ldir, sessions


def test_cli_appends_once_and_rejects_the_same_label(tmp_path, capsys):
    ldir, sessions = _write_round(tmp_path, [[5] * 15, [4] * 15])
    args = [a for k, ids in sessions.items() for s in ids for a in ("--session", f"{k}={s}")]
    board = tmp_path / "scoreboard.json"
    base = ["--label", "v1", "--commit", "not-a-ref", "--listener-dir", str(ldir),
            "--scoreboard", str(board)] + args
    assert score_dj.main(base) == 0
    out = capsys.readouterr().out
    assert "NEW BEST" in out and "| **overall** | **100.0** |" in out
    assert score_dj.main(base) == 2
    assert len(json.loads(board.read_text(encoding="utf-8"))["entries"]) == 1
    assert score_dj.main(base + ["--replace"]) == 0
    assert len(json.loads(board.read_text(encoding="utf-8"))["entries"]) == 1


def test_manifest_gives_the_same_sessions(tmp_path):
    ldir, sessions = _write_round(tmp_path, [[5] * 15, [4] * 15])
    man = tmp_path / "round.json"
    man.write_text(json.dumps({"label": "m", "commit": "x", "sessions": sessions}),
                   encoding="utf-8")
    board = tmp_path / "scoreboard.json"
    assert score_dj.main(["--manifest", str(man), "--listener-dir", str(ldir),
                          "--scoreboard", str(board)]) == 0
    e = json.loads(board.read_text(encoding="utf-8"))["entries"][0]
    assert e["sessions"] == sessions and e["min_runs"] == 2 and not e["provisional"]


# ------------------------------------------------------------ scenarios
def test_scenarios_have_distinct_accounts_and_rendered_prompts():
    assert sorted(SCS) == ["vibe-en-workout", "vibe-hi-romantic", "vibe-kn-melody"]
    accounts = [sc["account"] for sc in CFG["scenarios"]]
    assert len(set(accounts)) == len(accounts)
    assert CFG["version"] == 2 and all(sc["picks"] == 15 for sc in CFG["scenarios"])
    for sc in CFG["scenarios"]:
        assert sc["prompt"] == score_dj.render_prompt(CFG, sc), \
            f"{sc['id']}: run score_dj.py --render-prompts"


def test_a_shared_account_is_refused():
    cfg = json.loads(json.dumps(CFG))
    cfg["scenarios"][1]["account"] = cfg["scenarios"][0]["account"]
    with pytest.raises(ScoreError):
        score_dj.check_scenarios(cfg)
