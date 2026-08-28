import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

# db_client sits next to this file in backend/. When the app runs from
# WORKDIR=/app/backend the module is importable directly; when db.py is
# imported as backend.db (e.g. from tests), fall back to the package path.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import db_client  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "vibescape.db"
SCHEMA_PATH = ROOT / "schema.sql"


def _table_info(conn: sqlite3.Connection, table: str):
    return conn.execute(f"PRAGMA table_info({table})").fetchall()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _has_column(conn: sqlite3.Connection, table: str, col: str) -> bool:
    return any(row[1] == col for row in _table_info(conn, table))


def _needs_rebuild(conn: sqlite3.Connection) -> bool:
    info = _table_info(conn, "tracks")
    if not info:
        return False
    cols = {row[1]: row for row in info}
    if "id" not in cols:
        return True
    apple = cols.get("apple_id")
    if apple and apple[5] == 1:
        return True
    return False


def _has_global_track_uniqueness(conn: sqlite3.Connection) -> bool:
    try:
        idxs = conn.execute("PRAGMA index_list(tracks)").fetchall()
    except sqlite3.OperationalError:
        return False
    for idx in idxs:
        name = idx[1]
        is_unique = int(idx[2]) == 1
        if not is_unique:
            continue
        try:
            cols = conn.execute(f"PRAGMA index_info({name})").fetchall()
        except sqlite3.OperationalError:
            continue
        col_names = [c[2] for c in cols]
        if col_names in (["apple_id"], ["spotify_id"]):
            return True
    return False


