// Parse the user's pasted UI text to count unique tracks precisely.
// The Spotify UI format per track (as pasted):
//   <title line>
//   <artist line(s)> (may span multiple lines separated by commas or "&")
//   <album line> (optional)
//   <blank>
//   <blank>
//   <duration mm:ss>
//   <blank>

const fs = require('fs');
const raw = fs.readFileSync('D:/Git/virtual457-projects/VibeScape/data/_ui_paste.txt', 'utf8');

// Split on duration lines (format: MM:SS at start of a line).
// Each block is one track ending in its duration.
const durationRe = /^\d+:\d{2}$/;

// Normalize a title for dedup: lowercase, strip parenthetical variants,
// collapse whitespace, remove common noise words.
function normTitle(t) {
  return t.toLowerCase()
    .replace(/[""'']/g, '')
    .replace(/\(.*?\)/g, '')     // strip "(feat. ...)", "(From ...)", etc.
    .replace(/\[.*?\]/g, '')     // strip "[...]"
    .replace(/[^a-z0-9]+/g, ' ') // collapse punctuation
    .trim()
    .replace(/\s+/g, ' ');
}

function normArtist(a) {
  // First artist only, lowercased, punctuation-stripped
  const first = a.split(/[,&]| and | ft\.? /i)[0] || a;
  return first.toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

const lines = raw.split(/\r?\n/).map(l => l.trim());
// Find duration line indices and collect the block above each.
const tracks = [];
let currentBlock = [];
for (const line of lines) {
  if (durationRe.test(line)) {
    // duration ends the previous block
    if (currentBlock.length) {
      tracks.push({ block: currentBlock.filter(l => l), duration: line });
    }
    currentBlock = [];
  } else {
    currentBlock.push(line);
  }
}

console.log(`Total blocks (tracks with a duration line): ${tracks.length}`);

// For each block, first non-empty line = title, second = first artist line.
const parsed = tracks.map(({ block, duration }, i) => {
  const filt = block.filter(l => l);
  const title = filt[0] || '(no title)';
  const artist = filt[1] || '(no artist)';
  return { pos: i + 1, title, artist, duration, key: normTitle(title) + '|' + normArtist(artist) };
});

const byKey = new Map();
parsed.forEach(p => {
  const prev = byKey.get(p.key);
  if (prev) prev.count++;
  else byKey.set(p.key, { title: p.title, artist: p.artist, count: 1 });
});

const uniques = [...byKey.entries()];
console.log(`Unique tracks (title|first-artist normalized): ${uniques.length}`);
console.log();
console.log('All unique tracks (sorted by count desc):');
uniques.sort((a, b) => b[1].count - a[1].count);
uniques.forEach(([key, info], i) => {
  console.log(`${(i+1).toString().padStart(3)}. x${info.count.toString().padStart(3)}  ${info.title.slice(0,50).padEnd(50)} | ${info.artist.slice(0,30)}`);
});
