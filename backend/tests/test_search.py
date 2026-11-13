"""Search: GET /api/search — one query over the notes index (block_fts in
pages.db, gamma/block_index.py) and the document index (text_fts): result
shape, ordering, folder scope, and a search right after every kind of block
write finding what it wrote (the triggers, tests/test_notes_index.py) — the
Ctrl+F panel's POST /block-search and /document-search, kept to the folders
and labels of its chips and the page ids its predicates send — plus the
document index itself: PDF and file hits, separator tolerance, stale index
versions, the reindex endpoint, the indexing job."""

import secrets
import sqlite3

from conftest import (account_of, doc_props, login, make_folder, make_label, make_page, make_user, mark_work_folder,
                      work_props, workspace_of)
from gamma import jobs, storage
from gamma.auth import DISK_CLIENT
from gamma.blocks_store import create_page, root_pages
from gamma.db import connect_data_db, connect_pages_db
from gamma.documents.model import document, stored_name, url_hash
from gamma.search import indexer
from gamma.search.index import Doc, store_doc
from gamma.textnorm import normalize_text
from test_disk_push import _pdf


def _index_pdf(user, doc_id, pages):
    """Store index rows the way the indexer does (search.index.store_doc:
    normalized text, current version — otherwise the endpoint schedules a
    re-index that would race the test and delete these rows)."""
    with connect_data_db(workspace_of(user)) as conn:
        store_doc(conn, doc_id, [(p, normalize_text(text)) for p, text in pages])


def _block(c, parent, content, props=None):
    r = c.post("/api/blocks", json={"parent_id": parent, "content": content, "properties": props or {}})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _search(c, q, **params):
    r = c.get("/api/search", params={"q": q, **params})
    assert r.status_code == 200, r.text
    return r.json()


def _documents(c, q, **body):
    r = c.post("/api/document-search", json={"q": q, **body})
    assert r.status_code == 200, r.text
    return r.json()


def test_search_mixes_notes_and_pdf_hits():
    make_user("searcher", "pw")
    c = login("searcher", "pw")
    paper = make_page(c, "Wombat paper", properties={**doc_props("srchdoc001"), "folders": [make_folder(c, "zoo")]})
    _index_pdf("searcher", "srchdoc001", [(4, "the wombat considered superconducting qubits")])
    notes = make_page(c, "Field notes", properties={"folders": [make_folder(c, "zoo/trips")]})
    top = _block(c, notes["id"], "saw a wombat at dusk")
    nested = _block(c, top, "the wombat was digging")
    hl = _block(c, paper["id"], "wombat highlight note",
                {"pdf_position": {"pageNumber": 1}, "quote": "unrelated quoted passage"})

    body = _search(c, "wombat")
    assert body["indexing"] == 0
    results = body["results"]
    sources = [r["source"] for r in results]
    assert sources == ["notes"] * 3 + ["pdf"]  # notes first, then PDF text
    by_block = {r["block_id"]: r for r in results if r["source"] == "notes"}
    assert set(by_block) == {top, nested, hl}  # highlights are notes too
    assert by_block[nested]["page_id"] == notes["id"] and by_block[nested]["title"] == "Field notes"
    assert "wombat" in by_block[nested]["snippet"]
    assert by_block[hl]["page_id"] == paper["id"] and "unrelated" not in by_block[hl]["snippet"]
    assert set(by_block[top]) == {"source", "block_id", "page_id", "title", "snippet"}
    pdf = results[-1]
    assert pdf == {"source": "pdf", "block_id": paper["id"], "page_id": paper["id"],
                   "doc_id": "srchdoc001", "title": "Wombat paper", "page": 4,
                   "snippet": pdf["snippet"]}
    assert "superconducting" in pdf["snippet"]
    # Page titles are not part of the notes index (root blocks are not indexed).
    assert all(r["block_id"] != notes["id"] for r in _search(c, "field")["results"])

    # Normalization: the query goes through the same rules as the index.
    _block(c, notes["id"], "a coherent 3,000-qubit system")
    assert any(r["source"] == "notes" for r in _search(c, "3000 qubit")["results"])

    # limit caps each source separately.
    body = _search(c, "wombat", limit=1)
    assert [r["source"] for r in body["results"]] == ["notes", "pdf"]

    # Empty / malformed queries are empty results, never errors.
    assert _search(c, "")["results"] == []
    assert _search(c, '"')["results"] == []


