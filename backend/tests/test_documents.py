"""A block's document as one property, ``doc`` (gamma/documents/model.py,
docs/dev/documents.md): the one builder's rules, the checks every writer
holds a doc to (the op path, the routes that take raw properties, a new
page's row), the lookups of "the page of this file", which a copy, a PDF
read in place, a nested document and a trashed page never answer, the
by-doc backfill, the files a block's doc references, and the format
registry's serving views, which are storage's tables of before.
tests/shared/document.json and formats.json hold the reads to the client's
(test_shared_fixtures.py)."""

import pytest

from conftest import doc_props, login, make_user, workspace_of
from gamma import storage
from gamma.blocks_store import get_or_create_doc_page, page_for_doc, pages_for_docs, root_pages
from gamma.db import connect_pages_db, safe_doc_id
from gamma.documents import formats
from gamma.documents.model import (check_document, copy_document, document, document_at, document_props,
                                   document_title, file_hash, in_place, is_listed, is_pdf, own_hash, page_document,
                                   parse_upload_url, pdf_file_hash, source_address, stored_name, upload_url, url_hash)

USER, PASSWORD = "docs_owner", "pw-docs-1"
PDF = {"format": "pdf", "name": "", "ext": ".pdf"}


@pytest.fixture(scope="module")
def owner():
    make_user(USER, PASSWORD)
    return login(USER, PASSWORD)


def _page(c, title, props=None):
    r = c.post("/api/blocks", json={"parent_id": "root", "content": title, "properties": props or {}})
    assert r.status_code == 200, r.text
    return r.json()


def _ops(c, page, ops):
    return c.post(f"/api/pages/{page}/ops", json={"client": "t", "ops": ops})


# --- the builder ----------------------------------------------------------------------

def test_document_rule_1_the_format_and_its_extension():
    assert document("d1") == {**PDF, "hash": "d1", "at": "store"}
    text = document("t1", format="text", ext=".tex")
    assert (text["format"], text["ext"]) == ("text", ".tex")
    for bad in ({"format": "epub"}, {"format": "text"}, {"format": "text", "ext": "tex"},
                {"format": "text", "ext": ".TEX"}, {"ext": ".png"}, {"format": "binary", "ext": ".toolongextens"}):
        with pytest.raises(ValueError):
            document("d1", **bad)


def test_document_rule_2_the_hash_is_a_stem():
    assert document(" d1 ")["hash"] == "d1" and document("a" * 128)["hash"] == "a" * 128
    for bad in ("../d1", "a/b", ".", "..", "a" * 129, "a b"):
        with pytest.raises(ValueError):
            document(bad)
    with pytest.raises(ValueError):
        document(7)


def test_document_rule_3_an_uploads_address_alone_is_a_copy():
    assert document(url="/api/uploads/d1.pdf") == {**PDF, "hash": "d1", "at": "store", "copy": True}
    assert document(url="/api/uploads/d1.pdf", at="url")["copy"] is True
    with pytest.raises(ValueError):
        document(url="/api/uploads/d1.pdf", at="path")
    # another extension, a query, another host: an address like any
    for url in ("/api/uploads/d1.png", "/api/uploads/d1.pdf?x=1", "https://x.test/api/uploads/d1.pdf"):
        assert document(url=url) == {**PDF, "hash": "", "at": "url", "url": url}
    tex = document(url="/api/uploads/t1.tex", format="text", ext=".tex")
    assert (tex["hash"], tex["at"], tex.get("copy")) == ("t1", "store", True)


def test_document_rule_4_its_own_stored_address_is_no_address():
    assert document("d1", "/api/uploads/d1.pdf") == {**PDF, "hash": "d1", "at": "store"}
    assert document("d1", "/api/uploads/d1.pdf", at="url") == {**PDF, "hash": "d1", "at": "store"}
    assert document("d1", "/api/uploads/d1.pdf", at="path") == {**PDF, "hash": "d1", "at": "path"}
    assert document("d1", "/api/uploads/k1.pdf") == {**PDF, "hash": "d1", "at": "url", "url": "/api/uploads/k1.pdf"}


def test_document_rule_5_where_it_is_read_from():
    assert document("d1")["at"] == "store"
    assert document(url="https://x.test/p.pdf")["at"] == "url"
    assert document("d1", at="path")["at"] == "path"
    with pytest.raises(ValueError):
        document("d1", at="disk")


