// Try iTunes ISRC lookup with country codes. The default country used to
// be US; maybe Apple changed the default or requires it now.

const isrc = "GBAHS1700024"; // Ed Sheeran - Perfect (definitely on iTunes)

const countries = ["", "US", "GB", "IN", "us", "gb"];

(async () => {
  for (const c of countries) {
    const url = c
      ? `https://itunes.apple.com/lookup?isrc=${isrc}&country=${c}&entity=song`
      : `https://itunes.apple.com/lookup?isrc=${isrc}&entity=song`;
    const r = await fetch(url);
    const j = await r.json();
    console.log(`country='${c}' -> resultCount=${j.resultCount}`);
    if (j.results?.length) {
      console.log(`  first: ${j.results[0].trackName || j.results[0].collectionName || '?'}`);
    }
  }

  // Also try the search endpoint directly on Ed Sheeran Perfect
  console.log("\n--- search fallback ---");
  const searchUrl = `https://itunes.apple.com/search?term=perfect+ed+sheeran&entity=song&limit=1&country=US`;
  const r = await fetch(searchUrl);
  const j = await r.json();
  console.log(`resultCount=${j.resultCount}`);
  if (j.results?.length) {
    const t = j.results[0];
    console.log(`  trackName: ${t.trackName}`);
    console.log(`  isrc:      ${t.isrc || '(not in response)'}`);
    console.log(`  previewUrl:${t.previewUrl ? 'yes (' + t.previewUrl.slice(0,60) + '...)' : 'no'}`);
  }
})();