def test_search_folder_scope():
    c = login("searcher", "pw")
    all_hits = _search(c, "wombat")["results"]
    assert {r["source"] for r in all_hits} == {"notes", "pdf"}
    trips = _search(c, "wombat", scope=make_folder(c, "zoo/trips"))["results"]  # the scope is a folder id
    assert trips and all(r["title"] == "Field notes" for r in trips)
    zoo = _search(c, "wombat", scope=make_folder(c, "zoo"))["results"]  # a folder includes its subfolders
    assert {r["title"] for r in zoo} == {"Field notes", "Wombat paper"}
    assert _search(c, "wombat", scope=make_folder(c, "elsewhere"))["results"] == []
    assert _search(c, "wombat", scope="zoo")["results"] == []  # a path names no folder


def test_a_search_finds_every_write_at_once():
    """Whatever wrote the blocks — edits, creates, deletes, subtree
    replacement, cross-page moves, trashing and restoring the page — the
    next search reads what it left: nothing is rebuilt, nothing is pending."""
    c = login("searcher", "pw")
    page = make_page(c, "Dirty page")
    other = make_page(c, "Other page")
    a = _block(c, page["id"], "alpha lorem")
    b = _block(c, other["id"], "beta lorem")
    body = _search(c, "lorem")
    assert {r["block_id"] for r in body["results"]} == {a, b} and body["indexing"] == 0

    # PUT /blocks/{id}: the old text is gone, the new one found.
    assert c.put(f"/api/blocks/{a}", json={"content": "gamma ipsum"}).status_code == 200
    assert [r["block_id"] for r in _search(c, "gamma ipsum")["results"]] == [a]
    assert a not in {r["block_id"] for r in _search(c, "alpha")["results"]}

    # PUT /blocks/{id}/children (autosave): whole subtree replaced.
    r = c.put(f"/api/blocks/{page['id']}/children",
              json={"blocks": [{"content": "delta one", "children": [{"content": "delta two"}]}]})
    assert r.status_code == 200
    hits = _search(c, "delta")["results"]
    assert len(hits) == 2 and all(h["page_id"] == page["id"] for h in hits)
    assert _search(c, "gamma ipsum")["results"] == []
    child = next(h["block_id"] for h in hits if "two" in h["snippet"])

    # DELETE /blocks/{id} of a nested block.
    assert c.delete(f"/api/blocks/{child}").status_code == 200
    assert [h["snippet"] for h in _search(c, "delta")["results"]] == ["delta one"]

    # POST /blocks/{id}/reorder across pages: the block re-keys to its new page.
    assert c.post(f"/api/blocks/{b}/reorder", json={"parent_id": page["id"]}).status_code == 200
    (hit,) = _search(c, "beta")["results"]
    assert hit["block_id"] == b and hit["page_id"] == page["id"] and hit["title"] == "Dirty page"

    # A page in Recently deleted is out of the search, back when restored;
    # deleted for good, its rows go.
    assert c.delete(f"/api/blocks/{page['id']}").status_code == 200
    assert _search(c, "delta")["results"] == [] and _search(c, "beta")["results"] == []
    assert c.post(f"/api/trash/{page['id']}/restore").status_code == 200
    assert [h["block_id"] for h in _search(c, "beta")["results"]] == [b]
    assert c.delete(f"/api/blocks/{page['id']}").status_code == 200
    assert c.delete(f"/api/trash/{page['id']}").status_code == 200
    with connect_pages_db(workspace_of("searcher")) as conn:
        assert not conn.execute("SELECT 1 FROM block_fts WHERE block_fts MATCH 'delta OR beta'").fetchone()
    assert _search(c, "lorem")["results"] == []  # other's only block moved away


def test_document_search_and_block_search_shapes():
    """The panel's document group answers the PDF hit GET /search answers
    (the panel reads one shape); /block-search keeps its own; the PDF-only
    predecessor is gone."""
    c = login("searcher", "pw")
    (hit,) = [h for h in _documents(c, "wombat")["results"] if h["doc_id"] == "srchdoc001"]
    assert hit == next(h for h in _search(c, "wombat")["results"] if h["source"] == "pdf")
    r = c.get("/api/block-search", params={"q": "wombat"})
    assert r.status_code == 200 and r.json()["blocks"]
    assert c.get("/api/pdf-search", params={"q": "wombat"}).status_code in (404, 405)
    assert c.post("/api/pdf-search", json={"q": "wombat"}).status_code in (404, 405)


