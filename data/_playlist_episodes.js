// Try requesting /items with additional_types=track,episode to see if
// there are podcast episodes hidden in the playlist.

const TOKEN = process.env.SPOTIFY_TOKEN;
const PLAYLIST_ID = '0MJUvoYH65rFHpMYijHqH8';
const BASE = 'https://api.spotify.com/v1';

async function get(url) {
  const r = await fetch(url, { headers: { Authorization: `Bearer ${TOKEN}` } });
  if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 200)}`);
  return r.json();
}

async function walk(qs) {
  const uniqueIds = new Set();
  const uniqueNames = new Set();
  const typeCount = {};
  let total = 0;
  let nullItems = 0;
  const examples = { track: [], episode: [], unknown: [], null: [] };

  let url = `${BASE}/playlists/${PLAYLIST_ID}/items?limit=50&${qs}`;
  while (url) {
    const data = await get(url);
    for (const it of data.items || []) {
      total++;
      const t = it.track || it.item;
      if (!t) {
        nullItems++;
        if (examples.null.length < 3) examples.null.push({ added_at: it.added_at, is_local: it.is_local, rawKeys: Object.keys(it) });
        continue;
      }
      const type = t.type || 'unknown';
      typeCount[type] = (typeCount[type] || 0) + 1;
      if (t.id) uniqueIds.add(t.id);
      const name = t.name || '(no name)';
      const artist = (t.artists && t.artists[0]?.name) || (t.show?.name) || '(no artist)';
      uniqueNames.add(`${name} — ${artist}`);
      const key = type === 'track' || type === 'episode' ? type : 'unknown';
      if (examples[key] && examples[key].length < 3) {
        examples[key].push({ id: t.id, name, artist, duration_ms: t.duration_ms, type });
      }
    }
    url = data.next;
  }

  return { qs, total, nullItems, uniqueIds: uniqueIds.size, uniqueNames: uniqueNames.size, typeCount, examples };
}

async function main() {
  console.log('=== A: no additional_types ===');
  console.log(JSON.stringify(await walk(''), null, 2));

  console.log('\n=== B: additional_types=track,episode ===');
  console.log(JSON.stringify(await walk('additional_types=track,episode'), null, 2));

  console.log('\n=== C: additional_types=episode ===');
  console.log(JSON.stringify(await walk('additional_types=episode'), null, 2));
}

main().catch((e) => { console.error(e.message); process.exit(1); });
