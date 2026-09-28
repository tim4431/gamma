"""Writers that must not overwrite or duplicate what someone else wrote
meanwhile: an embed card's edit of a source block changed elsewhere, an AI
replace while the user types, concurrent get-or-create of a PDF's page, a
Logseq import into an existing page, and the writers that must tell the
page's open tabs (docs/dev/collab.md)."""

import io
import json
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from fractional_indexing import generate_key_between

from conftest import login, make_user, workspace_of
from gamma import blocks_store
from gamma.ai_context import notes_focus_section
from gamma.ai_tools import _NOTE_SNIPPET, run_agent_tool
from gamma.app import app
from gamma.db import connect_pages_db, ws_uploads_dir

USER = "wr_owner"


@pytest.fixture(scope="module")
def owner(client):
    make_user(USER, "pw")
    return login(USER, "pw"), workspace_of(USER)


def _page(c, title, props=None):
    r = c.post("/api/pages", json={"title": title, "properties": props or {}})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _block(c, parent, content):
    r = c.post("/api/blocks", json={"parent_id": parent, "content": content})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _content(c, block_id):
    return c.get(f"/api/blocks/{block_id}").json()["content"]


def _type(c, page_id, block_id, content, base):
    """The user's browser saving a keystroke run (a set with its base)."""
    r = c.post(f"/api/pages/{page_id}/ops", json={
        "client": "browser", "ops": [{"op": "set", "id": block_id, "content": content, "base": base}]})
    assert r.status_code == 200, r.text


def _recv(sock, kind):
    for _ in range(20):
        msg = sock.receive_json()
        if msg["t"] == kind:
            return msg
    raise AssertionError(f"no {kind} message")


def _pdf(tag: str) -> bytes:
    return b"%PDF-1.4 writer races " + tag.encode() + b"\n" + b"w" * 800


def _upload(c, tag):
    r = c.post("/api/uploads", files={"file": (f"{tag}.pdf", _pdf(tag), "application/pdf")})
    assert r.status_code == 200, r.text
    return r.json()["doc_id"]


# --- an embed card's edit merges into its source ----------------------------

def test_embed_card_edit_merges_into_a_source_edited_elsewhere(owner):
    c, _ = owner
    source_page = _page(c, "Embed source page")
    card_copy = "- [ ] buy qubits\nnotes v1"
    src = _block(c, source_page, card_copy)
    # The source is edited on its own page after another page's card read it.
    _type(c, source_page, src, card_copy + "\nIMPORTANT new finding", card_copy)
    # The card ticks its checkbox from its (older) copy: the edit carries it.
    r = c.put(f"/api/blocks/{src}", json={"content": card_copy.replace("- [ ]", "- [x]", 1),
                                          "base": card_copy})
    assert r.status_code == 200, r.text
    assert _content(c, src) == "- [x] buy qubits\nnotes v1\nIMPORTANT new finding"
    assert r.json()["content"] == _content(c, src)  # the card shows what is stored
    # Without a base a PUT still replaces the text (bulk writers).
    r = c.put(f"/api/blocks/{src}", json={"content": "replaced"})
    assert r.status_code == 200 and r.json()["content"] == "replaced" == _content(c, src)


# --- an AI replace keeps what the user typed meanwhile ------------------------

def _scope(page_id):
    return {"type": "page", "page_id": page_id, "actor": USER, "can_write": True, "read_texts": {}}


def test_ai_replace_keeps_what_the_user_typed_while_it_wrote(owner):
    c, ws = owner
    page_id = _page(c, "AI replace page")
    b = _block(c, page_id, "The qubit has T1 = 300 us.")
    scope = _scope(page_id)
    run_agent_tool(ws, scope, "read_block", {"block_id": b})
    # While the model writes its rewrite, the user keeps typing in the block.
    _type(c, page_id, b, "The qubit has T1 = 300 us. USER: measured again on Tuesday.",
          "The qubit has T1 = 300 us.")
    text, action = run_agent_tool(ws, scope, "edit_block",
                                  {"block_id": b, "content": "The qubit has T1 = 300 µs (energy relaxation)."})
    assert text.startswith("ok") and not action.get("error"), text
    stored = _content(c, b)
    assert "µs (energy relaxation)" in stored and "USER: measured again on Tuesday." in stored
    # A second replace this turn starts from the model's own text, so the
    # user's next keystrokes survive it too.
    _type(c, page_id, b, stored + " Again.", stored)
    run_agent_tool(ws, scope, "edit_block",
                   {"block_id": b, "content": "The qubit has T1 = 300 µs (energy relaxation), at 20 mK."})
    stored = _content(c, b)
    assert "at 20 mK" in stored and "USER: measured again on Tuesday." in stored and "Again." in stored


