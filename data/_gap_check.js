const { DatabaseSync } = require('node:sqlite');
const db = new DatabaseSync('D:/Git/virtual457-projects/VibeScape/data/vibescape.db');
const q = (sql, params = []) => db.prepare(sql).all(...params);

const uid = 10; // darshan

console.log("=== global tracks table totals ===");
console.table(q(`
  SELECT
    COUNT(*)                                                     AS all_tracks,
    SUM(CASE WHEN audio_path IS NOT NULL THEN 1 ELSE 0 END)      AS with_audio,
    SUM(CASE WHEN vibe_score_ml IS NOT NULL THEN 1 ELSE 0 END)   AS with_vibe_ml,
    SUM(CASE WHEN classification_source = 'ml_mert' THEN 1 ELSE 0 END) AS from_modal,
    SUM(CASE WHEN classification_source LIKE 'itunes%' THEN 1 ELSE 0 END) AS from_itunes,
    SUM(CASE WHEN classification_source = 'none' OR classification_source IS NULL THEN 1 ELSE 0 END) AS bare
  FROM tracks
`));

console.log("=== darshan linked vs global ===");
console.table(q(`
  SELECT
    (SELECT COUNT(*) FROM tracks)                                   AS global_total,
    (SELECT COUNT(*) FROM user_tracks WHERE user_id = ?)            AS darshan_linked,
    (SELECT COUNT(*) FROM tracks) - (SELECT COUNT(*) FROM user_tracks WHERE user_id = ?) AS not_linked_to_darshan
`, [uid, uid]));

console.log("=== user_tracks by user (who has what) ===");
console.table(q(`
  SELECT u.display_name, COUNT(ut.track_id) AS linked
  FROM users u LEFT JOIN user_tracks ut ON ut.user_id = u.id
  GROUP BY u.id
  ORDER BY linked DESC
`));

console.log("=== sample of tracks that exist globally but darshan DOES NOT have linked ===");
console.table(q(`
  SELECT t.spotify_id, t.title, t.artist, t.classification_source, t.audio_path IS NOT NULL AS has_audio
  FROM tracks t
  WHERE NOT EXISTS (SELECT 1 FROM user_tracks ut WHERE ut.user_id = ? AND ut.track_id = t.id)
  ORDER BY t.id DESC
  LIMIT 10
`, [uid]));

console.log("=== most recent ingest job status (from JOBS table if any) ===");
const tables = q("SELECT name FROM sqlite_master WHERE type='table'").map(r => r.name);
console.log("Tables:", tables.join(", "));
