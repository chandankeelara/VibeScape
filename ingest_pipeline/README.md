# ingest_pipeline

The staged ingest pipeline. Takes a track that the app queued with nothing
but metadata and turns it into something playable, classified and
recommendable.

Run it with `scripts/run_ingest_v2.py`. Nothing here is reachable from an
HTTP request — the backend writes a metadata row and returns; everything
below happens out of band.

## The chain

```
ingestion_status='pending'
   ├──► preview ──► download ──► librosa ──► classify ──┐
   │      │                                              ├─► fuse ──► youtube
   └──► language ──► [STOPS: waiting for a tag] ──┘                     │
          │                                                               │
   no preview found ──► 'no_preview'             ingestion_status='done' ─┘
```

`language` is the only stage not in the armed chain, and the only one that
does no work. It reads nothing and classifies nothing: it parks every live
row at `language_status='pending'` and a **pipeline run stops there**. A
Claude Code session tags those rows by querying the database directly (see
§ Tagging languages); the next run sees `'done'` and proceeds. `fuse` waits
for **both** lanes.

| stage | status column | does | cost |
|---|---|---|---|
| `preview` | `preview_status` | resolves a 30s preview URL (iTunes) | network, rate-limited |
| `download` | `download_status` | caches the audio to `data/audio/` | network |
| `librosa` | `librosa_status` | 18-column DSP feature bank | CPU |
| `classify` | `ml_status` | MERT forward pass → vibe scalars **and** the 768-d embedding | GPU |
| `language` | `language_status` | parks rows at `'pending'` for a tagging session; classifies nothing | none (no model, no audio) |
| `fuse` | `fuse_status` | builds the 788-d retrieval vector | trivial |
| `youtube` | `youtube_status` | first `ytsearch` hit, then settles `ingestion_status` | network |

## How a stage becomes eligible: arming

A stage runs when **its own** status column reads `'pending'`. Who writes
that matters:

- `preview` is the entry point. It triggers off `ingestion_status='pending'`
  — the one column the app's INSERT writes literally.
- Every later stage is **armed** by the one before it: on success a stage
  sets the next stage's column to `'pending'`, in the same UPDATE that
  writes its own status (`arms` in `base.py`).

This exists because the old design relied on a schema default. `'pending'`
was never written at runtime — it came from
`ALTER TABLE ... DEFAULT 'pending'` in `backend/db.py`. That default does
not exist in Turso, whose table was rebuilt from `PRAGMA table_info`'s
`type` field alone, silently dropping every DEFAULT. Result: app-inserted
rows landed NULL in all six columns, `= 'pending'` matched nothing, and the
pipeline idled on a full backlog while looking perfectly healthy.

**Arming is not proof the upstream ran.** A column also reaches `'pending'`
from a migration default or any manual UPDATE. So every gate additionally
spells out its real preconditions rather than trusting that it was armed —
see `stage_fuse.fetch_pending`, which checks the embedding's
`model_version` and that language actually finished. Skipping that let fuse
run before language once, silently bucketing tracks as `'other'`.

## Failure: the chain stops where it broke

On `failed`, a stage arms nothing and writes
`ingestion_status='<stage>_stage_error'` (`preview_stage_error`,
`classify_stage_error`, …) plus the exception in `ingestion_error`. One
`GROUP BY ingestion_status` tells you which stage is failing and how often,
with no log parsing. The row also stops re-entering at `preview`, whose
gate only matches `'pending'`, while its own column keeps `'failed'` so a
retry pass knows where to resume.

A stage writes its whole batch in one transaction (`execute_batch`, one
round trip on Turso). When that fails nothing in it landed, and what
happens next depends on the error (`base.py`, "Writing a batch"):

- **Not about a row** — the network, a timeout, a busy or locked database,
  or a failure no statement owns: raised. Nothing is parked; the rows stay
  pending for the next pass.
- **About one row** — the error names its statement, so its row is set
  aside and the batch written again without it. Then that row alone: a
  UNIQUE collision on the legacy `apple_id` constraint is retried without
  `apple_id`, `track_view_url` and `genre`; otherwise it is parked like a
  failure above, with `ingestion_error='[<stage>] write failed: …'`.