def test_document_rule_6_the_invariants():
    for bad in ({"hash": "", "at": "store"}, {"hash": "", "at": "path"}, {"hash": "d1", "at": "url"},
                {"hash": "d1", "url": "https://x.test/p.pdf", "at": "store"},
                {"hash": "d1", "url": "https://x.test/p.pdf", "at": "path"},
                {"hash": "", "url": "https://x.test/p.pdf", "copy": True},
                {"hash": "d1", "at": "path", "copy": True}):
        with pytest.raises(ValueError):
            document(**bad)
    assert document("u1", "https://x.test/p.pdf") == {**PDF, "hash": "u1", "at": "url", "url": "https://x.test/p.pdf"}


def test_document_rule_7_the_size_and_the_name():
    assert document("d1", size=0)["size"] == 0 and "size" not in document("d1")
    # JSON's 1.0, which a client reads as 1, is the whole number it names
    assert document("d1", size=1.0)["size"] == 1 and type(document("d1", size=1e3)["size"]) is int
    for bad in (-1, -1.0, True, 1.5, float("inf"), float("nan"), "10"):
        with pytest.raises(ValueError):
            document("d1", size=bad)
    assert document("d1", name="C:\\Users\\me\\paper.pdf")["name"] == "paper.pdf"
    assert document("d1", name="a/b/" + "x" * 600)["name"] == "x" * 500
    # the canonical key order, whatever the call's
    full = document("d1", name="p.pdf", size=3, copy=True)
    assert list(full) == ["format", "name", "hash", "ext", "at", "size", "copy"]
    assert list(document("u1", "https://x.test/p.pdf", size=3)) == ["format", "name", "hash", "ext", "at", "url", "size"]


def test_document_rule_8_a_listed_file_has_no_bytes_here():
    """A work folder's file too large to keep (config.vault_keep_bytes): at
    ``listed``, no hash and no address, its size named, never a PDF or a
    Markdown file (no limit measures them), never a copy. Its view has no
    address and no stored name, so nothing reads, stores or looks it up."""
    listed = document("", name="weights.ckpt", at="listed", format="binary", ext=".ckpt", size=5_000_000)
    assert listed == {"format": "binary", "name": "weights.ckpt", "hash": "", "ext": ".ckpt", "at": "listed", "size": 5_000_000}
    assert list(listed) == ["format", "name", "hash", "ext", "at", "size"]
    for bad in ({"hash": "d1"}, {"url": "https://x.test/w.ckpt"}, {"url": "/api/uploads/d1.ckpt"}, {"size": None},
                {"copy": True}, {"format": "pdf", "ext": ".pdf"}, {"format": "markdown", "ext": ".md"}):
        with pytest.raises(ValueError):
            document(**{"name": "w", "at": "listed", "format": "binary", "ext": ".ckpt", "size": 1, **bad})
    view = page_document({"doc": listed})
    assert (view["at"], view["hash"], view["file"], view["url"], view["size"]) == ("listed", "", "", "", 5_000_000)
    assert is_listed(view) and not in_place(view) and not is_pdf(view)
    assert (own_hash(view), file_hash(view), pdf_file_hash(view), source_address(view)) == ("", "", "", "")
    text = page_document({"doc": document("", name="run.log", at="listed", format="text", ext=".log", size=3)})
    assert text["language"] == "plaintext"
    for other in (None, page_document(doc_props("d1")), page_document(doc_props("d1", at="path"))):
        assert not is_listed(other)
    with pytest.raises(ValueError):  # nothing to copy: a duplicate of a listed page writes no doc
        copy_document(view)


def test_check_document_rebuilds_what_a_writer_supplies():
    assert check_document({"hash": "d1"}) == document("d1")
    assert check_document({"url": "/api/uploads/d1.pdf"}) == document("d1", copy=True)
    for bad in ("x", None, [], {"hash": "d1", "pages": 3}, {"hash": 1}, {"hash": "d1", "copy": "yes"},
                {"hash": "d1", "name": None}, {"hash": "d1", "at": ""}):
        with pytest.raises(ValueError):
            check_document(bad)


