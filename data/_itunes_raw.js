// Dump raw iTunes responses to see WHY ISRC lookups miss for tracks that
// definitely exist on iTunes (Hips Don't Lie, etc.)

const CASES = [
  { title: "Hips Don't Lie",         isrc: "USSM10600677" },
  { title: "Careless Whisper",       isrc: "GBBBM8402006" },
  { title: "Apologize",              isrc: "USUM70757102" },
  { title: "Perfect (Ed Sheeran)",   isrc: "GBAHS1700024" },
  { title: "Bad Habits",             isrc: "GBAHS2100318" },
  // Indian tracks — should truly miss
  { title: "Saadi Galli Aaja",       isrc: "INS181302177" },
  { title: "Elevated",               isrc: "QZNWT2102336" },
];

(async () => {
  for (const c of CASES) {
    const url = `https://itunes.apple.com/lookup?isrc=${c.isrc}&entity=song`;
    const r = await fetch(url);
    const j = await r.json();
    const withPreview = (j.results || []).filter(x => x.previewUrl);
    const anyResults = (j.results || []);
    console.log(`\n${c.title.padEnd(35)} isrc=${c.isrc}`);
    console.log(`  resultCount=${j.resultCount}  results=${anyResults.length}  with_preview=${withPreview.length}`);
    if (anyResults.length && !withPreview.length) {
      console.log(`  ⚠ has results but no previewUrl!`);
      console.log(`  sample: kind=${anyResults[0].kind} wrapperType=${anyResults[0].wrapperType} name=${anyResults[0].trackName || anyResults[0].collectionName}`);
    }
    if (withPreview.length) {
      console.log(`  ✓ ${withPreview[0].trackName} — ${withPreview[0].artistName}`);
    }
  }
})();