def test_ai_replace_needs_the_full_text_read_in_this_turn(owner):
    c, ws = owner
    page_id = _page(c, "AI read-first page")
    short = _block(c, page_id, "short note")
    long_text = "L" * (_NOTE_SNIPPET + 200)
    long = _block(c, page_id, long_text)
    scope = _scope(page_id)
    # Never read: refused, nothing written.
    text, action = run_agent_tool(ws, scope, "edit_block", {"block_id": short, "content": "new"})
    assert text.startswith("error: read the block first") and action.get("error")
    assert _content(c, short) == "short note"
    # The page outline shows the long child snipped: still not a full read.
    outline, _ = run_agent_tool(ws, scope, "read_block", {"block_id": page_id})
    assert "[truncated — read_block(" in outline
    text, _ = run_agent_tool(ws, scope, "edit_block", {"block_id": long, "content": "LL"})
    assert text.startswith("error: read the block first")
    # A rewrite copied from the snipped view is refused even after a read.
    run_agent_tool(ws, scope, "read_block", {"block_id": long})
    snipped = long_text[:_NOTE_SNIPPET] + f'… [truncated — read_block(block_id="{long}") for the full text]'
    text, _ = run_agent_tool(ws, scope, "edit_block", {"block_id": long, "content": snipped})
    assert "truncation marker" in text and _content(c, long) == long_text
    # Read in full: the short child from the outline, the long one by id.
    text, _ = run_agent_tool(ws, scope, "edit_block", {"block_id": short, "content": "rewritten"})
    assert text.startswith("ok") and _content(c, short) == "rewritten"
    text, _ = run_agent_tool(ws, scope, "edit_block", {"block_id": long, "content": "L" * 10})
    assert text.startswith("ok") and _content(c, long) == "L" * 10
    # The next turn (a new scope) has read nothing yet.
    text, _ = run_agent_tool(ws, _scope(page_id), "edit_block", {"block_id": short, "content": "again"})
    assert text.startswith("error: read the block first")
    # append / patch never need a read: they apply to the current text.
    text, _ = run_agent_tool(ws, _scope(page_id), "edit_block",
                             {"block_id": short, "mode": "append", "content": "more"})
    assert text.startswith("ok") and _content(c, short) == "rewritten\nmore"


def test_what_the_chat_showed_counts_as_read(owner):
    c, ws = owner
    page_id = _page(c, "AI context page")
    b = _block(c, page_id, "a note in the page")
    # read_page shows the notes in full.
    scope = _scope(page_id)
    run_agent_tool(ws, scope, "read_page", {"page_id": page_id})
    assert scope["read_texts"][b] == "a note in the page"
    # So does the chat's own context: the cursor block and attached chips.
    seen = {}
    payload = SimpleNamespace(focus_block_id=b, context_blocks=[], note_selections=[],
                              pages=[], page_id=page_id)
    assert notes_focus_section(ws, payload, notes_seen=seen)
    assert seen == {b: "a note in the page"}


# --- one page per PDF ---------------------------------------------------------

def _slowly(monkeypatch, module, name, delay=0.15):
    """Widen a get-or-create's window between its lookup and its insert."""
    real = getattr(module, name)

    def slow(*args, **kwargs):
        time.sleep(delay)
        return real(*args, **kwargs)
    monkeypatch.setattr(module, name, slow)


