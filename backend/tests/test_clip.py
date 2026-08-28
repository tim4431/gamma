"""The browser extension's endpoints: /api/clip (the one-shot "save this
paper" ingest), /api/library/lookup + /folders (popup helpers), and
/api/clip/note (clipped selections). Upstream fetches are faked — no network,
and the metadata thread is stubbed out."""

import hashlib
import io
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

import gamma.routers.clip as clip_mod
import gamma.routers.pdf as pdf_mod
from gamma.db import user_db_path, user_uploads_dir

PDF_BYTES = b"%PDF-1.4 clip test\n" + b"y" * 10_000


class FakeUpstream:
    def __init__(self, url, data=PDF_BYTES, ctype="application/pdf"):
        self._url, self._buf = url, io.BytesIO(data)
        self.headers = {"Content-Type": ctype, "Content-Length": str(len(data))}
        self.closed = False

    def read(self, n=-1):
        return self._buf.read(n)

    def geturl(self):
        return self._url

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


@pytest.fixture
def upstream(monkeypatch):
    """Fake the SSRF-guarded fetch: PDF for *.pdf URLs, HTML otherwise."""
    calls = []

    def fake_urlopen(req, timeout=30):
        calls.append(req.full_url)
        if req.full_url.endswith(".pdf") or "arxiv.org/pdf/" in req.full_url:
            return FakeUpstream(req.full_url)
        return FakeUpstream(req.full_url, data=b"<html><body>nothing here</body></html>", ctype="text/html")

    monkeypatch.setattr(pdf_mod, "guarded_urlopen", fake_urlopen)
    return calls


@pytest.fixture
def meta_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(clip_mod, "_start_metadata", lambda user, block_id: calls.append((user, block_id)))
    return calls


def _props(block_id):
    with sqlite3.connect(user_db_path("guest", "pages.db")) as conn:
        row = conn.execute("SELECT content, properties FROM unified_blocks WHERE id = ?", (block_id,)).fetchone()
    return row[0], json.loads(row[1])