def test_root_pages_names_each_pages_document():
    """``root_pages`` answers the document a page owns, of any format, with
    its format, extension and place; no hash for a copy or no document."""
    c = login("searcher", "pw")
    paper = make_page(c, "Root paper", properties=doc_props("srchroot01"))
    tex = make_page(c, "main.tex", properties=doc_props("srchroot02", name="main.tex", format="text", ext=".tex"))
    copy = make_page(c, "Root copy", properties=doc_props(url="/api/uploads/srchroot01.pdf"))
    note = make_page(c, "Root note")
    with connect_pages_db(workspace_of("searcher")) as conn:
        found = root_pages(conn)
    assert found[paper["id"]] == {"title": "Root paper", "doc_id": "srchroot01", "format": "pdf", "ext": ".pdf",
                                  "at": "store", "name": found[paper["id"]]["name"]}
    assert found[tex["id"]] == {"title": "main.tex", "doc_id": "srchroot02", "format": "text", "ext": ".tex",
                                "at": "store", "name": "main.tex"}
    assert found[copy["id"]]["doc_id"] == "" and found[copy["id"]]["format"] == "pdf"
    assert found[note["id"]] == {"title": "Root note", "doc_id": "", "format": "", "ext": "", "at": "", "name": ""}


def test_block_search_keeps_to_the_chips_and_pages_on():
    """The Ctrl+F panel's notes group: its folder and label chips go along
    as ``scope`` (each must hold), so a match inside them is found however
    many newer ones elsewhere come first; ``next`` goes on where a page
    stopped, nothing listed twice or passed by — blocks written in one
    request (one timestamp) too."""
    make_user("srch_scope", "pw")
    c = login("srch_scope", "pw")
    folder, sub, label = make_folder(c, "atoms"), make_folder(c, "atoms/traps"), make_label(c, "toread")
    inside = make_page(c, "Inside", properties={"folders": [sub], "labels": [label]})
    filed = make_page(c, "Filed", properties={"folders": [folder]})
    old = _block(c, inside["id"], "quokka in the trap")  # the oldest: last, newest first
    filed_hit = _block(c, filed["id"], "quokka filed away")
    elsewhere = make_page(c, "Elsewhere")
    newer = [_block(c, elsewhere["id"], f"quokka note {i}") for i in range(25)]

    def notes(**params):
        r = c.get("/api/block-search", params={"q": "quokka", "limit": 20, **params})
        assert r.status_code == 200, r.text
        return r.json()

    def ids(body):
        return [b["id"] for b in body["blocks"]]

    first = notes()
    assert old not in ids(first) and first["next"]  # the newest 20 are all elsewhere
    assert ids(notes(scope=folder)) == [filed_hit, old]  # a folder takes in its subfolders
    assert ids(notes(scope=sub)) == ids(notes(scope=label)) == [old]
    assert ids(notes(scope=f"{folder},{label}")) == [old]
    assert notes(scope=f"{folder},{make_label(c, 'unread')}") == {"blocks": []}
    assert notes(scope="nosuchid") == {"blocks": []}
    assert "next" not in notes(scope=folder)

    second = notes(cursor=first["next"])
    assert "next" not in second
    assert ids(first) + ids(second) == [*reversed(newer), filed_hit, old]
    assert c.get("/api/block-search", params={"q": "quokka", "cursor": "nonsense"}).status_code == 400

    r = c.put(f"/api/blocks/{elsewhere['id']}/children",
              json={"blocks": [{"content": f"quokka tie {i}"} for i in range(12)]})
    assert r.status_code == 200, r.text
    whole = ids(notes(limit=100))
    assert len(whole) == 14  # the twelve, filed_hit and old
    pages, cursor = [], ""
    while True:
        body = notes(limit=5, cursor=cursor)
        pages.append(ids(body))
        cursor = body.get("next", "")
        if not cursor:
            break
    assert [len(p) for p in pages] == [5, 5, 4]
    assert [i for p in pages for i in p] == whole


