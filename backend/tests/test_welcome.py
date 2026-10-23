"""The Welcome page and the Sketchbook every new account starts with
(gamma/seed.py ``seed_welcome``, docs/dev/onboarding.md "The welcome page and
its sample PDF" and "The Sketchbook"): gamma/onboarding/welcome.md parsed by
the .md importer, its notes written through the op log, a sample PDF the
notes-as-PDF writer typeset from the same text, ``properties.seeded =
"welcome"``; beside it the notebook gamma/sketchbook.py draws, ``seeded =
"sketchbook"``; never in an existing workspace, a shared one or one that
already has pages. The drawing itself is test_sketchbook.py's."""

import json

import pytest

from conftest import account_of, login, make_user


@pytest.fixture(autouse=True)
def _app_started(client):
    """The app starts first: its startup seeds the first admin only while no
    account exists, which accounts made here would otherwise prevent."""
    yield


def _pages(ws):
    """The workspace's pages in library order."""
    from gamma.db import connect_pages_db
    with connect_pages_db(ws) as conn:
        rows = conn.execute("SELECT id, content, properties, updated_at FROM unified_blocks "
                            "WHERE parent_id = 'root' ORDER BY position").fetchall()
    return [{"id": r[0], "content": r[1], "properties": json.loads(r[2] or "{}"), "updated_at": r[3]}
            for r in rows]


def _seeded(ws, kind):
    [page] = [p for p in _pages(ws) if p["properties"].get("seeded") == kind]
    return page


def _notes(ws, page_id):
    from gamma.blocks_store import fetch_subtree
    from gamma.db import connect_pages_db
    with connect_pages_db(ws) as conn:
        return [r[3] for r in fetch_subtree(conn, page_id) if r[0] != page_id]


def _pdf_text(data):
    """The text of every page, in order."""
    import pymupdf
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        return "\n".join(page.get_text("text") for page in doc)
    finally:
        doc.close()


def test_a_new_account_starts_with_the_welcome_page_and_its_pdf():
    from gamma import seed
    from gamma.db import connect_pages_db
    from gamma.storage import find_upload_file, is_pdf

    ws = seed.create_account("wl_alice", "wl_alice_pw")
    page = _seeded(ws, "welcome")
    props = page["properties"]
    assert page["content"] == "Welcome" and props["seeded"] == "welcome"
    # the sample PDF, stored by content hash like any upload
    doc_id = props["doc_id"]
    assert "source_url" not in props  # seeded in the current shape: the URL is derived
    data = find_upload_file(f"{doc_id}.pdf", ws).read_bytes()
    assert is_pdf(data)
    text = _pdf_text(data)
    assert "Welcome to Gamma" in text and "Your first five minutes" in text
    assert "Attention(Q, K, V) = softmax" in text  # the first tour boxes this formula
    # its own record: opening the page looks nothing up, asks no AI for a citation
    assert props["meta"]["source"] == "manual" and props["ppt_cite"]
    # the notes are welcome.md's outline, written as one op batch by the account
    notes = _notes(ws, page["id"])
    assert "## Your first five minutes" in notes and "## Practice on this PDF" in notes
    assert any(n.startswith("**Add a paper**") for n in notes)
    assert not any("Guest workspace" in n for n in notes)
    with connect_pages_db(ws) as conn:
        batches = conn.execute("SELECT actor, ops FROM page_ops WHERE page_id = ?", (page["id"],)).fetchall()
    assert len(batches) == 1 and batches[0][0] == account_of("wl_alice")
    ops = json.loads(batches[0][1])
    assert {o["op"] for o in ops} == {"insert"} and len(ops) == len(notes)


