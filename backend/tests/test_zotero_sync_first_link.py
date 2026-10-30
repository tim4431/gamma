"""A Zotero link's first link of an existing workspace, and a force, which
runs as one (gamma/zotero_sync_first.py), against the fake Zotero
(zotero_fake.py): pages and items paired one to one (by key, DOI, arXiv id,
title, a PDF's MD5), resumed when cut off; the record a first link keeps (a
value one side lacks goes to the other whatever adopt says); the PDFs it
keeps (Zotero's over a different one whose annotations it holds or under
"Take Zotero's", Gamma's over an unannotated one under "Keep Gamma's",
Zotero's kept here for "theirs"); a force pull and push, each taking one
side's version of every pair, its trash and its PDF too, on a receive-only
link as well, deleting nothing the other side holds. Rounds run through
``POST …/sync?wait=1`` with the budget lifted; background rounds are
stubbed. Accounts are ``zk_…``."""

import json

import pytest
from zotero_engine import (LIB, _block, _due, _edit, _link, _page_of, _pages, _paper, _rows, _stored_pdf,  # noqa: F401
                           _sync, _writes, background, fake, idle, made, me, no_relocation, owner_id)
from zotero_fixtures import pdf_bytes

from gamma import storage, zotero_local, zotero_sync
from gamma.db import connect_pages_db
from gamma.zotero_annotations import file_md5
from gamma.zotero_web import ZoteroUnreachable, item_uri

USER, PASSWORD = "zk_owner", "pw-zk_owner-123"


def _existing_ws(owner_id, name):
    from gamma import workspaces
    return workspaces.create(name, owner_id)["id"]


def _existing(owner_id, keys, ws=None):
    """An existing workspace of papers, some of them the library's."""
    from gamma import workspaces
    ws = ws or workspaces.create("Existing papers", owner_id)["id"]
    _paper(ws, "ziByKey", "My copy of attention", zotero_key=item_uri(LIB, keys["attention"]),
           meta={"title": "Attention, as I filed it", "year": "2017"})
    _paper(ws, "ziByArxiv", "PPO", meta={"title": "PPO algorithms", "arxiv_id": "1707.06347v2", "year": "2017"})
    _paper(ws, "ziByTitle", "Deep learning", meta={"title": "Deep learning", "year": "2016"})
    _paper(ws, "ziTitleOnly", "Bosonic codes", meta={"title": "Quantum error correction with bosonic codes",
                                                       "year": "2019", "doi": "10.1000/other"})
    _paper(ws, "ziByRdf", "From an RDF import", zotero_key="#item_12",
           meta={"title": "Retrieval-augmented generation", "doi": "10.5555/rag", "year": "2020"})
    zotero_local.create_page(ws, page_id="ziByMd5", title="handout copy.pdf",
                             props={"doc_id": _stored_pdf(ws, pdf_bytes("A standalone handout without metadata."))},
                             actor="zi", client="t")
    return ws


# --- pages and items paired -----------------------------------------------------------------------

@pytest.mark.parametrize("adopt", ["theirs", "mine"])
def test_a_first_link_of_an_existing_workspace_pairs_pages_and_items_one_to_one(me, made, fake, owner_id, adopt):
    keys = made["keys"]
    fake.edit(LIB, keys["conference"], DOI="10.5555/rag")
    twin = fake.add_item(LIB, {"itemType": "journalArticle", "title": "Retrieval-augmented generation (dup)",
                               "DOI": "10.5555/RAG", "date": "2020",
                               "dateAdded": "2030-01-01T00:00:00Z"})  # a duplicate in Zotero, added later
    ws = _existing(owner_id, keys)
    assert _link(me, workspace_id=ws, adopt=adopt) == ws
    _sync(me, ws)
    items = {row["page_id"]: key for key, row in _rows(ws, "item").items()}
    assert items["ziByKey"] == keys["attention"] and items["ziByArxiv"] == keys["ppo"]
    assert items["ziByTitle"] == keys["deep_learning"] and items["ziByRdf"] == keys["conference"]
    assert _rows(ws, "attachment")[keys["standalone_pdf"]]["page_id"] == "ziByMd5"
    # title and year alone pair nothing when one side has a DOI: both stay, each new on the other side
    assert items["ziTitleOnly"] not in (keys["thesis"],) and _page_of(ws, keys["thesis"]) != "ziTitleOnly"
    assert _block(ws, _page_of(ws, twin))["content"] == "Retrieval-augmented generation (dup)"  # one to one
    assert len(_pages(ws)) == 6 + 3  # the thesis, the twin and the standalone note, nothing else made here
    assert [d["title"] for d in fake.items(LIB) if d.get("DOI") == "10.1000/other"] == ["Bosonic codes"]  # new there
    meta = _block(ws, "ziByKey")["props"]["meta"]
    title = fake.data(LIB, keys["attention"])["title"]
    if adopt == "theirs":
        assert meta["title"] == title == "Attention is all you need"
    else:
        assert meta["title"] == title == "Attention, as I filed it"
    conflicts = me.get(f"/api/zotero/links/{ws}/conflicts?resolved=1").json()["conflicts"]
    assert any(c["kind"] == "zotero_field" and c["page_id"] == "ziByKey" for c in conflicts)
    assert me.get(f"/api/zotero/links/{ws}").json()["conflicts_open"] == 0  # decided by adopt: resolved rows
    assert not any(row["detail"].get("first") for row in _rows(ws).values())
    # the handout page holds Zotero's very file: bound, never downloaded
    assert not any(path.endswith(f"/items/{keys['standalone_pdf']}/file") for _, path, _ in fake.calls)
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == [] and len(_pages(ws)) == 9


