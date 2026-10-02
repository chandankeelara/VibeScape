# Recency-aware re-ranking for DJ mode — plan

Status: **proposal, nothing built.** No application code was changed to write
this. Measurements below were taken against the local SQLite DB on 2026-10-02
and are marked **verified**; everything else is marked **assumed**.

---

## The recommendation, in short

Re-rank the DJ candidate list with a **subtractive** penalty whose size is
**calibrated to the width of the output window**, not to an absolute constant:

```
final(c)  = sim(c) − λ · P(c)

λ         = W · (sim[#1] − sim[#limit])        in the un-penalised order
P(c)      = P_play(c) + P_skip(c)
P_play    = 2 ^ ( −Δt_played  / 72h )          0 if never played
P_skip    = B · 2 ^ ( −Δt_skipped / 168h )     0 if never skipped
B         = 2.0 − 1.5 · bail_fraction          ∈ [0.5, 2.0]; 1.0 if unknown
W         = 2.0
```

A track that was never played scores exactly what it scores today. The whole
mechanism is one-sided: **no term in this design can raise a track's score.**

The two parameters have a single joint meaning worth memorising:

> At one half-life (72 h), the recency penalty is worth exactly `W/2` = one
> output-window of cosine similarity.

It runs in `_similar_dj` after the candidate query and before the slice to
`limit`, on all three paths (Turso vector ranking, numpy fallback, and the
`_similar_vibe` fallback), at a cost of **zero extra database round trips** on
the Turso path — the stats arrive on a `LEFT JOIN` in the query that is already
being issued.

---

## 1. Why the shape of the penalty matters more than the penalty

**Verified, 2026-10-02, local DB, 3,783 fused vectors (788-dim).** I built four
synthetic taste vectors in the shape `_similar_dj` builds them
(`normalise(Σpos − 0.4·Σneg)`, 5 positives / 2 negatives) and measured the
cosine similarity profile of the full library:

| | top #1 | #8 | #30 | #60 | #120 | #1 − #8 | #1 − #60 |
|---|---|---|---|---|---|---|---|
| q1 | 0.9370 | 0.9288 | 0.9203 | 0.9146 | 0.9094 | **0.0081** | 0.0224 |
| q2 | 0.9532 | 0.9370 | 0.9259 | 0.9195 | 0.9076 | **0.0162** | 0.0337 |
| q3 | 0.9738 | 0.9601 | 0.9547 | 0.9503 | 0.9453 | **0.0137** | 0.0236 |
| q4 | 0.9657 | 0.9558 | 0.9507 | 0.9482 | 0.9442 | **0.0099** | 0.0174 |

Library-wide median similarity is ~0.76 and the 90th percentile ~0.83
(measured separately on three real seed tracks).

**This is the governing fact of the whole design.** All the information the
ranker has about "which of these eight is best" lives in a window roughly
**0.01 wide**, sitting at an absolute level of ~0.95. Cosine is bounded in
[−1, 1] and the usable signal occupies about 0.5% of that range.

Consequences:

- Anything that scales with the **magnitude** of the similarity (a
  multiplicative penalty) operates on ~0.95 of signal, which is ~100× the size
  of the part that discriminates. It will obliterate the vector match.
- Anything with a **small fixed** additive weight (0.001–0.005) is inside the
  noise between adjacent candidates and will do nothing visible.
