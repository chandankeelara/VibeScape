const { DatabaseSync } = require('node:sqlite');
const db = new DatabaseSync('D:/Git/virtual457-projects/VibeScape/data/vibescape.db');
const q = (sql, params = []) => db.prepare(sql).all(...params);

console.log("=== global tracks by classification_source ===");
console.table(q(`
  SELECT COALESCE(classification_source, '(null)') AS source, COUNT(*) AS n
  FROM tracks GROUP BY classification_source ORDER BY n DESC
`));

console.log("\n=== global tracks total & audio coverage ===");
console.table(q(`
  SELECT
    COUNT(*)                                                     AS total_tracks,
    SUM(CASE WHEN audio_path IS NOT NULL THEN 1 ELSE 0 END)      AS with_audio,
    SUM(CASE WHEN audio_path IS NULL THEN 1 ELSE 0 END)          AS no_audio,
    SUM(CASE WHEN classification_source LIKE '%deezer%' THEN 1 ELSE 0 END) AS deezer_classified
  FROM tracks
`));

console.log("\n=== Saadi Galli Aaja (INS181302177) — Deezer hit earlier — is it linked to darshan? ===");
console.table(q(`
  SELECT t.spotify_id, t.title, t.artist, t.classification_source, t.audio_path,
         EXISTS(SELECT 1 FROM user_tracks ut WHERE ut.user_id = 10 AND ut.track_id = t.id) AS linked_darshan
  FROM tracks t
  WHERE t.title LIKE '%Saadi Galli%'
`));

console.log("\n=== recent tracks (highest ids) ===");
console.table(q(`
  SELECT id, spotify_id, title, artist, classification_source, audio_path
  FROM tracks ORDER BY id DESC LIMIT 10
`));

console.log("\n=== all users' track counts ===");
console.table(q(`
  SELECT u.display_name, COUNT(ut.track_id) AS linked
  FROM users u LEFT JOIN user_tracks ut ON ut.user_id = u.id
  GROUP BY u.id ORDER BY linked DESC
`));