def _together(n, fn):
    barrier = threading.Barrier(n)
    out, errors = [], []

    def run():
        barrier.wait()
        try:
            out.append(fn())
        except Exception as e:  # noqa: BLE001 — reported below
            errors.append(e)
    threads = [threading.Thread(target=run) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not errors, errors
    return out


def _pages_carrying(ws, doc_id):
    with connect_pages_db(ws) as conn:
        return [r[0] for r in conn.execute(
            "SELECT id FROM unified_blocks WHERE parent_id = 'root' "
            "AND json_extract(properties, '$.doc_id') = ?", (doc_id,))]


def test_concurrent_get_or_create_makes_one_page_per_pdf(owner, monkeypatch):
    c, ws = owner
    doc_id = _upload(c, "race")
    _slowly(monkeypatch, blocks_store, "create_page")

    def create():
        with connect_pages_db(ws) as conn:
            return blocks_store.get_or_create_doc_page(conn, doc_id, "Race paper", ws=ws, actor=USER)["id"]
    ids = _together(5, create)
    assert len(set(ids)) == 1
    assert _pages_carrying(ws, doc_id) == [ids[0]]


def test_concurrent_first_clip_notes_make_one_web_clips_page(owner, monkeypatch):
    import gamma.routers.clip as clip_mod
    c, ws = owner
    _slowly(monkeypatch, clip_mod, "create_page")
    clients = [TestClient(app, cookies=c.cookies) for _ in range(4)]
    for tc in clients:
        tc.headers["X-Gamma-Workspace"] = ws
    it = iter(clients)
    lock = threading.Lock()

    def note():
        with lock:
            tc = next(it)
        r = tc.post("/api/clip/note", json={"text": "a selection", "source_url": "https://x.test/"})
        assert r.status_code == 200, r.text
        return r.json()["page_id"]
    ids = _together(4, note)
    assert len(set(ids)) == 1
    with connect_pages_db(ws) as conn:
        pages = conn.execute("SELECT id FROM unified_blocks WHERE parent_id = 'root' "
                             "AND json_extract(properties, '$.web_clips') = 1").fetchall()
        assert len(pages) == 1
        assert conn.execute("SELECT COUNT(*) FROM unified_blocks WHERE parent_id = ?",
                            (ids[0],)).fetchone()[0] == 4


def test_concurrent_attach_of_one_pdf_gives_it_one_page(owner, monkeypatch):
    import gamma.routers.pages as pages_mod
    c, ws = owner
    doc_id = _upload(c, "attach-race")
    targets = iter([_page(c, "Attach A"), _page(c, "Attach B")])
    _slowly(monkeypatch, pages_mod, "attachment_props")
    clients = iter([TestClient(app, cookies=c.cookies) for _ in range(2)])
    lock = threading.Lock()

    def attach():
        with lock:
            tc, page_id = next(clients), next(targets)
        tc.headers["X-Gamma-Workspace"] = ws
        return tc.post(f"/api/pages/{page_id}/attachment", json={"doc_id": doc_id}).status_code
    assert sorted(_together(2, attach)) == [200, 409]
    assert len(_pages_carrying(ws, doc_id)) == 1


def test_page_for_doc_answers_the_oldest_page(owner):
    _, ws = owner
    doc_id = "f" * 24
    with connect_pages_db(ws) as conn:
        # (an older copy of the data holding two: the newer one listed first)
        for page_id, created in (("wrNewer", "2026-02-01T00:00:00.000000Z"),
                                 ("wrOlder", "2026-01-01T00:00:00.000000Z")):
            position = generate_key_between(blocks_store.last_child_position(conn, "root"), None)
            conn.execute("INSERT INTO unified_blocks (id, parent_id, position, content, properties, "
                         "created_at, updated_at) VALUES (?, 'root', ?, 't', ?, ?, ?)",
                         (page_id, position, json.dumps({"doc_id": doc_id}), created, created))
        conn.commit()
        assert blocks_store.page_for_doc(conn, doc_id)[0] == "wrOlder"


# --- a Logseq import into an existing page ------------------------------------

EDN = ('{:highlights [{:id #uuid "6500e1f4-0000-4000-8000-000000000001" :page 1 :position '
       '{:bounding {:x1 1 :y1 2 :x2 3 :y2 4 :width 10 :height 10} :rects [] :page 1} '
       ':content {:text "a quote"} :properties {:color "yellow"}}]}')
MD = "- my own note, not a highlight\n- another thought\n- another thought\n"


def test_logseq_import_into_an_existing_page_tells_the_page_and_never_duplicates(owner):
    c, ws = owner
    pdf = _pdf("logseq")
    doc_id = c.post("/api/uploads", files={"file": ("paper.pdf", pdf, "application/pdf")}).json()["doc_id"]
    page_id = c.post(f"/api/blocks/by-doc/{doc_id}", json={"default_title": "paper"}).json()["id"]

    def do_import():
        return c.post("/api/import/logseq", files={
            "pdf": ("paper.pdf", pdf, "application/pdf"),
            "edn": ("hls.edn", EDN.encode(), "application/octet-stream"),
            "md": ("hls.md", MD.encode(), "text/markdown")})

    with TestClient(app, cookies=c.cookies) as sc, \
            sc.websocket_connect(f"/api/ws/page/{page_id}?ws={ws}&client=lg") as sock:
        _recv(sock, "hello")
        r = do_import()
        assert r.status_code == 200, r.text
        assert r.json()["block_id"] == page_id and r.json()["imported"] == 4
        assert _recv(sock, "reload")
    batches = c.get(f"/api/pages/{page_id}/ops?since=0").json()["batches"]
    assert batches[-1]["actor"] == USER and batches[-1]["ops"] == [{"op": "reload"}]
    children = [b["content"] for b in c.get(f"/api/blocks/{page_id}/children").json()["children"]]
    assert children.count("another thought") == 2  # a note written twice stays twice
    # A retry of the same files adds nothing.
    r = do_import()
    assert r.status_code == 200 and r.json()["imported"] == 0
    assert len(c.get(f"/api/blocks/{page_id}/children").json()["children"]) == len(children)


# --- every writer tells the page's open tabs ----------------------------------

def test_clip_of_a_saved_paper_files_it_through_the_page_room(owner):
    c, ws = owner
    doc_id = _upload(c, "clip-dedup")
    body = {"doc_id": doc_id, "title": "Clipped paper", "fetch_metadata": False,
            "source_url": "https://x.test/abs/dedup"}
    r = c.post("/api/clip", json=body)
    assert r.status_code == 200, r.text
    page_id = r.json()["block_id"]
    with TestClient(app, cookies=c.cookies) as sc, \
            sc.websocket_connect(f"/api/ws/page/{page_id}?ws={ws}&client=cl") as sock:
        _recv(sock, "hello")
        r = c.post("/api/clip", json={**body, "folder": "to-read", "labels": ["qec"]})
        assert r.status_code == 200 and r.json()["existed"], r.text
        msg = _recv(sock, "ops")
        assert msg["actor"] == USER
        assert msg["ops"][0]["props"]["folder"] == "to-read"


def test_embedded_annotations_under_a_nested_block_reload_its_page(owner, monkeypatch):
    from PyPDF2 import PdfWriter

    import gamma.routers.imports as imports_mod
    c, ws = owner
    page_id = _page(c, "Annotated paper")
    nested = _block(c, page_id, "a section")
    buf = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(buf)
    path = ws_uploads_dir(ws) / "writer-races-annots.pdf"
    path.write_bytes(buf.getvalue())
    position = {"pageNumber": 1, "boundingRect": {"x1": 1, "y1": 1, "x2": 9, "y2": 9,
                                                  "width": 200, "height": 200, "pageNumber": 1}, "rects": []}
    monkeypatch.setattr(imports_mod, "_extract_pdf_annotations", lambda reader: [
        {"key": "1:/Highlight:1:1:9", "page": 1, "content": "", "quote": "q",
         "color": "rgba(255, 226, 143, 0.65)", "position": position}])
    with TestClient(app, cookies=c.cookies) as sc, \
            sc.websocket_connect(f"/api/ws/page/{page_id}?ws={ws}&client=an") as sock:
        _recv(sock, "hello")
        result = imports_mod.import_embedded_annotations(ws, nested, path, False, actor=USER)
        assert result["imported"] == 1
        assert _recv(sock, "reload")
    batches = c.get(f"/api/pages/{page_id}/ops?since=0").json()["batches"]
    assert batches[-1]["ops"] == [{"op": "reload"}] and batches[-1]["actor"] == USER
    with connect_pages_db(ws) as conn:
        assert not conn.execute("SELECT 1 FROM page_ops WHERE page_id = ?", (nested,)).fetchone()