- The only way to get a predictable amount of reordering is to express the
  penalty **in units of the pool's own spread**, which is what `λ = W · (sim[#1]
  − sim[#limit])` does.

This also makes the design survive things that would otherwise silently break
it: a change of embedding variant (`fused` → `mert`, different dimensionality
and different similarity scale), a model retrain that tightens or loosens the
distribution, and the `_similar_vibe` fallback — which ranks by a weighted L1
distance in a roughly 0–3.3 range and would need a completely different
constant if λ were absolute.

### Why the window and not the whole pool

λ could be anchored to the spread across the whole candidate pool
(`sim[#1] − sim[#pool]`). Rejected: that makes the strength of the penalty a
function of how big a pool you happened to fetch — widening the pool from 100
to 300 would silently make the penalty harsher, which is a horrible property
for a tuning knob. `sim[#1] − sim[#limit]` is the width of the thing the user
actually sees and is independent of pool size.

Degenerate guard: if that window is < 1e-6 (every candidate identical, or
fewer than two candidates), skip re-ranking entirely rather than dividing into
a degenerate scale.

---

## 2. The decay function

**Exponential, base-2 half-life form, H = 72 hours**, with `Δt` clamped at ≥ 0.

### Why exponential

- One parameter with a plain-English meaning ("after three days a track is
  half-forgiven"), which is exactly what you want for a number you will have to
  re-tune later against real data.
- Memoryless: the penalty depends only on *when*, never on an accumulated
  history. That matters for the narrowing question in §6 — there is no state
  that can make a track permanently harder to surface.
- It reaches effectively zero. At 2 weeks `P_play` = 0.036; at a month,
  0.0009. A track genuinely drops out of the penalised set rather than carrying
  a residue forever.

### Why 72 hours, specifically

The backend penalty is **not** the thing that prevents repeats inside a
session — the frontend already does that, hard, and better:
`excludeIds()` in `frontend-next/src/features/queue/dj.js` sends up to 200
`tracks.id` values (`DJ_MAX_EXCLUDES = 200`) covering the seen-set and the
pending queue, and `_similar_dj` filters them out of the SQL entirely
(**verified** at `backend/app.py:1420-1426`). Within a listening session, a
recently played track is *excluded*, not *penalised*.

So the job this penalty is being hired for is the **cross-session** one:
yesterday's session, last weekend's session. That sets the timescale.

- **An hour** is the wrong answer: it is inside one session, where the
  exclude list already applies, so a 1-hour half-life would be a no-op on the
  problem the user described.
- **A week** is the wrong answer in the other direction: against a
  3,786-track library (verified: user 21 has 3,786 `user_tracks` rows) a
  one-week half-life keeps roughly a month of listening suppressed. That stops
  being "don't repeat yourself" and starts being "never play anything you
  like", which is a different and worse product.
- **Three days** suppresses roughly the last fortnight of listening. At a
  plausible 20–30 tracks a day that is 300–400 tracks, about 10% of this
  library — enough to feel like variety, small enough that a favourite comes
  back around within the week.

The decay profile, for reference:

| Δt | 1 h | 6 h | 1 d | 3 d | 1 wk | 2 wk | 1 mo |
|---|---|---|---|---|---|---|---|
| `P_play` | 0.990 | 0.944 | 0.794 | 0.500 | 0.198 | 0.036 | 0.0009 |

**Assumed**, not verified: the 20–30 tracks/day listening rate. There are
20 events in the local DB spanning 35 minutes (verified). This number cannot
be estimated from that — see §8.

### Rejected shapes

**Step function** ("exclude anything played in the last N hours"). Rejected on
three counts. It cannot express degree, so a track at 23 h 59 is banned and one
at 24 h 01 is free, which is both arbitrary and gameable by session timing. It
has no graceful degenerate case: in a small library where everything is recent,
a step function bans everything and the DJ returns an empty list. And it is
*already implemented* — `exclude_ids` is precisely a step function at session
scope. Adding a second one at a different scale buys nothing the parameter on
the first one couldn't.

**Hyperbolic**, `P = 1 / (1 + Δt/τ)`. Rejected for the tail. At τ = 3 d a track
played a month ago still carries P ≈ 0.09, and one played a year ago ≈ 0.008.
Summed across a long history that stops being a recency signal and becomes a
**popularity penalty** — the system would systematically prefer tracks the user
has never played, which is a different and more dangerous bias (see §6, the
narrowing argument runs the other way than you'd expect). Hyperbolic is the
right shape if we ever want a separate *fatigue* term ("you have played this
200 times, ever"), which is a deliberate non-goal here.

**Linear ramp to zero over N days.** Rejected as strictly worse than
exponential: it has a discontinuity in slope at the cutoff and gives up the
memoryless property, in exchange for being marginally easier to compute. The
computation is not the hard part.

### Never played is P = 0, and that is load-bearing

A `LEFT JOIN` miss means `last_played IS NULL`, which means `P_play = 0` —
maximally fresh. It must not be imputed to a mid-range value, and not to "very
old" either (which happens to give the same answer here but for the wrong
reason, and would stop being true if anyone ever adds a `first_played_at`
term).

The practical payoff: **a user with no `user_track_stats` rows at all gets a
response byte-identical to today's.** The new code path is a no-op in the
absence of data, which is the correct risk profile for a ranking change.

---

## 3. The arithmetic, worked

Take the real q2 profile from §1: window = `sim[#1] − sim[#8]` = 0.9532 −
0.9370 = **0.0162**, so λ = W · 0.0162 = **0.0324** at W = 2.0.

Two candidates:

- **A** — never played, `sim = 0.9370` (it is #8 in the raw order)
- **B** — `sim = 0.9532` (it is #1 in the raw order), played Δt ago, never
  skipped

| Δt for B | `P_play` | penalty λ·P | **final(B)** | final(A) = 0.9370 | winner |
|---|---|---|---|---|---|
| 1 h | 0.990 | 0.0321 | 0.9211 | 0.9370 | **A** |
| 6 h | 0.944 | 0.0306 | 0.9226 | 0.9370 | **A** |
| 1 d | 0.794 | 0.0257 | 0.9275 | 0.9370 | **A** |
| 3 d | 0.500 | 0.0162 | **0.9370** | 0.9370 | **tie** |
| 1 wk | 0.198 | 0.0064 | 0.9468 | 0.9370 | **B** |
| 1 mo | 0.001 | 0.00003 | 0.9532 | 0.9370 | **B** |

The exact tie at one half-life is the identity stated at the top: 72 h of
recency is worth W/2 = 1.0 output-windows of similarity. That is the sentence
to argue about when re-tuning.

### The same pair under the rules we are not using

**Multiplicative, `sim · (1 − P)`:**

| Δt for B | final(B) | vs A = 0.9370 |
|---|---|---|
| 1 h | 0.9532 × 0.0096 = **0.0092** | annihilated |
| 1 d | 0.9532 × 0.206 = **0.196** | annihilated |
| 1 wk | 0.9532 × 0.802 = **0.764** | still below the library *median* (0.76) |
| 1 mo | 0.9532 × 0.999 = 0.9523 | B |

A track played a week ago lands below the median of the entire 3,783-track
library — i.e. below roughly 1,900 tracks it has nothing in common with. The
penalty is not competing with the 0.016 that carries the ranking information;
it is competing with the 0.95 that does not. Rejected.

**Multiplicative with a bounded coefficient, `sim · (1 − 0.05·P)`:** at 1 h,
0.9532 × 0.9505 = 0.9060, which beats A correctly. This one *works*. It is
rejected as redundant rather than wrong: expanded, it is `sim − 0.05·sim·P`,
which is the subtractive rule with `λ = 0.05 · sim`. Since `sim` varies by
0.016 across the window, λ is constant to within 1.7% — so it is the
**fixed-λ** scheme wearing a multiplicative costume, and it inherits fixed-λ's
only real flaw: 0.05 is tuned to this embedding's scale and silently becomes
wrong on the `mert` variant or on the `_similar_vibe` fallback.

**Fixed small additive, λ = 0.005:** B at 1 h → 0.9532 − 0.00495 = 0.9483,
still ahead of A. The penalty is smaller than the gap it is trying to close.
This is the "does nothing" failure, and it is the more likely one to ship by
accident because it never looks broken.

**Rank-space re-ranking** (score each candidate by `1 − rank/pool`, then
subtract). Considered seriously — it is immune to every scale question at
once. Rejected because it discards the magnitude of the gaps, and the gaps are
not uniform: the #1 → #2 step is frequently 5–10× the #30 → #31 step, which
means rank-space would treat a clear winner and a three-way tie identically.
Worth revisiting if the window-relative λ turns out to be unstable in practice.

### A property worth noting

If every candidate in the pool has the **same** `P`, all scores shift by the
same constant and **the order is unchanged**. This is why §6's "small library
where everything is recent" case degrades to "today's behaviour" for free, and
it is an argument against normalising `P` within the pool (which would
manufacture a spurious ordering out of a uniformly-recent pool).

---

## 4. Where it runs, and what it costs

### Candidate pool

Re-ranking needs more candidates than `limit`. Proposed:

```
POOL = min(300, max(150, 12 * limit))
```

With `limit ≤ 25` (clamped at `backend/app.py:1259`), that is 150 for the
common case and 300 at the maximum. The justification is §3's table: at λ =
2 windows, a freshly played track falls about 2–5 output-windows. The pool has
to be deep enough that there is something underneath it to promote. 150 is
~10 windows deep on the measured profiles, which is comfortable headroom.

### Turso path — one query, zero extra round trips

The existing query at `backend/app.py:1427-1440` already joins `user_tracks`
and `track_embeddings`. Add one `LEFT JOIN` and four columns:

```sql
SELECT {TRACK_COLUMNS},
       vector_distance_cos(te.fused_embedding, vector32(?)) AS distance,
       s.last_played                                        AS uts_last_played,
       s.last_skipped_at                                    AS uts_last_skipped_at,
       COALESCE(s.u_skip_count, 0) + COALESCE(s.s_skip_count, 0)
                                                            AS uts_skip_count,
       COALESCE(s.u_skip_position_ms_sum, 0)
     + COALESCE(s.s_skip_position_ms_sum, 0)                AS uts_skip_pos_sum
FROM tracks t
JOIN user_tracks ut        ON ut.track_id = t.id
JOIN track_embeddings te   ON te.track_id = t.id
LEFT JOIN user_track_stats s ON s.user_id = ut.user_id AND s.track_id = t.id
WHERE ut.user_id = ?
  AND t.ingestion_status = 'done'
  AND te.fused_embedding IS NOT NULL
  {exclude clause, unchanged}
ORDER BY distance ASC
LIMIT ?            -- POOL, was `limit`
```

`t.duration_ms` is already in `TRACK_COLUMNS` (**verified** — it is a column on
`tracks`, non-NULL and non-zero on all 3,783 `done` rows), so the bail fraction
needs nothing extra.

The join is on `user_track_stats`'s primary key `(user_id, track_id)`
(**verified**, `schema.sql`), so it is an index seek per candidate row inside
the DB, not a scan, and it adds **no** round trip. That matters: every
`db_client.execute()` is its own Hrana POST with its own stream open/close
(backlog 2.2), so a per-candidate stats query would be 150 HTTPS round trips
per recommendation. That option is not on the table.

**Cost that does change:** the response streams 150 full track rows instead of
8. `TRACK_COLUMNS` is wide (~64 columns, including `mfcc_json` and
`chroma_mean_json`), so this is plausibly 150–400 KB per request rather than
~10 KB. The DB-side cost is unchanged — there is no ANN index (the
`libsql_vector_idx` DDL is commented out in `schema.sql`), so it was already a
full scan over the user's library.

The alternative is a two-stage fetch: query #1 returns only
`t.id, distance, duration_ms` + the four stats columns for POOL rows (~15 KB),
re-rank, then query #2 fetches `TRACK_COLUMNS` for the winning `limit` ids
(~10 KB). That is 25 KB across **two** round trips instead of ~300 KB across
one. Which wins depends on whether Cloud Run → Turso is latency-bound or
bandwidth-bound, and I have not measured it. **Recommendation: ship the
single-query form, instrument the timing, and switch to two-stage only if the
payload shows up in the latency.** One round trip from a cold Cloud Run
instance is of the order of 100–200 ms (**assumed**, not measured here), and
300 KB over a warm HTTPS connection is usually cheaper than that.

A third option — computing the whole penalty in SQL with
`julianday('now') − julianday(s.last_played)` and ordering by the final score —
is tempting because it keeps `LIMIT 8`. Rejected: the exponentials and the bail
fraction would be inlined into a generated SQL string, the arithmetic becomes
untestable without a database, and the `explain` field of §7 becomes
impossible to produce. The penalty is cheap in Python over 150 rows.

### numpy fallback path (local dev, cold start, Turso error)

This path (`backend/app.py:1466-1512`) already loads **every** candidate vector
into memory, so there is no pool question — re-rank the whole library. One
additional query, issued once per request:

```sql
SELECT track_id, last_played, last_skipped_at,
       COALESCE(u_skip_count, 0)            + COALESCE(s_skip_count, 0)            AS skip_count,
       COALESCE(u_skip_position_ms_sum, 0)  + COALESCE(s_skip_position_ms_sum, 0)  AS skip_pos_sum
FROM user_track_stats
WHERE user_id = ?
  AND (last_played     > datetime('now', '-30 day')
    OR last_skipped_at > datetime('now', '-30 day'))
```

Served by `idx_user_track_stats_recent (user_id, last_played DESC)` for the
first predicate. It is bounded by "tracks this user touched in 30 days", which
is 11 rows today (**verified**) and would be a few hundred for a heavy user.
The 30-day cutoff is safe because `P_play(30 d) = 0.0009` — below the float
noise on the similarity scores.

Build a `{track_id: P}` dict, apply it to the `sims` vector before `argsort`,
and the existing `top_scores` bookkeeping carries through unchanged.

### Cold start (`query_vec is None`)

This path currently takes a **uniform random** slice of the candidate pool
(`backend/app.py:1498-1503`). The recency penalty is arguably more valuable
here than anywhere else, since there is no similarity signal to trade against.
Replace the uniform permutation with a weighted sample, `w = 1 / (1 + P)`,
which keeps it random (important — cold start should not be deterministic) but
makes a track you played an hour ago about half as likely as one you have never
heard. No λ is involved; there is no similarity scale to calibrate to.

### `_similar_vibe` fallback — the path with no embedding at all

`_similar_dj` falls back here whenever the seed has no vector in either variant
(`backend/app.py:1288-1294`), and the GET endpoint uses it unconditionally.
It ranks by a weighted-L1 `distance` computed in SQL, in a range of roughly
0–3.3 — a completely different scale, and *ascending* rather than descending.

The window-relative λ handles this with no special-casing, provided the
implementation is written against a generic `score` (higher is better) rather
than against cosine:

- define `score = −distance`,
- `λ = W · (score[#1] − score[#limit])` = `W · (distance[#limit] − distance[#1])`,
- everything else is identical.

Concretely: `_similar_vibe` gains the same `LEFT JOIN user_track_stats` (it
already joins `user_tracks`), `LIMIT ?` becomes POOL, and it shares the
re-ranking helper. **This must be done.** If the penalty exists only on the DJ
path, then the moment the DJ falls back to vibe — which is exactly the moment
the library is thinnest and repeats are most likely — the anti-repeat behaviour
silently disappears, and `mode_used` is the only evidence.

The one thing to be careful about: `_similar_vibe`'s pool spread is much wider
in absolute terms than cosine's, so λ is much larger in absolute terms too.
That is correct and is the entire point of making λ relative — but it means
nobody should ever compare a λ value between the two paths and conclude
something is wrong. Put that in the docstring.

### A shared helper, not three copies

All three paths should call one function:

```python
def _recency_penalty(now, last_played, last_skipped_at,
                     skip_count, skip_pos_sum, duration_ms) -> tuple[float, dict]
```

returning `(P, explain_dict)`. It is pure, takes no connection, and is the only
place the constants appear — which also makes it the first thing in this
codebase that could have a unit test (there are currently none anywhere:
backlog header, verified).

---

## 5. Skips are not plays

**A skip is a stronger negative than a recent play, and it lasts longer.**

The reasoning: `last_played` says *"you have had this lately"* — a statement
about scheduling. `last_skipped_at` says *"not this"* — a statement about the
track. The first should wear off in days, because the point of a favourite is
that it comes back. The second is the only explicit negative the system ever
receives (`schema.sql`'s own framing, and the reason the table is an event log)
and throwing it away in 72 hours wastes it.

Hence `H_skip = 168 h` (7 days) against `H_play = 72 h`, and an amplitude `B`
that can exceed 1.

Note that the two terms are **additive, not `max()`**: a skip also writes
`last_played` (verified — `_upsert_track_stats` sets `last_played = now` on
every event regardless of type or reason), so a track skipped an hour ago
carries both terms and should be the single most suppressed thing in the pool.
That is deliberate.

### Depth of the skip

`B = 2.0 − 1.5 · bail_fraction`, where
`bail_fraction = (skip_pos_sum / skip_count) / duration_ms`, clamped to
[0, 1]; `B = 1.0` when `skip_count = 0` or `duration_ms` is missing.

| bailed at | `B` | meaning |
|---|---|---|
| 5% | 1.93 | cut it off in the intro — a real rejection |
| 25% | 1.63 | didn't want it |
| 50% | 1.25 | ambivalent |
| 90% | 0.65 | basically listened to it; probably hit next near the outro |

Both real skips in the local data (**verified**) land where you'd want:

- track 941, skipped at 66,252 ms of 263,036 ms → 25% → `B` = 1.63
- track 1740, skipped at 171,229 ms of 333,426 ms → 51% → `B` = 1.23

Worked, one hour after track 941's skip, with λ = 0.0324 from §3:

```
P_play = 2^(-1/72)        = 0.990
P_skip = 1.63 · 2^(-1/168) = 1.623
P      = 2.613
penalty = 0.0324 × 2.613  = 0.0847   ≈ 5.2 output-windows
```

On the q2 profile, 0.0847 below the top score is past #120 — effectively
banished. A week later: `P_play` = 0.198, `P_skip` = 0.815, penalty = 0.0329,
about two windows — pushed out of the top 8 but plainly still in the running.
A month later it is back. That is the intended arc.

**Using the mean skip position rather than the most recent one** is a
deliberate simplification: we do not store per-skip positions in the aggregate,
only a sum and a count, and going to `track_events` for the last skip position
would be a second query per candidate, which §4 rules out. The mean is
monotone in the right direction and that is enough. If the distinction ever
matters, the right fix is a `last_skip_position_ms` column on
`user_track_stats` written by `_accumulate_stats` — one more column, no new
query, and it would need `scripts/rebuild_user_track_stats.py` updated in the
same commit (they are declared to be one spec in two languages).

---

## 6. The `u_` / `s_` split — exactly what is read, and why it does not close the loop

### Columns this design reads

| Column | Family | Used for |
|---|---|---|
| `last_played` | label-agnostic | `P_play` |
| `last_skipped_at` | label-agnostic | `P_skip` timing |
| `u_skip_count` + `s_skip_count` | **both, summed** | skip depth denominator |
| `u_skip_position_ms_sum` + `s_skip_position_ms_sum` | **both, summed** | skip depth numerator |
| `tracks.duration_ms` | — | skip depth denominator |

### Columns this design does not read, at all

`play_count`, `end_count`, `total_played_ms`, `dj_play_count`,
`first_played_at`, every `*_complete_count`, every `*_replace_count`, every
`*_vibe_count` / `*_vibe_sum` / `*_vibe_sum_sq`, and both `*_play_count`.

### Why summing `u_` and `s_` skips is safe here

The schema's prohibition is on **averaging the two populations into one
preference estimate**, because `s_*` is the recommender's own output coming
back as if it were the user's stated taste. That is a prohibition on a
*positive* feedback path. Three reasons it does not apply here:

1. **Every term in this design is non-positive.** `final = sim − λ·P`, `P ≥ 0`.
   The DJ cannot make a track more likely by choosing it. The only thing the
   `s_` family can do in this design is make a track *less* likely, and only
   after a human pressed skip. A loop that only ever damps cannot run away.

2. **`vibe_source` labels the slider, not the action.** From `schema.sql`'s own
   comment: it records *"WHO put that number on the slider"* — the user
   dragging it, versus DJ mode echoing back the vibe of the track it just
   loaded. It says nothing about who ended the track. **A skip is a human act
   in both populations.** `s_skip_count` is not "the DJ skipped it"; it is "the
   user skipped a track while the slider happened to be under DJ control".
   Treating that as less real than `u_skip_count` would be a misreading of the
   column.

3. **Empirically, `u_`-only is a guaranteed no-op.** All 20 events in the local
   DB carry `vibe_source = 'system'` and `dj_mode = 1` (**verified** — see §9,
   finding B). The entire `u_*` family is zero. A design that read only `u_*`
   would ship, look correct in code review, and do literally nothing.

### The asymmetry, stated so nobody has to re-derive it

If a future change adds a term that **raises** a score — a preference boost, a
vibe-affinity match, a completion-rate weight — that term **must** read `u_*`
only. The rule is not "recency is special"; the rule is **positive terms read
`u_`, negative terms may read both**. Worth putting in the docstring of
`_recency_penalty`'s caller, because the next person will reasonably assume
the split is about the column prefix rather than about the direction of the
effect.

---

## 7. Failure and degenerate cases

**A user with no stats at all.** `LEFT JOIN` misses, all `P = 0`, all scores
shift by zero, ordering identical to today. The feature is inert until there is
data. This is the single most important property of the design and is the
reason `P` is subtractive-from-zero rather than, say, a multiplier centred on 1.

**A small library where everything is recent.** If all candidates share
roughly the same `P`, all scores shift by roughly the same amount and the order
is preserved (§3). The DJ still returns `limit` tracks — it never returns fewer,
because nothing is excluded, only reordered. Contrast with the step-function
design, which would return an empty list. The partial case (half the pool
recent) reorders within the pool, which is the desired behaviour.

**The shared guest row.** **Verified**: `auth_guest` (`backend/app.py:700`)
keys all guests on a single `users` row with `display_name = 'Guest'` and seeds
it with the entire catalogue; `backend/app.py:777-780` documents this as a
known, deliberate property. So **all guests pool one listening history**, and
one guest's skips suppress tracks for every other guest. Three options:

- *(a) Accept and document.* For a demo this is arguably mildly good: it makes
  the shared demo less repetitive across visitors. The harm is bounded by
  "tracks played by any guest in the last ~2 weeks", which at demo volume is
  tens of tracks out of 3,783.
- *(b) Disable the penalty for guests* — `sess["display_name"] == "Guest"`,
  one condition, and `is_guest` is already computed at `backend/app.py:430`.
- *(c) Key recency on the session token.* Rejected: `track_events` is
  append-only and keyed on `user_id`; this means a schema change to the table
  the backlog explicitly declares immutable in shape.

**Recommendation: (a), with (b) written down as the one-line escape hatch.**
The case to watch is scale — if guest traffic ever reaches a few hundred plays
a week, the shared row accumulates enough recent history to suppress a visible
fraction of the catalogue for everyone, and the symptom will present as "the
demo recommends weird tracks" with no obvious cause. That is the one scenario
where this feature could get meaningfully worse over time rather than better.

**A future `last_played`.** If anything ever writes a non-UTC timestamp, `Δt`
goes negative and `2^(−Δt/H)` grows without bound. Clamp `Δt ≥ 0`, so a
future timestamp yields `P = 1` (maximally recent), not `P = 40`. Today the
only writer is `_upsert_track_stats` with
`datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")` (**verified**,
`backend/app.py:3842`), which is directly comparable with SQLite/libSQL
`datetime('now')` — also UTC. Verified against the live rows: `last_played`
values are `2026-10-02 20:59:22` style and `datetime('now')` returned
`2026-10-02 21:00:07` in the same run.

**Does this narrow the rotation?** No — the mechanism can only move
recently-touched tracks *down*, and the set it can promote is everything else
in the library. The real risk runs the other way: it will, for the first time,
surface the long tail of tracks that have **never** been played, and some of
those are unplayed for a reason (bad audio, wrong language tag, mis-ingested).
The candidate query already gates on `ingestion_status = 'done'` and a non-NULL
embedding, which removes the obvious garbage — but **backlog item 3.5** notes
that `FuseStage`'s blind `UPDATE` can mark a track `done` with no fused vector,
and those are silently absent from the pool anyway. Expect quality complaints
after this ships, and expect them not to be caused by this code.

**Pool exhaustion.** If POOL > the number of candidates, everything is in the
pool and the window spread is computed over whatever exists. If fewer than two
candidates exist, the degenerate guard (§1) skips re-ranking.

---

## 8. Tunable, and observable

### Tunable: module constants with environment overrides, read once at import

```python
_DJ_RECENCY_HALFLIFE_H = float(os.environ.get("DJ_RECENCY_HALFLIFE_H", "72"))
_DJ_SKIP_HALFLIFE_H    = float(os.environ.get("DJ_SKIP_HALFLIFE_H",    "168"))
_DJ_RECENCY_WEIGHT     = float(os.environ.get("DJ_RECENCY_WEIGHT",     "2.0"))
_DJ_RECENCY_POOL       = int(  os.environ.get("DJ_RECENCY_POOL",       "150"))
```

Rationale: defaults live in code so the behaviour is readable from the source,
but the env override lets two Cloud Run revisions of the **same image** be
compared against each other without a rebuild. Setting `DJ_RECENCY_WEIGHT=0`
is a complete, instant kill switch — worth having for a ranking change that
cannot be validated before it ships.

**Not** request-body parameters. A client-settable ranking knob becomes a
contract you can never change, and the frontend has no basis for choosing a
half-life.

### Observable: an opt-in `explain` field

Add `explain: Optional[bool] = False` to `SimilarBody`. When set, each track in
`tracks[]` gains:

```json
"explain": {
  "sim": 0.9532,
  "lambda": 0.0324,
  "p_play": 0.990,
  "p_skip": 1.623,
  "penalty": 0.0847,
  "final": 0.8685,
  "rank_before": 1,
  "rank_after": 37,
  "last_played": "2026-10-02 20:51:41",
  "last_skipped_at": "2026-10-02 20:51:41"
}
```

`rank_before` / `rank_after` are the most useful two fields and cost nothing to
compute. Without this the only way to evaluate the change is to listen and form
an opinion, which is how the 72 h constant becomes permanent by default.

**[frontend contract]** — additive and opt-in, so nothing breaks, but the
frontend owner should know the field exists.

**Keep `score` meaning cosine similarity.** The existing field is documented in
`_similar_dj` as similarity (`d["score"] = 1.0 - dist`) and the response order
already carries the ranking. Add `final_score` alongside rather than
redefining `score` — a silent change of meaning on an existing numeric field is
exactly backlog item 1.2's failure mode. (I grepped
`frontend-next/src/features/queue/` and found no consumer of `score`
(**verified**), so redefining it would probably be harmless today — which is
not a good enough reason.)

---

## 9. How we would know it is working

Every metric below is computable from `track_events` alone, with no new
instrumentation. Measure a 2-week window before and after.

**Primary — repeat rate (should fall).** Fraction of `play_start` events with
`source='dj'` whose `track_id` appeared in a `play_start` by the same user in
the preceding 7 days.

```sql
SELECT AVG(is_repeat) FROM (
  SELECT e.id,
         EXISTS (SELECT 1 FROM track_events p
                 WHERE p.user_id = e.user_id AND p.track_id = e.track_id
                   AND p.type = 'play_start' AND p.id < e.id
                   AND p.server_ts > datetime(e.server_ts, '-7 day')) AS is_repeat
  FROM track_events e
  WHERE e.type = 'play_start' AND e.source = 'dj'
    AND e.server_ts > datetime('now', '-14 day')
);
```

**Secondary — catalogue coverage (should rise).** `COUNT(DISTINCT track_id)`
of `play_start` per user per 30 days, as a fraction of their `user_tracks`
count. This is the "widening the rotation" claim stated numerically.

**Guardrail — DJ skip rate (must not rise).** `s_skip_count / s_end_count` per
user, from `user_track_stats`. This is the `s_` family doing the job it exists
for: grading the recommender. If repeat rate falls **and** skip rate rises, the
penalty is pushing past relevance into noise — lower `W`, do not lower `H`.

**Diagnostic — mean bail position on DJ picks.**
`s_skip_position_ms_sum / s_skip_count`, against mean `duration_ms`. It
separates the two ways a skip rate can rise: *earlier* bails mean the picks got
worse, *later* bails mean more variety and more partial listens, which is a
win wearing a loss's clothes.

**Offline, before shipping — rank displacement.** With `explain` on, replay a
set of seeds through both rankings and histogram `|rank_before − rank_after|`
for never-played, day-old and week-old candidates. This is how `W` gets tuned
**without** a deploy, and it is the only tuning that is possible right now.

### What cannot be tuned yet, plainly

The local DB holds **20 events spanning 35 minutes, from one user, one
session, all `dj_mode = 1`** (verified). You cannot estimate a 72-hour
half-life from 35 minutes of data; you cannot estimate a weekly repeat rate
from a single session; and with 11 rows in `user_track_stats` against 3,786
library tracks, the penalty would touch 0.3% of the candidate pool.

**Therefore: ship the defaults, ship `explain`, and do not touch the constants
until there are roughly 2,000 events spanning at least two weeks and at least
two distinct listening days per user.** Any "tuning" before that is fitting
noise, and it will bake in a number that looks authoritative because it has a
decimal point. The one thing worth doing immediately is the offline rank
displacement check, which needs no event data at all.

---

## 10. Things found while planning this, that are not part of it

Reported rather than fixed, per the house rule.

**A. Completed plays record `position_ms = 0` — `total_played_ms` is unusable.**
**Verified**: all 20 local events. Every `play_end` with `reason='completed'`
has `position_ms = 0` and `duration_ms = NULL`; only the two skips carry real
positions (66,252 and 171,229 ms). `user_track_stats.total_played_ms` is 0 for
every completed track.

The cause is in `frontend-next/src/lib/listenLog.js`, `endPlay()`. The code
already anticipates this — the comment explains that Spotify's inferred
`ended` leaves the SDK playhead reading zero — and patches it with:

```js
if (reason === 'completed' && duration != null) position = duration;
```

But the guard requires `duration != null`, and `duration` is `null` on every
one of these events. The wall-clock fallback above it (`position = Date.now() −
play.startedAt`) does not rescue it either, because it only fires when
`position == null`, and the SDK reported a *finite* 0. So a completed Spotify
play with unknown duration reports zero listening time.

Consequence for this plan: the "played to the end vs cut off at six seconds"
distinction in §5 **cannot** be computed from `total_played_ms / end_count`,
which is why §5 uses `skip_position_ms_sum` against `tracks.duration_ms`
instead. That route is unaffected. But the schema comment's
`mean_bail_ms` derivation is the only usable thing in that family right now,
and any future design that assumes `total_played_ms` means listening time will
be wrong. Frontend-owned file; flagging, not touching.

**B. The `u_*` family is empty; 100% of events are `vibe_source='system'`.**
**Verified**: 20/20 local events are `vibe_source='system', dj_mode=1`. Backlog
§5's instruction that `/similar` "should read the `u_*` family for preference"
is currently unimplementable — it would read all zeros. This is not necessarily
a bug (all the testing so far happened in DJ mode, where the slider is
system-driven by design), but it means the preference half of the split is
unvalidated end to end, and it should be confirmed with one manual slider drag
before anyone builds on `u_*`.

**C. `last_played` means "last touched", not "last listened".** It is written
on every accepted event including a skip and including a `play_end` with no
matching `play_start` (local event id 5 is exactly that). Harmless for this
design — arguably correct for it — but anyone reading the column as listening
history should know.

**D. Local event id 5 is an orphan `play_end`** with no preceding
`play_start`, so `end_count` can exceed `play_count` for a track. Expected
given the buffer can start mid-session; noted so it is not mistaken for drift
when `scripts/rebuild_user_track_stats.py` is next run.

---

## Summary of the decisions

| Question | Decision |
|---|---|
| Decay shape | Exponential, base-2 half-life |
| Play half-life | 72 h (3 days) |
| Skip half-life | 168 h (7 days), amplitude 0.5–2.0 by bail depth |
| Never played | `P = 0`, maximally fresh — never imputed |
| Combination | Subtractive: `sim − λ·P` |
| λ | `W · (sim[#1] − sim[#limit])`, window-relative, `W = 2.0` |
| Pool | `min(300, max(150, 12·limit))` |
| Turso cost | One `LEFT JOIN`, **zero** extra round trips, wider payload |
| numpy cost | One extra query per request, bounded by 30-day history |
| Vibe fallback | Same helper, `score = −distance`; must not be skipped |
| Cold start | Weighted random by `1/(1+P)` instead of uniform |
| Columns read | `last_played`, `last_skipped_at`, `u_+s_` skip count and position sum |
| Positive terms | None. Any future positive term must read `u_*` only |
| Tunability | Module constants, env-overridable; `W = 0` is the kill switch |
| Observability | Opt-in `explain` block; `score` keeps its current meaning |
| Guest row | Accept the shared history; document the one-line opt-out |