def test_an_interrupted_first_link_resumes_without_duplicates(me, made, fake, owner_id, monkeypatch):
    keys = made["keys"]
    ws = _existing(owner_id, keys)
    _link(me, workspace_id=ws)
    real = zotero_sync._list_zotero

    def dies(r):
        raise ZoteroUnreachable("Zotero could not be reached: the round died after the match")

    monkeypatch.setattr(zotero_sync, "_list_zotero", dies)
    _sync(me, ws, ok=False)
    matched = {k for k, row in _rows(ws).items() if row["detail"].get("first")}
    assert keys["attention"] in matched and keys["ppo"] in matched
    monkeypatch.setattr(zotero_sync, "_list_zotero", real)
    _sync(me, ws)
    _sync(me, ws)
    pages = _pages(ws)
    assert len(pages) == 6 + 3  # the thesis, the conference paper and the standalone note came; nothing twice
    titles = [d.get("title") for d in fake.items(LIB) if d.get("itemType") not in ("attachment", "note", "annotation")]
    assert len(titles) == len(set(titles))


# --- the record a first link keeps ----------------------------------------------------------------

def _novel(fake):
    item = fake.add_item(LIB, {"itemType": "journalArticle", "title": "A novel paper", "date": "2021",
                               "DOI": "10.1234/novel.5678", "publicationTitle": "J. Novel", "volume": "12",
                               "pages": "3-9"})
    return item


def test_a_first_link_keeping_gammas_leaves_zoteros_fields_gammas_record_lacks(me, made, fake, owner_id):
    item = _novel(fake)
    ws = _existing_ws(owner_id, "Existing, a poorer record")
    _paper(ws, "zfxPoor", "A novel paper (Gamma's title)",
           meta={"title": "A novel paper (Gamma's title)", "year": "2021", "doi": "10.1234/novel.5678"})
    _link(me, workspace_id=ws, adopt="mine")
    _sync(me, ws)
    data = fake.data(LIB, item)
    assert (data["title"], data["publicationTitle"], data["volume"], data["pages"]) == \
        ("A novel paper (Gamma's title)", "J. Novel", "12", "3-9")
    meta = _block(ws, "zfxPoor")["props"]["meta"]
    assert (meta["venue"], meta["volume"], meta["pages"]) == ("J. Novel", "12", "3-9")


# --- the PDFs a first link keeps ------------------------------------------------------------------