def test_document_props_is_every_writers_rule():
    assert document_props({}) == {} and document_props({"title": "x"}) == {"title": "x"}
    raw = {"doc": {"hash": " d1 ", "size": 3}, "doc_id": None, "source_url": None, "linked_file": None,
           "x": 1}
    assert document_props(raw) == {"doc": document("d1", size=3), "x": 1}
    assert raw["doc"] == {"hash": " d1 ", "size": 3}  # a new dict
    # a null doc is no document; in a patch it removes the key
    assert document_props({"doc": None, "x": 1}) == {"x": 1}
    assert document_props({"doc": None, "doc_id": None}, patch=True) == {"doc": None}
    for key, value in (("doc_id", "x"), ("source_url", "https://x.test/p.pdf"), ("linked_file", True),
                       ("doc_id", "")):
        with pytest.raises(ValueError, match="no longer a block property"):
            document_props({key: value})
        assert document_props({key: value, "x": 1}, refuse=False) == {"x": 1}
    with pytest.raises(ValueError):
        document_props({"doc": {"hash": "../x"}})
    assert document_props({"doc": {"hash": "../x"}, "x": 1}, refuse=False) == {"x": 1}


def test_copy_document_is_the_duplicates_doc():
    assert copy_document(document("d1", name="p.pdf", size=3)) == document("d1", name="p.pdf", size=3, copy=True)
    assert copy_document(document("d1", at="path")) == document("d1", copy=True)
    assert copy_document(document("u1", "https://x.test/p.pdf")) == document(url="https://x.test/p.pdf")
    # an address that is another upload's: a copy of that one
    assert copy_document(document("d1", "/api/uploads/k1.pdf")) == document("k1", copy=True)
    assert copy_document(page_document(doc_props("d1"))) == document("d1", copy=True)


def test_the_view_and_its_predicates():
    stored = page_document(doc_props("d1", name="paper.pdf"))
    assert (own_hash(stored), file_hash(stored), source_address(stored), stored["url"]) == \
        ("d1", "d1", "", "/api/uploads/d1.pdf")
    assert is_pdf(stored) and pdf_file_hash(stored) == "d1" and not in_place(stored)
    copy = page_document(doc_props(url="/api/uploads/d1.pdf"))
    assert (own_hash(copy), file_hash(copy), copy["file"], copy["url"]) == ("", "d1", "d1.pdf", "/api/uploads/d1.pdf")
    assert pdf_file_hash(copy) == "d1"
    linked = page_document(doc_props("d1", at="path"))
    assert in_place(linked) and pdf_file_hash(linked) == "d1" and own_hash(linked) == "d1"
    shown = page_document(doc_props("u1", url="https://x.test/p.pdf"))
    assert (own_hash(shown), source_address(shown), shown["file"]) == ("u1", "https://x.test/p.pdf", "u1.pdf")
    bare = page_document(doc_props(url="https://x.test/p.pdf"))
    assert (own_hash(bare), file_hash(bare), bare["file"]) == ("", "", "")
    for none in (None, page_document({}), page_document({"doc": "x"})):
        assert (own_hash(none), file_hash(none), source_address(none)) == ("", "", "")
        assert not is_pdf(none) and not in_place(none) and pdf_file_hash(none) == ""
    text = page_document({"doc": document("t1", name="main.tex", format="text", ext=".tex")})
    assert not is_pdf(text) and text["language"] == "latex" and text["file"] == "t1.tex"
    assert file_hash(text) == "t1" and pdf_file_hash(text) == ""
    # moved between the store and the disk, its file stays
    assert document_at(linked, "store") == document("d1")
    assert document_at(stored, "path") == document("d1", name="paper.pdf", at="path")


def test_names_and_addresses():
    assert stored_name("d1", ".pdf") == "d1.pdf" and upload_url("d1.pdf") == "/api/uploads/d1.pdf"
    assert parse_upload_url("/api/uploads/d1.pdf") == ("d1", ".pdf")
    assert parse_upload_url("/api/uploads/t1.tex") == ("t1", ".tex")
    for other in ("/api/uploads/d1.pdf?x=1", "https://x.test/api/uploads/d1.pdf", "/api/uploads/d.1.pdf",
                  f"/api/uploads/{'a' * 129}.pdf", "/api/uploads/d1.PDF", "", None):
        assert parse_upload_url(other) is None, other
    assert url_hash("https://x.test/p.pdf") == storage.digest_id(__import__("hashlib").sha256(b"https://x.test/p.pdf"))
    assert len(url_hash("https://x.test/p.pdf")) == storage.DIGEST_CHARS
    assert document_title("C:\\a\\paper.pdf", "https://x.test/q.pdf", "d1") == "paper.pdf"
    assert document_title("", "https://x.test/q.pdf", "d1") == "q.pdf"
    assert document_title("", "/api/uploads/d1.pdf", "d1") == "d1.pdf"  # the raw address, as attach always titled
    assert document_title("", "", "d1") == "d1"


