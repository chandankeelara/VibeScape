"""score_dj.py: the vibe and steer maths, session validation, the
scoreboard's best and provisional flag, and label idempotence. Pure: no
server, no data; sessions are synthetic listener.py state files."""
from __future__ import annotations

import json
import sys
from argparse import Namespace
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from dj_eval import listener, score_dj  # noqa: E402
from dj_eval.score_dj import (ScoreError, add_entry, score_round, steer_metrics,  # noqa: E402
                              validate, vibe_metrics)

CFG = score_dj.load_scenarios()
SCS = {sc["id"]: sc for sc in CFG["scenarios"]}
VIBE = SCS["vibe-kn-melody"]
STEER = SCS["steer-kn-mass-to-kn-melody"]


def _pick(fit, outcome=None, how="dj", sounds_like=None, step=0):
    return {"step": step, "how": how, "spotify_id": "x", "title": "song", "artist": "a",
            "outcome": outcome or ("finished" if fit >= 3 else "skipped"), "fit": fit,
            "sounds_like": sounds_like}


def _search(song_key, step=0):
    s = CFG["songs"][song_key]
    return {"step": step, "how": "search", "spotify_id": s["spotify_ids"][0], "title": s["title"],
            "artist": s["artist"], "outcome": "finished", "fit": 4, "sounds_like": None}


def _session(sc, picks_by_phase, sid="s", account=None):
    """A valid session for sc: per phase, the search then the given picks
    (fits, or ready-made play dicts). Steer B picks are tagged 'B'."""
    plays = []
    for n, (ph, picks) in enumerate(zip(sc["phases"], picks_by_phase)):
        plays.append(_search(ph["song"]))
        tag = "B" if sc["kind"] == "steer" and n == 1 else None
        plays += [p if isinstance(p, dict) else _pick(p, sounds_like=tag) for p in picks]
    for i, p in enumerate(plays, 1):
        p["step"] = i
    return {"id": sid, "persona": account or sc["account"], "plays": plays}


def _good_session(sc, sid, fits=None):
    if sc["kind"] == "vibe":
        return _session(sc, [fits or [5] * 15], sid)
    return _session(sc, [[5] * 5, fits or [5] * 10], sid)


def _round(fits_by_run):
    """Every scenario: one session per run; vibe runs get the fits, steer
    runs get the first 10 as their B picks (their A picks are all 5)."""
    runs = {}
    for sc in CFG["scenarios"]:
        runs[sc["id"]] = [_good_session(sc, f"{sc['id']}-{i}",
                                        f if sc["kind"] == "vibe" else f[:10])
                          for i, f in enumerate(fits_by_run)]
    return runs


# ------------------------------------------------------------ vibe maths
def test_a_perfect_vibe_run_scores_100_without_a_recovery():
    m = vibe_metrics([_pick(5) for _ in range(15)])
    assert m == {"hold": 1.0, "skip_rate": 0.0, "skip_recovery": None, "early": 1.0,
                 "score": 100.0}


def test_vibe_hold_skip_rate_and_early():
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


def test_vibe_score_weights_and_a_missing_recovery_is_neutral():
    picks = [_pick(4, "skipped")] + [_pick(4)] * 14      # recovery 1, skip 1/15
    m = vibe_metrics(picks)
    assert m["score"] == pytest.approx(50 * 1 + 25 * (14 / 15) + 15 * 1 + 10 * 1)
    # No skip at all: recovery is left out, not scored as 0 or as 1.
    picks = [_pick(3)] * 15                              # all finished, none good
    assert vibe_metrics(picks)["score"] == pytest.approx(100 * 25 / 85)


# ----------------------------------------------------------- steer maths
def _b(outs, fits=None, tags=None):
    fits = fits or [5] * len(outs)
    tags = tags or ["B"] * len(outs)
    return [_pick(f, "finished" if o == "f" else "skipped", sounds_like=t)
            for o, f, t in zip(outs, fits, tags)]


def test_switch_at_step_1_and_full_hold_is_100():
    m = steer_metrics([_pick(5)] * 5, _b("f" * 10))
    assert m["switch_step"] == 1 and m["switch"] == 1.0 and m["hold_b"] == 1.0
    assert m["score"] == 100.0


def test_switch_needs_three_finishes_in_a_row():
    m = steer_metrics([_pick(5)] * 5, _b("ffsfffssss"))
    assert m["switch_step"] == 4
    assert m["switch"] == pytest.approx(0.7)