def test_a_first_link_takes_zoteros_pdf_over_a_different_one_whose_annotations_zotero_holds(
        me, made, fake, owner_id, no_relocation):
    keys = made["keys"]
    from gamma import workspaces
    ws = workspaces.create("Existing with a PDF", owner_id)["id"]
    mine = _stored_pdf(ws, pdf_bytes("My own scan of attention."))
    _paper(ws, "ziOwnPdf", "Attention", doc_id=mine, zotero_key=item_uri(LIB, keys["attention"]),
           meta={"title": "Attention is all you need", "year": "2017"})
    from gamma.highlights import position
    _edit(ws, owner_id, "ziOwnPdf", [{"op": "insert", "id": "ziMyHighlight", "parent": "ziOwnPdf", "content": "",
                                      "props": {"color": "#ffd400", "quote": "My own scan",
                                                "pdf_position": position(1, 612, 792, [(72, 60, 300, 80)])}}])
    _link(me, workspace_id=ws, adopt="mine")
    _sync(me, ws)
    props = _block(ws, "ziOwnPdf")["props"]
    assert props["doc_id"] != mine  # Zotero's file, whose annotations it holds, became the page's
    assert file_md5(storage.open_path(ws, f"{props['doc_id']}.pdf")) == fake.data(LIB, keys["pdf"])["md5"]
    assert fake.data(LIB, keys["pdf"])["md5"] != file_md5(storage.open_path(ws, f"{mine}.pdf"))
    assert [p for p, _, _ in no_relocation] == ["ziOwnPdf"]  # Gamma's highlights are placed on it


def test_a_first_link_taking_zoteros_brings_zoteros_pdf_over_the_pages_own(me, made, fake, owner_id, no_relocation):
    from gamma.zotero_sync_blocks import WAIT_DIFFER
    from gamma.zotero_web import item_uri
    keys = made["keys"]
    ws = _existing_ws(owner_id, "Existing, theirs")
    mine = _stored_pdf(ws, pdf_bytes("My own scan of attention."))
    _paper(ws, "zfxOwnPdf", "Attention", doc_id=mine, zotero_key=item_uri(LIB, keys["attention"]),
           meta={"title": "Attention is all you need", "year": "2017"})
    _link(me, workspace_id=ws, adopt="theirs")
    _sync(me, ws)
    _sync(me, ws)
    props = _block(ws, "zfxOwnPdf")["props"]
    assert file_md5(storage.open_path(ws, f"{props['doc_id']}.pdf")) == fake.data(LIB, keys["pdf"])["md5"]
    assert not [k for k, row in _rows(ws, "annotation").items() if row["detail"].get("reason") == WAIT_DIFFER]


def test_a_first_link_keeping_gammas_sends_its_pdf_over_an_unannotated_zotero_one(me, made, fake, owner_id):
    item = fake.add_item(LIB, {"itemType": "journalArticle", "title": "A novel paper", "date": "2021",
                               "DOI": "10.1234/novel.5678", "publicationTitle": "J. Novel"})
    att = fake.add_item(LIB, {"itemType": "attachment", "parentItem": item, "linkMode": "imported_file",
                              "title": "Full Text PDF", "contentType": "application/pdf", "filename": "novel.pdf"})
    fake.add_file(LIB, att, pdf_bytes("Zotero's copy of the novel paper."))
    ws = _existing_ws(owner_id, "Existing, mine")
    mine = _stored_pdf(ws, pdf_bytes("Gamma's own copy of the novel paper."))
    _paper(ws, "zfxNovel", "A novel paper (Gamma's title)", doc_id=mine,
           meta={"title": "A novel paper (Gamma's title)", "year": "2021", "doi": "10.1234/novel.5678"})
    _link(me, workspace_id=ws, adopt="mine")
    _sync(me, ws)
    _sync(me, ws)
    assert _block(ws, "zfxNovel")["props"]["doc_id"] == mine
    assert fake.data(LIB, att)["md5"] == file_md5(storage.open_path(ws, f"{mine}.pdf"))


def test_a_standalone_pdf_paired_by_key_taking_zoteros_brings_zoteros_file(me, made, fake, owner_id, no_relocation):
    from gamma.zotero_web import item_uri
    keys = made["keys"]
    key = keys["standalone_pdf"]
    ws = _existing_ws(owner_id, "Existing standalone")
    mine = _stored_pdf(ws, pdf_bytes("My own different copy of the standalone."))
    zoteros = fake.data(LIB, key)["md5"]
    zotero_local.create_page(ws, page_id="zfxStand", title="Scanned handout", actor="zi", client="t",
                             props={"doc_id": mine, "zotero_key": item_uri(LIB, key)})
    _link(me, workspace_id=ws, adopt="theirs")
    _sync(me, ws)
    _sync(me, ws)
    assert fake.data(LIB, key)["md5"] == zoteros
    props = _block(ws, "zfxStand")["props"]
    assert file_md5(storage.open_path(ws, f"{props['doc_id']}.pdf")) == zoteros