def test_vault_extension_stores_a_blocked_name_as_bin():
    """The vault's variant of stored_extension: every name answers, a
    blocked or missing extension as .bin, and the two agree on the rest."""
    for name in ("main.tex", "fig.PNG", "Makefile", ".gitignore", "2301.12345v2", "odd.abcdefghijklm", "trailing."):
        assert formats.vault_extension(name) == formats.stored_extension(name), name
    for blocked in ("run-local.bat", "Setup.EXE", "a.ps1"):
        assert formats.vault_extension(blocked) == ".bin"
        with pytest.raises(ValueError):
            formats.stored_extension(blocked)


def test_stored_format_reads_the_stored_extension_then_the_name_then_the_bytes():
    """The format of a stored file is settled on the server (an attach, a
    by-file, the vault's file class): its stored extension's row unless
    that is .bin or unknown, else the whole name, else the name's own
    extension, else the first bytes, else binary."""
    assert formats.stored_format("shot", ".png", None) == ("image", None)       # named by its media type
    assert formats.stored_format("photo.jpeg", ".jpg", None) == ("image", None)
    assert formats.stored_format("Makefile", ".bin", None) == ("text", "makefile")
    assert formats.stored_format("notes.txt", ".bin", None) == ("text", "plaintext")
    assert formats.stored_format("2301.12345v2", ".12345v2", b"%PDF-1.7\n") == ("pdf", None)
    assert formats.stored_format("data.xyz", ".xyz", None) == ("binary", None)
    assert formats.stored_format("data.xyz", ".xyz", b"a,b\n1,2\n") == ("text", "plaintext")
    assert formats.stored_format("blob.bin", ".bin", b"\x00\x01") == ("binary", None)
    assert formats.stored_format("run-local.bat", ".bin", b"@echo off\n") == ("text", "plaintext")
    assert formats.stored_format("", ".bin", None) == ("binary", None)
    # the extension's row wins over the name: a .bin is the one exception, since it names nothing
    assert formats.stored_format("Makefile", ".py", None) == ("text", "python")
    assert formats.stored_format("paper.pdf", ".png", None) == ("image", None)


# --- the writers ----------------------------------------------------------------------

def test_the_op_path_holds_every_block_to_the_doc(owner):
    page = _page(owner, "Op path")
    nested = {"op": "insert", "id": "dmNested", "parent": page["id"], "content": ""}
    for props in ({"doc_id": "x"}, {"source_url": "https://x.test/p.pdf"}, {"linked_file": True}):
        assert _ops(owner, page["id"], [{"op": "set", "id": page["id"], "props": props}]).status_code == 400, props
        assert _ops(owner, page["id"], [{**nested, "props": props}]).status_code == 400, props
    assert _ops(owner, page["id"], [{"op": "set", "id": page["id"], "props": {"doc": {"hash": "../x"}}}]).status_code == 400
    assert _ops(owner, page["id"], [{**nested, "props": {"doc": {"format": "epub", "hash": "d1"}}}]).status_code == 400
    # a removal of a flat key is dropped (a mirror's stale base, an older client)
    r = _ops(owner, page["id"], [{"op": "set", "id": page["id"], "props": {"doc_id": None, "source_url": None,
                                                                            "pinned": True}}])
    assert r.status_code == 200 and r.json()["ops"][0]["props"] == {"pinned": True}, r.text
    # a valid doc is stored canonical, on a page and on a nested block
    r = _ops(owner, page["id"], [{"op": "set", "id": page["id"], "props": {"doc": {"hash": "d1", "name": "a/p.pdf"}}},
                                 {**nested, "props": {"doc": {"url": "/api/uploads/d2.pdf"}, "doc_id": None}}])
    assert r.status_code == 200, r.text
    got = owner.get(f"/api/blocks/{page['id']}").json()["properties"]
    assert got["doc"] == {**PDF, "name": "p.pdf", "hash": "d1", "at": "store"}
    assert owner.get("/api/blocks/dmNested").json()["properties"] == {"doc": {**PDF, "hash": "d2", "at": "store",
                                                                              "copy": True}}
    # doc: None removes the document
    assert _ops(owner, page["id"], [{"op": "set", "id": page["id"], "props": {"doc": None}}]).status_code == 200
    assert "doc" not in owner.get(f"/api/blocks/{page['id']}").json()["properties"]