- **About every row** — the retry fails at its first statement with the
  same error (a column production lacks, a read-only token): raised, the
  stage stops, nothing is parked.

Before 2026-10-15 one bad row cost the batch: the GPU work was done, the
loop aborted, SQLite rolled everything back and the next pass repeated it.

`no_match` is different — the stage ran fine and found nothing. For
`preview`/`download` that is terminal for the track (`'no_preview'`); for
`language`/`youtube` it is a finished outcome the chain continues past.

## The invariant

**`ingestion_status='done'` means every stage reached a successful terminal
outcome.** Nothing errored, nothing is still pending. `youtube` writes it as
its finishing act, and its gate requires every upstream stage — spelled out,
not inferred. Readers may rely on this.

It used to be looser than that for language: `'whisper_done'` (Whisper ran,
nothing had verified it) and `'no_match'` counted as finished. Both are gone
— `fuse` and `youtube` now require `language_status='done'` exactly. See
§ Tagging languages for why, and for what that costs.

## Tagging languages

**This section is the interface between the pipeline and whoever tags.** A
Claude Code session should be able to work from it alone.

Until 2026-10-10 language was a Whisper forward pass over the 30s preview.
Whisper's own stage docstring admitted it "reliably mispredicts on musical
audio — Kannada songs frequently misclassify as Telugu / Sanskrit / Khmer /
Norwegian Nynorsk, and instrumentals drift randomly", which is why its
terminal state was `whisper_done` and it deferred to a verification stage
that was never written. The 3,756 rows reading `'done'` today were tagged by
a human driving two scripts by hand.

Language is now read from **title / artist / album**. For a film-industry
catalogue that is far stronger evidence than 30 seconds of singing — the
title of a Kannada film song says what it is; the vocal does not. And it
needs no preview, no cached audio and no GPU, which is why the stage sits
outside the armed chain and is reachable the instant the app inserts a row.

**There is no queue file, no export and no apply step. The database is the
queue.**

### 1. Find the work

```sql
SELECT id, spotify_id, title, artist, album, language AS prior_language
FROM tracks
WHERE language_status = 'pending'
ORDER BY id ASC;
```

That predicate — `language_status = 'pending'` — is the whole contract, and
it is deliberately a single equality with no variants to remember. It can be
that simple only because `LanguageStage` normalises the other two spellings
of "not answered" into it first:

- **NULL**, which is what production's Turso `tracks` writes. That table was
  rebuilt from `PRAGMA table_info`'s `type` field alone, dropping every
  `DEFAULT 'pending'`, so app-inserted rows land NULL there and `'pending'`
  locally.
- **`'whisper_done'`**, the retired Whisper terminal state.

A row normalised out of `'whisper_done'` keeps `tracks.language` as Whisper's
guess. Treat `prior_language` as a weak hint and nothing more — mistrusting
it is the entire reason this flow exists.

Rows parked at `ingestion_status='no_preview'` are deliberately **not**
normalised: they can never be analysed, so asking about them buys nothing.
(This used to add that a `'pending'` row would jam the head of every pass.
Since 2026-10-15 a pass only takes rows some stage can act on; see
§ Cohorts.)

### 2. Decide

The language of the **lyrics**, from the metadata. Not the artist's
nationality: a Kannada film composer also releases Hindi tracks, and the
title usually says which. Answer only what you are willing to assert — a row
you skip simply stays `'pending'` for the next session.

### 3. Write it

```python
from ingest_pipeline import language_tagging as lt
from db import get_conn                      # backend/ on sys.path

conn = get_conn()
lt.tag(conn, spotify_id="20OCKNxSXJxt5ZbnxoIDGt", language="kn")
lt.tag(conn, spotify_id="49d0AMzgCtKk9uOayVvqMJ", clear=True)   # instrumental
```

or from a shell:

```bash
python scripts/language_tags.py --set 20OCKNxSXJxt5ZbnxoIDGt=kn --apply
python scripts/language_tags.py --clear 49d0AMzgCtKk9uOayVvqMJ --apply
```

Two verdicts, and the second one is the point:

| | writes | means |
|---|---|---|
| `language='kn'` | `language='kn', language_confidence=1.0, language_status='done'` | the lyrics are in that language (ISO 639-1, lowercase) |
| `clear=True` | `language=NULL, language_confidence=NULL, language_status='done'` | the track has **no** language — instrumental, or a title that genuinely cannot be called |

`clear` is a **verdict, not a failure**. It satisfies fuse's `'done'`-only
gate exactly like a real language does, and the fused vector simply uses the
`'other'` language bucket. A failure would be a stage error, which parks the
row and blocks it — a different thing entirely, and not something a tagging
session should ever produce.

Both also write `language_model_version='claude_session_metadata_v1'` and
`language_predicted_at`, so a tag's provenance stays legible against the
`'whisper_small'` rows.

**And both fire the cascade** — `fuse_status='pending'` — but only when the
language VALUE actually changes. 20% of the 788-d retrieval vector is a
language one-hot, so a changed tag means the stored vector is in the wrong
region of the similarity space; rebuilding takes milliseconds and no GPU,
because the MERT half is already on disk. Making the cascade conditional on a
real change is also what makes re-running harmless: tagging a row you already
tagged writes the same values and does not re-arm fuse.

Doing it in raw SQL is fine too, as long as all of it lands in one statement:

```sql
UPDATE tracks
   SET language = 'kn',            -- or NULL for "no language"
       language_confidence = 1.0,  -- or NULL
       language_model_version = 'claude_session_metadata_v1',
       language_status = 'done',
       fuse_status = 'pending'     -- OMIT only if the value is unchanged
 WHERE spotify_id = '...';
```

Key on `spotify_id`, not `id`: local and prod track ids have diverged (see
§ Prod), so a verdict keyed on `spotify_id` applies in either database.

### Why fuse blocks on this

`fuse` gates on `language_status='done'`, strictly. Fusing without a real tag
does not merely lose precision — it places the track in the wrong region of
the similarity space, and nothing downstream knows the vector is provisional.
`'whisper_done'` and `'no_match'` were removed from that gate rather than left
in: neither has a producer any more, and dead tolerance would silently let an
untagged row through.

The cost is accepted and real: **a track nobody tags never fuses, so it never
reaches the DJ pool.**

### What is waiting, and for how long

```sql
-- how many
SELECT COUNT(*) FROM tracks WHERE language_status = 'pending';

-- how long they have waited (metadata is answerable from insert, so
-- created_at is the honest clock)
SELECT MIN(created_at) AS oldest, COUNT(*) FROM tracks
 WHERE language_status = 'pending';

-- the number that means "the DJ pool is smaller than it should be":
-- encoded by MERT but unfused, with language as the blocker
SELECT COUNT(*) FROM tracks
 WHERE ml_status = 'done'
   AND COALESCE(language_status, '') != 'done'
   AND COALESCE(fuse_status, '')     != 'done';
```

or all three at once, with a non-zero exit when anything is waiting:

```bash
python scripts/language_tags.py --status
```

**On production the wait ages cover only rows that have a `created_at`.** A
read-only probe on 2026-10-15 found it NULL on 4,844 of 6,238 tracks (789
of the 827 pending), not only on rows the app inserted: production's
`tracks` has no column defaults. Rows created from 2026-10-15 on are stamped
by `_process_track`; the existing ones are not, so `MIN(created_at)` and the
oldest / median wait from `--status` (which skips NULLs) describe the
stamped minority until a backfill (`docs/backend-todo.md` 3.11). The counts
are unaffected.

### Leftover states

- **`whisper_done`** — no producer. `LanguageStage` normalises it to
  `'pending'`, and its gate admits those rows (usually
  `ingestion_status='done'` with nothing else armed), which is what puts them
  in a cohort. The local DB has zero of them, and production had none on
  2026-10-15 (read-only probe).
- **`no_match`** — no producer either (27 rows locally, all already fused and
  `ingestion_status='done'`; production also had 27 on 2026-10-15). Left
  alone on purpose: they have a vector and are in the DJ pool, so re-asking
  them is an improvement, not an unblock. To re-ask: `UPDATE tracks SET
  language_status='pending' WHERE language_status='no_match'`.