def _published(fake, owner_id, name, *, stored=True):
    item = fake.add_item(LIB, {"itemType": "journalArticle", "title": f"Published {name}", "DOI": f"10.1000/{name}",
                               "date": "2022", "publicationTitle": "Science"})
    att = fake.add_item(LIB, {"itemType": "attachment", "parentItem": item, "linkMode": "imported_file",
                              "title": "Full Text PDF", "contentType": "application/pdf", "filename": "published.pdf"})
    fake.add_file(LIB, att, pdf_bytes(f"The publisher's typeset version {name}."), stored=stored)
    ws = _existing_ws(owner_id, f"Published {name}")
    doc = _stored_pdf(ws, pdf_bytes(f"My arXiv preprint copy {name}."))
    _paper(ws, f"zfx{name}", f"Published {name}", doc_id=doc,
           meta={"title": f"Published {name}", "year": "2022", "venue": "Science", "doi": f"10.1000/{name}",
                 "kind": "journalArticle"})
    return ws, att, doc


def _zotero_file_conflict(me, ws):
    found = [c for c in me.get(f"/api/zotero/links/{ws}/conflicts").json()["conflicts"] if c["kind"] == "zotero_file"]
    assert len(found) == 1, found
    return found[0]


def test_keeping_gammas_at_a_first_link_over_an_unannotated_pdf_keeps_zoteros(me, made, fake, owner_id):
    ws, att, doc = _published(fake, owner_id, "Kept")
    zoteros = fake.data(LIB, att)["md5"]
    _link(me, workspace_id=ws, adopt="mine")
    _sync(me, ws)
    _sync(me, ws)
    assert fake.data(LIB, att)["md5"] == file_md5(storage.open_path(ws, f"{doc}.pdf"))
    kept = json.loads(_zotero_file_conflict(me, ws)["base"])["doc_id"]
    assert file_md5(storage.open_path(ws, f"{kept}.pdf")) == zoteros


# --- force ----------------------------------------------------------------------------------------

def test_force_pull_and_push_take_one_sides_version_and_delete_nothing(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["thesis"])
    me.post(f"/api/zotero/links/{ws}/detach")
    meta = _block(ws, page)["props"]["meta"]
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "props": {"meta": {**meta, "year": "2001"}}}])
    fake.edit(LIB, keys["thesis"], title="Bosonic codes, Zotero's")
    me.post(f"/api/zotero/links/{ws}/relink", json={})
    me.post(f"/api/zotero/links/{ws}/force", json={"direction": "pull"})
    _sync(me, ws)
    meta = _block(ws, page)["props"]["meta"]
    assert meta["title"] == "Bosonic codes, Zotero's" and meta["year"] == "2019"  # Zotero's, everywhere
    assert fake.data(LIB, keys["thesis"])["date"] == "2019" and len(_pages(ws)) == 7
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "props": {"meta": {**meta, "year": "2002"}}}])
    me.post(f"/api/zotero/links/{ws}/detach")
    fake.edit(LIB, keys["thesis"], date="2003")
    me.post(f"/api/zotero/links/{ws}/relink", json={})
    me.post(f"/api/zotero/links/{ws}/force", json={"direction": "push"})
    _sync(me, ws)
    assert fake.data(LIB, keys["thesis"])["date"] == "2002" and _block(ws, page)["props"]["meta"]["year"] == "2002"
    assert len(_pages(ws)) == 7 and not any(d.get("deleted") for d in fake.items(LIB) if d["key"] != keys["trashed"])
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def test_a_force_pull_on_a_receive_only_link_takes_zoteros_version_of_every_pair(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me, mode="pull")
    _sync(me, ws)
    page = _page_of(ws, keys["thesis"])
    meta = _block(ws, page)["props"]["meta"]
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "props": {"meta": {**meta, "year": "1999"}}}])
    _sync(me, ws)
    assert _block(ws, page)["props"]["meta"]["year"] == "1999"  # a receive-only link keeps what Gamma changed
    assert me.post(f"/api/zotero/links/{ws}/force", json={"direction": "pull"}).status_code == 200
    _sync(me, ws)
    _sync(me, ws)
    assert _block(ws, page)["props"]["meta"]["year"] == "2019" and fake.data(LIB, keys["thesis"])["date"] == "2019"
    assert [k for k, row in _rows(ws).items() if row["detail"].get("first")] == []
    status = zotero_sync.get_link(ws)["status"]
    assert "adopt" not in status and "forced" not in status


