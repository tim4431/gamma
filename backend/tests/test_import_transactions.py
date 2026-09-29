"""Imports write in short transactions: the Zotero and Markdown-zip imports
store a page's files first, outside any transaction, then write the rows of
up to 50 new pages in one short transaction, their roots stamped at its
commit (a transaction per page made big imports twice as slow). Other
writers are never shut
out for the length of an import (they used to wait out the 10 s busy
timeout and fail with "database is locked"), and a page is never stamped
with the import's start time (the change feed's 60 s grace missed pages of
an import that ran longer). A Zotero merge into an existing page is an op
batch, so the page's open tabs and the workspace's mirrors see it."""

import io
import sqlite3
import zipfile

from conftest import login, make_user

from gamma import markdown_zip_import
from gamma.db import connect_pages_db, page_now, ws_db_path
from gamma.routers import imports

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _pdf(text: bytes) -> bytes:
    stream = b"BT /F1 12 Tf 72 720 Td (" + text + b") Tj ET"
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n%s\nendobj\n" % (i, obj))
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref))
    return out.getvalue()


def _zotero_zip(items):
    """``items``: [(key, title, pdf bytes or None, [(note key, note html)], [tags])]."""
    parts = []
    files = {}
    for n, (key, title, pdf, notes, tags) in enumerate(items):
        refs = "".join(f'<dcterms:isReferencedBy rdf:resource="#{nk}"/>' for nk, _ in notes)
        link = f'<link:link rdf:resource="#att_{n}"/>' if pdf else ""
        subjects = "".join(f"<dc:subject>{t}</dc:subject>" for t in tags)
        parts.append(f"""<bib:Article rdf:about="{key}"><z:itemType>journalArticle</z:itemType>
            {link}{refs}<dc:title>{title}</dc:title>{subjects}</bib:Article>""")
        if pdf:
            parts.append(f"""<z:Attachment rdf:about="#att_{n}"><z:itemType>attachment</z:itemType>
                <z:path rdf:resource="files/{n}/paper{n}.pdf"/><dc:title>PDF</dc:title>
                <link:type>application/pdf</link:type></z:Attachment>""")
            files[f"export/files/{n}/paper{n}.pdf"] = pdf
        for nk, html in notes:
            parts.append(f'<bib:Memo rdf:about="#{nk}"><rdf:value>{html}</rdf:value></bib:Memo>')
    rdf = ("""<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
 xmlns:z="http://www.zotero.org/namespaces/export#" xmlns:dcterms="http://purl.org/dc/terms/"
 xmlns:bib="http://purl.org/net/biblio#" xmlns:link="http://purl.org/rss/1.0/modules/link/"
 xmlns:dc="http://purl.org/dc/elements/1.1/">""" + "".join(parts) + "</rdf:RDF>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("export/library.rdf", rdf)
        for path, data in files.items():
            zf.writestr(path, data)
    return buf.getvalue()


def _other_writer_gets_in(ws) -> bool:
    """Whether another connection can take the write lock right now (with a
    short wait instead of the usual 10 s)."""
    conn = sqlite3.connect(ws_db_path(ws, "pages.db"), timeout=0.2)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.rollback()
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


def _stamp(ws, page_id):
    with connect_pages_db(ws) as conn:
        return conn.execute("SELECT updated_at FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()[0]


def test_zotero_import_stores_files_outside_its_transactions(monkeypatch):
    monkeypatch.setattr(imports, "ZOTERO_PAGES_PER_COMMIT", 2)
    ws = make_user("it_zotero", "it-password-1")
    c = login("it_zotero", "it-password-1")
    seen = []
    real = imports.store_pdf

    def watching_store(ws_, data):
        # every item's PDF is stored while no transaction of the import is open
        seen.append((_other_writer_gets_in(ws_), page_now()))
        return real(ws_, data)
    monkeypatch.setattr(imports, "store_pdf", watching_store)
    data = _zotero_zip([(f"https://example.org/it-{i}", f"Paper {i}", _pdf(b"paper %d" % i), [], [])
                        for i in range(3)])
    r = c.post("/api/import/zotero", files={"file": ("lib.zip", data, "application/zip")})
    assert r.status_code == 200, r.text
    assert r.json()["pages_created"] == 3 and len(seen) == 3
    assert all(ok for ok, _ in seen)
    # each page is stamped at its batch's commit, after its file was stored —
    # not with the time the import started; the first batch (two pages)
    # committed before the third file was stored
    pages = sorted(r.json()["pages"], key=lambda p: p["title"])
    for (_, stored_at), page in zip(seen, pages):
        assert _stamp(ws, page["id"]) > stored_at
    assert _stamp(ws, pages[0]["id"]) == _stamp(ws, pages[1]["id"]) < seen[2][1]


def test_zotero_merge_is_an_op_batch():
    """A re-import that adds a note and a label to an existing page reaches
    the page's open tabs and its op log (the change feed's seq) like any
    edit, under the importing account."""
    ws = make_user("it_merge", "it-password-1")
    c = login("it_merge", "it-password-1")
    key = "https://example.org/it-merge"
    first = _zotero_zip([(key, "Merged paper", None, [("n1", "first note")], ["alpha"])])
    r = c.post("/api/import/zotero", files={"file": ("lib.zip", first, "application/zip")})
    assert r.status_code == 200, r.text
    page_id = r.json()["pages"][0]["id"]
    with connect_pages_db(ws) as conn:
        assert conn.execute("SELECT COUNT(*) FROM page_ops WHERE page_id = ?", (page_id,)).fetchone()[0] == 0

    again = _zotero_zip([(key, "Merged paper", None, [("n1", "first note"), ("n2", "second note")],
                          ["alpha", "beta"])])
    with c.websocket_connect(f"/api/ws/page/{page_id}?ws={ws}&client=tab1") as sock:
        assert sock.receive_json()["t"] == "hello"
        r = c.post("/api/import/zotero", files={"file": ("lib.zip", again, "application/zip")})
        assert r.status_code == 200, r.text
        assert r.json()["pages_merged"] == 1 and r.json()["notes_imported"] == 1
        # logged first (so a broken merge fails here instead of waiting on the socket)
        log = c.get(f"/api/pages/{page_id}/ops", params={"since": 0}).json()
        assert [b["actor"] for b in log["batches"]] == ["it_merge"] and log["seq"] == 1
        msg = sock.receive_json()
    assert msg["t"] == "ops" and msg["actor"] == "it_merge"
    kinds = [(o["op"], o.get("props")) for o in msg["ops"]]
    assert ("set", {"category": "alpha, beta"}) in kinds
    assert ("insert", {"zotero_note": "#n2"}) in kinds  # a note's key is its rdf:about
    feed = c.get("/api/sync/changes", params={"since": ""}).json()
    assert {"id": page_id, "seq": 1}.items() <= next(p for p in feed["pages"] if p["id"] == page_id).items()
    # nothing new: no batch at all
    r = c.post("/api/import/zotero", files={"file": ("lib.zip", again, "application/zip")})
    assert r.status_code == 200 and r.json()["pages_merged"] == 1
    assert c.get(f"/api/pages/{page_id}/ops", params={"since": 0}).json()["seq"] == 1


def test_markdown_zip_import_commits_in_batches(monkeypatch):
    monkeypatch.setattr(markdown_zip_import, "PAGES_PER_COMMIT", 2)
    ws = make_user("it_mdzip", "it-password-1")
    c = login("it_mdzip", "it-password-1")
    seen = []
    real = markdown_zip_import.store_file

    def watching_store(ws_, data, ext):
        seen.append((_other_writer_gets_in(ws_), page_now()))
        return real(ws_, data, ext)
    monkeypatch.setattr(markdown_zip_import, "store_file", watching_store)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(3):
            zf.writestr(f"vault/Note {i}.md", f"# Note {i}\n\n![pic](img{i}.png)\n")
            zf.writestr(f"vault/img{i}.png", PNG + bytes([i]))
    r = c.post("/api/import/markdown-zip", files={"file": ("notes.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["pages_created"] == 3 and d["assets_stored"] == 3 and len(seen) == 3
    assert all(ok for ok, _ in seen)  # the earlier pages' rows are committed, no lock held
    pages = sorted(d["pages"], key=lambda p: p["title"])
    for (_, stored_at), page in zip(seen, pages):
        assert _stamp(ws, page["id"]) > stored_at
    assert _stamp(ws, pages[0]["id"]) == _stamp(ws, pages[1]["id"]) < seen[2][1]
    body = c.get(f"/api/blocks/{pages[2]['id']}/subtree").json()["block"]["children"][0]["content"]
    assert "/api/uploads/" in body