def test_the_raw_property_routes_refuse_the_flat_keys(owner):
    for props in ({"doc_id": "d1"}, {"source_url": "/api/uploads/d1.pdf"}, {"linked_file": True},
                  {"doc": {"hash": "d1", "at": "url"}}):
        r = owner.post("/api/blocks", json={"parent_id": "root", "content": "Flat", "properties": props})
        assert r.status_code == 400, (props, r.text)
    page = _page(owner, "A copy", doc_props(url="/api/uploads/d1.pdf"))
    assert page["properties"]["doc"] == {**PDF, "hash": "d1", "at": "store", "copy": True}
    r = owner.post("/api/blocks", json={"parent_id": page["id"], "content": "", "properties": {"doc_id": "d1"}})
    assert r.status_code == 400, r.text
    r = owner.post("/api/blocks", json={"parent_id": page["id"], "content": "",
                                        "properties": {"doc": {"url": "/api/uploads/d3.pdf"}}})
    assert r.status_code == 200 and r.json()["properties"]["doc"]["copy"] is True, r.text
    # PUT children: a flat key on any node refuses the whole tree, nothing written
    _ops(owner, page["id"], [{"op": "insert", "id": "dmHolder", "parent": page["id"], "content": "holder"},
                             {"op": "insert", "id": "dmKept", "parent": "dmHolder", "content": "kept"}])
    r = owner.put("/api/blocks/dmHolder/children", json={"blocks": [
        {"id": "dmT1", "content": "one", "children": [{"id": "dmT2", "content": "two", "properties": {"doc_id": "d1"}}]}]})
    assert r.status_code == 400, r.text
    assert [b["id"] for b in owner.get("/api/blocks/dmHolder/children").json()["children"]] == ["dmKept"]
    r = owner.put("/api/blocks/dmHolder/children", json={"blocks": [
        {"id": "dmT3", "content": "a copy", "properties": {"doc": {"hash": "d1", "copy": True}}}]})
    assert r.status_code == 200, r.text
    assert owner.get("/api/blocks/dmT3").json()["properties"]["doc"] == {**PDF, "hash": "d1", "at": "store",
                                                                        "copy": True}
    # null flat keys and a null doc are no document, as on the op path
    r = owner.put("/api/blocks/dmHolder/children", json={"blocks": [
        {"id": "dmT4", "content": "", "properties": {"doc_id": None, "source_url": None, "doc": None, "x": 1}}]})
    assert r.status_code == 200, r.text
    assert owner.get("/api/blocks/dmT4").json()["properties"] == {"x": 1}


def test_a_new_page_with_a_file_another_page_owns_is_a_copy_of_it(owner):
    """An open tab from before the doc property duplicates a page as it did
    then: the subtree read fresh, ``doc_id`` taken out (now absent), the
    rest of the properties POSTed at root, ``doc`` with them. The page it
    makes owned the original's file beside it; it is a copy of that file,
    as the duplicate a tab of today writes (``copy_document``)."""
    ws = workspace_of(USER)
    h, u = "c" * 24, "e" * 24
    page = _page(owner, "Owned", {"doc": document(h, name="paper.pdf", size=5)})

    def duplicate(src):
        props = dict(owner.get(f"/api/blocks/{src['id']}/subtree").json()["block"]["properties"])
        pdf_id = props.pop("doc_id", None)
        if pdf_id and not props.get("source_url"):
            props["source_url"] = f"/api/uploads/{pdf_id}.pdf"
        return _page(owner, f"{src['content']} (copy)", props)

    dup = duplicate(page)
    assert dup["properties"]["doc"] == document(h, name="paper.pdf", size=5, copy=True)
    shown = duplicate(_page(owner, "Shown", doc_props(u, url="https://x.test/u.pdf")))
    assert shown["properties"]["doc"] == document(url="https://x.test/u.pdf")
    with connect_pages_db(ws) as conn:
        assert conn.execute("SELECT id FROM unified_blocks WHERE doc_id = ?", (h,)).fetchall() == [(page["id"],)]
        assert page_for_doc(conn, u) is not None
    # a page read in place whose file another page reads in place: a copy too
    linked = _page(owner, "Read here", doc_props("d" * 24, at="path"))
    assert linked["properties"]["doc"]["at"] == "path"
    assert duplicate(linked)["properties"]["doc"] == document("d" * 24, copy=True)
    # nobody's file stays the new page's own
    assert _page(owner, "New", doc_props("a" * 24))["properties"]["doc"] == document("a" * 24)