def test_block_search_post_keeps_to_the_pages_and_kinds_it_names():
    """The panel's notes group as a POST: the folders and labels of its
    chips (resolved on the server) and the page ids its predicates keep go
    in the body, so a match inside them is found however many newer ones
    elsewhere come first, ``next`` goes on without a repeat or a skip, and
    ``kinds`` keeps to the block kinds ``in:`` names. What it answers is
    what the GET answers."""
    make_user("srch_post", "pw")
    c = login("srch_post", "pw")
    folder = make_folder(c, "atoms")
    label = make_label(c, "marked")
    inside = make_page(c, "Inside", properties={"folders": [folder]})
    old = _block(c, inside["id"], "quokka in the trap")
    marked = _block(c, inside["id"], "quokka marked", {"pdf_position": {"pageNumber": 1}})
    elsewhere = make_page(c, "Elsewhere", properties={"labels": [label]})
    newer = [_block(c, elsewhere["id"], f"quokka note {i}") for i in range(25)]
    trashed = make_page(c, "Trashed")
    _block(c, trashed["id"], "quokka in the bin")
    assert c.delete(f"/api/blocks/{trashed['id']}").status_code == 200

    def notes(status=200, **body):
        r = c.post("/api/block-search", json={"q": "quokka", "limit": 20, **body})
        assert r.status_code == status, r.text
        return r.json()

    def ids(body):
        return [b["id"] for b in body["blocks"]]

    first = notes()
    assert first == c.get("/api/block-search", params={"q": "quokka", "limit": 20}).json()
    assert old not in ids(first) and first["next"]
    assert ids(notes(pages=[inside["id"]])) == [marked, old]
    assert ids(notes(folders=[folder])) == [marked, old]  # one id for the folder's pages
    assert ids(notes(folders=[folder], pages=[inside["id"], elsewhere["id"]])) == [marked, old]  # scope ∩ pages
    assert notes(folders=[folder], pages=[elsewhere["id"]]) == {"blocks": []}
    assert notes(folders=[folder], labels=[label]) == {"blocks": []}  # every chip holds
    assert len(ids(notes(labels=[label], limit=100))) == 25
    assert "next" not in notes(pages=[inside["id"]])
    assert notes(pages=[]) == {"blocks": []}
    # a folder's id, a trashed page and a foreign id are no pages of the library
    assert notes(pages=[folder, trashed["id"], "nosuchpage00"]) == {"blocks": []}
    second = notes(cursor=first["next"])
    assert "next" not in second
    assert ids(first) + ids(second) == [*reversed(newer), marked, old]
    assert ids(notes(kinds=["highlight"])) == [marked]
    assert ids(notes(kinds=["note"], pages=[inside["id"]])) == [old]
    assert ids(notes(kinds=["page"], q="inside")) == [inside["id"]]
    hit = notes(pages=[inside["id"]], limit=1)["blocks"][0]
    assert hit["page_root_id"] == inside["id"] and hit["page_title"] == "Inside" and hit["kind"] == "highlight"
    assert notes(400, kinds=["bogus"])["detail"] == "unknown kind: bogus"
    assert notes(400, cursor="nonsense")["detail"] == "bad cursor"
    assert notes(q="") == {"blocks": [], "error": "empty query"}
    too_many = notes(413, pages=[f"p{i:011d}" for i in range(50_001)])
    assert "50000" in too_many["detail"]
    assert notes(pages=[f"p{i:011d}" for i in range(50_000)]) == {"blocks": []}, "the cap itself passes"
    assert notes(regex=1) == first, "a regex field is ignored: the query is text"


def test_document_search_keeps_to_the_pages_it_names():
    """The panel's document group: the documents of the ``pages`` only, a
    document's best three per round, ``next`` and the 400 for a bad cursor,
    ``formats`` keeping to PDFs or files, and ``indexing`` over those pages
    alone."""
    make_user("srch_pdfpost", "pw")
    c = login("srch_pdfpost", "pw")
    book = make_page(c, "Big book", properties=doc_props("ppbook001"))
    _index_pdf("srch_pdfpost", "ppbook001", [(p, f"axolotl axolotl on page {p}") for p in range(1, 11)])
    paper = make_page(c, "Paper", properties=doc_props("pppaper01"))
    _index_pdf("srch_pdfpost", "pppaper01", [(1, "an axolotl in the paper")])
    unindexed = make_page(c, "Not indexed", properties=doc_props("ppmissing1"))

    def hits(status=200, **body):
        r = c.post("/api/document-search", json={"q": "axolotl", **body})
        assert r.status_code == status, r.text
        return r.json()

    def keys(body):
        return [(h["doc_id"], h["page"]) for h in body["results"]]

    # First, before any search hands the unindexed paper to the indexer
    # (which, finding no file, records it as done).
    assert hits(pages=[paper["id"], unindexed["id"]])["indexing"] == 1
    assert hits(pages=[paper["id"]])["indexing"] == 0
    assert keys(hits(pages=[paper["id"]])) == [("pppaper01", 1)]
    assert hits(pages=[]) == {"results": [], "indexing": 0}
    first = hits(pages=[book["id"], paper["id"]], limit=4)
    assert [d for d, _ in keys(first)] == ["ppbook001"] * 3 + ["pppaper01"] and first["next"] == "4"
    rest = hits(pages=[book["id"], paper["id"]], limit=100, cursor="4")
    assert len(keys(rest)) == 7 and "next" not in rest
    assert keys(hits(pages=[book["id"], paper["id"]], exclude=book["id"])) == [("pppaper01", 1)]
    assert keys(hits(pages=[paper["id"]], formats=["text"])) == []
    assert keys(hits(pages=[paper["id"]], formats=["pdf"])) == [("pppaper01", 1)]
    assert hits(400, cursor="x")["detail"] == "bad cursor"
    assert hits(400, formats=["image"])["detail"] == "unknown format: image"
    assert "50000" in hits(413, pages=["x"] * 50_001)["detail"]


