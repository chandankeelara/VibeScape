"""The listener CLI shows a listener agent only what a user sees: a handle,
title, artist and album. Never language, vibe, mood, tempo, scores or ids.

The first two tests are pure and always run. The session test drives the
real CLI against a real server and is skipped unless DJ_EVAL_DB points at a
setup_copy.py copy (it signs a persona in and resets its history there).
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from dj_eval import listener  # noqa: E402
from dj_eval.client import DjClient, Session, Track  # noqa: E402

DB = os.environ.get("DJ_EVAL_DB")
_HIDDEN_WORDS = re.compile(r"\b(language|vibe|mood|tempo|score|energy|valence|spotify)\b", re.I)


def _track(**kw):
    base = dict(spotify_id="4uLU6hMCjMI75M1A2tKUQC", id=4321, title="Song", artist="Singer",
                album='Film (Original Motion Picture Soundtrack)', language="kn", vibe=0.91,
                duration_ms=200_000)
    base.update(kw)
    return Track(**base)


def test_show_is_handle_title_artist_album_only():
    line = listener._show("t-ab12", _track())
    assert line == 't-ab12  Song — Singer  [Film (Original Motion Picture Soundtrack)]'
    assert listener._show("t-ab12", _track(album="")) == "t-ab12  Song — Singer"


def test_state_file_carries_no_language_or_vibe():
    t = _track()
    s = Session(DjClient("http://127.0.0.1:1", "x"), {t.spotify_id: t})
    st = s.to_state()["catalog"][t.spotify_id]
    assert st["language"] is None and st["vibe"] is None


@pytest.mark.skipif(not DB, reason="set DJ_EVAL_DB to a setup_copy.py copy")
def test_scripted_session_prints_nothing_hidden(tmp_path, monkeypatch, capsys):
    from dj_eval.server import LocalServer
    monkeypatch.setattr(listener, "STATE_DIR", tmp_path)
    db = Path(DB)
    with LocalServer(db) as srv:
        def run(*argv):
            rc = listener.main(list(argv))
            out = capsys.readouterr().out
            assert rc == 0, out
            return out.splitlines()

        outputs = []
        sid = run("start", "--persona", "kannada-mass", "--db", str(db),
                  "--base-url", srv.base_url)[0].split()[-1]
        outputs.append(f"session {sid}")
        results = run("search", "--session", sid, "love")
        outputs += results
        h = results[0].split()[0]
        outputs += run("play", "--session", sid, h)
        outputs += run("finish", "--session", sid, h)
        outputs += run("feedback", "--session", sid, h, "--fit", "4", "--reason", "good")
        for i in range(3):
            lines = run("next", "--session", sid)
            outputs += lines
            h = lines[0].split()[2]
            if i % 2:
                outputs += run("finish", "--session", sid, h)
            else:
                outputs += run("skip", "--session", sid, h, "--played", "0.2")
            outputs += run("feedback", "--session", sid, h, "--fit", "3", "--reason", "ok")
        outputs += run("review", "--session", sid, "fine")

    st = listener._load(sid)
    conn = sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True)
    rows = {}
    for h, spid in st["handles"].items():
        r = conn.execute("SELECT id, title, artist, album FROM tracks WHERE spotify_id = ?",
                         (spid,)).fetchone()
        rows[h] = (spid, r)
    conn.close()

    text = "\n".join(outputs)
    for h, (spid, (tid, title, artist, album)) in rows.items():
        assert spid not in text
        want = f"{h}  {title or '?'} — {artist or '?'}" + (f"  [{album}]" if album else "")
        for line in outputs:
            if re.search(rf"(^|\s){re.escape(h)}\s\s", line):
                # A track line is exactly the user-visible fields, nothing more.
                assert line.removeprefix("now playing: ").strip() == want, line
    for line in outputs:
        if not re.search(r"t-[0-9a-f]{4}  ", line):
            assert not _HIDDEN_WORDS.search(line), line
