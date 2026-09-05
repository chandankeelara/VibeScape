const { DatabaseSync } = require('node:sqlite');
const db = new DatabaseSync('D:/Git/virtual457-projects/VibeScape/data/vibescape.db');

function q(sql, params = []) {
  const stmt = db.prepare(sql);
  return stmt.all(...params);
}

const users = q("SELECT id, display_name, spotify_display_name, created_at FROM users ORDER BY id");
console.log("=== users ===");
console.table(users);

const dharshan = users.find(u => String(u.display_name).toLowerCase().includes('darsh'));
if (!dharshan) {
  console.log("No user found matching 'dharsh'.");
  process.exit(0);
}
const uid = dharshan.id;
console.log(`\n=== dharshan resolved: user_id=${uid} (display_name='${dharshan.display_name}') ===\n`);

const totals = q(`
  SELECT
    COUNT(*)                                                     AS linked,
    SUM(CASE WHEN t.audio_path IS NOT NULL THEN 1 ELSE 0 END)    AS with_audio,
    SUM(CASE WHEN t.vibe_score IS NOT NULL THEN 1 ELSE 0 END)    AS with_vibe_formula,
    SUM(CASE WHEN t.vibe_score_ml IS NOT NULL THEN 1 ELSE 0 END) AS with_vibe_ml,
    SUM(CASE WHEN t.audio_path IS NULL THEN 1 ELSE 0 END)        AS bare_no_audio,
    SUM(CASE WHEN t.language IS NOT NULL THEN 1 ELSE 0 END)      AS with_language
  FROM tracks t JOIN user_tracks ut ON ut.track_id = t.id
  WHERE ut.user_id = ?
`, [uid]);
console.log("=== totals ===");
console.table(totals);

const bySource = q(`
  SELECT COALESCE(t.classification_source, '(null)') AS source, COUNT(*) AS n
  FROM tracks t JOIN user_tracks ut ON ut.track_id = t.id
  WHERE ut.user_id = ?
  GROUP BY t.classification_source
  ORDER BY n DESC
`, [uid]);
console.log("\n=== by classification_source ===");
console.table(bySource);

const byMood = q(`
  SELECT COALESCE(t.mood, '(null)') AS mood, COUNT(*) AS n
  FROM tracks t JOIN user_tracks ut ON ut.track_id = t.id
  WHERE ut.user_id = ?
  GROUP BY t.mood
  ORDER BY n DESC
`, [uid]);
console.log("\n=== by mood ===");
console.table(byMood);

const bySrcAudio = q(`
  SELECT
    COALESCE(t.classification_source, '(null)') AS source,
    SUM(CASE WHEN t.audio_path IS NOT NULL THEN 1 ELSE 0 END) AS with_audio,
    SUM(CASE WHEN t.audio_path IS NULL     THEN 1 ELSE 0 END) AS no_audio,
    COUNT(*) AS total
  FROM tracks t JOIN user_tracks ut ON ut.track_id = t.id
  WHERE ut.user_id = ?
  GROUP BY t.classification_source
  ORDER BY total DESC
`, [uid]);
console.log("\n=== source × audio-present ===");
console.table(bySrcAudio);
