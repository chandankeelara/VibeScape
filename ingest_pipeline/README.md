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
normalised: they can never be analysed, so asking about them buys nothing,
and promoting them would make them permanently cohort-eligible and jam the
head of every pass (`docs/backend-todo.md` 3.1).

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

### Leftover states

- **`whisper_done`** — no producer. `LanguageStage` normalises it to
  `'pending'`, and `select_cohort` carries a legacy term for it so those rows
  (usually `ingestion_status='done'` with nothing else armed) can still enter
  a cohort and be seen. The local DB has zero of them; production was not
  checked.
- **`no_match`** — no producer either (27 rows locally, all already fused and
  `ingestion_status='done'`). Left alone on purpose: they have a vector and
  are in the DJ pool, so re-asking them is an improvement, not an unblock.
  To re-ask: `UPDATE tracks SET language_status='pending' WHERE
  language_status='no_match'`.
- **NULL** — normalised to `'pending'` for live rows; left NULL for the 210
  local rows parked at `'no_preview'`.

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

# local GPU instead of Modal
VIBESCAPE_ML_MODE=local python scripts/run_ingest_v2.py --batch 50
```

Batch size sets the **commit granularity** — `run_batch` commits once per
stage per batch. A large batch means a kill loses everything uncommitted;
`--batch 2500` once lost 42 minutes of preview work.

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

## Prod

The pipeline runs locally against `data/vibescape.db`; prod is Turso.

```bash
python scripts/_turso_pull_to_local.py          # prod -> local
python scripts/_sync_local_to_turso.py --apply  # local -> prod
```

The sync matches on `spotify_id` and UPDATEs in place. It never DROPs,
never INSERTs and never writes `tracks.id`, because **local and prod ids
have diverged** — the pull discards `id` and lets SQLite re-autonumber, so
~58% of tracks carry a different id locally. Writing local ids into prod
would re-point `user_tracks` at the wrong songs, and dropping `tracks`
would cascade `user_tracks` to nothing.