def test_a_force_push_leaves_zoteros_fields_gammas_record_lacks(me, made, fake, owner_id):
    item = _novel(fake)
    ws = _link(me)
    _sync(me, ws)
    page = _rows(ws)[item]["page_id"]
    me.post(f"/api/zotero/links/{ws}/detach")
    meta = _block(ws, page)["props"]["meta"]
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "props": {"meta": {**meta, "volume": "", "year": "2022"}}}])
    me.post(f"/api/zotero/links/{ws}/relink", json={})
    me.post(f"/api/zotero/links/{ws}/force", json={"direction": "push"})
    _sync(me, ws)
    data = fake.data(LIB, item)
    assert (data["date"], data["volume"], data["publicationTitle"]) == ("2022", "12", "J. Novel")
    assert _block(ws, page)["props"]["meta"]["volume"] == "12"


def test_a_force_pull_leaves_an_item_zotero_trashed_in_its_trash_and_deletes_its_page_here(me, made, fake):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _rows(ws)[keys["ppo"]]["page_id"]
    fake.trash(LIB, keys["ppo"])  # before a round sees it, the user asks for Zotero's version of everything
    zotero_sync.force_sync(ws, "pull")
    calls = len(fake.calls)
    _sync(me, ws)
    assert [w for w in _writes(fake, calls) if keys["ppo"] in w[1]] == []
    assert not [b for _, p, b in fake.sent if isinstance(b, list) and any(o.get("key") == keys["ppo"] for o in b)]
    assert fake.data(LIB, keys["ppo"]).get("deleted")
    assert _block(ws, page)["parent"] != "root"  # in Recently deleted
    row = _rows(ws)[keys["ppo"]]
    assert row["state"] == "trashed" and not row["detail"].get("first")
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def test_a_force_push_sends_a_page_deleted_here_to_zoteros_trash(me, made, fake, owner_id):
    from gamma import ops
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _rows(ws)[keys["ppo"]]["page_id"]
    me.post(f"/api/zotero/links/{ws}/detach")
    with connect_pages_db(ws) as conn:
        ops.trash_page(ws, conn, page, actor=owner_id, client="t")
        conn.commit()
    me.post(f"/api/zotero/links/{ws}/relink", json={})
    me.post(f"/api/zotero/links/{ws}/force", json={"direction": "push"})
    _sync(me, ws)
    assert _block(ws, page)["parent"] != "root"  # Gamma's version: deleted, here and in Zotero's trash
    assert fake.data(LIB, keys["ppo"]).get("deleted")
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def test_a_force_push_over_a_parked_pdf_keeps_zoteros_and_says_so(me, made, fake, owner_id):
    ws, att, doc = _published(fake, owner_id, "Parked")
    zoteros = fake.data(LIB, att)["md5"]
    _link(me, workspace_id=ws, adopt="mine", upload=False)
    _sync(me, ws)
    _sync(me, ws)
    assert fake.data(LIB, att)["md5"] == zoteros  # parked: both files stay
    me.patch(f"/api/zotero/links/{ws}", json={"upload": True})
    zotero_sync.force_sync(ws, "push")
    _sync(me, ws)
    _sync(me, ws)
    mine = file_md5(storage.open_path(ws, f"{doc}.pdf"))
    assert fake.data(LIB, att)["md5"] == mine
    conflict = _zotero_file_conflict(me, ws)
    kept = json.loads(conflict["base"])["doc_id"]
    assert kept and file_md5(storage.open_path(ws, f"{kept}.pdf")) == zoteros
    r = me.post(f"/api/zotero/links/{ws}/conflicts/{conflict['id']}", json={"choice": "theirs"})
    assert r.status_code == 200, r.text
    _sync(me, ws)
    assert fake.data(LIB, att)["md5"] == zoteros


def test_a_force_push_over_a_pdf_zotero_storage_does_not_hold_sends_nothing(me, made, fake, owner_id):
    ws, att, doc = _published(fake, owner_id, "Webdav", stored=False)
    _link(me, workspace_id=ws, adopt="theirs")
    _sync(me, ws)
    me.patch(f"/api/zotero/links/{ws}", json={"upload": True})
    zotero_sync.force_sync(ws, "push")
    calls = len(fake.calls)
    for _ in range(3):
        _due(ws)
        _sync(me, ws)
    assert not [1 for m, p, _ in fake.calls[calls:] if m == "POST" and p.endswith(f"/items/{att}/file")]
    row = _rows(ws)[att]
    assert not row["detail"].get("first")
    assert _block(ws, "zfxWebdav")["props"]["doc_id"] == doc
