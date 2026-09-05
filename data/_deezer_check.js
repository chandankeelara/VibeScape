// Post-sync spot-check: dump darshan's (user_id=10) tracks whose current
// classification_source is one of the sources that "actually succeeded" in
// the pre-Deezer world — itunes_term_search or ml_mert. Useful as a
// baseline to diff against after re-syncing with the new Deezer cascade.
//
// Usage:
//   node data/_deezer_check.js            # dumps to stdout
//   node data/_deezer_check.js --json     # emits JSON array
//
// Extend the SOURCES list to include new post-sync sources when comparing:
//   const SOURCES = ["itunes_term_search","ml_mert","deezer_isrc","deezer_search","metadata_only"];

const path = require("path");
const Database = (() => {
  try { return require("better-sqlite3"); } catch (_) { return null; }
})();

const DB_PATH = path.join(__dirname, "vibescape.db");
const USER_ID = 10;
const SOURCES = ["itunes_term_search", "ml_mert"];
const ASJSON = process.argv.includes("--json");

if (!Database) {
  console.error("better-sqlite3 not installed. Try: npm i better-sqlite3");
  process.exit(2);
}

const db = new Database(DB_PATH, { readonly: true });

const placeholders = SOURCES.map(() => "?").join(",");
const sql = `
  SELECT t.id, t.spotify_id, t.title, t.artist, t.album,
         t.classification_source, t.preview_url, t.audio_path,
         t.mood, t.activation, t.valence
    FROM tracks t
    JOIN user_tracks ut ON ut.track_id = t.id
   WHERE ut.user_id = ?
     AND t.classification_source IN (${placeholders})
   ORDER BY t.artist COLLATE NOCASE, t.title COLLATE NOCASE
`;

const rows = db.prepare(sql).all(USER_ID, ...SOURCES);

if (ASJSON) {
  process.stdout.write(JSON.stringify(rows, null, 2) + "\n");
} else {
  console.log(`user_id=${USER_ID}  source IN (${SOURCES.join(", ")})  -> ${rows.length} tracks`);
  console.log("-".repeat(80));
  for (const r of rows) {
    console.log(
      `[${r.classification_source.padEnd(18)}] ${r.artist} — ${r.title}`
      + (r.mood ? `  (${r.mood})` : "")
      + (r.preview_url ? "" : "  [no preview_url]")
    );
  }
  console.log("-".repeat(80));
  const bySource = rows.reduce((acc, r) => {
    acc[r.classification_source] = (acc[r.classification_source] || 0) + 1;
    return acc;
  }, {});
  for (const [src, n] of Object.entries(bySource)) {
    console.log(`  ${src}: ${n}`);
  }
}

db.close();
