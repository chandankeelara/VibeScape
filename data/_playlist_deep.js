// Dump every item's type/track/episode fields. Log any weirdness.

const TOKEN = process.env.SPOTIFY_TOKEN;
const PLAYLIST_ID = '0MJUvoYH65rFHpMYijHqH8';
const BASE = 'https://api.spotify.com/v1';

async function get(url) {
  const r = await fetch(url, { headers: { Authorization: `Bearer ${TOKEN}` } });
  if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 200)}`);
  return r.json();
}

async function main() {
  console.log('=== per-item deep inspection ===');
  const typeCount = {};
  const uniqueIds = new Set();
  const uniqueUris = new Set();
  const uniqueNames = new Set();
  const perTypeExamples = {};
  let totalSlots = 0;
  let itemsWithoutId = 0;
  let episodesFound = 0;

  let url = `${BASE}/playlists/${PLAYLIST_ID}/items?limit=50`;
  while (url) {
    const data = await get(url);
    for (const it of data.items || []) {
      totalSlots++;
      // Try both possible field names
      const t = it.track || it.item;
      if (!t) { itemsWithoutId++; continue; }
      const type = t.type || 'unknown';
      typeCount[type] = (typeCount[type] || 0) + 1;
      if (t.id) uniqueIds.add(t.id);
      if (t.uri) uniqueUris.add(t.uri);
      const name = t.name || '(no name)';
      const artist = (t.artists && t.artists[0]?.name) || (t.show?.name) || '(no artist)';
      uniqueNames.add(`${name} — ${artist}`);
      if (!perTypeExamples[type]) perTypeExamples[type] = [];
      if (perTypeExamples[type].length < 3) {
        perTypeExamples[type].push({ id: t.id, uri: t.uri, name, artist, has_track_wrapper: !!it.track, has_item_wrapper: !!it.item });
      }
      if (type === 'episode') episodesFound++;
    }
    url = data.next;
  }

  console.log(`Total slots walked: ${totalSlots}`);
  console.log(`Items with no track/item field: ${itemsWithoutId}`);
  console.log(`Unique IDs: ${uniqueIds.size}`);
  console.log(`Unique URIs: ${uniqueUris.size}`);
  console.log(`Unique (name — artist) strings: ${uniqueNames.size}`);
  console.log();
  console.log('By type:');
  console.log(typeCount);
  console.log();
  console.log('Sample per type:');
  for (const [type, samples] of Object.entries(perTypeExamples)) {
    console.log(`  --- ${type} ---`);
    samples.forEach((s) => console.log(`    ${JSON.stringify(s)}`));
  }

  console.log(`\nEpisodes found: ${episodesFound}`);
  console.log(`\nUnique NAMES: ${uniqueNames.size} vs unique IDs: ${uniqueIds.size}`);
  if (uniqueNames.size > uniqueIds.size) {
    console.log('=> Some items have same ID but different name (shouldn\'t happen).');
  }
  if (uniqueNames.size < uniqueIds.size) {
    console.log('=> Some items have unique IDs but the SAME (name — artist). Different re-releases of the same track.');
  }
}

main().catch((e) => { console.error(e.message); process.exit(1); });
