"""Search: GET /api/search — one query over the notes index (block_fts in
pages.db, gamma/block_index.py) and the PDF index (pdf_fts): result shape,
ordering, folder scope, and a search right after every kind of block write
finding what it wrote (the triggers, tests/test_notes_index.py) — plus the
PDF index itself: /api/pdf-search hits and separator tolerance, stale index
versions, the reindex endpoint, the indexing job."""

import sqlite3

from conftest import login, make_folder, make_label, make_page, make_user, workspace_of
from gamma.db import connect_data_db, connect_pages_db
from gamma.pdf_index import store_doc
from gamma.textnorm import normalize_text


def _index_pdf(user, doc_id, pages):
    """Store index rows the way _index_doc does (pdf_index.store_doc:
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


def test_search_mixes_notes_and_pdf_hits():
    make_user("searcher", "pw")
    c = login("searcher", "pw")
    paper = make_page(c, "Wombat paper", properties={"doc_id": "srchdoc001", "folders": [make_folder(c, "zoo")]})
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


def test_pdf_search_and_block_search_unchanged():
    """The predecessors the frontend still uses keep their shapes."""
    c = login("searcher", "pw")
    r = c.get("/api/pdf-search", params={"q": "wombat"})
    assert r.status_code == 200
    (hit,) = [h for h in r.json()["results"] if h["doc_id"] == "srchdoc001"]
    assert hit["page"] == 4 and "source" not in hit
    r = c.get("/api/block-search", params={"q": "wombat"})
    assert r.status_code == 200 and r.json()["blocks"]


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


# --- the PDF index ---------------------------------------------------------------

def test_pdf_search_hits_indexed_docs(guest):
    user = guest.get("/api/session").json()["user"]
    make_page(guest, "FTS paper", properties={"doc_id": "ftsdoc001"})
    _index_pdf(user, "ftsdoc001", [(3, "the wombat considered superconducting qubits carefully")])

    r = guest.get("/api/pdf-search", params={"q": "wombat superconducting"})
    assert r.status_code == 200
    hits = r.json()["results"]
    assert any(h["page"] == 3 and h["title"] == "FTS paper" and h["doc_id"] == "ftsdoc001"
               for h in hits)

    # unknown terms → no hits, no error
    r = guest.get("/api/pdf-search", params={"q": "zzznothingzzz"})
    assert r.json()["results"] == []


def test_pdf_search_is_separator_tolerant(guest):
    """"3000" must find "3,000-qubit": the index stores normalized text and
    the query is normalized the same way."""
    user = guest.get("/api/session").json()["user"]
    make_page(guest, "Qubit paper", properties={"doc_id": "ftsdoc002"})
    _index_pdf(user, "ftsdoc002",
                [(1, "Continuous operation of a coherent 3,000-qubit system")])

    for q in ("3000", "3,000", "3000-qubit", "3000 qubit system"):
        hits = guest.get("/api/pdf-search", params={"q": q}).json()["results"]
        assert any(h["doc_id"] == "ftsdoc002" for h in hits), f"query {q!r} missed"


def test_pdf_search_scope_pages_and_per_paper_cap():
    """The panel's PDF group: a book that matches on every page lists its
    best three, then waits for every other paper's best three; ``next``
    pages on through the same order; the chips (``scope``) and the open
    paper (``exclude``) are applied before the ranking, so a paper outranked
    by the whole library is still found inside its folder."""
    make_user("srch_pdfpages", "pw")
    c = login("srch_pdfpages", "pw")
    user = "srch_pdfpages"
    folder, label = make_folder(c, "zoo"), make_label(c, "amphibians")
    book = make_page(c, "Big book", properties={"doc_id": "pgbook001"})
    _index_pdf(user, "pgbook001", [(p, f"axolotl axolotl on page {p}") for p in range(1, 41)])
    for n in range(6):
        make_page(c, f"Paper {n}", properties={"doc_id": f"pgpaper00{n}"})
        _index_pdf(user, f"pgpaper00{n}", [(1, f"an axolotl in paper {n} among many other words here"),
                                           (2, f"one more axolotl in paper {n} with yet more words around it")])
    filed = make_page(c, "Filed paper", properties={"doc_id": "pgfiled01", "folders": [folder], "labels": [label]})
    _index_pdf(user, "pgfiled01", [(7, "a lone axolotl " + "and plenty of unrelated words " * 20)])

    def hits(**params):
        r = c.get("/api/pdf-search", params={"q": "axolotl", **params})
        assert r.status_code == 200, r.text
        return r.json()

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

    assert keys(hits(scope=folder)) == keys(hits(scope=label)) == [("pgfiled01", 7)]
    assert keys(hits(scope=f"{folder},{make_label(c, 'reptiles')}")) == []
    assert keys(hits(limit=5, scope=folder, cursor="5")) == []
    assert "pgbook001" not in {d for d, _ in keys(hits(limit=100, exclude=book["id"]))}
    assert filed["id"] in {h["block_id"] for h in hits(limit=100)["results"]}
    assert c.get("/api/pdf-search", params={"q": "axolotl", "cursor": "x"}).status_code == 400


def test_stale_index_version_counts_as_missing(guest):
    from gamma.db import ws_db_path
    from gamma.pdf_index import ensure_schema

    user = guest.get("/api/session").json()["user"]
    make_page(guest, "Stale paper", properties={"doc_id": "ftsdoc003"})
    with sqlite3.connect(ws_db_path(workspace_of(user), "data.db")) as conn:
        ensure_schema(conn)
        conn.execute("INSERT OR REPLACE INTO pdf_fts_docs (doc_id, indexed_at, pages, ver) "
                     "VALUES ('ftsdoc003', '2025', 1, 0)")  # pre-normalization row
        conn.commit()

    r = guest.get("/api/pdf-search", params={"q": "anything"})
    assert r.json()["indexing"] >= 1  # stale doc scheduled for re-indexing


def test_search_reindex_marks_everything_stale(guest):
    from gamma.db import ws_db_path
    from gamma.textnorm import INDEX_VERSION

    user = guest.get("/api/session").json()["user"]
    make_page(guest, "Rebuild me", properties={"doc_id": "ftsdoc004"})
    _index_pdf(user, "ftsdoc004", [(1, "some indexed text")])

    r = guest.post("/api/search-reindex")
    assert r.status_code == 200
    body = r.json()
    assert body["scheduled"] >= 1 or body["busy"]  # started, or an indexer already runs

    # The doc's bookkeeping row survives (ver may be 0 = stale or already
    # re-stamped by the background thread — no PDF file makes that instant).
    with sqlite3.connect(ws_db_path(workspace_of(user), "data.db")) as conn:
        ver = conn.execute("SELECT ver FROM pdf_fts_docs WHERE doc_id = 'ftsdoc004'").fetchone()[0]
    assert ver in (0, INDEX_VERSION)


def test_search_reindex_targeted_single_doc(guest):
    """doc_ids re-indexes just those papers: no global stale stamp, and ids
    outside the caller's library are ignored."""
    from gamma.db import ws_db_path
    from gamma.textnorm import INDEX_VERSION

    user = guest.get("/api/session").json()["user"]
    make_page(guest, "Keep me", properties={"doc_id": "ftsdoc005"})
    make_page(guest, "Reindex me", properties={"doc_id": "ftsdoc006"})
    _index_pdf(user, "ftsdoc005", [(1, "already indexed text")])

    r = guest.post("/api/search-reindex", json={"doc_ids": ["ftsdoc006", "not-my-doc"]})
    assert r.status_code == 200
    body = r.json()
    assert body["scheduled"] == 1 or body["busy"]

    # The untouched doc keeps its current-version stamp (a full rebuild would
    # have zeroed it).
    with sqlite3.connect(ws_db_path(workspace_of(user), "data.db")) as conn:
        ver = conn.execute(
            "SELECT ver FROM pdf_fts_docs WHERE doc_id = 'ftsdoc005'").fetchone()[0]
    assert ver == INDEX_VERSION


