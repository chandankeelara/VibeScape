// Try ISRC as a search TERM instead of a lookup parameter.

const CASES = [
  { title: "Perfect (Ed Sheeran)",   isrc: "GBAHS1700024" },
  { title: "Careless Whisper",       isrc: "GBBBM8402006" },
  { title: "Bad Habits",             isrc: "GBAHS2100318" },
  { title: "Apologize",              isrc: "USUM70757102" },
  { title: "Elevated (Shubh)",       isrc: "QZNWT2102336" },
  { title: "Saadi Galli Aaja",       isrc: "INS181302177" },
  { title: "Khairiyat (Pritam)",     isrc: "INS181904667" },
];

(async () => {
  for (const c of CASES) {
    // Method A: lookup?isrc=
    const rA = await fetch(`https://itunes.apple.com/lookup?isrc=${c.isrc}&entity=song`);
    const jA = await rA.json();

    // Method B: search?term=<isrc>
    const rB = await fetch(`https://itunes.apple.com/search?term=${c.isrc}&media=music&entity=song&limit=5`);
    const jB = await rB.json();

    console.log(`\n${c.title}  (isrc=${c.isrc})`);
    console.log(`  lookup?isrc=  -> resultCount=${jA.resultCount}`);
    console.log(`  search?term=  -> resultCount=${jB.resultCount}`);
    if (jB.results?.length) {
      const t = jB.results[0];
      console.log(`    top: ${t.trackName || t.collectionName || '?'} / ${t.artistName || '?'}   preview=${t.previewUrl ? 'yes' : 'no'}`);
    }
  }
})();
