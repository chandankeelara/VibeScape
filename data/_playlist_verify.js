// Verify: does Spotify's /playlists/{id}/items ACTUALLY have 1115 unique
// tracks, or is the endpoint serving duplicates?
//
// Tests four things:
//   1. Follow `next` URL like our _paginate does — count unique IDs seen
//   2. Query explicit offsets (0, 250, 500, 750, 1000) — different results?
//   3. Query offset=0 twice — same result twice (cache invariant)?
//   4. Return the raw playlist metadata to confirm the API's `total` value
//
// Usage: set TOKEN below, then `node data/_playlist_verify.js`

const TOKEN = process.env.SPOTIFY_TOKEN || 'PASTE_TOKEN_HERE';
const PLAYLIST_ID = '0MJUvoYH65rFHpMYijHqH8';
const BASE = 'https://api.spotify.com/v1';

async function get(url) {
  const r = await fetch(url, { headers: { Authorization: `Bearer ${TOKEN}` } });
  if (!r.ok) {
    throw new Error(`${r.status} ${r.statusText}: ${(await r.text()).slice(0, 200)}`);
  }
  return r.json();
}

async function main() {
  if (TOKEN === 'PASTE_TOKEN_HERE') {
    console.error('Set SPOTIFY_TOKEN env var or paste token into script.');
    process.exit(1);
  }

  // (0) Playlist metadata
  console.log('=== playlist metadata ===');
  const meta = await get(`${BASE}/playlists/${PLAYLIST_ID}?fields=id,name,tracks(total),owner(id,display_name)`);
  console.log(JSON.stringify(meta, null, 2));

  // (1) Follow next like _paginate does
  console.log('\n=== (1) follow next URL (like our _paginate) ===');
  const seenIds1 = new Set();
  const orderedIds1 = [];
  let url1 = `${BASE}/playlists/${PLAYLIST_ID}/items?limit=50`;
  let page = 0;
  while (url1) {
    page++;
    const data = await get(url1);
    const items = data.items || [];
    for (const it of items) {
      const t = it.track || it.item;
      const id = t?.id;
      if (id) {
        orderedIds1.push(id);
        seenIds1.add(id);
      }
    }
    console.log(`  page ${page}: got ${items.length} items, unique-so-far=${seenIds1.size}, total-seen=${orderedIds1.length}, next=${data.next ? 'yes' : 'no'}`);
    url1 = data.next;
  }
  console.log(`  final: raw=${orderedIds1.length}  unique=${seenIds1.size}  dups=${orderedIds1.length - seenIds1.size}`);

  // (2) Query explicit offsets, spread across the playlist
  console.log('\n=== (2) explicit offsets ===');
  const offsets = [0, 100, 250, 500, 750, 1000];
  const idsByOffset = {};
  for (const off of offsets) {
    const data = await get(`${BASE}/playlists/${PLAYLIST_ID}/items?limit=10&offset=${off}`);
    const ids = (data.items || []).map((it) => (it.track || it.item)?.id).filter(Boolean);
    idsByOffset[off] = ids;
    console.log(`  offset=${off}: [${ids.slice(0, 5).join(', ')}...] (${ids.length} items)`);
  }
  const uniqueAcrossOffsets = new Set();
  Object.values(idsByOffset).flat().forEach((id) => uniqueAcrossOffsets.add(id));
  console.log(`  across ${offsets.length} offsets (${offsets.length * 10} items): ${uniqueAcrossOffsets.size} unique ids`);

  // (3) Query offset=0 twice, compare
  console.log('\n=== (3) same offset twice (cache stability) ===');
  const a = await get(`${BASE}/playlists/${PLAYLIST_ID}/items?limit=10&offset=0`);
  const b = await get(`${BASE}/playlists/${PLAYLIST_ID}/items?limit=10&offset=0`);
  const aIds = (a.items || []).map((it) => (it.track || it.item)?.id);
  const bIds = (b.items || []).map((it) => (it.track || it.item)?.id);
  console.log(`  call A: [${aIds.join(', ')}]`);
  console.log(`  call B: [${bIds.join(', ')}]`);
  console.log(`  match: ${aIds.join(',') === bIds.join(',')}`);

  // (4) Verdict
  console.log('\n=== VERDICT ===');
  console.log(`Playlist metadata says: tracks.total = ${meta.tracks?.total}`);
  console.log(`_paginate walk found:   ${orderedIds1.length} raw items, ${seenIds1.size} unique`);
  console.log(`Explicit offsets found: ${uniqueAcrossOffsets.size} unique across ${offsets.length} sampled offsets`);
  if (seenIds1.size < meta.tracks.total * 0.5) {
    console.log(`\nCONCLUSION: Spotify's paginated /items is returning duplicates.`);
    console.log(`The playlist may have ${meta.tracks.total} slots but only ~${seenIds1.size} unique tracks,`);
    console.log(`OR Spotify's pagination is broken for this playlist (compare with explicit-offset results above).`);
  } else {
    console.log(`\nCONCLUSION: pagination looks healthy — most items are unique.`);
  }
}

main().catch((e) => {
  console.error(e.message || e);
  process.exit(1);
});