def _dedupe_spotify_ids(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        UPDATE tracks SET spotify_id = NULL
        WHERE spotify_id IS NOT NULL
          AND rowid NOT IN (
              SELECT MIN(rowid) FROM tracks
              WHERE spotify_id IS NOT NULL
              GROUP BY spotify_id
          )
        """
    )
    conn.commit()


def _rebuild_tracks_legacy_composite(conn: sqlite3.Connection) -> None:
    """
    Legacy rebuild path: single-user global-unique tracks -> per-user
    composite-unique tracks. Kept so intermediate legacy DBs that never
    saw the multi-user era can still climb the migration ladder. The
    subsequent split-tracks migration then rewrites this shape into the
    global tracks + user_tracks model.
    """
    _dedupe_spotify_ids(conn)
    conn.execute("BEGIN")
    try:
        conn.execute("ALTER TABLE tracks RENAME TO tracks_old")
        conn.execute(
            """
            CREATE TABLE tracks (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER REFERENCES users(id) ON DELETE CASCADE,
                apple_id      INTEGER,
                spotify_id    TEXT,
                isrc          TEXT,
                title         TEXT NOT NULL,
                artist        TEXT NOT NULL,
                album         TEXT,
                genre         TEXT,
                artwork_url   TEXT,
                preview_url   TEXT,
                track_view_url TEXT,
                duration_ms   INTEGER,
                tempo         REAL,
                energy        REAL,
                brightness    REAL,
                zcr           REAL,
                mfcc_json     TEXT,
                tempo_stability     REAL,
                onset_rate          REAL,
                energy_mean         REAL,
                energy_std          REAL,
                bandwidth           REAL,
                rolloff             REAL,
                spectral_contrast   REAL,
                flatness            REAL,
                timbre_variability  REAL,
                valence_mode        REAL,
                tonnetz_std         REAL,
                acousticness        REAL,
                chroma_mean_json    TEXT,
                activation          REAL,
                valence             REAL,
                activation_relative REAL,
                vibe_score    REAL NOT NULL,
                mood          TEXT,
                audio_path    TEXT,
                classification_source TEXT,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (user_id, spotify_id),
                UNIQUE (user_id, apple_id)
            )
            """
        )
        old_cols = {row[1] for row in _table_info(conn, "tracks_old")}
        carry = [c for c in [
            "user_id", "apple_id", "spotify_id", "isrc", "title", "artist",
            "album", "genre", "artwork_url", "preview_url", "track_view_url",
            "duration_ms",
            "tempo", "energy", "brightness", "zcr", "mfcc_json",
            "tempo_stability", "onset_rate", "energy_mean", "energy_std",
            "bandwidth", "rolloff", "spectral_contrast", "flatness",
            "timbre_variability", "valence_mode", "tonnetz_std", "acousticness",
            "chroma_mean_json",
            "activation", "valence", "activation_relative",
            "vibe_score", "mood", "audio_path", "classification_source",
            "created_at",
        ] if c in old_cols]
        col_list = ", ".join(carry)
        conn.execute(f"INSERT INTO tracks ({col_list}) SELECT {col_list} FROM tracks_old")
        conn.execute("DROP TABLE tracks_old")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


_EXTENDED_COLUMNS = [
    "tempo_stability",
    "onset_rate",
    "energy_mean",
    "energy_std",
    "bandwidth",
    "rolloff",
    "spectral_contrast",
    "flatness",
    "timbre_variability",
    "valence_mode",
    "tonnetz_std",
    "acousticness",
    "activation",
    "valence",
    "activation_relative",
]


DEFAULT_USER_NAME = "chandan"


def _seed_default_user(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM users WHERE display_name = ?", (DEFAULT_USER_NAME,)).fetchone()
    if row:
        return int(row[0])
    row = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
    if row:
        return int(row[0])
    cur = conn.execute(
        "INSERT INTO users (display_name, pin_hash) VALUES (?, NULL)",
        (DEFAULT_USER_NAME,),
    )
    conn.commit()
    return int(cur.lastrowid)


_CLASSIFICATION_PRIORITY = {
    "spotify_preview": 3,
    "itunes_isrc": 2,
    "itunes_term_search": 1,
    "unknown": 0,
    "none": 0,
    None: -1,
    "": -1,
}


def _pick_canonical(rows: list) -> int:
    """
    From a list of duplicate track rows, choose the canonical row id.
    Priority: highest classification_source rank, then latest created_at,
    then lowest id (deterministic tie-breaker).
    """
    def key(r):
        src = r["classification_source"] if "classification_source" in r.keys() else None
        prio = _CLASSIFICATION_PRIORITY.get(src, 0)
        return (-prio, -(_as_epoch(r["created_at"])), int(r["id"]))
    return sorted(rows, key=key)[0]["id"]


def _as_epoch(ts) -> float:
    if not ts:
        return 0.0
    if isinstance(ts, (int, float)):
        return float(ts)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return time.mktime(time.strptime(str(ts)[:19], fmt))
        except ValueError:
            continue
    return 0.0


_FEATURE_MERGE_COLS = [
    "isrc", "title", "artist", "album", "genre", "artwork_url", "preview_url",
    "track_view_url", "duration_ms",
    "tempo", "energy", "brightness", "zcr", "mfcc_json",
    "tempo_stability", "onset_rate", "energy_mean", "energy_std", "bandwidth",
    "rolloff", "spectral_contrast", "flatness", "timbre_variability",
    "valence_mode", "tonnetz_std", "acousticness", "chroma_mean_json",
    "activation", "valence", "activation_relative",
    "vibe_score", "mood", "audio_path", "classification_source",
]


def _dedup_key(row) -> str:
    """
    Primary group key. Preferred order: spotify_id, apple_id, isrc,
    (title|artist|album). Rows sharing this key are merged into one
    global track. A second pass merges any secondary collisions
    (e.g. two spotify_id-groups that share an apple_id or ISRC) via
    union-find so the global uniqueness constraints don't fire.
    """
    sid = row["spotify_id"]
    if sid:
        return f"s:{sid}"
    aid = row["apple_id"]
    if aid is not None:
        return f"a:{aid}"
    isrc = row["isrc"]
    if isrc:
        return f"i:{isrc}"
    t = (row["title"] or "").strip().lower()
    a = (row["artist"] or "").strip().lower()
    al = (row["album"] or "").strip().lower()
    return f"t:{t}|{a}|{al}"


class _UnionFind:
    def __init__(self):
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        p = self.parent.setdefault(x, x)
        if p == x:
            return x
        root = self.find(p)
        self.parent[x] = root
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _merge_secondary_collisions(groups: dict[str, list]) -> dict[str, list]:
    """
    Union groups that share an apple_id, spotify_id, or ISRC. Prevents the
    global uniqueness indexes from failing when the primary dedup key
    (usually spotify_id) misses a same-song collision on apple_id (e.g.
    two Spotify releases pointing at the same iTunes trackId).
    """
    uf = _UnionFind()
    for k in groups:
        uf.find(k)

    apple_seen: dict[int, str] = {}
    spotify_seen: dict[str, str] = {}
    isrc_seen: dict[str, str] = {}

    for k, rows in groups.items():
        for r in rows:
            aid = r["apple_id"]
            if aid is not None:
                other = apple_seen.get(aid)
                if other is not None:
                    uf.union(other, k)
                else:
                    apple_seen[aid] = k
            sid = r["spotify_id"]
            if sid:
                other = spotify_seen.get(sid)
                if other is not None:
                    uf.union(other, k)
                else:
                    spotify_seen[sid] = k
            isrc = r["isrc"]
            if isrc:
                other = isrc_seen.get(isrc)
                if other is not None:
                    uf.union(other, k)
                else:
                    isrc_seen[isrc] = k

    merged: dict[str, list] = {}
    for k, rows in groups.items():
        root = uf.find(k)
        merged.setdefault(root, []).extend(rows)
    return merged


def _migrate_split_tracks(conn: sqlite3.Connection, log_fn=print) -> dict:
    """
    Split the per-user `tracks` table into global `tracks` + `user_tracks`.
    Idempotent: no-op once user_tracks exists.

    Steps:
      1. Bail if user_tracks exists.
      2. Snapshot old counts.
      3. Build tracks_v2 with the target schema (no user_id, adds ml
         columns + timestamps).
      4. For each dedup key across old rows, insert one canonical row into
         tracks_v2; backfill NULLs from siblings; record audio_path
         conflicts.
      5. Build user_tracks and populate from every old (user_id, track_id)
         pair mapped to the canonical tracks_v2.id.
      6. Drop old tracks, rename tracks_v2 -> tracks, build indexes.
    """
    if _table_exists(conn, "user_tracks"):
        return {"status": "already_migrated"}
    if not _table_exists(conn, "tracks"):
        return {"status": "no_tracks_table"}
    if not _has_column(conn, "tracks", "user_id"):
        return {"status": "already_global_shape_but_no_user_tracks"}

    conn.row_factory = sqlite3.Row

    old_total = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    per_user_before = {
        int(r[0]) if r[0] is not None else None: int(r[1])
        for r in conn.execute(
            "SELECT user_id, COUNT(*) FROM tracks GROUP BY user_id"
        ).fetchall()
    }

    select_cols = (
        "id, user_id, spotify_id, apple_id, isrc, title, artist, album, "
        "genre, artwork_url, preview_url, track_view_url, duration_ms, "
        "tempo, energy, brightness, zcr, mfcc_json, "
        "tempo_stability, onset_rate, energy_mean, energy_std, bandwidth, "
        "rolloff, spectral_contrast, flatness, timbre_variability, "
        "valence_mode, tonnetz_std, acousticness, chroma_mean_json, "
        "activation, valence, activation_relative, "
        "vibe_score, mood, audio_path, classification_source, created_at"
    )
    all_rows = conn.execute(f"SELECT {select_cols} FROM tracks").fetchall()

    groups: dict[str, list] = {}
    for r in all_rows:
        groups.setdefault(_dedup_key(r), []).append(r)
    groups = _merge_secondary_collisions(groups)

    audio_path_conflicts = 0

    conn.execute("BEGIN")
    try:
        conn.execute(
            """
            CREATE TABLE tracks_v2 (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                spotify_id    TEXT,
                apple_id      INTEGER,
                isrc          TEXT,
                title         TEXT NOT NULL,
                artist        TEXT NOT NULL,
                album         TEXT,
                genre         TEXT,
                duration_ms   INTEGER,
                artwork_url   TEXT,
                preview_url   TEXT,
                track_view_url TEXT,
                tempo         REAL,
                energy        REAL,
                brightness    REAL,
                zcr           REAL,
                mfcc_json     TEXT,
                tempo_stability     REAL,
                onset_rate          REAL,
                energy_mean         REAL,
                energy_std          REAL,
                bandwidth           REAL,
                rolloff             REAL,
                spectral_contrast   REAL,
                flatness            REAL,
                timbre_variability  REAL,
                valence_mode        REAL,
                tonnetz_std         REAL,
                acousticness        REAL,
                chroma_mean_json    TEXT,
                activation          REAL,
                valence             REAL,
                activation_relative REAL,
                vibe_score    REAL,
                mood          TEXT,
                audio_path    TEXT,
                classification_source TEXT,
                energy_pred      REAL,
                danceability_pred REAL,
                valence_pred     REAL,
                vibe_score_ml    REAL,
                model_version    TEXT,
                features_extracted_at TIMESTAMP,
                ml_predicted_at       TIMESTAMP,
                created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE user_tracks (
                user_id     INTEGER NOT NULL REFERENCES users(id)  ON DELETE CASCADE,
                track_id    INTEGER NOT NULL REFERENCES tracks_v2(id) ON DELETE CASCADE,
                added_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                source      TEXT,
                play_count  INTEGER DEFAULT 0,
                last_played TIMESTAMP,
                PRIMARY KEY (user_id, track_id)
            )
            """
        )

        old_id_to_new: dict[int, int] = {}
        insert_cols = [
            "spotify_id", "apple_id", "isrc", "title", "artist", "album",
            "genre", "duration_ms", "artwork_url", "preview_url",
            "track_view_url",
            "tempo", "energy", "brightness", "zcr", "mfcc_json",
            "tempo_stability", "onset_rate", "energy_mean", "energy_std",
            "bandwidth", "rolloff", "spectral_contrast", "flatness",
            "timbre_variability", "valence_mode", "tonnetz_std", "acousticness",
            "chroma_mean_json",
            "activation", "valence", "activation_relative",
            "vibe_score", "mood", "audio_path", "classification_source",
            "created_at",
        ]
        placeholders = ", ".join(["?"] * len(insert_cols))
        insert_sql = (
            f"INSERT INTO tracks_v2 ({', '.join(insert_cols)}) "
            f"VALUES ({placeholders})"
        )

        for group in groups.values():
            canon_id = _pick_canonical(group)
            canon = next(r for r in group if r["id"] == canon_id)
            merged = {c: canon[c] for c in insert_cols if c != "spotify_id" and c != "apple_id"}
            merged["spotify_id"] = canon["spotify_id"]
            merged["apple_id"] = canon["apple_id"]

            for other in group:
                if other["id"] == canon_id:
                    continue
                for c in _FEATURE_MERGE_COLS:
                    if merged.get(c) in (None, "") and other[c] not in (None, ""):
                        merged[c] = other[c]
                if merged.get("spotify_id") in (None, "") and other["spotify_id"] not in (None, ""):
                    merged["spotify_id"] = other["spotify_id"]
                if merged.get("apple_id") in (None,) and other["apple_id"] is not None:
                    merged["apple_id"] = other["apple_id"]
                canon_ap = canon["audio_path"]
                other_ap = other["audio_path"]
                if canon_ap and other_ap and canon_ap != other_ap:
                    audio_path_conflicts += 1
                    log_fn(
                        f"[migrate] audio_path conflict for dedup group "
                        f"canon_id={canon_id}: kept={canon_ap!r} discarded={other_ap!r}"
                    )

            values = [merged.get(c) for c in insert_cols]
            cur = conn.execute(insert_sql, values)
            new_id = int(cur.lastrowid)
            for r in group:
                old_id_to_new[int(r["id"])] = new_id

        ut_rows = 0
        for r in all_rows:
            uid = r["user_id"]
            if uid is None:
                continue
            new_track_id = old_id_to_new.get(int(r["id"]))
            if new_track_id is None:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO user_tracks (user_id, track_id, added_at, source) "
                "VALUES (?, ?, ?, 'migrated')",
                (int(uid), new_track_id, r["created_at"]),
            )
            ut_rows += 1

        conn.execute("DROP TABLE tracks")
        conn.execute("ALTER TABLE tracks_v2 RENAME TO tracks")

        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_tracks_spotify_id "
            "ON tracks(spotify_id) WHERE spotify_id IS NOT NULL"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_tracks_apple_id "
            "ON tracks(apple_id) WHERE apple_id IS NOT NULL"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_vibe ON tracks(vibe_score)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_mood ON tracks(mood)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_activation ON tracks(activation)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_activation_rel ON tracks(activation_relative)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_tracks_user  ON user_tracks(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_tracks_track ON user_tracks(track_id)")

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    new_tracks = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    per_user_after = {
        int(r[0]): int(r[1])
        for r in conn.execute(
            "SELECT user_id, COUNT(*) FROM user_tracks GROUP BY user_id"
        ).fetchall()
    }

    summary = {
        "status": "migrated",
        "old_rows": old_total,
        "new_tracks": new_tracks,
        "user_tracks_rows": ut_rows,
        "dedupe_savings": old_total - new_tracks,
        "audio_path_conflicts": audio_path_conflicts,
        "per_user_before": per_user_before,
        "per_user_after": per_user_after,
    }
    log_fn(f"[migrate] split-tracks summary: {summary}")
    return summary


def _backup_db(reason: str) -> Path:
    if not DB_PATH.exists():
        raise RuntimeError(f"cannot backup: {DB_PATH} does not exist")
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    dst = DB_PATH.with_name(f"{DB_PATH.name}.{reason}-{ts}")
    shutil.copyfile(str(DB_PATH), str(dst))
    return dst


def _migrate(conn: sqlite3.Connection) -> None:
    stmts = [
        "ALTER TABLE tracks ADD COLUMN audio_path TEXT",
        "ALTER TABLE tracks ADD COLUMN spotify_id TEXT",
        "ALTER TABLE tracks ADD COLUMN classification_source TEXT",
        "ALTER TABLE tracks ADD COLUMN chroma_mean_json TEXT",
        "ALTER TABLE tracks ADD COLUMN user_id INTEGER",
        "ALTER TABLE tracks ADD COLUMN youtube_id TEXT",
        "ALTER TABLE tracks ADD COLUMN youtube_queried_at TIMESTAMP",
        "ALTER TABLE tracks ADD COLUMN energy_pred REAL",
        "ALTER TABLE tracks ADD COLUMN danceability_pred REAL",
        "ALTER TABLE tracks ADD COLUMN valence_pred REAL",
        "ALTER TABLE tracks ADD COLUMN vibe_score_ml REAL",
        "ALTER TABLE tracks ADD COLUMN model_version TEXT",
        "ALTER TABLE tracks ADD COLUMN language TEXT",
        "ALTER TABLE tracks ADD COLUMN language_confidence REAL",
        "ALTER TABLE tracks ADD COLUMN language_top3_json TEXT",
        "ALTER TABLE tracks ADD COLUMN language_model_version TEXT",
        "ALTER TABLE tracks ADD COLUMN language_predicted_at TIMESTAMP",
    ]
    for c in _EXTENDED_COLUMNS:
        stmts.append(f"ALTER TABLE tracks ADD COLUMN {c} REAL")
    for stmt in stmts:
        try:
            conn.execute(stmt)
            conn.commit()
        except sqlite3.OperationalError:
            pass

    for stmt in (
        "ALTER TABLE users ADD COLUMN spotify_user_id TEXT",
        "ALTER TABLE users ADD COLUMN spotify_display_name TEXT",
    ):
        try:
            conn.execute(stmt)
            conn.commit()
        except sqlite3.OperationalError:
            pass

    if _table_exists(conn, "tracks") and _has_column(conn, "tracks", "user_id"):
        default_uid = _seed_default_user(conn)
        conn.execute(
            "UPDATE tracks SET user_id = ? WHERE user_id IS NULL",
            (default_uid,),
        )
        conn.commit()

        if _needs_rebuild(conn) or _has_global_track_uniqueness(conn):
            _rebuild_tracks_legacy_composite(conn)
            conn.execute(
                "UPDATE tracks SET user_id = ? WHERE user_id IS NULL",
                (default_uid,),
            )
            conn.commit()

        _backfill_classification_source(conn)

    # Split per-user tracks into global tracks + user_tracks. Must run
    # BEFORE schema.sql executes CREATE UNIQUE INDEX on spotify_id/apple_id,
    # otherwise those indexes would fail against the still-duplicated
    # legacy layout. Backup only when there is actual work to do.
    needs_split = (
        _table_exists(conn, "tracks")
        and _has_column(conn, "tracks", "user_id")
        and not _table_exists(conn, "user_tracks")
    )
    if needs_split:
        backup_path = _backup_db("pre-user-tracks")
        try:
            _migrate_split_tracks(conn)
        except Exception:
            raise RuntimeError(
                f"split-tracks migration failed; DB unchanged from backup at {backup_path}"
            )


def _backfill_classification_source(conn: sqlite3.Connection) -> int:
    try:
        rows = conn.execute(
            "SELECT id, preview_url FROM tracks "
            "WHERE classification_source IS NULL OR classification_source = ''"
        ).fetchall()
    except sqlite3.OperationalError:
        return 0
    updates = 0
    for row in rows:
        pid, url = row[0], (row[1] or "").lower()
        if "p.scdn.co" in url:
            src = "spotify_preview"
        elif "itunes-assets" in url or "audio-ssl.itunes" in url or "mzaf_" in url:
            src = "itunes_term_search"
        elif not url:
            src = "none"
        else:
            src = "unknown"
        conn.execute("UPDATE tracks SET classification_source = ? WHERE id = ?", (src, pid))
        updates += 1
    if updates:
        conn.commit()
    return updates


def ensure_db():
    # Bootstrap uses the same backend as runtime queries. For sqlite this
    # is equivalent to the previous sqlite3.connect(DB_PATH); for
    # DB_BACKEND=turso this connects to the remote libSQL instance so
    # schema/migrations run there. The parent dir mkdir is still cheap on
    # the sqlite path and a no-op-safe on turso (path may not exist).
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = db_client.create_connection()
    try:
        _bootstrap_and_migrate(conn)
    finally:
        conn.close()


def _bootstrap_and_migrate(conn: sqlite3.Connection) -> None:
    """
    Fresh install: run schema.sql, which creates the target shape and is
    a no-op on subsequent runs (all CREATE IF NOT EXISTS).
    Existing legacy install: run migrations first so schema.sql's
    partial-unique indexes see a deduped tracks table.
    """
    has_legacy = _table_exists(conn, "tracks") and _has_column(conn, "tracks", "user_id")
    if has_legacy:
        _migrate(conn)
        schema = SCHEMA_PATH.read_text(encoding="utf-8")
        conn.executescript(schema)
        conn.commit()
    else:
        schema = SCHEMA_PATH.read_text(encoding="utf-8")
        conn.executescript(schema)
        _migrate(conn)
        conn.commit()


# Runtime connection factory used by FastAPI request handlers.
#
# Delegates to db_client.create_connection(), which picks between local
# sqlite3 and a libsql-client-backed adapter based on the DB_BACKEND env
# var. The returned object still exposes the same .execute() / .commit()
# / .rollback() / .close() surface + row_factory=sqlite3.Row semantics,
# so callers in backend/app.py continue to work unchanged.
def get_conn():
    ensure_db()
    return db_client.create_connection()
