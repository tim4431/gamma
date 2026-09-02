"""Share links are keyed by page, not by PDF.

Every page is a root block; a paper's PDF is just a doc_id/source_url on it.
Shares follow the block model: a token names one page and confines
unauthenticated reads to that page's subtree and assets — so note pages
without any PDF share exactly like papers. Rows minted by the old doc-keyed
model are resolved to their page on first use.
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from conftest import login, make_user, make_page


@pytest.fixture(scope="module")
def bob():
    make_user("bob_share", "bobpw1234567")
    return login("bob_share", "bobpw1234567")


@pytest.fixture
def anon():
    from gamma.app import app
    return TestClient(app)  # no cookies


def _child(client, parent_id, content):
    r = client.post("/api/blocks", json={"parent_id": parent_id, "content": content})
    assert r.status_code == 200, r.text
    return r.json()


def test_note_page_without_pdf_is_shareable(bob, anon):
    page = make_page(bob, "Plain notes")            # no doc_id, no source_url
    note = _child(bob, page["id"], "a private thought")
    other = make_page(bob, "Another note page")

    r = bob.post(f"/api/share/{page['id']}")
    assert r.status_code == 200, r.text
    token = r.json()["token"]

    resolved = anon.get(f"/api/share/{token}")
    assert resolved.status_code == 200
    assert resolved.json()["page_id"] == page["id"]
    assert resolved.json()["doc_id"] == ""
    assert resolved.json()["username"] == "bob_share"

    q = {"share": token}
    assert anon.get(f"/api/blocks/{page['id']}", params=q).status_code == 200
    assert anon.get(f"/api/blocks/{page['id']}/children", params=q).status_code == 200
    assert anon.get(f"/api/blocks/{note['id']}", params=q).status_code == 200
    tree = anon.get(f"/api/blocks/{page['id']}/subtree", params=q).json()["block"]
    assert [c["content"] for c in tree["children"]] == ["a private thought"]
    # the read-only export path works for a note page too
    assert anon.get(f"/api/pages/{page['id']}/export", params=q).status_code == 200

    # nothing beyond the page
    assert anon.get(f"/api/blocks/{other['id']}", params=q).status_code == 403
    assert anon.get(f"/api/blocks/{other['id']}/children", params=q).status_code == 403
    assert anon.get("/api/blocks/root/children", params=q).status_code == 403
    assert anon.get(f"/api/pages/{other['id']}/export", params=q).status_code == 403
    # and no token at all is still a 401
    assert anon.get(f"/api/blocks/{page['id']}").status_code == 401


def test_only_page_roots_can_be_shared(bob):
    page = make_page(bob, "Root only")
    child = _child(bob, page["id"], "child block")
    assert bob.post(f"/api/share/{child['id']}").status_code == 400
    assert bob.post("/api/share/does_not_exist").status_code == 404


def test_share_token_is_stable_per_page(bob):
    page = make_page(bob, "Stable link")
    t1 = bob.post(f"/api/share/{page['id']}").json()["token"]
    t2 = bob.post(f"/api/share/{page['id']}").json()["token"]
    assert t1 == t2
    other = make_page(bob, "Different page")
    assert bob.post(f"/api/share/{other['id']}").json()["token"] != t1


def test_share_requires_a_session(anon, bob):
    page = make_page(bob, "No anon sharing")
    assert anon.post(f"/api/share/{page['id']}").status_code == 401


def test_legacy_doc_keyed_share_row_still_resolves(bob, anon):
    """Rows minted before shares were keyed by page carry only doc_id; the
    first use resolves them to the page and backfills page_id."""
    from gamma.db import connect_users_db, page_now

    page = make_page(bob, "Old paper", properties={"doc_id": "legacy_doc_1"})
    token = "legacy-token-abc"
    with connect_users_db() as conn:
        conn.execute(
            "INSERT INTO shares (token, username, doc_id, page_id, created_at) VALUES (?, ?, ?, NULL, ?)",
            (token, "bob_share", "legacy_doc_1", page_now()),
        )
        conn.commit()

    r = anon.get(f"/api/share/{token}")
    assert r.status_code == 200, r.text
    assert r.json() == {"page_id": page["id"], "doc_id": "legacy_doc_1", "username": "bob_share"}
    assert anon.get(f"/api/blocks/{page['id']}/subtree", params={"share": token}).status_code == 200
    assert anon.get("/api/blocks/by-doc/legacy_doc_1", params={"share": token}).status_code == 200

    with connect_users_db() as conn:
        row = conn.execute("SELECT page_id FROM shares WHERE token = ?", (token,)).fetchone()
    assert row[0] == page["id"]


def test_legacy_row_for_a_deleted_document_is_dead(bob, anon):
    from gamma.db import connect_users_db, page_now

    token = "legacy-token-gone"
    with connect_users_db() as conn:
        conn.execute(
            "INSERT INTO shares (token, username, doc_id, page_id, created_at) VALUES (?, ?, ?, NULL, ?)",
            (token, "bob_share", "doc_that_never_existed", page_now()),
        )
        conn.commit()
    assert anon.get(f"/api/share/{token}").status_code == 404
    page = make_page(bob, "Unrelated")
    assert anon.get(f"/api/blocks/{page['id']}", params={"share": token}).status_code == 401


def test_share_reads_only_assets_its_page_references(bob, anon):
    png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    up = bob.post("/api/upload-image", files={"file": ("a.png", png, "image/png")})
    assert up.status_code == 200, up.text
    url = up.json()["url"]

    with_img = make_page(bob, "Has the image")
    _child(bob, with_img["id"], f"look: ![fig]({url})")
    without = make_page(bob, "No image here")

    t_with = bob.post(f"/api/share/{with_img['id']}").json()["token"]
    t_without = bob.post(f"/api/share/{without['id']}").json()["token"]
    assert anon.get(url, params={"share": t_with}).status_code == 200
    assert anon.get(url, params={"share": t_without}).status_code == 403
    assert anon.get(url).status_code == 401


def test_pdf_proxy_only_serves_the_pages_own_source(bob, anon):
    paper = make_page(bob, "Remote paper",
                      properties={"doc_id": "remote_doc", "source_url": "https://example.com/paper.pdf"})
    notes = make_page(bob, "Just notes")
    t_paper = bob.post(f"/api/share/{paper['id']}").json()["token"]
    t_notes = bob.post(f"/api/share/{notes['id']}").json()["token"]
    other = {"source_url": "https://example.com/other.pdf"}
    assert anon.get("/api/pdf", params={**other, "share": t_paper}).status_code == 403
    assert anon.get("/api/pdf", params={**other, "share": t_notes}).status_code == 403
    assert anon.get("/api/pdf", params={"source_url": "https://example.com/paper.pdf",
                                         "share": t_notes}).status_code == 403
