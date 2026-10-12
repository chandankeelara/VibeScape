"""
Language stage -- the stopping point. It classifies nothing.

Replaced the Whisper stage on 2026-10-10. Whisper's own docstring admitted
it "reliably mispredicts on musical audio -- Kannada songs frequently
misclassify as Telugu / Sanskrit / Khmer / Norwegian Nynorsk, and
instrumentals drift randomly", which is why its terminal state was
'whisper_done' and it deferred to a verification stage that was never
written. Language is now read off metadata -- title, artist, album -- by a
Claude Code session querying the database directly.

So the pipeline runs preview -> download -> librosa -> classify and STOPS
here. This stage's entire job is to leave every live row in one
unambiguous waiting state so the session's query is a single obvious
predicate:

    language_status = 'pending'

That takes real work, because "not answered yet" has three spellings in
this database and a session cannot be expected to remember them:

  NULL           production's Turso `tracks` was rebuilt from
                 PRAGMA table_info's `type` field alone, dropping every
                 DEFAULT 'pending' (see base.py). App-inserted rows land
                 NULL there, 'pending' locally.
  'whisper_done' the retired Whisper terminal state -- a guess nothing had
                 verified. No producer any more.
  'pending'      the one we want.

The stage folds the first two into the third. A row normalised out of
'whisper_done' keeps `tracks.language` as Whisper's guess, so the session
sees it as a weak prior rather than losing it.

It is deliberately NOT normalising every NULL in the table. Rows parked at
ingestion_status='no_preview' keep their NULL: they can never be analysed,
so asking about them is work that buys nothing, and promoting them to
'pending' would make them permanently cohort-eligible and jam the head of
every pass (see docs/backend-todo.md 3.1).

Three consequences of classifying from metadata rather than audio:

  - No preview, no cached file, no GPU. The old stage gated on
    download_status='done'; this one has no upstream dependency at all and
    is armed at ingest entry, in parallel with preview. The row is
    answerable the instant the app inserts it, so a session can tag it
    while the GPU is still working on the audio stages.
  - This stage never writes 'done'. Only a tagging session does, through
    language_tagging.tag(), which also fires the fuse cascade.
  - It arms nothing. fuse_status='pending' is written by the tag, which is
    the only moment at which fuse could act on it.
"""
from __future__ import annotations

import logging

from . import language_tagging as lt
from .base import (RowResult, Stage, STATUS_PENDING, STATUS_WHISPER_DONE,
                   id_filter)


log = logging.getLogger("vibescape.ingest.language")


class LanguageStage(Stage):
    name = "language"
    status_column = "language_status"
    # Arms nothing -- see the module docstring.
    arms = ()
    max_workers = 1

    def fetch_pending(self, conn, limit: int, only_ids=None) -> list:
        """Live rows whose language_status is not yet the waiting spelling.

        No upstream gate: metadata is present from the INSERT. The only
        preconditions are that the row has a title to read and that it is
        still worth asking about.
        """
        idf, idp = id_filter(only_ids)
        return list(conn.execute(
            "SELECT id, spotify_id, title, artist, album, language "
            "FROM tracks "
            "WHERE (language_status IS NULL OR language_status = ?) "
            # Don't promote rows that can never be analysed. A 'no_preview'
            # row's NULL stays NULL. The whisper_done arm overrides that:
            # a row in a state nothing recognises is worse than a pointless
            # question, and it is a handful of rows at most.
            "AND (ingestion_status = 'pending' OR ingestion_status IS NULL "
            "     OR language_status = ?) "
            "AND title IS NOT NULL AND title != '' "
            f"{idf}"
            "ORDER BY id ASC LIMIT ?",
            (STATUS_WHISPER_DONE, STATUS_WHISPER_DONE, *idp, limit),
        ).fetchall())

    def process_row(self, row) -> RowResult:
        """Normalise to the waiting state. No network, no model, no audio.

        Returning STATUS_PENDING is the point: the base class stamps the
        result straight onto language_status, and because 'pending' is not
        in `arms_on` and is not 'failed', nothing downstream is armed and
        no failure is recorded. The row simply sits, correctly labelled,
        until somebody answers it.
        """
        return RowResult(track_id=int(row["id"]), status=STATUS_PENDING, fields={})

    def run_batch(self, conn, limit: int, log_, only_ids=None) -> dict[str, int]:
        counts = super().run_batch(conn, limit, log_, only_ids)
        # The waiting count is the thing worth seeing in a pass log: it is
        # the size of the backlog that only a tagging session can clear,
        # and since fuse gates strictly on 'done' it is also the number of
        # tracks that cannot reach the DJ pool until it is cleared.
        try:
            waiting = conn.execute(
                f"SELECT COUNT(*) FROM tracks WHERE {lt.WAITING_PREDICATE}"
            ).fetchone()[0]
            log_.info("[%s] %d row(s) waiting for a language tag "
                      "(see ingest_pipeline/README.md 'Tagging languages')",
                      self.name, int(waiting))
        except Exception as e:
            log_.warning("[%s] could not count waiting rows: %s", self.name, e)
        return counts
