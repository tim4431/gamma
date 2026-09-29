"""The SPA static route (gamma/app.py): hashed assets cache forever, unhashed
files revalidate and answer 304 when the browser already holds them."""

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
