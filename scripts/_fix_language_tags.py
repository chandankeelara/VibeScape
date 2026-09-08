"""
Correct Whisper-mispredicted language tags via a curated artist→language
map + title-substring patterns. Applies to both local sqlite and Turso
(reads DB_BACKEND env from _load_gcp_secrets.ps1). Dry-run by default.

Whisper's language head misfires often on musical audio — instrumentals
drift to random defaults (sa/km/nn/jw/sn/la/cy), and multilingual sung
tracks confuse it. This script uses knowledge of the artist and title
that Whisper doesn't have.

Run:
    D:/Softwares/MiniConda/python.exe scripts/_fix_language_tags.py             # dry run
    D:/Softwares/MiniConda/python.exe scripts/_fix_language_tags.py --apply     # local + turso
    D:/Softwares/MiniConda/python.exe scripts/_fix_language_tags.py --apply --local-only
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_REPO = Path(__file__).resolve().parents[1]
_LOCAL_DB = _REPO / "data" / "vibescape.db"
_PS1 = _REPO / "scripts" / "_load_gcp_secrets.ps1"


# Artists whose catalog is almost exclusively one language.
# Case-insensitive exact-match on tracks.artist (which is the first artist
# from Spotify's payload). Multi-artist tracks may not match — we fall
# through to title patterns for those.
ARTIST_TO_LANG = {
    # ---------------- Kannada ----------------
    "v. harikrishna":           "kn",
    "arjun janya":              "kn",
    "charanraj mr":             "kn",
    "b. ajaneesh loknath":      "kn",
    "sanjith hegde":            "kn",
    "vasuki vaibhav":           "kn",
    "judah sandhy":             "kn",
    "puneeth rajkumar":         "kn",
    "raghu dixit":              "kn",
    "chandan shetty":           "kn",
    "hemanth kumar":            "kn",
    "nakul abhyankar":          "kn",
    "ananya bhat":              "kn",
    "madhuri seshadri":         "kn",
    "bharath b j":              "kn",
    "harsha uppar":             "kn",
    "sumedh k":                 "kn",
    "kaviraj":                  "kn",
    "adithi sagar":             "kn",
    "hamsalekha":               "kn",
    "prem's":                   "kn",
    "vani harikrishna":         "kn",
    "prithvi rajkumar":         "kn",
    "guru kiran":               "kn",
    "santhosh venky":           "kn",
    "deepak blue":              "kn",
    # ---------------- Hindi/Bollywood ----------------
    "pritam":                   "hi",
    "sachin-jigar":             "hi",
    "shankar-ehsaan-loy":       "hi",
    "vishal-shekhar":           "hi",
    "vishal mishra":            "hi",
    "mithoon":                  "hi",
    "sonu nigam":               "hi",   # mostly Hindi in this catalog
    "armaan malik":             "hi",
    "neeti mohan":              "hi",
    "javed ali":                "hi",
    "rashid ali":               "hi",
    "shreya ghoshal":           "hi",   # multi-lang; correct per-title below
    "arijit singh":             "hi",
    "amit trivedi":             "hi",
    "gajendra verma":           "hi",
    "yo yo honey singh":        "hi",
    "himesh reshammiya":        "hi",
    "kk":                       "hi",
    "shaan":                    "hi",
    "asha bhosle":              "hi",
    "sadhana sargam":           "hi",   # often Hindi; title-checked below
    "jatin-lalit":              "hi",
    "prateek kuhad":            "hi",
    "jalraj":                   "hi",
    "sultana":                  "hi",
    "rekha bhardwaj":           "hi",
    "duncan laurence":          "en",
    # ---------------- Telugu ----------------
    "sid sriram":               "te",   # mostly Telugu, title-checked below
    "anup rubens":              "te",
    "prithvi harish":           "te",
    "srinidhi venkatesh":       "te",
    "thaman s":                 "te",
    "haricharan":               "te",   # multi; title-checked
    "ram miriyala":             "te",
    "jassie gift":              "kn",   # actually Malayalam mostly, but Kannada + Malayalam songs common
    # ---------------- Tamil ----------------
    "dhanush":                  "ta",
    "g. v. prakash":            "ta",
    "hiphop tamizha":           "ta",
    "silambarasan tr":          "ta",
    "anirudh ravichander":      "ta",   # mostly Tamil, some Telugu
    "yuvan shankar raja":       "ta",
    # ---------------- Punjabi / Urdu ----------------
    "dr zeus":                  "pa",
    "karan aujla":              "pa",
    "juss":                     "pa",
    "avvy sra":                 "pa",
    "ali sethi":                "ur",   # Pasoori is Urdu/Punjabi mix — closer to ur
    # ---------------- Malayalam ----------------
    "hesham abdul wahab":       "ml",
    "aju varghese":             "ml",
    "jakes bejoy":              "ml",
    "dhibu ninan thomas":       "ml",
    # ---------------- Western / English ----------------
    "martin garrix":            "en",
    "eliza rose":               "en",
    "elyotto":                  "en",
    "ptasinski":                "en",   # actually no lyrics — instrumental EDM
    "ogryzek":                  "en",
    "pxlwyse":                  "en",
    "slxughter":                "en",
    "willy william":            "en",
    "warriyo":                  "en",
    "mercury":                  "en",
    "nakama":                   "en",
    "eternxlkz":                "en",
    "d'angello & francis":      "en",
    "rj pasin":                 "en",
    "esdeekid":                 "en",
    "2hollis":                  "en",
    "lune":                     "en",
    "qmiir":                    "en",
    "raaban":                   "en",
    "hovia edwards":            "en",
    "kushagra":                 "en",
    "фрози":                    "en",   # 'frozy' — russian handle, produces English/instrumental
    "atlxs":                    "en",   # Brazilian phonk / instrumental
    "andromeda":                "pt",   # Brazilian phonk
    "zxkai":                    "pt",   # Brazilian phonk
    "ian asher":                "en",
    "walk the moon":            "en",
    "keane":                    "en",
    "maroon 5":                 "en",
    "adele":                    "en",
    "eminem":                   "en",
    "kanye west":               "en",
    "drake":                    "en",
    "britney spears":           "en",
    "farruko":                  "es",
    "lana del rey":             "en",
    "imagine dragons":          "en",
    "backstreet boys":          "en",
    "avicii":                   "en",
    "the xx":                   "en",
    "awolnation":               "en",
    "ed sheeran":               "en",
    "charlie puth":             "en",
    "pitbull":                  "en",
    "lil wayne":                "en",
    "zedd":                     "en",
    "sigala":                   "en",
    "j. cole":                  "en",
    "jack harlow":              "en",
    "notd":                     "en",
    "celina sharma":            "en",   # song has both Emiway hindi verse though
    "stromae":                  "fr",
    "lady gaga":                "en",
    "alan walker":              "en",
    "lil nas x":                "en",
    "mike perry":               "en",
    "sachin-jigar":             "hi",
    "kas:st":                   "en",   # French techno, but instrumental
    "trackgoneat":              "en",   # instrumental
    "friedrich habetler":       "en",   # instrumental gaming remix
    "logic":                    "en",
    "ayushmann khurrana":       "hi",
    "krishna das":              "sa",   # actual Sanskrit chants — keep!
    "ravindra upadhyay":        "sa",   # actual Sanskrit chants
    "luci rain":                "sa",   # Aigiri Nandini — Sanskrit stotra
    "rushi vakil":              "hi",   # Kathak Tarana — instrumental
    "shashwat sachdev":         "hi",
    "hitesh sonik":             "hi",
    "kanika kapoor":            "hi",
    "ritviz":                   "hi",
}

# Title-substring patterns → language. Case-insensitive. Applied AFTER
# artist rules when the artist wasn't in the map or the artist rule is
# too broad (multi-lang artists).
TITLE_LANG_HINTS = [
    # ---------------- Kannada (song titles) ----------------
    (r"\b(nee|neenu|nanna|hesare|malebillu|maleyagi|maleyali|sanchariyagu|sikkalu|"
     r"badukina|amrithadhare|bombay|karnataka|kannaduva|kannada|preetiya|hesari|"
     r"gange|hridaya|mele|kavithe|shakuntle|shaakuntle|kanmani|helide|nudi|"
     r"sridevi|hesaru|hesaridali|dhaari|tarali|katheyondu|hoo chandu|masthu|"
     r"upendra|mungaru|jotheyali|maayada|kaavalugaara|mohini|sridevi|maleyali|"
     r"hoovinantha|nadedaduva|hindeye|preethse|preetiya|nadeda|puttamallige|"
     r"neelavaru|niveditha|jeevithava|malebillu|beladingala|beladingale|prem's|"
     r"olavu|neenaadena|shararat|guruvara|ninyaarele|maleyali|manam|dheera|"
     r"jackie|marali|bandhu|jaagam|mele|nadedadava|karnataka|namma|kannada)\b", "kn"),
    (r"\bkannada\b", "kn"),
    # ---------------- Telugu ----------------
    (r"\b(chebutunna|maate vinadhuga|maate|nijame|manamohaka|neeli meghamulalo|"
     r"chaleya|manmadhuda|maari kannu|oh priya priya|neeli|priya|dola dola|"
     r"varisu|naa peru|kanaka|samajavaragamana|chaleya|jaragandi|verithanam|"
     r"tholi parichayama|thee thalapathy|butta bomma|butta|guruvaram|"
     r"aa gadidhurupadi|manasu manasu|manasu|nippulanti|nippulanti nirudyogi|"
     r"telugu|telangana)\b", "te"),
    (r"\btelugu\b", "te"),
    # ---------------- Tamil ----------------
    (r"\b(pori superoo|thalapathy|porkkalam|takkaru|humma|kolaveri|kollam|"
     r"varisu|jimikki|tamil|takkaru takkaru|maari|maari kannu|arrasu|arasu|"
     r"vaseegara|masakali|singam|bigil|dj pedithe)\b", "ta"),
    (r"\btamil\b", "ta"),
    # ---------------- Hindi ----------------
    (r"\b(kesariya|zamaane|dil chori|kabira|kabhi|jeena|pehle bhi|papa meri|"
     r"kudukku|bulaava|ishq bulaava|kabira|patakha|jee karda|sanam|shararat|"
     r"caller tune|jab tak|humma|bollywood|humsafar|humdum|ambarsariya|"
     r"kesariya|toofan|dastaan|softly|softly|milkha|bhaag milkha|dua|kya baat|"
     r"raabta|bones|toxic|tabaahi|tere vaaste|zara hatke|kolaveri|dil dhoondta|"
     r"mora saiyan|main dhoondne|dil se re|udd gaye|ved|animal|zindagi|"
     r"hindustani|hindi|bollywood|azhar|itni si baat|jaanam|jaani ve|"
     r"pachtaoge)\b", "hi"),
    (r"\bhindi\b", "hi"),
    (r"\b(anthem|attention|animals|arcade|bad romance|bad habits|bella ciao|"
     r"bad boy|celestial|animal|attention|blade|lobster|sail|arcade|"
     r"instrumental)\b.*", None),  # ambiguous — do not auto-assign
]


def _load_turso_creds() -> tuple[str, str]:
    text = _PS1.read_text(encoding="utf-8")
    url = re.search(r'\$turso_url\s*=\s*"([^"]+)"', text).group(1)
    tok = re.search(r'\$turso_token\s*=\s*"([^"]+)"', text).group(1)
    return url, tok


def infer_language(title: str, artist: str) -> str | None:
    """Return corrected language code, or None if we can't confidently improve."""
    a = (artist or "").strip().lower()
    t = (title or "").strip().lower()

    if a in ARTIST_TO_LANG:
        return ARTIST_TO_LANG[a]

    # Try each substring pattern
    for pat, lang in TITLE_LANG_HINTS:
        if lang is None:
            # ambiguous marker — bail without change
            if re.search(pat, t):
                return None
            continue
        if re.search(pat, t):
            return lang

    # Nothing matched — leave as-is
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="Actually write corrections. Default is dry-run.")
    ap.add_argument("--local-only", action="store_true",
                    help="Only update local sqlite. Skip Turso.")
    args = ap.parse_args()

    lconn = sqlite3.connect(str(_LOCAL_DB))
    lconn.row_factory = sqlite3.Row
    suspects = lconn.execute(
        """
        SELECT id, spotify_id, title, artist, language, language_confidence
        FROM tracks
        WHERE language IS NULL
           OR language_confidence < 0.5
           OR language IN ('sa','km','nn','jw','la','sn','si','ny','so','mi','cy',
                           'eu','yo','haw','ga','af','ha','st','sq','ru','it','ko','ja','zh')
        ORDER BY id
        """
    ).fetchall()
    print(f"suspects: {len(suspects)}")

    corrections: list[tuple[int, str, str, str, str, str]] = []
    # (id, spotify_id, title, artist, old_lang, new_lang)
    for r in suspects:
        new = infer_language(r["title"] or "", r["artist"] or "")
        if new and new != r["language"]:
            corrections.append((r["id"], r["spotify_id"], r["title"], r["artist"],
                                r["language"] or "(null)", new))

    print(f"\ncorrections proposed: {len(corrections)}")
    from collections import Counter
    tr = Counter((c[4], c[5]) for c in corrections)
    print("\ntransition (old -> new) top 15:")
    for (o, n), k in tr.most_common(15):
        print(f"  {o:8s} -> {n:2s}  {k}")

    if not args.apply:
        print("\ndry-run. Pass --apply to write.")
        return 0

    print("\napplying to local ...")
    for (tid, _sid, _t, _a, _old, new) in corrections:
        lconn.execute(
            "UPDATE tracks SET language = ?, "
            "language_confidence = COALESCE(language_confidence, 1.0) "
            "WHERE id = ?",
            (new, tid),
        )
    lconn.commit()
    print(f"  updated {len(corrections)} rows locally")

    if args.local_only:
        lconn.close()
        return 0

    # Also push to Turso — match by spotify_id (track_ids differ across DBs).
    print("\napplying to Turso ...")
    turso_url, turso_token = _load_turso_creds()
    os.environ["TURSO_DATABASE_URL"] = turso_url
    os.environ["TURSO_AUTH_TOKEN"]   = turso_token
    os.environ["DB_BACKEND"]         = "turso"
    sys.path.insert(0, str(_REPO / "backend"))
    sys.path.insert(0, str(_REPO / "ingest"))
    import db_client  # noqa: E402
    tconn = db_client.create_connection()
    n_tsync = 0
    for (_tid, sid, _t, _a, _old, new) in corrections:
        if not sid:
            continue
        try:
            tconn.execute(
                "UPDATE tracks SET language = ?, "
                "language_confidence = COALESCE(language_confidence, 1.0) "
                "WHERE spotify_id = ?",
                (new, sid),
            )
            n_tsync += 1
        except Exception as e:
            print(f"  turso update failed sid={sid}: {e}")
    print(f"  updated {n_tsync} rows on Turso")
    tconn.close()
    lconn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