# --- the document index ----------------------------------------------------------

def test_document_search_hits_indexed_docs(guest):
    user = guest.get("/api/session").json()["user"]
    make_page(guest, "FTS paper", properties=doc_props("ftsdoc001"))
    _index_pdf(user, "ftsdoc001", [(3, "the wombat considered superconducting qubits carefully")])

    hits = _documents(guest, "wombat superconducting")["results"]
    assert any(h["page"] == 3 and h["title"] == "FTS paper" and h["doc_id"] == "ftsdoc001"
               for h in hits)

    # unknown terms → no hits, no error
    assert _documents(guest, "zzznothingzzz")["results"] == []


def test_document_search_is_separator_tolerant(guest):
    """"3000" must find "3,000-qubit": the index stores normalized text and
    the query is normalized the same way."""
    user = guest.get("/api/session").json()["user"]
    make_page(guest, "Qubit paper", properties=doc_props("ftsdoc002"))
    _index_pdf(user, "ftsdoc002",
               [(1, "Continuous operation of a coherent 3,000-qubit system")])

    for q in ("3000", "3,000", "3000-qubit", "3000 qubit system"):
        hits = _documents(guest, q)["results"]
        assert any(h["doc_id"] == "ftsdoc002" for h in hits), f"query {q!r} missed"


def test_document_search_scope_and_per_document_cap():
    """The panel's document group: a book that matches on every page lists
    its best three, then waits for every other document's best three;
    ``next`` pages on through the same order; the chips (``folders``,
    ``labels``, resolved on the server) and the open paper (``exclude``)
    are applied before the ranking, so a paper outranked by the whole
    library is still found inside its folder."""
    make_user("srch_pdfpages", "pw")
    c = login("srch_pdfpages", "pw")
    user = "srch_pdfpages"
    folder, label = make_folder(c, "zoo"), make_label(c, "amphibians")
    book = make_page(c, "Big book", properties=doc_props("pgbook001"))
    _index_pdf(user, "pgbook001", [(p, f"axolotl axolotl on page {p}") for p in range(1, 41)])
    for n in range(6):
        make_page(c, f"Paper {n}", properties=doc_props(f"pgpaper00{n}"))
        _index_pdf(user, f"pgpaper00{n}", [(1, f"an axolotl in paper {n} among many other words here"),
                                           (2, f"one more axolotl in paper {n} with yet more words around it")])
    filed = make_page(c, "Filed paper", properties={**doc_props("pgfiled01"), "folders": [folder], "labels": [label]})
    _index_pdf(user, "pgfiled01", [(7, "a lone axolotl " + "and plenty of unrelated words " * 20)])

    def hits(**body):
        return _documents(c, "axolotl", **body)

    def keys(body):
        return [(h["doc_id"], h["page"]) for h in body["results"]]

    first = hits(limit=15)
    assert sum(d == "pgbook001" for d, _ in keys(first)) == 3
    assert {d for d, _ in keys(first)} >= {f"pgpaper00{n}" for n in range(6)}
    assert first["next"] == "15"

    whole = keys(hits(limit=100))
    assert len(whole) == len(set(whole)) == 40 + 12 + 1
    pages, cursor = [], ""
    while True:
        body = hits(limit=15, cursor=cursor)
        pages += keys(body)
        cursor = body.get("next", "")
        if not cursor:
            break
    assert pages == whole

    assert keys(hits(folders=[folder])) == keys(hits(labels=[label])) == [("pgfiled01", 7)]
    assert keys(hits(folders=[folder], labels=[make_label(c, "reptiles")])) == []
    assert keys(hits(limit=5, folders=[folder], cursor="5")) == []
    assert keys(hits(folders=[folder], pages=[filed["id"], book["id"]])) == [("pgfiled01", 7)]  # scope ∩ pages
    assert keys(hits(folders=[folder], pages=[book["id"]])) == []
    assert keys(hits(folders=["nosuchfolder"])) == []
    assert "pgbook001" not in {d for d, _ in keys(hits(limit=100, exclude=book["id"]))}
    assert filed["id"] in {h["block_id"] for h in hits(limit=100)["results"]}


def _indexing_jobs(c):
    r = c.get("/api/jobs")
    assert r.status_code == 200, r.text
    return [j for j in r.json()["jobs"] if j["kind"] == "indexing"]


def _settle(c):
    """Wait for the workspace's indexing job, if one runs."""
    for job in _indexing_jobs(c):
        jobs.wait(job["id"])