def test_by_doc_refuses_what_can_be_no_stored_file(owner):
    for bad in ("a" * 129, "a b", "a$b"):
        r = owner.post(f"/api/blocks/by-doc/{bad}", json={"default_title": "Refused"})
        assert r.status_code == 400 and r.json()["detail"] == "invalid doc_id", (bad, r.text)


# --- the lookups ----------------------------------------------------------------------

def test_the_page_of_a_file_is_a_library_pdf_it_owns(owner):
    ws = workspace_of(USER)
    h = "f" * 24
    _page(owner, "A copy of f", doc_props(url=f"/api/uploads/{h}.pdf"))
    _page(owner, "f read in place", doc_props(h, at="path"))
    host = _page(owner, "Holds f")
    assert _ops(owner, host["id"], [{"op": "insert", "id": "dmNestedF", "parent": host["id"], "content": "",
                                      "props": doc_props(h)}]).status_code == 200
    trashed = _page(owner, "f trashed", doc_props(h))
    assert owner.delete(f"/api/blocks/{trashed['id']}").status_code == 200
    text = _page(owner, "f as text", {"doc": document(h, format="text", ext=".txt")})
    with connect_pages_db(ws) as conn:
        assert page_for_doc(conn, h) is None
        assert pages_for_docs(conn, [h]) == {}
    own = _page(owner, "f itself", doc_props(h, url="https://x.test/f.pdf"))
    with connect_pages_db(ws) as conn:
        assert page_for_doc(conn, h)[0] == own["id"]
        assert pages_for_docs(conn, [h]) == {h: {"id": own["id"], "title": "f itself"}}
        pages = root_pages(conn)
    assert pages[own["id"]]["doc_id"] == h
    assert pages[text["id"]] == {"title": "f as text", "doc_id": h, "format": "text", "ext": ".txt",
                                 "at": "store", "name": pages[text["id"]]["name"]}  # a document of any format
    assert pages[host["id"]]["doc_id"] == ""


def test_pages_for_docs_still_finds_a_markdown_page(owner):
    ws = workspace_of(USER)
    md = "9" * 24
    page = _page(owner, "From a note", {"original_filename": "Reading.md", "markdown_import": md})
    with connect_pages_db(ws) as conn:
        assert pages_for_docs(conn, [md]) == {md: {"id": page["id"], "title": "From a note"}}


def test_get_or_create_doc_page_backfills_its_document(owner):
    ws = workspace_of(USER)
    h = "b" * 24
    with connect_pages_db(ws) as conn:
        page = get_or_create_doc_page(conn, h, "Backfilled", ws=ws, actor="")
    assert page["properties"]["doc"] == {**PDF, "hash": h, "at": "store"} and page["content"] == "Backfilled"
    with connect_pages_db(ws) as conn:  # the name it lacked, and the address a stored one gets
        again = get_or_create_doc_page(conn, h, "", "https://x.test/b.pdf", "b.pdf", ws=ws, actor="")
    assert again["id"] == page["id"]
    assert again["properties"]["doc"] == {**PDF, "name": "b.pdf", "hash": h, "at": "url", "url": "https://x.test/b.pdf"}
    ops = owner.get(f"/api/pages/{page['id']}/ops").json()["batches"][-1]["ops"]
    assert ops == [{"op": "set", "id": page["id"], "props": {"doc": again["properties"]["doc"]}}]
    with connect_pages_db(ws) as conn:  # a name it has and an address it has stay
        third = get_or_create_doc_page(conn, h, "", "https://x.test/other.pdf", "other.pdf", ws=ws, actor="")
    assert third["properties"]["doc"] == again["properties"]["doc"]
    with connect_pages_db(ws) as conn, pytest.raises(ValueError):
        get_or_create_doc_page(conn, "../x", "Bad", ws=ws, actor="")


