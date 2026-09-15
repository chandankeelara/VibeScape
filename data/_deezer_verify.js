// Side-by-side check: iTunes term search vs Deezer ISRC lookup vs Deezer
// search, for the exact tracks that failed iTunes in darshan's sync.

const CASES = [
  // The 5 tracks the log showed as no_preview
  { title: "Saadi Galli Aaja",       artist: "Rochak Kohli",    isrc: "INS181302177" },
  { title: "Khairiyat (Bonus Track)",artist: "Pritam",          isrc: "INS181904667" },
  { title: "Let Her Go x Husn",      artist: "Lewis Hanton",    isrc: "GX89G2479648" },
  { title: "Husna",                  artist: "Piyush Mishra",   isrc: "INE182302428" },
  { title: "Elevated",               artist: "Shubh",           isrc: "QZNWT2102336" },
  // Others from the log that landed via fast-path (chandan had them)
  { title: "Kinni Kinni",            artist: "Diljit Dosanjh",  isrc: "TCAHK2379648" },
  { title: "Pasoori",                artist: "Ali Sethi",       isrc: "FR10S2289473" },
  { title: "Pehle Bhi Main",         artist: "Vishal Mishra",   isrc: "INS182303389" },
  { title: "295",                    artist: "Sidhu Moose Wala",isrc: "INU252102408" },
  { title: "Sajni",                  artist: "Arijit Singh",    isrc: "INS182400369" },
  // Known Western hits
  { title: "Bad Habits",             artist: "Ed Sheeran",      isrc: "GBAHS2100318" },
  { title: "Perfect",                artist: "Ed Sheeran",      isrc: "GBAHS1700024" },
];

async function itunesTerm(title, artist) {
  const url = `https://itunes.apple.com/search?term=${encodeURIComponent(title + ' ' + artist)}&media=music&entity=song&limit=1`;
  try {
    const r = await fetch(url);
    const j = await r.json();
    const t = (j.results || [])[0];
    return t?.previewUrl ? 'YES' : 'no';
  } catch (e) { return 'err'; }
}

async function deezerIsrc(isrc) {
  const url = `https://api.deezer.com/track/isrc:${encodeURIComponent(isrc)}`;
  try {
    const r = await fetch(url);
    const j = await r.json();
    if (j.error) return 'no';
    return j.preview ? 'YES' : (j.id ? '(no preview)' : 'no');
  } catch (e) { return 'err'; }
}

async function deezerSearch(title, artist) {
  const url = `https://api.deezer.com/search?q=${encodeURIComponent(`artist:"${artist}" track:"${title}"`)}&limit=1`;
  try {
    const r = await fetch(url);
    const j = await r.json();
    const t = (j.data || [])[0];
    return t?.preview ? 'YES' : 'no';
  } catch (e) { return 'err'; }
}

(async () => {
  console.log("Track — Artist                                  | iTunes term | Deezer ISRC   | Deezer search");
  console.log("------------------------------------------------|-------------|---------------|--------------");
  let itunesHits = 0, deezerIsrcHits = 0, deezerSearchHits = 0, deezerAny = 0;
  for (const c of CASES) {
    const [it, dz, ds] = await Promise.all([
      itunesTerm(c.title, c.artist),
      deezerIsrc(c.isrc),
      deezerSearch(c.title, c.artist),
    ]);
    if (it === 'YES') itunesHits++;
    if (dz === 'YES') deezerIsrcHits++;
    if (ds === 'YES') deezerSearchHits++;
    if (dz === 'YES' || ds === 'YES') deezerAny++;
    const label = `${c.title} — ${c.artist}`.slice(0, 48).padEnd(48);
    console.log(`${label}| ${it.padEnd(11)} | ${dz.padEnd(13)} | ${ds}`);
  }
  console.log(`\nSummary (${CASES.length} tracks):`);
  console.log(`  iTunes term search hits:  ${itunesHits}/${CASES.length}`);
  console.log(`  Deezer ISRC hits:         ${deezerIsrcHits}/${CASES.length}`);
  console.log(`  Deezer search hits:       ${deezerSearchHits}/${CASES.length}`);
  console.log(`  Deezer combined (any):    ${deezerAny}/${CASES.length}`);
})();