METHOD = [f"line {n} of the method" for n in range(1, 51)]
METHOD[44] = r"\subsection{Fit} We fit the model by gradient descent."


def test_a_file_hit_names_its_path_and_line():
    """A text file's runs are indexed like a PDF's pages: a hit names the
    file's path in its work folder, the line the query matched and the
    run's span, on every page of the hash (two files of equal bytes are
    two hits with two paths); a library text document has no path. A
    folder chip of the work folder reaches its files by one id."""
    make_user("srch_files", "pw")
    c = login("srch_files", "pw")
    ws, account = workspace_of("srch_files"), account_of("srch_files")
    W, D = make_folder(c, "srchW"), make_folder(c, "srchW/sections")
    mark_work_folder(c, W)
    tex, txt = secrets.token_hex(32), secrets.token_hex(32)
    storage.put_upload(ws, stored_name(tex, ".tex"), "\n".join(METHOD).encode())
    storage.put_upload(ws, stored_name(txt, ".txt"), b"a note on gradient flow\n")
    doc = document(tex, name="method.tex", at="path", format="text", ext=".tex")
    with connect_pages_db(ws) as conn:
        method = create_page(conn, "method.tex", work_props(W, "method.tex", doc=doc, directory=D),
                             actor=account, client=DISK_CLIENT)["id"]
        twin = create_page(conn, "method.tex", work_props(W, "method.tex", doc=doc),
                           actor=account, client=DISK_CLIENT)["id"]
    library = make_page(c, "notes.txt", properties=doc_props(txt, name="notes.txt", format="text", ext=".txt"))["id"]

    assert _documents(c, "gradient")["indexing"] == 2
    _settle(c)
    body = _documents(c, "gradient")
    assert body["indexing"] == 0
    by_page = {h["page_id"]: h for h in body["results"]}
    assert set(by_page) == {method, twin, library}
    hit = by_page[method]
    assert hit == {"source": "file", "block_id": method, "page_id": method, "doc_id": tex, "title": "method.tex",
                   "path": "sections/method.tex", "work_folder": W, "format": "text", "language": "latex",
                   "line": 45, "lines": [41, 50], "snippet": hit["snippet"]}
    assert "gradient descent" in hit["snippet"] and "\n" not in hit["snippet"]
    assert by_page[twin]["path"] == "method.tex" and by_page[twin]["line"] == 45
    assert {k: by_page[library][k] for k in ("path", "work_folder", "language", "line", "lines")} == {
        "path": "", "work_folder": "", "language": "plaintext", "line": 1, "lines": [1, 1]}

    assert {h["page_id"] for h in _documents(c, "gradient", folders=[W])["results"]} == {method, twin}
    assert [h["page_id"] for h in _documents(c, "gradient", folders=[D])["results"]] == [method]
    assert _documents(c, "gradient", formats=["pdf"])["results"] == []
    assert _documents(c, "subsection fit", folders=[D])["results"][0]["line"] == 45
    found = _search(c, "gradient")["results"]
    assert {h["page_id"] for h in found if h["source"] == "file"} == {method, twin, library}


SECRET = b"API_KEY=sk-livesecret\nDB_PASSWORD=hunter2wombat\n"


def _rename(ws, page_id, name):
    with connect_pages_db(ws) as conn:
        conn.execute("UPDATE unified_blocks SET content = ?, properties = json_set(properties, '$.doc.name', ?) "
                     "WHERE id = ?", (name, name, page_id))
        conn.commit()