def test_the_account_sees_it_in_its_library():
    from gamma import seed
    seed.create_account("wl_bob", "wl_bob_pw")
    c = login("wl_bob", "wl_bob_pw")
    children = c.get("/api/blocks/root/children").json()["children"]
    assert [b["content"] for b in children] == ["Welcome", "Sketchbook"]
    welcome = children[0]
    assert welcome["properties"]["seeded"] == "welcome"
    pdf = c.get(f"/api/uploads/{welcome['properties']['doc_id']}.pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"


def test_cloud_accounts_start_with_it_too():
    from gamma import seed
    ws = seed.create_cloud_account("wl_cloud")
    assert [p["properties"].get("seeded") for p in _pages(ws)] == ["welcome", "sketchbook"]


def test_every_workspace_stores_the_same_pdf():
    from gamma import seed
    a = seed.create_account("wl_same_a", None)
    b = seed.create_account("wl_same_b", None)
    assert _seeded(a, "welcome")["properties"]["doc_id"] == _seeded(b, "welcome")["properties"]["doc_id"]


def test_the_sketchbook_is_a_notebook_beside_it():
    """Its folded sheets on their paper, each holding its drawing (a stored
    ink file) and its text boxes, written as one op batch by the account."""
    from gamma import ink as inkmod
    from gamma import seed
    from gamma.blocks_store import block_to_dict, fetch_subtree
    from gamma.db import connect_pages_db, ws_uploads_dir
    from gamma.notebook import sheets_of
    from gamma.sketchbook import sheets

    ws = seed.create_account("wl_sketch", None)
    page = _seeded(ws, "sketchbook")
    assert page["content"] == "Sketchbook" and page["properties"] == {"seeded": "sketchbook"}
    with connect_pages_db(ws) as conn:
        blocks = [block_to_dict(r) for r in fetch_subtree(conn, page["id"]) if r[0] != page["id"]]
        batches = conn.execute("SELECT actor, ops FROM page_ops WHERE page_id = ?", (page["id"],)).fetchall()
    assert len(batches) == 1 and batches[0][0] == account_of("wl_sketch")
    assert len(json.loads(batches[0][1])) == len(blocks)
    found, drawn = sheets_of(blocks, page["id"]), sheets()
    assert [s["paper"] for s in found] == [s.paper for s in drawn]
    titles = {b["id"]: b for b in blocks}
    for sheet, made in zip(found, drawn):
        assert titles[sheet["id"]]["content"] == made.title
        assert titles[sheet["id"]]["properties"]["collapsed"] is True
        [group] = [b for b in sheet["blocks"] if "ink_url" in b["properties"]]
        ink = inkmod.read_upload(ws_uploads_dir(ws), group["properties"]["ink_url"])
        assert ink is not None and group["properties"]["ink_strokes"] == len(ink.strokes) == len(made.strokes)
        assert ink.space.kind == "canvas"
        assert (ink.space.width, ink.space.height) == (made.paper["width"], made.paper["height"])
        boxes = [(b["content"], b["properties"]["text_box"]) for b in sheet["blocks"] if "text_box" in b["properties"]]
        assert boxes == [(b["content"], b["text_box"]) for b in made.boxes]


def test_the_welcome_page_leads_the_library_in_every_order():
    """Placed before the Sketchbook (the manual order) and made after it (the
    library's default order, the newest change first)."""
    from gamma import seed
    ws = seed.create_account("wl_order", None)
    welcome, sketchbook = _pages(ws)
    assert welcome["properties"]["seeded"] == "welcome" and sketchbook["properties"]["seeded"] == "sketchbook"
    assert welcome["updated_at"] > sketchbook["updated_at"]


def test_the_sketchbook_exports_as_a_pdf_of_its_sheets():
    import pymupdf
    from gamma import seed
    from gamma.sketchbook import sheets
    seed.create_account("wl_export", "wl_export_pw")
    c = login("wl_export", "wl_export_pw")
    children = c.get("/api/blocks/root/children").json()["children"]
    sketchbook = next(b for b in children if b["content"] == "Sketchbook")
    r = c.get(f"/api/pages/{sketchbook['id']}/export-pdf")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    assert pymupdf.open(stream=r.content, filetype="pdf").page_count == len(sheets())
    text = _pdf_text(r.content)
    assert "Write your name here:" in text and "Changed your mind?" in text  # the text boxes, as real text


def test_existing_shared_and_filled_workspaces_are_never_seeded():
    from gamma import seed, workspaces

    # an account that already has its workspace (manage.py setup, sign-in)
    ws = make_user("wl_old", "wl_old_pw")
    assert _pages(ws) == []
    assert workspaces.ensure_personal(account_of("wl_old"), welcome=True) == ws
    assert _pages(ws) == []
    # a shared workspace (and an offline copy: workspaces.create without welcome)
    shared = workspaces.create("Lab", account_of("wl_old"), kind="shared")
    assert _pages(shared["id"]) == []
    # a workspace with a page of its own: seeding is a no-op, twice
    c = login("wl_old", "wl_old_pw")
    c.post("/api/pages", json={"title": "Mine"})
    assert seed.seed_welcome(ws, actor=account_of("wl_old")) is None
    assert [p["content"] for p in _pages(ws)] == ["Mine"]


def test_a_refused_pdf_still_leaves_the_welcome_page(monkeypatch):
    """A full quota refuses the PDF and the Sketchbook's drawings: the
    Welcome page goes in without its PDF, and no Sketchbook at all."""
    from fastapi import HTTPException
    from gamma import seed, storage

    def full(ws, data, ext):
        raise HTTPException(status_code=507, detail="storage quota exceeded")
    monkeypatch.setattr(storage, "store_file", full)
    ws = seed.create_account("wl_full", None)
    [page] = _pages(ws)
    assert page["properties"] == {"seeded": "welcome"}
    assert "## Your first five minutes" in _notes(ws, page["id"])


def test_a_broken_drawing_still_leaves_the_welcome_page(monkeypatch):
    from gamma import seed, sketchbook

    def broken(store):
        raise ValueError("path command 'A' is not drawn here")
    monkeypatch.setattr(sketchbook, "blocks", broken)
    ws = seed.create_account("wl_broken", None)
    [page] = _pages(ws)
    assert page["properties"]["seeded"] == "welcome" and page["properties"]["doc_id"]


def test_a_share_host_seeds_nothing(monkeypatch):
    """Its accounts' workspaces hold published pages only, each counted
    against the plan's cap (gamma/publish.py)."""
    from gamma import seed
    monkeypatch.setenv("GAMMA_CLOUD_ISSUER", "https://cloud.example.org")
    monkeypatch.setenv("GAMMA_CLOUD_SHARE_HOST", "1")
    ws = seed.create_cloud_account("wl_published")
    assert _pages(ws) == []


def test_a_missing_welcome_md_seeds_nothing(monkeypatch, tmp_path):
    from gamma import seed
    monkeypatch.setattr(seed, "WELCOME_MD", tmp_path / "missing.md")
    ws = seed.create_account("wl_missing", None)
    assert _pages(ws) == []
