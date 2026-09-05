// Spot-check iTunes for the specific tracks that log showed as hit=False.
// Uses public iTunes Search API — no auth needed.

const CASES = [
  { title: "Saadi Galli Aaja", artist: "Rochak Kohli",     isrc: "INS181302177" },
  { title: "Khairiyat (Bonus Track)", artist: "Pritam",    isrc: "INS181904667" },
  { title: "Let Her Go x Husn", artist: "Lewis Hanton",    isrc: "GX89G2479648" },
  { title: "Husna", artist: "Piyush Mishra",               isrc: "INE182302428" },
  { title: "Elevated", artist: "Shubh",                    isrc: "QZNWT2102336" },
  // Also try tracks that DID work (as a sanity check)
  { title: "Tera Hone Laga Hoon", artist: "Pritam",        isrc: null },
  { title: "Hips Don't Lie", artist: "Shakira",            isrc: "USSM10600677" },
];

async function itunesLookupByIsrc(isrc) {
  const url = `https://itunes.apple.com/lookup?isrc=${encodeURIComponent(isrc)}&entity=song`;
  const r = await fetch(url);
  const j = await r.json();
  return (j.results || []).filter(x => x.previewUrl);
}

async function itunesSearch(term) {
  const url = `https://itunes.apple.com/search?media=music&entity=song&limit=5&term=${encodeURIComponent(term)}`;
  const r = await fetch(url);
  const j = await r.json();
  return (j.results || []);
}

(async () => {
  console.log("Track                                     | ISRC hit | Term hit (top match)                         | Preview?");
  console.log("------------------------------------------|---------|----------------------------------------------|---------");
  for (const c of CASES) {
    // ISRC lookup
    let isrcHit = "—";
    if (c.isrc) {
      try {
        const r = await itunesLookupByIsrc(c.isrc);
        isrcHit = r.length ? `YES (${r[0].trackName?.slice(0, 24) || '?'})` : "no";
      } catch (e) { isrcHit = "err"; }
    }

    // Term search
    let termHit = "no", preview = "no";
    try {
      const results = await itunesSearch(`${c.title} ${c.artist}`);
      if (results.length) {
        const top = results[0];
        termHit = `${(top.trackName || '?').slice(0, 22)} / ${(top.artistName || '?').slice(0, 18)}`;
        preview = top.previewUrl ? "yes" : "no";
      }
    } catch (e) { termHit = "err"; }

    const line = `${(c.title + ' — ' + c.artist).slice(0, 41).padEnd(41)} | ${isrcHit.padEnd(7)} | ${termHit.padEnd(44)} | ${preview}`;
    console.log(line);
  }
})();