def test_a_secrets_file_is_never_a_hit_whatever_its_bytes_were_indexed_as():
    """The index is keyed by the bytes, the secrets rule by the name: a
    file indexed as settings.txt and renamed to .env (same bytes, same
    hash) is no hit in any search, nor a copy of a .env whose owner is
    gone; a .env never indexed is found once renamed to a plain name."""
    from gamma.ai_tools import run_agent_tool

    make_user("srch_secret", "pw")
    c = login("srch_secret", "pw")
    ws = workspace_of("srch_secret")
    h = secrets.token_hex(32)
    storage.put_upload(ws, stored_name(h, ".txt"), SECRET)
    page = make_page(c, "settings.txt", properties=doc_props(h, name="settings.txt", format="text", ext=".txt"))["id"]
    _documents(c, "hunter2wombat")
    _settle(c)
    assert _documents(c, "hunter2wombat")["results"]

    _rename(ws, page, ".env")
    assert _documents(c, "hunter2wombat") == {"results": [], "indexing": 0}
    assert not [h for h in _search(c, "hunter2wombat")["results"] if h["source"] == "file"]
    said, _ = run_agent_tool(ws, {"type": "folder", "folder": ""}, "search_library", {"query": "hunter2wombat"})
    assert "DB_PASSWORD" not in said and "No notes or document text match" in said

    other = secrets.token_hex(32)
    storage.put_upload(ws, stored_name(other, ".bin"), SECRET + b"OTHER=zzcopiedsecret\n")
    original = make_page(c, ".env", properties=doc_props(other, name=".env", format="text", ext=".bin"))["id"]
    make_page(c, ".env", properties=doc_props(url=f"/api/uploads/{other}.bin", name=".env", format="text",
                                              ext=".bin", copy=True))
    c.delete(f"/api/blocks/{original}").raise_for_status()
    c.delete(f"/api/trash/{original}").raise_for_status()
    for _ in range(2):
        said, _ = run_agent_tool(ws, {"type": "folder", "folder": ""}, "search_library", {"query": "zzcopiedsecret"})
        assert "OTHER=" not in said and "No notes or document text match" in said
        _settle(c)
    with connect_data_db(ws) as conn:
        assert conn.execute("SELECT 1 FROM text_fts_docs WHERE doc_id = ?", (other,)).fetchone() is None

    _rename(ws, page, "settings.txt")
    assert _documents(c, "hunter2wombat")["results"]
    fresh = secrets.token_hex(32)
    storage.put_upload(ws, stored_name(fresh, ".txt"), b"TOKEN=zzfreshsecret\n")
    env = make_page(c, ".env", properties=doc_props(fresh, name=".env", format="text", ext=".txt"))["id"]
    assert _documents(c, "zzfreshsecret") == {"results": [], "indexing": 0}
    _rename(ws, env, "token.txt")
    _documents(c, "zzfreshsecret")
    _settle(c)
    assert [h["page_id"] for h in _documents(c, "zzfreshsecret")["results"]] == [env]


def test_a_pdf_at_an_address_is_indexed_through_the_chats_resolver(monkeypatch, tmp_path):
    """A page opened from an address carries the address's hash: the
    indexer reads it through the chat's guarded resolver (which downloads
    it when it is not stored), never a stored file's path alone."""
    make_user("srch_remote", "pw")
    c = login("srch_remote", "pw")
    ws = workspace_of("srch_remote")
    url = "https://example.org/srch/remote.pdf"
    page = make_page(c, "Remote paper", properties=doc_props(url_hash(url), url=url))
    path = tmp_path / "remote.pdf"
    path.write_bytes(_pdf("the remote quokka"))
    asked = []
    monkeypatch.setattr("gamma.ai_context.pdf_path", lambda ws, doc_id: asked.append(doc_id) or path)
    with connect_pages_db(ws) as conn:
        doc = root_pages(conn)[page["id"]]
    assert doc["at"] == "url" and doc["doc_id"] == url_hash(url)
    found = indexer.index_doc(ws, Doc(doc["doc_id"], "pdf", ".pdf", "url"))
    assert asked == [url_hash(url)]
    assert found[0] == url_hash(url) and "quokka" in found[1][0][1] and found[2]["format"] == "pdf"


def test_stale_index_version_counts_as_missing(guest):
    from gamma.db import ws_db_path
    from gamma.search.index import ensure_schema

    user = guest.get("/api/session").json()["user"]
    make_page(guest, "Stale paper", properties=doc_props("ftsdoc003"))
    with sqlite3.connect(ws_db_path(workspace_of(user), "data.db")) as conn:
        ensure_schema(conn)
        conn.execute("INSERT OR REPLACE INTO text_fts_docs (doc_id, indexed_at, units, ver) "
                     "VALUES ('ftsdoc003', '2025', 1, 0)")  # pre-normalization row
        conn.commit()

    assert _documents(guest, "anything")["indexing"] >= 1  # stale doc scheduled for re-indexing


def test_search_reindex_marks_everything_stale(guest):
    from gamma.db import ws_db_path
    from gamma.textnorm import INDEX_VERSION

    user = guest.get("/api/session").json()["user"]
    make_page(guest, "Rebuild me", properties=doc_props("ftsdoc004"))
    _index_pdf(user, "ftsdoc004", [(1, "some indexed text")])

    r = guest.post("/api/search-reindex")
    assert r.status_code == 200
    body = r.json()
    assert body["scheduled"] >= 1 or body["busy"]  # started, or an indexer already runs

    # The doc's bookkeeping row survives (ver may be 0 = stale or already
    # re-stamped by the background thread — no PDF file makes that instant).
    with sqlite3.connect(ws_db_path(workspace_of(user), "data.db")) as conn:
        ver = conn.execute("SELECT ver FROM text_fts_docs WHERE doc_id = 'ftsdoc004'").fetchone()[0]
    assert ver in (0, INDEX_VERSION)