- **NULL** — normalised to `'pending'` for live rows; left NULL for the 210
  local rows parked at `'no_preview'`. Production had 1,145 NULL rows on
  2026-10-15, 799 of them pending rows that had never entered the pipeline,
  which a pass normalises as its cohorts reach them.

## Re-runs are cheap

Every stage short-circuits on what is already on the row — `preview` on
`preview_url`, `download` on the cached file, `youtube` on `youtube_id` —
*before* doing any network work. Re-ingesting the whole library is then a
status flip plus GPU time, not thousands of iTunes lookups and yt-dlp
scrapes. The guards are explicit rather than emergent: `preview` used to
skip only because `SpotifyPreview` happened to sit first in the chain.

## Cohorts

`run_pass` picks **one** cohort of tracks and walks it through every stage,
passing `only_ids` so each gate is restricted to those rows. Stages still
batch internally. Without this each stage ran its own SELECT, so a pass
could classify one set of tracks and fuse a completely different set.

The cohort is the lowest `--batch` ids that **some selected stage's own
gate** (`fetch_pending`) accepts: the union of what each stage could act on
right now. A pass on a non-empty cohort therefore always processes
something, and `--loop` only reports "drained" when no stage has work.

It used to be a predicate of its own (`ingestion_status` pending or NULL, or
any stage column `'pending'`), and since language stopped writing `'done'`
that stalled for good: rows through classify sit at
`language_status='pending'` + `fuse_status='pending'` with
`ingestion_status='pending'`, which matched, were the lowest ids, and could
be moved by no stage. Every pass re-selected them, processed nothing, and
the loop slept while every higher id waited. It was live on production: a
read-only probe on 2026-10-15 found 28 such rows at the head of the id
order, ahead of 799 pending rows that had never entered the pipeline.
`tests/ingest/test_cohort.py` reproduces it.

## One MERT pass, two outputs

`classify` used to run the fine-tuned checkpoint for scalars while a
separate stage ran the **base** checkpoint for the embedding — the same
encoder architecture over the same file, twice. They looked incompatible
because the head takes 1536 dims and we store 768, but that is only
pooling: the encoder emits 768 per frame either way, and the head's input
is `cat([mean_pool, max_pool])`. The `mean_pool` half *is* the embedding.