def test_switch_at_step_8_the_last_possible_and_never():
    m = steer_metrics([_pick(5)] * 5, _b("sssssssfff"))
    assert m["switch_step"] == 8 and m["switch"] == pytest.approx(0.3)
    m = steer_metrics([_pick(5)] * 5, _b("ssssssssff"))
    assert m["switch_step"] is None and m["switch"] == 0.0 and m["hold_b"] == 0.0


def test_hold_b_counts_fit_from_the_switch_on_and_not_before():
    # switch at 3; B picks 3..10 are 8, of which fit >= 4: the switch run's
    # 5, 4, 4 and step 9; steps 1-2 are 5s but before the switch.
    fits = [5, 5, 5, 4, 4, 3, 3, 3, 4, 2]
    m = steer_metrics([_pick(5)] * 5, _b("ssffffffff", fits))
    assert m["switch_step"] == 3
    assert m["hold_b"] == pytest.approx(4 / 8)


def test_hold_a_and_leak():
    a = [_pick(f) for f in (5, 4, 3, 2, 4)]
    tags = ["A", "A", "B", "other", "B", "B", "A", "B", "B", "B"]
    m = steer_metrics(a, _b("f" * 10, tags=tags))
    assert m["hold_a"] == pytest.approx(3 / 5)
    assert m["leak"] == pytest.approx(3 / 10)
    assert m["score"] == pytest.approx(15 * 0.6 + 35 * 1 + 40 * 1 + 10 * 0.7)


def test_leak_is_the_tag_not_a_low_fit():
    # fit 1 everywhere but tagged 'other': wrong, but not A.
    m = steer_metrics([_pick(5)] * 5, _b("f" * 10, [1] * 10, ["other"] * 10))
    assert m["leak"] == 0.0


def test_weights_sum_to_100():
    assert sum(score_dj.VIBE_WEIGHTS.values()) == 100
    assert sum(score_dj.STEER_WEIGHTS.values()) == 100


# ----------------------------------------------------------- validation
def test_clean_sessions_are_valid():
    for sc in (VIBE, STEER):
        assert validate(_good_session(sc, "s"), sc, CFG) == ([], [])


def test_a_vibe_run_of_18_is_kept_and_scored_on_its_first_15():
    st = _session(VIBE, [[5] * 15 + [1, 1, 1]])
    st["plays"][-1]["fit"] = None                        # an unrated overshoot does not matter
    bad, warn = validate(st, VIBE, CFG)
    assert bad == [] and warn == ["3 plays after play 16 ignored (dj x3)"]
    assert score_dj.score_run(st, VIBE)["hold"] == 1.0


def test_an_up_next_play_after_the_last_step_is_ignored():
    st = _session(STEER, [[5] * 5, [5] * 10 + [_pick(1, how="picked")]])
    bad, warn = validate(st, STEER, CFG)
    assert bad == [] and warn == ["1 plays after play 17 ignored (picked x1)"]


def test_an_up_next_play_inside_the_window_is_rejected():
    st = _session(VIBE, [[5] * 9 + [_pick(5, how="picked")] + [5] * 6])
    bad, _ = validate(st, VIBE, CFG)
    assert bad == ["play 11 is a 'picked' play, want 'dj'"]


def test_a_steer_search_too_early_is_rejected():
    # 4 A picks then the B search: the second search lands on play 6.
    st = _session(STEER, [[5] * 4, [5] * 11])
    bad, _ = validate(st, STEER, CFG)
    assert bad[0].startswith("play 6 is a 'search' play, want 'dj'")


def test_an_extra_search_in_a_vibe_run_is_rejected():
    st = _session(VIBE, [[5] * 15])
    st["plays"].insert(5, _search("anisuthide"))
    bad, _ = validate(st, VIBE, CFG)
    assert bad[0].startswith("play 6 is a 'search' play, want 'dj'")


def test_too_few_plays_are_rejected():
    bad, _ = validate(_session(VIBE, [[5] * 14]), VIBE, CFG)
    assert bad == ["15 plays, want 16 (1 search + 15 DJ)"]
    bad, _ = validate(_session(STEER, [[5] * 5, [5] * 9]), STEER, CFG)
    assert bad == ["16 plays, want 17 (1 search + 5 DJ + 1 search + 10 DJ)"]


def test_the_wrong_start_song_is_rejected():
    st = _good_session(VIBE, "s")
    st["plays"][0]["spotify_id"] = "somethingelse"
    bad, _ = validate(st, VIBE, CFG)
    assert len(bad) == 1 and "want 'Anisuthide'" in bad[0]
    st = _good_session(STEER, "s")                       # the other Salaam Rocky Bhai is fine
    st["plays"][0]["spotify_id"] = CFG["songs"]["salaam-rocky-bhai"]["spotify_ids"][1]
    assert validate(st, STEER, CFG)[0] == []