def test_search_reindex_targeted_single_doc(guest):
    """doc_ids re-indexes just those documents: no global stale stamp, and
    ids outside the caller's library are ignored."""
    from gamma.db import ws_db_path
    from gamma.textnorm import INDEX_VERSION

    user = guest.get("/api/session").json()["user"]
    make_page(guest, "Keep me", properties=doc_props("ftsdoc005"))
    make_page(guest, "Reindex me", properties=doc_props("ftsdoc006"))
    _index_pdf(user, "ftsdoc005", [(1, "already indexed text")])

    r = guest.post("/api/search-reindex", json={"doc_ids": ["ftsdoc006", "not-my-doc"]})
    assert r.status_code == 200
    body = r.json()
    assert body["scheduled"] == 1 or body["busy"]

    # The untouched doc keeps its current-version stamp (a full rebuild would
    # have zeroed it).
    with sqlite3.connect(ws_db_path(workspace_of(user), "data.db")) as conn:
        ver = conn.execute(
            "SELECT ver FROM text_fts_docs WHERE doc_id = 'ftsdoc005'").fetchone()[0]
    assert ver == INDEX_VERSION


def test_indexing_is_the_workspaces_job(guest):
    """A reindex runs as the workspace's indexing job: listed with its
    documents counted, one row per workspace however often it runs."""
    make_page(guest, "Indexed as a job", properties=doc_props("ftsjob001"))
    for _ in range(2):
        _settle(guest)
        assert guest.post("/api/search-reindex", json={"doc_ids": ["ftsjob001"]}).json()["scheduled"] == 1
    listed = _indexing_jobs(guest)
    assert len(listed) == 1, listed
    job = jobs.wait(listed[0]["id"])
    assert job["owner"] == jobs.WORKSPACE and job["workspace"] == workspace_of(guest.get("/api/session").json()["user"])
    assert job["state"] == "done" and job["result"] == {"documents": 1}
    assert job["progress"] == {"done": 1, "total": 1, "unit": "documents"}


def test_a_running_indexer_is_stopped_between_documents(guest, monkeypatch):
    """Stopping the job ends it before the next document; the rest stay
    stale for the next search. A second run meanwhile is refused as busy."""
    import threading

    _settle(guest)
    user = guest.get("/api/session").json()["user"]
    ids = [f"ftsstop00{i}" for i in range(3)]
    for doc_id in ids:
        make_page(guest, f"Stop at {doc_id}", properties=doc_props(doc_id))
    reached, release, seen = threading.Event(), threading.Event(), []

    def slow(ws, doc):
        seen.append(doc.hash)
        reached.set()
        release.wait(10)

    docs = {d: Doc(d, "pdf", ".pdf", "store") for d in ids}
    monkeypatch.setattr(indexer, "index_doc", slow)
    assert indexer.index_missing_async(workspace_of(user), docs) is True
    assert reached.wait(10)
    assert indexer.index_missing_async(workspace_of(user), docs) is False  # one per workspace
    job = next(j for j in _indexing_jobs(guest) if j["state"] == "running")
    assert job["stoppable"] and job["progress"]["total"] == 3
    assert guest.post(f"/api/jobs/{job['id']}/cancel").status_code == 200
    release.set()
    assert jobs.wait(job["id"])["state"] == "cancelled"
    assert seen == ids[:1]


def test_a_stopped_run_stores_what_it_extracted(guest, monkeypatch):
    """The job writes its documents in batches: one stopped between two
    documents has stored the ones it read, the rest stay missing."""
    import threading

    from gamma.search.index import missing

    _settle(guest)
    ws = workspace_of(guest.get("/api/session").json()["user"])
    ids = [f"ftsbatch0{i}" for i in range(3)]
    for doc_id in ids:
        make_page(guest, f"Batch {doc_id}", properties=doc_props(doc_id))
    second, release = threading.Event(), threading.Event()

    def read(ws, doc):
        if doc.hash == ids[1]:
            second.set()
            release.wait(10)
        return doc.hash, [(1, f"text of {doc.hash}")], {"format": "pdf", "lines": 0, "truncated": False}

    monkeypatch.setattr(indexer, "index_doc", read)
    assert indexer.index_missing_async(ws, {d: Doc(d, "pdf", ".pdf", "store") for d in ids}) is True
    assert second.wait(10)
    job = next(j for j in _indexing_jobs(guest) if j["state"] == "running")
    assert guest.post(f"/api/jobs/{job['id']}/cancel").status_code == 200
    release.set()
    assert jobs.wait(job["id"])["state"] == "cancelled"
    with connect_data_db(ws) as conn:
        assert missing(conn, ids) == ids[2:]
