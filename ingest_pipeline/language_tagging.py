"""
The language tagging interface: find the rows waiting for a language, and
write one safely.

There is no queue file and no export/apply step. **The database is the
queue.** The pipeline runs preview -> download -> librosa -> classify and
stops at language; a Claude Code session queries for the waiting rows,
reads title/artist/album, and writes the tag back. The next pipeline run
sees `language_status='done'` and proceeds to fuse -> youtube.

Why metadata and not audio
--------------------------
Until 2026-10-10 this was a Whisper forward pass over the 30s preview.
Whisper's own stage docstring admitted it "reliably mispredicts on musical
audio -- Kannada songs frequently misclassify as Telugu / Sanskrit /
Khmer / Norwegian Nynorsk, and instrumentals drift randomly", which is why
its terminal state was 'whisper_done' and it deferred to a verification
stage that was never written. The title of a Kannada film song is far
stronger evidence than 30 seconds of singing, and reading it needs no GPU,
no preview and no cached audio.

This module is a thin helper over two SQL statements, not a mechanism. It
exists so the predicate has exactly one spelling and so the write cannot
be got half-right -- a tag is three columns plus a conditional cascade,
and forgetting the cascade leaves a stale vector that nothing flags.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone


log = logging.getLogger("vibescape.ingest.language")


# THE PREDICATE. One spelling, quoted by the stage, by the status report
# and by ingest_pipeline/README.md. A row waiting for a language is a row
# whose language_status reads exactly 'pending'.
#
# It is this simple only because LanguageStage normalises the other
# spellings of "not answered" into it first -- NULL (production's Turso
# tracks table was rebuilt without column defaults, so app-inserted rows
# land NULL there) and 'whisper_done' (the retired Whisper terminal
# state). Without that normalisation a tagging session would have to
# remember three variants and would silently miss two of them.
WAITING_PREDICATE = "language_status = 'pending'"

WAITING_SQL = (
    "SELECT id, spotify_id, title, artist, album, language AS prior_language, "
    "       created_at "
    "FROM tracks "
    f"WHERE {WAITING_PREDICATE} "
    "ORDER BY id ASC"
)

# Stamped into language_model_version so a tag's provenance is legible:
# 'whisper_small' means the retired model guessed it, this means a reading
# session asserted it.
MODEL_VERSION = "claude_session_metadata_v1"


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def fetch_waiting(conn, limit: int | None = None) -> list:
    """Rows waiting for a language, oldest id first."""
    sql = WAITING_SQL
    params: tuple = ()
    if limit:
        sql += " LIMIT ?"
        params = (int(limit),)
    return list(conn.execute(sql, params).fetchall())


def tag(conn, *, track_id: int | None = None, spotify_id: str | None = None,
        language: str | None = None, clear: bool = False,
        commit: bool = True) -> dict:
    """Write one language verdict. Safe to re-run over a row already done.

    Exactly one of `language` (a lowercase ISO 639-1 code) or `clear=True`.

      language='kn'  the lyrics are in Kannada.
      clear=True     the track has NO language: an instrumental, or a title
                     that genuinely cannot be called. Writes language=NULL
                     with language_status='done'. This is a VERDICT, not a
                     failure: it satisfies fuse's 'done'-only gate exactly
                     like a real language does, and the fused vector simply
                     uses the 'other' bucket. "Failed" would be a stage
                     error, which is a different thing and blocks the row.

    Identify the row by `spotify_id` where you can -- local and prod track
    ids have diverged (ingest_pipeline/README.md, "Prod"), so a verdict
    keyed on spotify_id applies in either database.

    THE CASCADE. `fuse_status='pending'` is written only when the language
    VALUE actually changes. 20% of the 788-d retrieval vector is a language
    one-hot, so a changed tag means the stored vector is in the wrong
    region of the similarity space and must be rebuilt -- milliseconds, no
    GPU, the MERT half is already on disk. Making it conditional is also
    what makes re-running harmless: tagging a row you already tagged writes
    the same three values and does not re-arm fuse.
    """
    if clear == (language is not None):
        raise ValueError("pass exactly one of language=<code> or clear=True")

    if spotify_id:
        row = conn.execute(
            "SELECT id, language FROM tracks WHERE spotify_id = ?", (spotify_id,)
        ).fetchone()
    elif track_id is not None:
        row = conn.execute(
            "SELECT id, language FROM tracks WHERE id = ?", (int(track_id),)
        ).fetchone()
    else:
        raise ValueError("pass track_id= or spotify_id=")
    if row is None:
        raise LookupError(f"no track for track_id={track_id!r} spotify_id={spotify_id!r}")

    tid = int(row["id"])
    old = row["language"]
    new = None if clear else str(language).strip().lower()
    changed = (old or None) != new

    fields = {
        "language": new,
        # 1.0 means "asserted by a reading session", not a softmax. Nothing
        # reads this as a probability since Whisper went away.
        "language_confidence": None if new is None else 1.0,
        "language_model_version": MODEL_VERSION,
        "language_predicted_at": _iso_now(),
        "language_status": "done",
    }
    if changed:
        fields["fuse_status"] = "pending"

    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE tracks SET {sets} WHERE id = ?", [*fields.values(), tid])
    if commit:
        conn.commit()
    return {"track_id": tid, "old": old, "new": new, "cascaded": changed}


def report(conn) -> dict:
    """Everything needed to answer "what is waiting, and for how long".

    Printed by scripts/language_tags.py --status; the same numbers are
    reachable with the three queries documented in
    ingest_pipeline/README.md.
    """
    by_status = {
        (r[0] or "NULL"): int(r[1]) for r in conn.execute(
            "SELECT language_status, COUNT(*) FROM tracks GROUP BY 1"
        ).fetchall()
    }
    waiting = int(conn.execute(
        f"SELECT COUNT(*) FROM tracks WHERE {WAITING_PREDICATE}"
    ).fetchone()[0])

    # Age is measured from created_at, not from when some stage touched the
    # row: a metadata question has been answerable since the instant the
    # app inserted it, so that is the honest "how long has this been
    # waiting".
    ages = []
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for (c,) in conn.execute(
        f"SELECT created_at FROM tracks WHERE {WAITING_PREDICATE} "
        "AND created_at IS NOT NULL"
    ).fetchall():
        try:
            ages.append((now - datetime.fromisoformat(str(c).replace("Z", ""))).days)
        except ValueError:
            pass
    ages.sort()

    # The number that means "the DJ pool is smaller than it should be":
    # MERT is encoded but nothing is fused, and language is the blocker.
    # Already-fused rows are excluded even if their language_status is a
    # legacy value -- they have a vector and are in the pool.
    blocked = int(conn.execute(
        "SELECT COUNT(*) FROM tracks "
        "WHERE ml_status = 'done' "
        "AND COALESCE(language_status, '') != 'done' "
        "AND COALESCE(fuse_status, '') != 'done'"
    ).fetchone()[0])

    return {
        "by_status": by_status,
        "waiting": waiting,
        "oldest_wait_days": ages[-1] if ages else None,
        "median_wait_days": ages[len(ages) // 2] if ages else None,
        "blocked_on_language": blocked,
    }
