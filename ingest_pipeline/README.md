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
        │
    preview ──► download ──► librosa ──► classify ──► language ──► fuse ──► youtube
        │                                                                      │
        └── no preview found ──► 'no_preview'          ingestion_status='done' ─┘
```

| stage | status column | does | cost |
|---|---|---|---|
| `preview` | `preview_status` | resolves a 30s preview URL (iTunes) | network, rate-limited |
| `download` | `download_status` | caches the audio to `data/audio/` | network |
| `librosa` | `librosa_status` | 18-column DSP feature bank | CPU |
| `classify` | `ml_status` | MERT forward pass → vibe scalars **and** the 768-d embedding | GPU |
| `language` | `language_status` | Whisper language detection | GPU |
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

"Successful" is slightly looser than "good" in two places, both deliberate:
`language_status='whisper_done'` (Whisper ran; LLM verification is still
outstanding) and either stage's `'no_match'` count as finished.

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