def _indexing_jobs(c):
    r = c.get("/api/jobs")
    assert r.status_code == 200, r.text
    return [j for j in r.json()["jobs"] if j["kind"] == "indexing"]


def test_indexing_is_the_workspaces_job(guest):
    """A reindex runs as the workspace's indexing job: listed with its
    papers counted, one row per workspace however often it runs."""
    from gamma import jobs

    make_page(guest, "Indexed as a job", properties={"doc_id": "ftsjob001"})
    for _ in range(2):
        for job in _indexing_jobs(guest):
            jobs.wait(job["id"])
        assert guest.post("/api/search-reindex", json={"doc_ids": ["ftsjob001"]}).json()["scheduled"] == 1
    listed = _indexing_jobs(guest)
    assert len(listed) == 1, listed
    job = jobs.wait(listed[0]["id"])
    assert job["owner"] == jobs.WORKSPACE and job["workspace"] == workspace_of(guest.get("/api/session").json()["user"])
    assert job["state"] == "done" and job["result"] == {"papers": 1}
    assert job["progress"] == {"done": 1, "total": 1, "unit": "papers"}


def test_a_running_indexer_is_stopped_between_papers(guest, monkeypatch):
    """Stopping the job ends it before the next paper; the rest stay stale
    for the next search. A second run meanwhile is refused as busy."""
    import threading
    from gamma import jobs
    from gamma.routers import search as search_mod

    for job in _indexing_jobs(guest):
        jobs.wait(job["id"])
    user = guest.get("/api/session").json()["user"]
    ids = [f"ftsstop00{i}" for i in range(3)]
    for doc_id in ids:
        make_page(guest, f"Stop at {doc_id}", properties={"doc_id": doc_id})
    reached, release, seen = threading.Event(), threading.Event(), []

    def slow(ws, doc_id):
        seen.append(doc_id)
        reached.set()
        release.wait(10)

    monkeypatch.setattr(search_mod, "_index_doc", slow)
    assert search_mod._index_missing_async(workspace_of(user), ids) is True
    assert reached.wait(10)
    assert search_mod._index_missing_async(workspace_of(user), ids) is False  # one per workspace
    job = next(j for j in _indexing_jobs(guest) if j["state"] == "running")
    assert job["stoppable"] and job["progress"]["total"] == 3
    assert guest.post(f"/api/jobs/{job['id']}/cancel").status_code == 200
    release.set()
    assert jobs.wait(job["id"])["state"] == "cancelled"
    assert seen == ids[:1]
