"""The SPA static route (gamma/app.py): hashed assets cache forever, unhashed
files revalidate and answer 304 when the browser already holds them, and the
build's Brotli and gzip copies go to the clients that accept them."""

import mimetypes

import pytest
from fastapi.testclient import TestClient

from gamma import config
from gamma.app import create_app


def _static_client(tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "index-abc123.js").write_text("console.log(1)")
    (tmp_path / "favicon.svg").write_text("<svg/>")
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "manifest.webmanifest").write_text('{"name": "Gamma"}')
    (tmp_path / "index.html").write_text("<!doctype html><title>Gamma</title>")
    monkeypatch.setattr(config, "STATIC_DIR", str(tmp_path))
    return TestClient(create_app())


def test_hashed_assets_are_immutable(tmp_path, monkeypatch):
    c = _static_client(tmp_path, monkeypatch)
    r = c.get("/assets/index-abc123.js")
    assert r.status_code == 200
    assert "immutable" in r.headers["cache-control"]


def test_unhashed_files_revalidate_with_a_real_304(tmp_path, monkeypatch):
    c = _static_client(tmp_path, monkeypatch)
    r = c.get("/favicon.svg")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    etag = r.headers["etag"]
    again = c.get("/favicon.svg", headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.headers["etag"] == etag
    assert again.content == b""
    # A changed file (new size) gets a new tag and the body again.
    (tmp_path / "favicon.svg").write_text("<svg></svg>")
    changed = c.get("/favicon.svg", headers={"If-None-Match": etag})
    assert changed.status_code == 200 and changed.headers["etag"] != etag


def test_index_html_falls_back_and_revalidates(tmp_path, monkeypatch):
    c = _static_client(tmp_path, monkeypatch)
    r = c.get("/?page=xyz")
    assert r.status_code == 200 and "Gamma" in r.text
    assert r.headers["cache-control"] == "no-cache"
    assert c.get("/some/deep/route", headers={"If-None-Match": r.headers["etag"]}).status_code == 304


def test_a_nul_byte_in_the_path_is_a_404(tmp_path, monkeypatch):
    # A scanner's "%00": Path.resolve() raised ValueError and the request
    # became a 500 in the server log.
    c = _static_client(tmp_path, monkeypatch)
    assert c.get("/%00.html").status_code == 404
    assert c.get("/assets/%00").status_code == 404


def test_web_app_manifest_has_its_media_type(tmp_path, monkeypatch):
    c = _static_client(tmp_path, monkeypatch)
    r = c.get("/media/manifest.webmanifest")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/manifest+json")
    assert r.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("name, media_type", [
    ("pdf.worker-abc123.mjs", "text/javascript"),
    ("index-abc123.js", "text/javascript"),
    ("index-abc123.css", "text/css"),
    ("index.html", "text/html"),
    ("manifest.webmanifest", "application/manifest+json"),
    ("inter.woff2", "font/woff2"),
])
@pytest.mark.parametrize("directory", ["assets", "media"])
def test_static_media_types_ignore_system_mappings(tmp_path, monkeypatch, name, media_type, directory):
    # Windows' registry (or a system mime.types file) can label JS as plain
    # text. Exercise both static-response paths without changing the host OS.
    mimetypes.init()
    suffix = "." + name.rsplit(".", 1)[1]
    monkeypatch.setitem(mimetypes.types_map, suffix, "text/plain")
    c = _static_client(tmp_path, monkeypatch)
    (tmp_path / directory / name).write_bytes(b"asset contents")

    r = c.get(f"/{directory}/{name}")
    assert r.status_code == 200
    assert r.content == b"asset contents"
    assert r.headers["content-type"].split(";")[0] == media_type
    assert r.headers["cache-control"] == (
        "public, max-age=31536000, immutable" if directory == "assets" else "no-cache"
    )


def test_spa_fallback_is_html_despite_system_mapping(tmp_path, monkeypatch):
    mimetypes.init()
    monkeypatch.setitem(mimetypes.types_map, ".html", "text/plain")
    c = _static_client(tmp_path, monkeypatch)
    r = c.get("/some/deep/route")
    assert r.status_code == 200
    assert r.headers["content-type"].split(";")[0] == "text/html"
    assert "Gamma" in r.text


def _precompressed_client(tmp_path, monkeypatch):
    """``_static_client`` with the copies the build writes beside a file
    (frontend/vite.config.js); their bytes only name them."""
    c = _static_client(tmp_path, monkeypatch)
    for name, body in (("assets/a.js", b"console.log('a')"), ("assets/a.js.br", b"br:a.js"),
                       ("assets/a.js.gz", b"gz:a.js"), ("index.html.br", b"br:index"),
                       ("index.html.gz", b"gz:index"), ("favicon.svg.gz", b"gz:svg"),
                       ("assets/pdf.worker.min-abc123.mjs", b"worker"),
                       ("assets/pdf.worker.min-abc123.mjs.br", b"br:worker")):
        (tmp_path / name).write_bytes(body)
    return c


def _fetch(c, path, accept, method="GET", headers=None):
    """The response and its body as sent (iter_raw: not decoded)."""
    with c.stream(method, path, headers={"Accept-Encoding": accept, **(headers or {})}) as r:
        return r, b"".join(r.iter_raw())


def test_precompressed_copies_follow_accept_encoding(tmp_path, monkeypatch):
    c = _precompressed_client(tmp_path, monkeypatch)
    br, body = _fetch(c, "/assets/a.js", "gzip, deflate, br, zstd")
    assert br.status_code == 200 and body == b"br:a.js"
    assert br.headers["content-encoding"] == "br"
    assert br.headers["content-length"] == str(len(body))
    assert br.headers["content-type"].split(";")[0] == "text/javascript"
    assert br.headers["vary"] == "Accept-Encoding"
    assert br.headers["cache-control"] == "public, max-age=31536000, immutable"

    gz, body = _fetch(c, "/assets/a.js", "gzip, deflate, br;q=0")
    assert body == b"gz:a.js" and gz.headers["content-encoding"] == "gzip"
    assert gz.headers["content-type"].split(";")[0] == "text/javascript"
    plain, body = _fetch(c, "/assets/a.js", "identity")
    assert body == b"console.log('a')" and "content-encoding" not in plain.headers
    assert plain.headers["vary"] == "Accept-Encoding"
    assert len({br.headers["etag"], gz.headers["etag"], plain.headers["etag"]}) == 3

    # the copy keeps the file's own media type; a file without copies does not vary
    svg, body = _fetch(c, "/favicon.svg", "gzip, br")
    assert body == b"gz:svg" and svg.headers["content-type"].split(";")[0] == "image/svg+xml"
    bare, body = _fetch(c, "/assets/index-abc123.js", "gzip, br")
    assert body == b"console.log(1)" and "content-encoding" not in bare.headers and "vary" not in bare.headers


def test_head_answers_the_headers_of_the_copy(tmp_path, monkeypatch):
    c = _precompressed_client(tmp_path, monkeypatch)
    r, body = _fetch(c, "/assets/a.js", "br", method="HEAD")
    assert r.status_code == 200 and body == b""
    assert r.headers["content-encoding"] == "br" and r.headers["content-length"] == str(len(b"br:a.js"))
    r, body = _fetch(c, "/", "gzip", method="HEAD")
    assert r.status_code == 200 and body == b"" and r.headers["content-encoding"] == "gzip"
    # an API route without HEAD answers 405, not the app's page
    assert c.head("/api/health").status_code == 405
    assert c.head("/api/no-such-route").status_code == 405


def test_index_html_revalidates_per_encoding(tmp_path, monkeypatch):
    c = _precompressed_client(tmp_path, monkeypatch)
    first, body = _fetch(c, "/some/route", "gzip, br")
    assert first.status_code == 200 and body == b"br:index"
    assert first.headers["cache-control"] == "no-cache"
    assert first.headers["content-type"].split(";")[0] == "text/html"
    etag = first.headers["etag"]
    again, body = _fetch(c, "/", "gzip, br", headers={"If-None-Match": etag})
    assert again.status_code == 304 and body == b""
    assert again.headers["etag"] == etag and again.headers["vary"] == "Accept-Encoding"
    assert again.headers["cache-control"] == "no-cache"
    # the gzip copy is another representation: a tag of its own, the body again
    gz, body = _fetch(c, "/", "gzip", headers={"If-None-Match": etag})
    assert gz.status_code == 200 and body == b"gz:index" and gz.headers["etag"] != etag
    assert _fetch(c, "/", "gzip", headers={"If-None-Match": gz.headers["etag"]})[0].status_code == 304


@pytest.mark.parametrize("accept, sent", [("gzip, br", b"br:worker"), ("identity", b"worker")])
def test_the_pdf_worker_keeps_its_media_type(tmp_path, monkeypatch, accept, sent):
    mimetypes.init()
    monkeypatch.setitem(mimetypes.types_map, ".mjs", "text/plain")
    c = _precompressed_client(tmp_path, monkeypatch)
    r, body = _fetch(c, "/assets/pdf.worker.min-abc123.mjs?mime=js", accept)
    assert r.status_code == 200 and body == sent
    assert r.headers["content-type"].split(";")[0] == "text/javascript"
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
