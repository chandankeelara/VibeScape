"""The catch-all that serves frontend-next/dist. A tab still running the
previous build lazily imports a hashed chunk the last deploy deleted; before
2026-10-21 that request got index.html (200, text/html), so the import failed
with a MIME error the client could not tell from a broken module. Hashed
assets are immutable, everything else is revalidated, and a missing file is a
404 rather than the app shell."""

import pytest

INDEX = "<!doctype html><script src=/assets/index-NEW.js></script>"


@pytest.fixture
def dist(tmp_path, monkeypatch, app_module):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text(INDEX)
    (tmp_path / "assets" / "LyricsPanel-NEW.js").write_text("export default 1")
    (tmp_path / "sw.js").write_text("// sw")
    monkeypatch.setattr(app_module, "NEXT_DIR", tmp_path)
    return tmp_path


def test_hashed_asset_is_immutable(client, dist):
    r = client.get("/assets/LyricsPanel-NEW.js")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert "javascript" in r.headers["content-type"]


@pytest.mark.parametrize("url", ["/", "/index.html", "/admin", "/some/client/route"])
def test_index_html_is_revalidated(client, dist, url):
    r = client.get(url)
    assert r.status_code == 200
    assert r.text == INDEX
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers["etag"] and r.headers["last-modified"]


def test_unhashed_root_file_is_revalidated(client, dist):
    r = client.get("/sw.js")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"


def test_missing_asset_is_404_not_index(client, dist):
    r = client.get("/assets/LyricsPanel-B-LlEb88.js")
    assert r.status_code == 404
    assert INDEX not in r.text
    assert r.headers["cache-control"] == "no-store"


def test_missing_file_with_extension_is_404(client, dist):
    assert client.get("/favicon.ico").status_code == 404


def test_revalidation_gets_304(client, dist):
    first = client.get("/admin")
    r = client.get("/admin", headers={"If-None-Match": first.headers["etag"]})
    assert r.status_code == 304
    assert r.content == b""
    assert r.headers["etag"] == first.headers["etag"]
    assert r.headers["cache-control"] == "no-cache"

    r = client.get("/", headers={"If-Modified-Since": first.headers["last-modified"]})
    assert r.status_code == 304


def test_changed_index_is_sent_again(client, dist):
    first = client.get("/")
    (dist / "index.html").write_text(INDEX + "<!-- next build -->")
    r = client.get("/", headers={"If-None-Match": first.headers["etag"]})
    assert r.status_code == 200
    assert r.text.endswith("next build -->")


def test_api_routes_are_not_swallowed(client, dist):
    # No session: the real /api route answers, not the SPA shell.
    r = client.get("/api/me/stats")
    assert r.status_code == 401