def test_clip_url_creates_filed_page_and_stores_pdf(guest, upstream, meta_calls):
    url = "https://example.org/papers/clip-one.pdf"
    r = guest.post("/api/clip", json={
        "source_url": "https://example.org/papers/clip-one",
        "pdf_url": url, "title": "  Clip   One  ", "folder": "reading/2026",
        "labels": ["to-read"],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    doc_id = hashlib.sha256(url.encode()).hexdigest()[:24]
    assert body["doc_id"] == doc_id and body["existed"] is False
    assert body["title"] == "Clip One"
    assert body["open_url"] == f"/?block={body['block_id']}"
    assert (user_uploads_dir("guest") / f"{doc_id}.pdf").read_bytes() == PDF_BYTES
    title, props = _props(body["block_id"])
    assert title == "Clip One" and props["auto_title"] == "Clip One"
    assert props["source_url"] == url
    assert props["web_url"] == "https://example.org/papers/clip-one"
    assert props["folder"] == "reading/2026" and props["category"] == "to-read"
    assert meta_calls == [("guest", body["block_id"])]


def test_clip_dedups_by_doi_and_adds_folder(guest, upstream, meta_calls):
    url = "https://example.org/papers/dedup.pdf"
    r = guest.post("/api/clip", json={"pdf_url": url, "title": "Dedup", "folder": "a"})
    block_id = r.json()["block_id"]
    # Pretend metadata landed with a DOI, as the lookup thread would.
    with sqlite3.connect(user_db_path("guest", "pages.db")) as conn:
        _, props = _props(block_id)
        props["meta"] = {"doi": "10.1000/DeDup.1", "title": "Dedup"}
        conn.execute("UPDATE unified_blocks SET properties = ? WHERE id = ?", (json.dumps(props), block_id))
        conn.commit()
    fetched_before = len(upstream)
    r2 = guest.post("/api/clip", json={
        "source_url": "https://publisher.example/article/whatever",
        "doi": "10.1000/dedup.1", "folder": "b", "labels": ["dup"],
    })
    assert r2.status_code == 200, r2.text
    assert r2.json()["existed"] is True and r2.json()["block_id"] == block_id
    assert len(upstream) == fetched_before  # nothing re-fetched
    _, props = _props(block_id)
    assert props["folder"] == "a, b" and props["category"] == "dup"
    # Same page, refined into a subfolder: the ancestor tag is replaced.
    guest.post("/api/clip", json={"doi": "10.1000/dedup.1", "folder": "a/deeper"})
    _, props = _props(block_id)
    assert props["folder"] == "b, a/deeper"


def test_clip_dead_link_leaves_no_page(guest, upstream, meta_calls):
    url = "https://example.org/not-a-paper"
    r = guest.post("/api/clip", json={"source_url": url, "title": "Ghost"})
    assert r.status_code == 400
    assert "PDF" in r.json()["detail"]
    with sqlite3.connect(user_db_path("guest", "pages.db")) as conn:
        assert not conn.execute("SELECT 1 FROM unified_blocks WHERE content = 'Ghost'").fetchone()
    assert meta_calls == []


def test_clip_no_copy_probes_only(guest, upstream, meta_calls):
    url = "https://example.org/papers/nocopy.pdf"
    r = guest.post("/api/clip", json={"pdf_url": url, "title": "No copy", "save_copy": False})
    assert r.status_code == 200, r.text
    doc_id = r.json()["doc_id"]
    assert not (user_uploads_dir("guest") / f"{doc_id}.pdf").exists()
    _, props = _props(r.json()["block_id"])
    assert props["source_url"] == url  # the app proxies it on open


def test_clip_from_uploaded_bytes(guest, upstream, meta_calls):
    data = b"%PDF-1.4 uploaded by the extension\n" + b"z" * 500
    up = guest.post("/api/uploads", files={"file": ("paywalled.pdf", data, "application/pdf")})
    assert up.status_code == 200, up.text
    doc_id = up.json()["doc_id"]
    r = guest.post("/api/clip", json={
        "doc_id": doc_id, "source_url": "https://journal.example/doi/10.1000/paywalled",
        "title": "Paywalled paper", "folder": "inbox",
    })
    assert r.status_code == 200, r.text
    assert r.json()["doc_id"] == doc_id
    assert upstream == []  # no server-side fetch at all
    _, props = _props(r.json()["block_id"])
    assert props["source_url"] == f"/api/uploads/{doc_id}.pdf"
    assert props["web_url"] == "https://journal.example/doi/10.1000/paywalled"
    # Second save from the same publisher page is found via web_url / its DOI.
    lk = guest.get("/api/library/lookup", params={"url": "https://journal.example/doi/10.1000/paywalled"})
    assert lk.status_code == 200 and lk.json()["block_id"] == r.json()["block_id"]
    # Unknown / unsafe doc ids never create a page.
    assert guest.post("/api/clip", json={"doc_id": "deadbeefdeadbeefdeadbeef"}).status_code == 404
    assert guest.post("/api/clip", json={"doc_id": "../../etc"}).status_code == 400


def test_lookup_by_arxiv_and_doi(guest, upstream, meta_calls):
    r = guest.post("/api/clip", json={"source_url": "https://arxiv.org/abs/2601.01234v2", "title": "Arx"})
    assert r.status_code == 200, r.text
    block_id = r.json()["block_id"]
    _, props = _props(block_id)
    # Bare arXiv ids are version-stripped before resolving: the canonical PDF is the latest.
    assert props["source_url"] == "https://arxiv.org/pdf/2601.01234"
    for params in ({"arxiv_id": "2601.01234"}, {"url": "https://arxiv.org/pdf/2601.01234v1"},
                   {"url": "arXiv:2601.01234"}, {"url": "https://arxiv.org/abs/2601.01234v2"}):
        lk = guest.get("/api/library/lookup", params=params)
        assert lk.status_code == 200, (params, lk.text)
        assert lk.json()["block_id"] == block_id
    assert guest.get("/api/library/lookup", params={"doi": "10.9999/nope"}).status_code == 404
    assert guest.get("/api/library/lookup", params={"url": "https://example.org/unknown"}).status_code == 404
    assert guest.get("/api/library/lookup").status_code == 400


def test_folders_lists_ancestors_and_labels(guest, upstream, meta_calls):
    guest.post("/api/clip", json={"pdf_url": "https://example.org/papers/f1.pdf", "folder": "qc/readout/fast",
                                  "labels": ["Zeta label"]})
    r = guest.get("/api/library/folders")
    assert r.status_code == 200
    body = r.json()
    for f in ("qc", "qc/readout", "qc/readout/fast"):
        assert f in body["folders"]
    assert "Zeta label" in body["labels"]


def test_clip_note_creates_web_clips_page_and_appends(guest):
    r = guest.post("/api/clip/note", json={
        "text": "First line\n\nSecond line", "source_url": "https://blog.example/post", "title": "A post",
    })
    assert r.status_code == 200, r.text
    page_id = r.json()["page_id"]
    title, props = _props(page_id)
    assert title == "Web clips" and props.get("web_clips") == 1
    content, _ = _props(r.json()["block_id"])
    assert content == "> First line\n>\n> Second line\n— [A post](https://blog.example/post)"
    r2 = guest.post("/api/clip/note", json={"text": "Another", "source_url": "https://blog.example/2"})
    assert r2.json()["page_id"] == page_id  # reused, not recreated
    kids = guest.get(f"/api/blocks/{page_id}/children").json()
    ids = [b["id"] for b in (kids if isinstance(kids, list) else kids.get("children", kids.get("blocks", [])))]
    assert ids[-1] == r2.json()["block_id"]
    # Explicit target page; unknown page → 404; empty text → 400.
    r3 = guest.post("/api/clip/note", json={"text": "Into a paper", "page_id": page_id})
    assert r3.status_code == 200 and r3.json()["page_id"] == page_id
    assert guest.post("/api/clip/note", json={"text": "x", "page_id": "nope"}).status_code == 404
    assert guest.post("/api/clip/note", json={"text": "   "}).status_code == 400


def test_clip_endpoints_require_a_session():
    from gamma.app import app
    anon = TestClient(app)
    assert anon.post("/api/clip", json={"pdf_url": "https://example.org/x.pdf"}).status_code == 401
    assert anon.get("/api/library/lookup", params={"doi": "10.1/x"}).status_code == 401
    assert anon.get("/api/library/folders").status_code == 401
    assert anon.post("/api/clip/note", json={"text": "x"}).status_code == 401