def test_a_skipped_start_song_is_rejected():
    st = _good_session(STEER, "s")
    st["plays"][6]["outcome"] = "skipped"                # song B
    bad, _ = validate(st, STEER, CFG)
    assert bad == ["start song at play 7 was skipped, want finished"]


def test_an_unrated_pick_and_the_wrong_account_are_rejected():
    st = _good_session(VIBE, "s")
    st["plays"][5]["fit"] = None
    assert validate(st, VIBE, CFG)[0] == ["play 6 has no fit 1-5"]
    st = _good_session(VIBE, "s")
    st["persona"] = "kannada-mass"
    assert any("account" in b for b in validate(st, VIBE, CFG)[0])


def test_an_untagged_b_pick_is_rejected_an_untagged_a_pick_is_not():
    st = _good_session(STEER, "s")
    st["plays"][9]["sounds_like"] = None                 # B pick 3
    assert validate(st, STEER, CFG)[0] == ["B pick at play 10 has no --vibe A|B|other"]
    st = _good_session(STEER, "s")
    st["plays"][2]["sounds_like"] = None                 # A pick 2
    assert validate(st, STEER, CFG)[0] == []


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
def test_vibe_steer_overall_and_spread():
    runs = _round([[5] * 15, [3] * 15])
    res = score_round(CFG, runs)
    low_vibe = 100 * 25 / 85                             # all finished, none good, no skips
    low_steer = 15 + 35 + 0 + 10                         # A good, switch 1, hold B 0, no leak
    for v in res["scenarios"].values():
        assert v["spread"][1] == 100.0
    assert res["vibe"] == pytest.approx((100 + low_vibe) / 2)
    assert res["steer"] == pytest.approx((100 + low_steer) / 2)
    assert res["overall"] == pytest.approx((3 * (100 + low_vibe) / 2 + 4 * (100 + low_steer) / 2)
                                           / 7)
    assert res["overall_band"] == pytest.approx(100 - (3 * low_vibe + 4 * low_steer) / 7)
    assert res["provisional"] == []


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
    accounts = [sc["account"] for sc in CFG["scenarios"]]
    assert len(set(accounts)) == len(accounts) == 7
    assert CFG["version"] == 2
    assert [sc["kind"] for sc in CFG["scenarios"]].count("steer") == 4
    for sc in CFG["scenarios"]:
        assert sc["prompt"] == score_dj.render_prompt(CFG, sc), \
            f"{sc['id']}: run score_dj.py --render-prompts"


def test_the_steer_prompt_names_both_vibes_and_both_searches():
    p = STEER["prompt"]
    assert CFG["vibes"]["kn-mass"] in p and CFG["vibes"]["kn-melody"] in p
    assert 'search --session <id> "Salaam Rocky Bhai"' in p
    assert 'search --session <id> "Anisuthide"' in p
    assert "exactly 5 times" in p and "exactly 10 times" in p and "--vibe" in p


def test_a_shared_account_is_refused():
    cfg = json.loads(json.dumps(CFG))
    cfg["scenarios"][1]["account"] = cfg["scenarios"][0]["account"]
    with pytest.raises(ScoreError):
        score_dj.check_scenarios(cfg)


# -------------------------------------------------- listener.py --vibe
def test_listener_feedback_stores_the_vibe_tag_and_does_not_print_it():
    st = {"plays": [{"handle": "t-ab12", "fit": None, "reason": None, "sounds_like": None}],
          "history": []}
    a = Namespace(handle="t-ab12", fit=2, reason="too loud", vibe="A")
    lines = listener.cmd_feedback(a, st, None)
    assert st["plays"][0]["sounds_like"] == "A"
    assert lines == ["noted: t-ab12 fit 2"]
    a = listener.parser().parse_args(["feedback", "--session", "s", "t-ab12", "--fit", "4",
                                      "--vibe", "other", "--reason", "x"])
    assert a.vibe == "other"


def test_a_steer_run_splits_its_picks_at_the_second_search():
    a = [5, 5, 2, 2, 2]                                  # hold A 2/5
    st = _session(STEER, [a, [4] * 10])
    m = score_dj.score_run(st, STEER)
    assert m["hold_a"] == pytest.approx(2 / 5) and m["hold_b"] == 1.0
