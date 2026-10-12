"""backend/playlist_import.py — link detection, readers, matching, Spotify budget.

No network: every HTTP call goes through a fake `get`.
"""
import json

import pytest

import playlist_import as P


class Resp:
    def __init__(self, status=200, payload=None, text="", headers=None, url=""):
        self.status_code = status
        self._payload = payload
        self.text = text
        self.headers = headers or {}
        self.url = url
        self.encoding = "utf-8"

    def json(self):
        return self._payload


class FakeGet:
    """Routes by URL prefix; records every call."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, **kw):
        self.calls.append((url, kw))
        for prefix, handler in self.routes.items():
            if url.startswith(prefix):
                return handler(url, **kw) if callable(handler) else handler
        raise AssertionError(f"unexpected request: {url}")


class FakePacer(P._Pacer):
    def __init__(self):
        self.slept = []
        super().__init__(0, clock=lambda: 0.0, sleep=self.slept.append)


# --------------------------------------------------------------------- detect

@pytest.mark.parametrize("url,kind,pid", [
    ("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M", "spotify", "37i9dQZF1DXcBWIGoYBM5M"),
    ("https://open.spotify.com/intl-de/playlist/37i9dQZF1DXcBWIGoYBM5M?si=x", "spotify", "37i9dQZF1DXcBWIGoYBM5M"),
    ("https://open.spotify.com/user/12155920234/playlist/310mBKTSzYVrHPqCSDeQJw", "spotify", "310mBKTSzYVrHPqCSDeQJw"),
    ("https://open.spotify.com/embed/playlist/37i9dQZF1DXcBWIGoYBM5M", "spotify", "37i9dQZF1DXcBWIGoYBM5M"),
    ("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M", "spotify", "37i9dQZF1DXcBWIGoYBM5M"),
    ("37i9dQZF1DXcBWIGoYBM5M", "spotify", "37i9dQZF1DXcBWIGoYBM5M"),
    ("https://music.apple.com/us/playlist/todays-hits/pl.f4d106fed2bd41149aaacabb233eb5eb", "apple", "pl.f4d106fed2bd41149aaacabb233eb5eb"),
    ("https://music.apple.com/in/playlist/my-mix/pl.u-abc123", "apple", "pl.u-abc123"),
    ("https://www.youtube.com/playlist?list=PL4fGSI1pDJn6puJdseH2Rt9sMvt9E2M4i", "youtube", "PL4fGSI1pDJn6puJdseH2Rt9sMvt9E2M4i"),
    ("https://music.youtube.com/playlist?list=PL4fGSI1pDJn6puJdseH2Rt9sMvt9E2M4i", "youtube", "PL4fGSI1pDJn6puJdseH2Rt9sMvt9E2M4i"),
    ("https://www.youtube.com/watch?v=abc&list=PL4fGSI1pDJn6puJdseH2Rt9sMvt9E2M4i", "youtube", "PL4fGSI1pDJn6puJdseH2Rt9sMvt9E2M4i"),
    ("https://www.deezer.com/en/playlist/3155776842", "deezer", "3155776842"),
    ("deezer.com/fr/playlist/3155776842", "deezer", "3155776842"),
    ("https://deezer.page.link/AbCd", "deezer", ""),
    ("https://music.amazon.com/playlists/B01M11SBC8", "amazon", "B01M11SBC8"),
    ("https://music.amazon.com/user-playlists/abc123def?ref=dm_sh", "amazon", "abc123def"),
])
def test_detect(url, kind, pid):
    src = P.detect(url)
    assert src is not None and (src.kind, src.id) == (kind, pid)


@pytest.mark.parametrize("url", ["", "hello", "https://example.com/playlist/1",
                                 "https://www.youtube.com/watch?v=abc", "https://music.apple.com/us/album/x/1",
                                 "https://music.amazon.com/albums/B0ABC"])
def test_detect_rejects(url):
    assert P.detect(url) is None


def test_apple_storefront_is_taken_from_the_link():
    assert P.detect("https://music.apple.com/in/playlist/x/pl.abc").storefront == "in"


# ------------------------------------------------------------ YouTube titles

@pytest.mark.parametrize("title,channel,artist,song", [
    ("Shakira, Burna Boy - Dai Dai (Official Video)", "Shakira", "Shakira, Burna Boy", "Dai Dai"),
    ("Taylor Swift - Patient Zero (Official Lyric Video)", "TaylorSwiftVEVO", "Taylor Swift", "Patient Zero"),
    ("KATSEYE (캣츠아이) 'Hootie Frutti' Official MV", "HYBE LABELS", "KATSEYE", "Hootie Frutti"),
    ("Song Name feat. Someone [Official Audio]", "Artist - Topic", "Artist", "Song Name"),
    ("Banjaare | Barsaat | Singer A | Big Label", "Big Label Music", "Big Label", "Banjaare"),
])
def test_parse_youtube_title(title, channel, artist, song):
    a, s, alts, _swapped = P.parse_youtube_title(title, channel)
    assert (a, s) == (artist, song)


def test_parse_youtube_title_offers_pipe_segments_and_the_swapped_reading():
    _a, _s, alts, _ = P.parse_youtube_title("Banjaare | Barsaat | Singer A", "T-Series")
    assert "Barsaat" in alts and "Singer A" in alts
    _a, _s, _alts, swapped = P.parse_youtube_title("Todo Lo Fue - Lenin Ramirez - DEL Records 2026", "DEL")
    assert swapped == ("Todo Lo Fue", "Lenin Ramirez")


# ------------------------------------------------------------------- scoring

def cand(name, artist, ms=200_000, cid="sp1", isrc=None):
    return {"id": cid, "name": name, "artists": [{"name": artist}], "duration_ms": ms,
            "external_ids": {"isrc": isrc} if isrc else {}}


def test_exact_match_is_accepted():
    it = P.Item(title="The Cure", artist="Olivia Rodrigo", duration_ms=297_000)
    best, s = P.choose(it, [cand("the cure", "Olivia Rodrigo", 297_000)])
    assert best and s > 0.95


def test_isrc_equality_wins_outright():
    it = P.Item(title="x", artist="y", isrc="USUG1")
    assert P.score(it, cand("totally different", "nobody", isrc="USUG1")) == 1.0


def test_a_different_song_is_rejected():
    it = P.Item(title="Patient Zero", artist="Taylor Swift", duration_ms=226_000)
    best, _ = P.choose(it, [cand("Love Story", "Taylor Swift", 235_000)])
    assert best is None


def test_duration_far_off_drops_a_strict_match_but_not_a_video():
    strict = P.Item(title="Song", artist="Artist", duration_ms=200_000)
    video = P.Item(title="Song", artist="Artist", duration_ms=260_000, loose_duration=True)
    c = cand("Song", "Artist", 200_000)
    assert P.score(video, c) > P.score(P.Item(title="Song", artist="Artist", duration_ms=260_000), c)
    assert P.choose(strict, [c])[0] is not None


def test_artist_inside_a_combined_credit_counts():
    it = P.Item(title="Dai Dai", artist="Shakira, Burna Boy")
    assert P.choose(it, [cand("Dai Dai", "Shakira")])[0] is not None


def test_one_query_per_song():
    assert P.search_queries(P.Item(title="Boston", artist="Stella Lefty")) == ["Boston Stella Lefty"]
    # combined credits search by the first artist; the scorer accepts the rest
    assert P.search_queries(P.Item(title="Dai Dai", artist="Shakira, Burna Boy")) == ["Dai Dai Shakira"]
    # an exact id, when a source has one, replaces the text search
    assert P.search_queries(P.Item(title="Boston", artist="X", isrc="QZJ842603468")) == ["isrc:QZJ842603468"]
    assert P.search_queries(P.Item(title="", artist="X")) == []


# ------------------------------------------------------------------- readers

def test_spotify_embed_reader_takes_exact_ids():
    nd = {"props": {"pageProps": {"state": {"data": {"entity": {"name": "Top", "trackList": [
        {"uri": "spotify:track:AAA", "title": "One", "subtitle": "Ann", "duration": 180000,
         "audioPreview": {"url": "https://p.scdn.co/x"}},
        {"uri": "spotify:episode:EEE", "title": "A podcast", "subtitle": "Show"},
    ]}}}}}}
    html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(nd)}</script>'
    pl = P.fetch(P.Source("spotify", "ID", "u"), FakeGet({"https://open.spotify.com/embed/": Resp(text=html)}))
    assert pl.name == "Top" and [i.spotify_id for i in pl.items] == ["AAA"]   # episodes skipped
    assert pl.note is None


def test_spotify_embed_cap_is_reported():
    tl = [{"uri": f"spotify:track:{i:022d}", "title": "t", "subtitle": "a"} for i in range(P.EMBED_CAP)]
    nd = {"props": {"pageProps": {"state": {"data": {"entity": {"name": "Big", "trackList": tl}}}}}}
    html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(nd)}</script>'
    pl = P.fetch(P.Source("spotify", "ID", "u"), FakeGet({"https://open.spotify.com/embed/": Resp(text=html)}))
    assert "first 100" in pl.note


def test_apple_reader_uses_json_ld_then_itunes_lookup():
    ld = {"@type": "MusicPlaylist", "name": "Today&#39;s Hits", "track": [
        {"url": "https://music.apple.com/us/song/a/111", "duration": "PT3M1S"},
        {"url": "https://music.apple.com/us/song/b/222", "duration": "PT2M"},
    ]}
    page = f'<script type="application/ld+json">{json.dumps(ld)}</script>'
    lookup = {"results": [
        {"wrapperType": "track", "trackId": 111, "trackName": "A", "artistName": "Ann",
         "collectionName": "Alb", "trackTimeMillis": 181000, "artworkUrl100": "https://x/100x100bb.jpg"},
    ]}
    get = FakeGet({"https://music.apple.com/": Resp(text=page), "https://itunes.apple.com/lookup": Resp(payload=lookup)})
    pl = P.fetch(P.detect("https://music.apple.com/us/playlist/x/pl.abc"), get)
    assert pl.name == "Today's Hits" and [(i.title, i.artist, i.album) for i in pl.items] == [("A", "Ann", "Alb")]
    assert "1 song" in pl.note                                   # 222 was not found
    assert pl.items[0].artwork_url.endswith("600x600bb.jpg")
    assert get.calls[1][1]["params"]["id"] == "111,222"           # one batched lookup


# ---------------------------------------------------------------- Amazon

AMAZON_PAGE = {"methods": [{"template": {"headerText": {"text": "Lo-Fi Hip-Hop"}, "widgets": [{"items": [
    {"primaryText": "katana", "secondaryText1": "chi square", "secondaryText2": "katana", "secondaryText3": "02:41"},
    {"primaryText": {"text": "Melancholy Café"}, "secondaryText1": {"text": "trak-side &amp; chill.farm"},
     "secondaryText2": "", "secondaryText3": "1:02:03"},
    {"primaryText": "", "secondaryText1": "skipped: no title"},
]}]}}]}


class FakePost:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, url, **kw):
        self.calls.append((url, kw))
        return self.answers.pop(0)


def test_parse_amazon_page():
    pl = P.parse_amazon_page(AMAZON_PAGE)
    assert pl.name == "Lo-Fi Hip-Hop"
    assert [(i.title, i.artist, i.album, i.duration_ms) for i in pl.items] == [
        ("katana", "chi square", "katana", 161_000),
        ("Melancholy Café", "trak-side & chill.farm", None, 3_723_000),
    ]


def test_amazon_reader_gets_a_visitor_token_then_the_page():
    cfg = {"csrf": {"token": "T", "ts": "1", "rnd": "2"}, "deviceId": "D", "sessionId": "S", "accessToken": "A"}
    post = FakePost(Resp(payload=cfg), Resp(payload=AMAZON_PAGE))
    pl = P.fetch(P.detect("https://music.amazon.com/playlists/B07M7FZKWQ"), get=None, post=post)
    assert len(pl.items) == 2
    assert post.calls[0][0].startswith("https://music.amazon.com/config.json")
    body = json.loads(post.calls[1][1]["data"])
    assert json.loads(body["deeplink"])["deeplink"] == "/playlists/B07M7FZKWQ"
    sent = json.loads(body["headers"])
    assert json.loads(sent["x-amzn-csrf"])["token"] == "T" and sent["x-amzn-device-id"] == "D"


def test_amazon_user_playlists_keep_their_path():
    cfg = {"csrf": {"token": "T"}}
    post = FakePost(Resp(payload=cfg), Resp(payload=AMAZON_PAGE))
    P.fetch(P.detect("https://music.amazon.com/user-playlists/abc123"), get=None, post=post)
    assert json.loads(json.loads(post.calls[1][1]["data"])["deeplink"])["deeplink"] == "/user-playlists/abc123"


@pytest.mark.parametrize("answers,code", [
    ([Resp(payload={})], "source_unreadable"),                                  # no visitor token
    ([Resp(payload={"csrf": {"token": "T"}}), Resp(status=403, payload={})], "source_unreadable"),
    ([Resp(payload={"csrf": {"token": "T"}}), Resp(payload={"methods": []})], "playlist_not_found"),
    ([Resp(status=500, payload=None)], "source_unreadable"),                    # config failed
])
def test_amazon_failures_are_polite(answers, code):
    with pytest.raises(P.ImportError_) as e:
        P.fetch(P.Source("amazon", "B0", "https://music.amazon.com/playlists/B0"), get=None, post=FakePost(*answers))
    assert e.value.code == code


def test_amazon_shape_change_is_unreadable_not_a_crash():
    class Boom(Resp):
        def json(self):
            raise ValueError("not json")
    with pytest.raises(P.ImportError_) as e:
        P.fetch(P.Source("amazon", "B0", "https://music.amazon.com/playlists/B0"), get=None,
                post=FakePost(Resp(payload={"csrf": {"token": "T"}}), Boom()))
    assert e.value.code == "source_unreadable"


@pytest.mark.parametrize("kind", ["deezer"])
def test_recognised_but_unsupported_services_are_refused(kind):
    assert kind not in P.SUPPORTED
    with pytest.raises(P.ImportError_) as e:
        P.fetch(P.Source(kind, "1", "u"), get=FakeGet({}))
    assert e.value.code == f"{kind}_not_supported"


def test_youtube_reader_cleans_titles():
    info = {"title": "Chart", "entries": [
        {"id": "v1", "title": "Artist - Song (Official Video)", "channel": "ArtistVEVO", "duration": 250},
        None,
    ]}
    pl = P.fetch(P.Source("youtube", "PL", "u"), get=None, ytdlp_extract=lambda url: info)
    it = pl.items[0]
    assert (it.title, it.artist, it.youtube_id, it.loose_duration) == ("Song", "Artist", "v1", True)




# ------------------------------------------------------- resolve and budget

def search_route(items_by_query):
    def handler(url, params=None, **kw):
        return Resp(payload={"tracks": {"items": items_by_query(params["q"])}})
    return handler


def test_a_spotify_id_costs_no_calls_and_drops_the_preview():
    get = FakeGet({})
    track, why = P.resolve(P.Item(title="T", artist="A", spotify_id="SID", preview_url="https://p"), "tok", get)
    assert track["id"] == "SID" and track["preview_url"] is None and get.calls == []


def test_a_catalogue_hit_costs_no_calls():
    get = FakeGet({})
    hit = {"id": "CAT"}
    track, _ = P.resolve(P.Item(title="T", artist="A"), "tok", get, catalog=lambda it: hit)
    assert track is hit and get.calls == []


def test_search_stops_at_the_first_confident_match():
    get = FakeGet({"https://api.spotify.com/v1/search": search_route(lambda q: [cand("Boston", "Stella Lefty")])})
    track, _ = P.resolve(P.Item(title="Boston", artist="Stella Lefty"), "tok", get, pacer=FakePacer())
    assert track["id"] == "sp1" and len(get.calls) == 1


def test_a_song_with_no_match_costs_one_search():
    get = FakeGet({"https://api.spotify.com/v1/search": search_route(lambda q: [])})
    track, why = P.resolve(P.Item(title="Nothing", artist="Nobody"), "tok", get, pacer=FakePacer())
    assert track is None and "no confident match" in why and len(get.calls) == 1


def test_an_ambiguous_youtube_title_gets_its_second_reading_and_no_more():
    get = FakeGet({"https://api.spotify.com/v1/search": search_route(lambda q: [])})
    it = P.Item(title="Lenin Ramirez", artist="Todo Lo Fue", alt_artists=["A", "B"],
                swapped=("Todo Lo Fue", "Lenin Ramirez"))
    P.resolve(it, "tok", get, pacer=FakePacer())
    assert [c[1]["params"]["q"] for c in get.calls] == ["Lenin Ramirez Todo Lo Fue", "Todo Lo Fue Lenin Ramirez"]
    assert len(get.calls) == P.MAX_SEARCHES_PER_SONG


def test_a_long_rate_limit_stops_instead_of_retrying():
    get = FakeGet({"https://api.spotify.com/v1/search": Resp(429, headers={"Retry-After": "70320"})})
    with pytest.raises(P.SpotifyRateLimited) as e:
        P.resolve(P.Item(title="T", artist="A"), "tok", get, pacer=FakePacer())
    assert e.value.retry_after_s == 70320 and len(get.calls) == 1


def test_a_short_rate_limit_is_waited_out_once():
    answers = iter([Resp(429, headers={"Retry-After": "2"}),
                    Resp(payload={"tracks": {"items": [cand("T", "A")]}})])
    get = FakeGet({"https://api.spotify.com/v1/search": lambda url, **kw: next(answers)})
    pacer = FakePacer()
    track, _ = P.resolve(P.Item(title="T", artist="A"), "tok", get, pacer=pacer)
    assert track is not None and 2.0 in pacer.slept


def test_two_short_rate_limits_in_a_row_stop_too():
    get = FakeGet({"https://api.spotify.com/v1/search": Resp(429, headers={"Retry-After": "1"})})
    with pytest.raises(P.SpotifyRateLimited):
        P.resolve(P.Item(title="T", artist="A"), "tok", get, pacer=FakePacer())


def test_pacer_spaces_calls():
    t = {"now": 0.0}
    slept = []

    def sleep(s):
        slept.append(round(s, 3))
        t["now"] += s
    pacer = P._Pacer(0.35, clock=lambda: t["now"], sleep=sleep)
    pacer.wait()
    pacer.wait()
    t["now"] += 1.0
    pacer.wait()
    assert slept == [0.35]              # second call waited; the third was already late enough