# --- references and serving -----------------------------------------------------------

def test_the_files_a_doc_references():
    cases = [(doc_props("s1"), {"s1.pdf"}), (doc_props("p1", at="path"), {"p1.pdf"}),
             (doc_props(url="/api/uploads/c1.pdf"), {"c1.pdf"}),
             (doc_props("u1", url="https://x.test/p.pdf"), {"u1.pdf"}),
             (doc_props("e5", url="/api/uploads/k1.pdf"), {"e5.pdf", "k1.pdf"}),
             (doc_props(url="https://x.test/p.pdf"), set()),
             ({"doc": document("m1", format="markdown", ext=".md")}, {"m1.md"}),
             ({"doc": "x"}, set())]
    for props, names in cases:
        assert storage.upload_refs("", props) == names, props
        assert storage.upload_refs("", props, held=False) == names, props
    # a doc no reader can read (a raw writer's, a later release's field) still
    # holds the file it names, but shows none
    for doc, names in (({"hash": "d1", "at": "url", "url": " "}, {"d1.pdf"}),
                       ({**document("t1", format="text", ext=".tex"), "pages": 3}, {"t1.tex"}),
                       ({"hash": "../d1"}, set()), ({"hash": "d1", "ext": ".tex\n"}, set())):
        assert page_document({"doc": doc}) is None, doc
        assert storage.upload_refs("", {"doc": doc}) == names, doc
        assert storage.upload_refs("", {"doc": doc}, held=False) == set(), doc


def test_a_name_that_ends_in_a_newline_is_no_stem_or_extension():
    """Python's ``$`` matches before a final newline: a document whose
    extension ended in one was stored, with a file name holding it, and
    every client read it as none."""
    with pytest.raises(ValueError):
        check_document({"hash": "t1", "format": "text", "ext": ".tex\n"})
    assert formats.extension_of("main.tex\n") == "" and parse_upload_url("/api/uploads/d1.pdf\n") is None
    with pytest.raises(ValueError):
        safe_doc_id("d1\n")


def test_the_serving_tables():
    """The serving tables: every image row's media type is allowed and served inline, every audio row
    inline, a video served with its own type, SVG and HTML sandboxed, every executable blocked."""
    assert formats.IMAGE_MEDIA_TYPES == {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                                         ".gif": "image/gif", ".webp": "image/webp", ".avif": "image/avif",
                                         ".bmp": "image/bmp", ".ico": "image/x-icon", ".svg": "image/svg+xml"}
    assert formats.IMAGE_EXTENSIONS == {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
                                        "image/webp": ".webp", "image/avif": ".avif", "image/bmp": ".bmp",
                                        "image/x-icon": ".ico", "image/svg+xml": ".svg"}
    assert formats.ALLOWED_IMAGE_TYPES == {"image/png", "image/jpeg", "image/gif", "image/webp", "image/avif", "image/bmp",
                                           "image/x-icon", "image/svg+xml"}
    assert formats.INLINE_EXTENSIONS == {".pdf", ".txt", ".md", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".bmp", ".ico",
                                         ".webm", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".flac", ".aac"}
    assert formats.media_type(".mov") == "video/quicktime" and formats.media_type(".m4v") == "video/x-m4v"
    assert formats.SANDBOXED_EXTENSIONS == {".svg", ".html"}
    assert formats.BLOCKED_EXTENSIONS == {
        ".exe", ".com", ".scr", ".pif", ".bat", ".cmd", ".msi", ".msix", ".appx",
        ".dll", ".cpl", ".sys", ".vbs", ".vbe", ".jse", ".wsf", ".wsh", ".hta",
        ".ps1", ".psm1", ".reg", ".lnk", ".jar", ".app"}
    for ext, row in formats.EXTENSIONS.items():
        assert row.format in formats.FORMATS and formats.EXTENSION_RE.match(ext), ext
        assert (row.language is not None) == (row.format == "text"), ext
        assert ext not in formats.BLOCKED_EXTENSIONS
    assert all(fmt == "text" for fmt, _ in formats.NAMES.values())