`predict_with_embedding` returns both from one pass. Storing `mean_pool`
only is measured, not incidental: `max_pool`'s norm is ~4.6x larger, so in
a jointly-L2'd vector it would carry ~95% of the energy, and its pairwise
similarities span only 0.92–0.99 (8x flatter than mean-pool's) — it would
replace a usable retrieval space with a nearly-degenerate one.

## The vectors

`fused_vector.py` owns the recipe. `track_embeddings` holds two columns per
track:

- `mert_embedding` (768) — mean-pooled hidden state, 30s, fine-tuned encoder
- `fused_embedding` (788) — `0.55*L2(mert) ‖ 0.25*L2(scalars 9) ‖ 0.20*lang(11)`, then L2'd

`/similar` ranks on `fused` by cosine. Only the 768 block is a model output;
the other 20 dims are engineered features, which is why correcting a
language tag needs a `fuse` re-run (milliseconds) and never a GPU re-encode
— set `fuse_status='pending'`.

Changing `SCALAR_COLS`, `TOP_LANGS` or the weights changes the vector
**space**. Cosine across two spaces is meaningless, so every stored vector
must be rebuilt, and `backend/app.py`'s `MERT_DIM`/`FUSED_DIM` must move in
step or `/similar` silently returns nothing.

## Files

```
base.py              Stage ABC — arming, finalizes, failure handling, batching
fused_vector.py      the 788-d recipe: dims, weights, _build_fused
language_tagging.py  the waiting predicate + the safe write (the tagging interface)
preview_providers.py provider chain + iTunes rate limiting/backoff
stage_*.py           the seven stages
```

`ingest/` next door is **not** an older copy of this. Despite the name it is
the shared client layer — `ml_backend`, `features`, `scoring`,
`itunes_client` — imported by both this package and `backend/app.py`.

## Operating it

```bash
# one pass, 50 tracks per stage
python scripts/run_ingest_v2.py --batch 50

# keep going until drained
python scripts/run_ingest_v2.py --batch 50 --loop --interval 30

# a subset, in order
python scripts/run_ingest_v2.py --stages preview,download

# only the library-wide score recompute, no stages
python scripts/run_ingest_v2.py --recompute-only

# which database: local (default) or production Turso -- see § Prod
python scripts/run_ingest_v2.py --target prod --batch 10
```

`--target local` is the SQLite file `db_client` opens (`VIBESCAPE_DB_PATH`,
else `data/vibescape.db`), whatever `DB_BACKEND` says. `--target prod` reads
the credentials from a gitignored `.env.prod` (§ Prod). Every log line
carries `target=…`, and the first one names the file or the host.

`classify` always runs MERT in this process, on the local GPU. There is no
Modal mode for it: `predict_and_embed_from_path` (`ingest/ml_backend.py`)
loads the local checkpoint whatever `VIBESCAPE_ML_MODE` says, and Modal's
`predict_from_url` returns no embedding, so it could not serve this stage.
The mode only decides whether classify warns at startup that no backend is
available. This used to list `VIBESCAPE_ML_MODE=local` as "local GPU instead
of Modal", but the stage had already ignored the mode.

### The score recompute runs here, not in the app

`activation_relative`, `vibe_score`, `activation`, `valence` and `mood` are
recomputed for the **whole catalogue** by `ingest/score_recompute.py`:
activation z-scored across every featured track (mean 50, one std = 15). It
is owed only when `librosa` or `classify` finished a row (librosa adds rows to
the baseline, classify overwrites the columns the recompute owns). A single
pass recomputes at its end. **`--loop` recomputes once, when the loop drains**
(the first pass that processes nothing, including the `--only-ids` exit),
never per pass: every newly scored track shifts the mean and std, so nearly
every row moves and the skip below saves nothing. Per pass, draining 1,000
tracks at `--batch 50` would mean 20 full recomputes of ~3,800 UPDATEs each
instead of one. A failed recompute stays owed and is retried at the next
idle point. Rows whose stored values already match are not written, and the
rest go out through `execute_batch` 200 at a time: one Turso round trip per
200 rows, where it used to be one per row (until 2026-10-15).
Import jobs ran it on every sync until 2026-10-13; a sync only adds
unanalysed `pending` rows, so the baseline cannot move until this pipeline
has scored them. `POST /api/recompute-scores` still exists, admin-only.

Batch size sets the **commit granularity** — `run_batch` writes once per
stage per batch, after every row is processed: one transaction, and on
Turso one request. A large batch means a kill loses everything unwritten;
`--batch 2500` once lost 42 minutes of preview work. It also sizes that
request: a classify row is ~5.7 KB of JSON and a fuse row ~4.9 KB (the
vectors), so `--batch 50` sends ~280 KB (measured 2026-10-15). No Turso
size limit has been hit or measured; a batch the server refuses is written
row by row instead.

### Draining a backlog

iTunes rate-limits on a rolling window, and the default backoff
(4 retries, 2/4/8/16s) is tuned for steady state. Against a backlog it gives
up while the window is still shut and writes false `no_match` rows — 49% of
one 422-track backlog turned out to be recoverable. The **gap** is the lever,
not the retry count: at 2s spacing the limiter never trips.

```bash
VIBESCAPE_ITUNES_MIN_GAP=2 VIBESCAPE_ITUNES_RETRIES=7 VIBESCAPE_ITUNES_BACKOFF=5 \
  python scripts/run_ingest_v2.py --batch 30 --loop
```

Distinguish the two failure modes in the logs: `search failed ... 403` is
throttling (retry it), while a plain `no_match` with no 403 means iTunes
answered and had nothing (genuine).

### The preview matcher returns the track or nothing

`ItunesPreview._pick_best` accepts a result only when the title names the
same recording (brackets, `feat.` and ` - ` suffixes aside; version markers
like 8D / slowed / remix / reprise must agree), at least one credited artist
is shared, and the length is within max(15 s, 10 %). There is **no "closest
result" fallback**. It had one until 2026-10-11, and since a search for one
song often returns its album, 124 tracks ended up carrying another song's
audio — identical vectors under different titles, which the DJ served to
each other at cosine 1.000. A miss is `no_preview`, which is visible; a
wrong match is not.

`scripts/reset_shared_previews.py` finds rows that share a `preview_url` or a
byte-identical MERT vector under different titles, clears everything derived
from the audio (including the cached file — re-runs short-circuit on it) and
re-arms them. `run_ingest_v2.py --only-ids FILE` then advances just those rows
and exits when they are idle, rather than also draining whatever else is
pending.

The matcher's tests are `tests/ingest/test_preview_matcher.py`.

## Prod

Production is Turso, and the pipeline runs **against it directly**, from
the machine with the GPU (decided 2026-10-15):

```bash
python scripts/backup_prod_db.py                                 # 1. a dated snapshot, always first
python scripts/run_ingest_v2.py --target prod --batch 10         # 2. one small pass; read the log
python scripts/run_ingest_v2.py --target prod --batch 50 --loop  # 3. then drain
python scripts/language_tags.py --target prod --status           # 4. tag what waits, on prod
python scripts/language_tags.py --target prod --set <spotify_id>=kn --apply
python scripts/run_ingest_v2.py --target prod --loop             # 5. fuse + youtube the tagged rows
python scripts/run_ingest_v2.py --target prod --recompute-only   # 6. scores, if a run stopped short
```

`--target prod` reads a gitignored `.env.prod` at the repo root that sets
`DB_BACKEND=turso`, `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN`, all three
in that file, into that process only (`scripts/_target.py`). Never put them
in `.env`: uvicorn reads it, and the dev app must not be able to reach
production by accident. Every log line says `target=prod`, the first one
the host; the token is never printed.

Until 2026-10-15 the pipeline ran on a local copy and
`scripts/_sync_local_to_turso.py --apply` pushed the results. That made two
sources of truth: measured against the 2026-10-12 backup for the decision,
a push would have nulled `preview_url` on 209 prod tracks in users'
libraries, regressed 74 language tags that existed only on prod and nulled
74 `youtube_id`s. The sync is gone; nothing writes a local result into prod
any more.

**Production on 2026-10-15** (read-only probe, from the owner's session):
6,238 tracks, `ingestion_status` done 4,882 / pending 827 / no_preview 529.
Of the 827 pending, 799 had every stage column NULL (never entered the
pipeline) and 28 were encoded but waiting at `language_status='pending'` +
`fuse_status='pending'`, at the head of the id order (§ Cohorts).
`language_status`: NULL 1,145 / done 5,038 / pending 28 / no_match 27.
`tracks` has no column DEFAULTs, no row with `ingestion_status` NULL and none
at `'whisper_done'`. `track_embeddings`: 4,910 rows, 4,882 with a fused
vector. So the first runs work through those 799, and the 28 move as soon as
they are tagged.

**The dev copy of prod is the backup file.** Copy it and point
`VIBESCAPE_DB_PATH` at the copy (the snapshot itself stays a restore
point). It keeps prod's track ids. A DB built by
`scripts/_turso_pull_to_local.py` does not: the pull discards `id` and lets
SQLite re-autonumber, so ~58% of tracks carry a different id there, which
is why tags are keyed on `spotify_id`. Never write such ids into prod (they
would re-point `user_tracks` at the wrong songs), and never drop `tracks`
(Turso enforces foreign keys, so `user_tracks` would cascade to nothing).

Against Turso a stage's batch is one transaction in one request, and the
score recompute writes 200 rows a request (§ Operating it). With every
stage selected (the default) each status column is read by some gate at the
start of each pass, so one missing on prod stops the pass with `no such
column` before anything is written. The retired sync used to add
`librosa_status` and `fuse_status` when prod lacked them; the 2026-10-15
probe read every stage column, so they were there then, and
`PRAGMA table_info(tracks)` on a fresh backup says whether they still are. A
missing *domain* column (one a stage writes, like `tonnetz_std`) is caught
per row instead: each affected row is parked at `<stage>_stage_error` with
the error text, so the first small pass is where to find out.
