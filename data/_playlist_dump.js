// Dump every unique (id, title, artist, position) that Spotify returns for
// the playlist. Also dumps the raw first-page JSON structure for inspection.

const TOKEN = process.env.SPOTIFY_TOKEN;
const PLAYLIST_ID = process.env.PLAYLIST_ID || '0MJUvoYH65rFHpMYijHqH8';
const BASE = 'https://api.spotify.com/v1';

async function get(url) {
  const r = await fetch(url, { headers: { Authorization: `Bearer ${TOKEN}` } });
  if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 200)}`);
  return r.json();
}

async function main() {
  if (!TOKEN) throw new Error('SPOTIFY_TOKEN env var required');

  // Playlist meta
  const meta = await get(`${BASE}/playlists/${PLAYLIST_ID}`);
  console.log('=== PLAYLIST META ===');
  console.log('name:', meta.name);
  console.log('owner:', meta.owner?.display_name);
  console.log('tracks.total (what Spotify claims):', meta.tracks?.total);
  console.log();

  // Raw page 1 (just first 3 items) so we can see the actual shape
  console.log('=== RAW PAGE 1 — first 3 items (verify field shape) ===');
  const p1 = await get(`${BASE}/playlists/${PLAYLIST_ID}/items?limit=3`);
  console.log(JSON.stringify(p1.items.slice(0, 3), null, 2).slice(0, 4000));
  console.log();

  // Walk every page, collect (position, id, name, artist)
  console.log('=== WALKING ALL PAGES ===');
  const allSlots = [];         // [{pos, id, name, artist}]
  const uniqueByIdMap = new Map(); // id -> {name, artist, count}
  let url = `${BASE}/playlists/${PLAYLIST_ID}/items?limit=50&offset=0`;
  let page = 0;
  let pos = 0;
  while (url) {
    page++;
    const data = await get(url);
    const items = data.items || [];
    for (const it of items) {
      pos++;
      const t = it.track || it.item;
      const id = t?.id || null;
      const name = t?.name || '';
      const artist = (t?.artists && t.artists[0]?.name) || '';
      allSlots.push({ pos, id, name, artist });
      if (id) {
        const prev = uniqueByIdMap.get(id);
        if (prev) prev.count++;
        else uniqueByIdMap.set(id, { name, artist, count: 1 });
      }
    }
    url = data.next;
  }

  console.log(`Total slots walked: ${allSlots.length}`);
  console.log(`Unique spotify_ids: ${uniqueByIdMap.size}`);
  console.log();

  // List unique tracks with their occurrence counts
  console.log('=== ALL UNIQUE TRACKS (id | count | title — artist) ===');
  const unique = [...uniqueByIdMap.entries()].sort((a, b) => b[1].count - a[1].count);
  unique.forEach(([id, info], idx) => {
    console.log(`${(idx + 1).toString().padStart(3)} | ${id} | x${info.count.toString().padStart(3)} | ${info.name} — ${info.artist}`);
  });

  // Sample of raw slot positions to prove positions really do have those IDs
  console.log('\n=== FIRST 15 SLOT POSITIONS (raw) ===');
  allSlots.slice(0, 15).forEach((s) => console.log(`  pos=${s.pos.toString().padStart(4)} id=${s.id} ${s.name} — ${s.artist}`));

  console.log('\n=== SLOT POSITIONS 500-514 (raw) ===');
  allSlots.slice(499, 515).forEach((s) => console.log(`  pos=${s.pos.toString().padStart(4)} id=${s.id} ${s.name} — ${s.artist}`));

  console.log('\n=== SLOT POSITIONS 1100-1114 (raw) ===');
  allSlots.slice(1099, 1115).forEach((s) => console.log(`  pos=${s.pos.toString().padStart(4)} id=${s.id} ${s.name} — ${s.artist}`));
}

main().catch((e) => {
  console.error('ERROR:', e.message);
  process.exit(1);
});
