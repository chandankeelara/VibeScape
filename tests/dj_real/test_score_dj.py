"""score_dj.py: the catch-and-hold maths, session validation, the
scoreboard's best and provisional flag, and label idempotence. Pure: no
server, no data; sessions are synthetic listener.py state files."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from dj_eval import score_dj  # noqa: E402
from dj_eval.score_dj import ScoreError, add_entry, catch_hold, score_round, validate  # noqa: E402

STEPS = 15
CFG = score_dj.load_scenarios()


def _session(sid, account, fits, start_fit=5, title="Anisuthide", hows=None):
    hows = hows or ["dj"] * len(fits)
    plays = [{"step": 1, "how": "search", "title": title, "artist": "Sonu Nigam",
              "outcome": "finished", "fit": start_fit}]
    for i, (f, h) in enumerate(zip(fits, hows), start=2):
        plays.append({"step": i, "how": h, "title": f"song {i}", "artist": "x",
                      "outcome": "finished" if f >= 3 else "skipped", "fit": f})
    return {"id": sid, "persona": account, "plays": plays}


def _round(fits_by_run):
    """Every scenario gets the same fits; one session per run."""
    runs = {}
    for sc in CFG["scenarios"]:
        runs[sc["id"]] = [_session(f"{sc['account']}-{i}", sc["account"], f, title=sc["title"],
                                   start_fit=sc["start_fit"])
                          for i, f in enumerate(fits_by_run)]
    return runs


# ------------------------------------------------------------ the maths
def test_caught_at_step_1_and_held_throughout_is_100():
    r = catch_hold([5] * 15, STEPS)
    assert r == {"catch_step": 1, "catch": 50.0, "hold": 50.0, "lost": 0, "total": 100.0}


def test_catch_needs_three_good_picks_in_a_row():
    # 4,4 then a 3 breaks the run; the catch is at 4 (picks 4, 5, 6).
    fits = [4, 4, 3, 4, 5, 4] + [3] * 9
    r = catch_hold(fits, STEPS)
    assert r["catch_step"] == 4
    assert r["catch"] == pytest.approx(50 * 12 / 15)


def test_caught_at_step_13_the_last_possible():
    r = catch_hold([1] * 12 + [4, 5, 4], STEPS)
    assert r["catch_step"] == 13
    assert r["catch"] == pytest.approx(10.0)
    assert r["hold"] == pytest.approx(50.0)      # 3 of 3 from the catch on
    assert r["total"] == pytest.approx(60.0)


def test_good_picks_only_at_steps_14_and_15_are_never_caught():
    r = catch_hold([1] * 13 + [5, 5], STEPS)
    assert r == {"catch_step": None, "catch": 0.0, "hold": 0.0, "lost": 0, "total": 0.0}


def test_never_caught_scores_zero_even_with_many_good_picks():
    r = catch_hold([4, 4, 2] * 5, STEPS)
    assert r["catch_step"] is None and r["total"] == 0.0


def test_hold_denominator_is_picks_from_the_catch_on():
    # Caught at 3; 13 picks from there, 3 + 2 good of them; picks before
    # the catch (two 5s would be good, but they are not in the run) don't count.
    fits = [5, 1, 4, 4, 4, 2, 1, 3, 3, 3, 3, 3, 5, 5, 1]
    r = catch_hold(fits, STEPS)
    assert r["catch_step"] == 3
    assert r["hold"] == pytest.approx(50 * 5 / 13)
    assert r["lost"] == 3                        # the 2, the 1 and the last 1; not step 2


def test_lost_counts_only_after_the_catch():
    r = catch_hold([1, 1, 5, 5, 5] + [4] * 10, STEPS)
    assert r["catch_step"] == 3 and r["lost"] == 0


def test_catch_rule_finished_catches_on_three_finishes_and_holds_on_fit():
    fits = [3, 3, 3, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5]
    fin = [True] * 15                                    # a fit 3 the listener let play
    assert catch_hold(fits, STEPS)["catch_step"] == 4
    r = catch_hold(fits, STEPS, fin, rule="finished")
    assert r["catch_step"] == 1 and r["catch"] == 50.0
    assert r["hold"] == pytest.approx(50 * 12 / 15)      # hold is still fit >= 4
    # Three good fits but a skip among them: no catch there under 'finished'.
    fin = [True, False, True] + [False] * 12
    assert catch_hold([5] * 15, STEPS, fin, rule="finished")["catch_step"] is None
    # And score_round passes the rule and the outcomes through.
    runs = _round([fits, fits])                          # _session finishes fit >= 3
    assert {v["catch_step"][0] for v in score_round(CFG, runs)["scenarios"].values()} == {4}
    assert {v["catch_step"][0]
            for v in score_round(CFG, runs, "finished")["scenarios"].values()} == {1}


def test_best_is_kept_per_catch_rule():
    board = {"best": None, "entries": []}
    e = _entry("a", [[5] * 15, [5] * 15])
    board, _ = add_entry(board, e)
    f = {**_entry("b", [[1] * 12 + [4, 4, 4]] * 2), "catch_rule": "finished"}
    board, lines = add_entry(board, f)
    assert lines[0].startswith("NEW BEST") and board["best"]["label"] == "b"


def test_wrong_number_of_fits_is_an_error():
    with pytest.raises(ValueError):
        catch_hold([5] * 14, STEPS)


# ----------------------------------------------------------- validation
def _sc():
    return CFG["scenarios"][0]


def test_a_clean_session_is_valid():
    sc = _sc()
    bad, warn = validate(_session("s", sc["account"], [4] * 15), sc, STEPS)
    assert bad == [] and warn == []


def test_an_extra_search_play_is_rejected():
    sc = _sc()
    st = _session("s", sc["account"], [4] * 15)
    st["plays"].insert(1, {**st["plays"][0], "step": 2})
    bad, _ = validate(st, sc, STEPS)
    assert any("search plays" in b for b in bad)


def test_a_run_that_does_not_start_with_the_search_is_rejected():
    sc = _sc()
    st = _session("s", sc["account"], [4] * 16)
    st["plays"] = st["plays"][1:]                        # 16 dj plays, no start song
    bad, _ = validate(st, sc, STEPS)
    assert bad == ["the first play is not the search play"]


def test_an_up_next_play_at_step_10_is_rejected():
    sc = _sc()
    hows = ["dj"] * 9 + ["picked"] + ["dj"] * 6          # still 15 dj plays in all
    st = _session("s", sc["account"], [4] * 16, hows=hows)
    bad, _ = validate(st, sc, STEPS)
    assert bad == ["1 up-next plays in DJ steps 1-15, want 0"]


def test_an_up_next_play_at_step_16_is_ignored():
    sc = _sc()
    st = _session("s", sc["account"], [4] * 15 + [1], hows=["dj"] * 15 + ["picked"])
    bad, warn = validate(st, sc, STEPS)
    assert bad == [] and warn == ["1 plays after DJ step 15 ignored (picked x1)"]


def test_a_run_of_18_is_kept_and_scored_on_its_first_15():
    sc = _sc()
    st = _session("s", sc["account"], [5] * 15 + [1, 1, 1])
    st["plays"][-1]["fit"] = None                        # an unrated overshoot does not matter
    bad, warn = validate(st, sc, STEPS)
    assert bad == [] and warn == ["3 plays after DJ step 15 ignored (dj x3)"]
    runs = _round([[5] * 15, [5] * 15])
    runs[sc["id"]][0] = {**st, "id": "s18"}
    res = score_round(CFG, runs)
    assert res["excluded"] == []
    assert res["scenarios"][sc["id"]]["total"] == 100.0  # the three 1s are not in it


def test_too_few_dj_plays_and_an_unrated_pick_are_rejected():
    sc = _sc()
    bad, _ = validate(_session("s", sc["account"], [4] * 14), sc, STEPS)
    assert bad == ["14 DJ plays, want 15"]
    st = _session("s", sc["account"], [4] * 15)
    st["plays"][5]["fit"] = None
    bad, _ = validate(st, sc, STEPS)
    assert any("no fit" in b for b in bad)


def test_the_wrong_account_is_rejected():
    sc = _sc()
    bad, _ = validate(_session("s", "kannada-mass", [4] * 15), sc, STEPS)
    assert any("account" in b for b in bad)


def test_invalid_sessions_are_excluded_from_the_score():
    runs = _round([[5] * 15, [5] * 15])
    sid = CFG["scenarios"][0]["id"]
    st = runs[sid][1]
    st["plays"][3]["how"] = "picked"
    st["plays"][3]["fit"] = 1
    res = score_round(CFG, runs)
    assert [x["session"] for x in res["excluded"]] == [st["id"]]
    assert len(res["scenarios"][sid]["runs"]) == 1
    assert res["scenarios"][sid]["total"] == 100.0
    assert any(sid in p for p in res["provisional"])


# ---------------------------------------------------- the round and best
def test_on_off_overall_and_spread():
    runs = _round([[5] * 15, [1] * 12 + [4, 4, 4]])      # 100 and 60 in every scenario
    res = score_round(CFG, runs)
    for v in res["scenarios"].values():
        assert v["total"] == pytest.approx(80.0) and v["spread"] == [60.0, 100.0]
    assert res["on"] == res["off"] == res["overall"] == pytest.approx(80.0)
    assert res["overall_band"] == pytest.approx(40.0)
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
    board, lines = add_entry(board, _entry("a", [[5] * 15, [1] * 12 + [4, 4, 4]]))   # 80
    assert lines[0].startswith("NEW BEST") and board["best"]["label"] == "a"
    board, lines = add_entry(board, _entry("b", [[5] * 15, [5] * 15]))               # 100
    assert lines[0].startswith("NEW BEST") and board["best"]["label"] == "b"
    board, lines = add_entry(board, _entry("c", [[5] * 15, [1] * 12 + [4, 4, 4]]))   # 80
    assert board["best"]["label"] == "b"
    assert lines[0].startswith("Δ vs best (b, 100.0): −20.0")
    assert "within the run-to-run spread (40.0)" in lines[0]
    # A provisional 100 beats nothing.
    board, lines = add_entry(board, _entry("d", [[5] * 15]))
    assert board["best"]["label"] == "b" and "provisional" in lines[0]


def test_a_gap_outside_the_spread_is_called_out():
    board = {"best": None, "entries": []}
    board, _ = add_entry(board, _entry("a", [[5] * 15, [5] * 15]))                   # 100, band 0
    _, lines = add_entry(board, _entry("b", [[1] * 12 + [4, 4, 4]] * 2))            # 60, band 0
    assert "outside the run-to-run spread (0.0)" in lines[0]


# --------------------------------------------------------- idempotence
def test_the_same_label_twice_is_rejected_unless_replace():
    board = {"best": None, "entries": []}
    board, _ = add_entry(board, _entry("a", [[5] * 15, [5] * 15]))
    with pytest.raises(ScoreError):
        add_entry(board, _entry("a", [[1] * 15, [1] * 15]))
    board, _ = add_entry(board, _entry("a", [[1] * 12 + [4, 4, 4]] * 2), replace=True)
    assert [e["label"] for e in board["entries"]] == ["a"]
    assert board["entries"][0]["overall"] == 60.0
    assert board["best"]["overall"] == 60.0     # recomputed, not the replaced 100


def test_cli_appends_once_and_rejects_the_same_label(tmp_path, capsys):
    ldir = tmp_path / "listener"
    ldir.mkdir()
    args = []
    for sid, sts in _round([[5] * 15, [4] * 15]).items():
        for st in sts:
            (ldir / f"{st['id']}.json").write_text(json.dumps(st), encoding="utf-8")
            args += ["--session", f"{sid}={st['id']}"]
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


def test_manifest_gives_the_same_sessions(tmp_path, capsys):
    ldir = tmp_path / "listener"
    ldir.mkdir()
    sessions = {}
    for sid, sts in _round([[5] * 15, [4] * 15]).items():
        for st in sts:
            (ldir / f"{st['id']}.json").write_text(json.dumps(st), encoding="utf-8")
            sessions.setdefault(sid, []).append(st["id"])
    man = tmp_path / "round.json"
    man.write_text(json.dumps({"label": "m", "commit": "x", "sessions": sessions}), encoding="utf-8")
    board = tmp_path / "scoreboard.json"
    assert score_dj.main(["--manifest", str(man), "--listener-dir", str(ldir),
                          "--scoreboard", str(board)]) == 0
    e = json.loads(board.read_text(encoding="utf-8"))["entries"][0]
    assert e["sessions"] == sessions and e["min_runs"] == 2 and not e["provisional"]


# ------------------------------------------------------------ scenarios
def test_scenarios_have_distinct_accounts_and_rendered_prompts():
    accounts = [sc["account"] for sc in CFG["scenarios"]]
    assert len(set(accounts)) == len(accounts) == 6
    assert CFG["steps"] == 15
    for sc in CFG["scenarios"]:
        assert sc["prompt"] == score_dj.render_prompt(CFG, sc), \
            f"{sc['id']}: run score_dj.py --render-prompts"


def test_a_shared_account_is_refused():
    cfg = json.loads(json.dumps(CFG))
    cfg["scenarios"][1]["account"] = cfg["scenarios"][0]["account"]
    with pytest.raises(ScoreError):
        score_dj.check_scenarios(cfg)
