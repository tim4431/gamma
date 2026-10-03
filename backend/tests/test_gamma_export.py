"""Gamma-to-Gamma export: mode=gamma produces a scoped account backup
(gamma-backup-1 layout) that /api/import-data?mode=merge on any Gamma imports
additively — pages with their whole block trees, the folders and labels they
are filed under, referenced uploads, chats."""

import io
import json
import sqlite3
import tempfile
import zipfile
from contextlib import closing

import pytest

from conftest import (folder_names, label_names, login as _login, make_folder, make_label, make_page,
                      make_user as _make_user, workspace_of)


@pytest.fixture(scope="module")
def gdonor(client):
    _make_user("gdonor", "gdonorpw")
    return _login("gdonor", "gdonorpw")


@pytest.fixture(scope="module")
def greceiver(client):
    _make_user("greceiver", "greceiverpw")
    return _login("greceiver", "greceiverpw")


def _blank_pdf_bytes(width=612):
    """width varies per test: uploads dedupe by content hash, so two tests
    using identical bytes would share one file — and orphan cleanup would then
    rightly keep it while the other test's page still references it."""
    from PyPDF2 import PdfWriter
    w = PdfWriter()
    w.add_blank_page(width=width, height=792)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _donor_library(gdonor):
    """The donor's folder "gxtop / gxfolder" (exported): a paper in its
    subfolder "sub" and in "gxfolderish" outside it, labelled "gxtag"; a note
    page in the folder itself; an empty subfolder "empty"."""
    up = gdonor.post("/api/uploads", files={"file": ("g.pdf", _blank_pdf_bytes(), "application/pdf")})
    assert up.status_code == 200, up.text
    folders = {path: make_folder(gdonor, path) for path in (
        "gxtop/gxfolder", "gxtop/gxfolder/sub", "gxtop/gxfolder/empty", "gxfolderish")}
    label = make_label(gdonor, "gxtag")
    paper = make_page(gdonor, "Gx paper", properties={
        "doc_id": up.json()["doc_id"], "source_url": up.json()["source_url"],
        "folders": [folders["gxtop/gxfolder/sub"], folders["gxfolderish"]], "labels": [label],
        "meta": {"title": "Gx paper", "authors": ["Ada"], "year": "2024"},
    })
    rect = {"x1": 50.0, "y1": 60.0, "x2": 250.0, "y2": 160.0}
    r = gdonor.put(f"/api/blocks/{paper['id']}/children", json={"blocks": [
        {"id": "gxh1", "content": "my thought", "children": [], "properties": {
            "quote": "a quote", "color": "rgba(170, 235, 170, 0.65)",
            "pdf_position": {"pageNumber": 1, "width": 612.0, "height": 792.0, "boundingRect": rect, "rects": [rect]},
        }},
    ]})
    assert r.status_code == 200, r.text
    note = make_page(gdonor, "Gx note page", properties={"folders": [folders["gxtop/gxfolder"]]})
    r = gdonor.put(f"/api/chats/{paper['id']}", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200, r.text
    return paper, note, up.json(), folders, label


def _say(text):
    return [{"role": "user", "content": text}]


@pytest.fixture(scope="module")
def gx_export(gdonor):
    """The donor's folder export (mode=gamma) and what it holds."""
    paper, note, up, folders, label = _donor_library(gdonor)
    # the paper's earlier conversation, the folder views' chats (the
    # exported folder's and a subfolder's), and one the export leaves out
    gdonor.post("/api/chat-history/archive", json={"bucket": paper["id"], "messages": _say("earlier")}
                ).raise_for_status()
    gdonor.put(f"/api/chats/{paper['id']}", json={"messages": _say("hi")}).raise_for_status()
    for path in ("gxtop/gxfolder", "gxtop/gxfolder/sub", "gxfolderish"):
        gdonor.put(f"/api/chats/{folders[path]}", json={"messages": _say(path)}).raise_for_status()
    r = gdonor.get(f"/api/folders/{folders['gxtop/gxfolder']}/export", params={"mode": "gamma"})
    assert r.status_code == 200, r.text
    return {"paper": paper, "note": note, "up": up, "folders": folders, "label": label, "zip": r.content}


def _merge(client, data):
    imp = client.post("/api/import-data", params={"mode": "merge"},
                      files={"file": ("gx.zip", data, "application/zip")})
    assert imp.status_code == 200, imp.text
    return imp.json()


def test_gamma_folder_export_carries_its_folders_and_labels(gx_export):
    z = zipfile.ZipFile(io.BytesIO(gx_export["zip"]))
    names = set(z.namelist())
    # the /api/export backup layout: pages.db (the chats in it) + manifest at
    # the root, flat uploads/; no data.db, which holds nothing to carry
    assert {"pages.db", "manifest.json"} <= names and "data.db" not in names
    manifest = json.loads(z.read("manifest.json"))
    assert manifest["format"] == "gamma-backup-1"
    assert manifest["scope"] == {"folder": gx_export["folders"]["gxtop/gxfolder"], "pages": 2}
    assert f"uploads/{gx_export['up']['source_url'].rsplit('/', 1)[-1]}" in names
    with tempfile.TemporaryDirectory() as td:
        path = f"{td}/pages.db"
        with open(path, "wb") as out:
            out.write(z.read("pages.db"))
        with closing(sqlite3.connect(path)) as conn:
            trees = dict(conn.execute("SELECT content, page_id FROM unified_blocks "
                                      "WHERE page_id IN ('folders', 'labels')").fetchall())
            buckets = {r[0] for r in conn.execute("SELECT bucket FROM chats")}
    # the exported folder's subtree (the empty subfolder too) and the folder
    # above it; the paper's other folder; its label
    assert trees == {"gxtop": "folders", "gxfolder": "folders", "sub": "folders", "empty": "folders",
                     "gxfolderish": "folders", "gxtag": "labels"}
    folders = gx_export["folders"]
    # the folder views' chats inside the exported folder, not the one outside it
    assert buckets == {gx_export["paper"]["id"], folders["gxtop/gxfolder"], folders["gxtop/gxfolder/sub"]}


def test_gamma_folder_export_merges_into_another_account(gx_export, greceiver):
    paper, up, folders, label = gx_export["paper"], gx_export["up"], gx_export["folders"], gx_export["label"]
    d = _merge(greceiver, gx_export["zip"])
    assert d["pages_added"] == 2 and d["uploads_added"] == 1

    # Whole block tree intact: same ids, highlight properties preserved.
    got = greceiver.get(f"/api/blocks/{paper['id']}").json()
    assert got["properties"]["meta"]["authors"] == ["Ada"]
    # The folders came along (an empty workspace: under their own ids), and
    # the pages are filed in them.
    paths = folder_names(greceiver)
    assert {tuple(p) for p in paths.values()} == {
        ("gxtop",), ("gxtop", "gxfolder"), ("gxtop", "gxfolder", "sub"), ("gxtop", "gxfolder", "empty"),
        ("gxfolderish",)}
    assert got["properties"]["folders"] == [folders["gxtop/gxfolder/sub"], folders["gxfolderish"]]
    assert got["properties"]["labels"] == [label] and label_names(greceiver) == {label: "gxtag"}
    note = greceiver.get(f"/api/blocks/{gx_export['note']['id']}").json()
    assert [paths[f] for f in note["properties"]["folders"]] == [["gxtop", "gxfolder"]]
    children = greceiver.get(f"/api/blocks/{paper['id']}/children").json()["children"]
    hl = next(c for c in children if c["id"] == "gxh1")
    assert hl["content"] == "my thought"
    assert hl["properties"]["pdf_position"]["pageNumber"] == 1
    # The PDF came along and serves.
    assert greceiver.get(up["source_url"]).status_code == 200
    # The paper's AI chat merged too.
    chat = greceiver.get(f"/api/chats/{paper['id']}").json()
    assert chat["messages"] and chat["messages"][0]["content"] == "hi"
    history = greceiver.get("/api/chat-history", params={"bucket": paper["id"]}).json()["sessions"]
    assert [s["preview"] for s in history] == ["earlier"]
    for path in ("gxtop/gxfolder", "gxtop/gxfolder/sub"):
        assert greceiver.get(f"/api/chats/{folders[path]}").json()["messages"] == _say(path)
    assert greceiver.get(f"/api/chats/{folders['gxfolderish']}").json()["messages"] == []
    assert d["chats_added"] == 4  # the paper's two conversations, the two folder views'

    # Merge is idempotent: the same zip again adds nothing, no folder twice.
    again = _merge(greceiver, gx_export["zip"])
    assert again["pages_added"] == 0 and again["pages_skipped"] == 2
    assert folder_names(greceiver) == paths and label_names(greceiver) == {label: "gxtag"}


def test_gamma_folder_export_maps_onto_the_same_paths(gx_export):
    """A workspace that has folders at the same paths (other ids) and a
    label of the same name: the import files the pages in those, and makes
    only the folders it lacks, below them."""
    _make_user("gmapped", "gmappedpw")
    mapped = _login("gmapped", "gmappedpw")
    mine = make_folder(mapped, "gxtop/gxfolder")
    my_label = make_label(mapped, "GXTAG")  # a name matches ignoring case
    folders = gx_export["folders"]
    assert mine != folders["gxtop/gxfolder"]
    d = _merge(mapped, gx_export["zip"])
    assert d["pages_added"] == 2
    paths = folder_names(mapped)
    assert sorted(paths.values()) == [["gxfolderish"], ["gxtop"], ["gxtop", "gxfolder"],
                                      ["gxtop", "gxfolder", "empty"], ["gxtop", "gxfolder", "sub"]]
    note = mapped.get(f"/api/blocks/{gx_export['note']['id']}").json()
    assert note["properties"]["folders"] == [mine]
    paper = mapped.get(f"/api/blocks/{gx_export['paper']['id']}").json()
    assert [paths[f] for f in paper["properties"]["folders"]] == [["gxtop", "gxfolder", "sub"], ["gxfolderish"]]
    assert paper["properties"]["labels"] == [my_label] and label_names(mapped) == {my_label: "GXTAG"}
    # the exported folder's chat follows it to the folder it mapped onto
    assert mapped.get(f"/api/chats/{mine}").json()["messages"] == _say("gxtop/gxfolder")


def test_gamma_export_delete_reimport_is_near_identical(gdonor):
    """The disaster-recovery round trip: export a folder, delete its pages
    from the SAME account (orphan cleanup removes their uploads), merge the
    zip back — every block row must come back byte-identical (id, parent,
    content, properties, created_at, updated_at, even sibling positions); the
    one allowed difference is the root pages' own position, which the merge
    regenerates to append after the existing pages. Files and chats too."""
    import sqlite3

    from gamma.db import ws_db_path, ws_uploads_dir

    up = gdonor.post("/api/uploads", files={"file": ("rt.pdf", _blank_pdf_bytes(width=611), "application/pdf")})
    assert up.status_code == 200, up.text
    pdf_name = up.json()["source_url"].rsplit("/", 1)[-1]
    paper = make_page(gdonor, "Rt paper", properties={
        "doc_id": up.json()["doc_id"], "source_url": up.json()["source_url"],
        "folders": [make_folder(gdonor, "rtfolder/deep")], "labels": [make_label(gdonor, "rt")],
        "meta": {"title": "Rt paper", "year": "2025"},
    })
    rect = {"x1": 50.0, "y1": 60.0, "x2": 250.0, "y2": 160.0}
    r = gdonor.put(f"/api/blocks/{paper['id']}/children", json={"blocks": [
        {"id": "rth1", "content": "thought", "properties": {
            "quote": "q", "color": "rgba(170, 235, 170, 0.65)",
            "pdf_position": {"pageNumber": 1, "width": 612.0, "height": 792.0, "boundingRect": rect, "rects": [rect]},
        }, "children": [
            {"id": "rth1a", "content": "nested note", "properties": {}, "children": []},
        ]},
        {"id": "rtn1", "content": "free note", "properties": {}, "children": []},
    ]})
    assert r.status_code == 200, r.text
    rtfolder = make_folder(gdonor, "rtfolder")
    note = make_page(gdonor, "Rt note", properties={"folders": [rtfolder]})
    assert gdonor.put(f"/api/chats/{paper['id']}",
                      json={"messages": [{"role": "user", "content": "rt chat"}]}).status_code == 200

    def rows_of(ids):
        with sqlite3.connect(ws_db_path(workspace_of("gdonor"), "pages.db")) as conn:
            placeholders = ",".join("?" for _ in ids)
            return sorted(conn.execute(
                "WITH RECURSIVE sub(id) AS ("
                f"  SELECT id FROM unified_blocks WHERE id IN ({placeholders})"
                "  UNION ALL"
                "  SELECT b.id FROM unified_blocks b JOIN sub ON b.parent_id = sub.id)"
                " SELECT id, parent_id, position, content, properties, created_at, updated_at"
                " FROM unified_blocks WHERE id IN (SELECT id FROM sub)", ids).fetchall())

    page_ids = [paper["id"], note["id"]]
    before = rows_of(page_ids)
    assert len(before) == 5  # 2 roots + highlight + nested note + free note
    pdf_bytes_before = (ws_uploads_dir(workspace_of("gdonor")) / pdf_name).read_bytes()

    exp = gdonor.get(f"/api/folders/{rtfolder}/export", params={"mode": "gamma"})
    assert exp.status_code == 200, exp.text

    for pid in page_ids:  # deleted for good: through Recently deleted, then out of it
        assert gdonor.delete(f"/api/blocks/{pid}").status_code == 200
        assert gdonor.delete(f"/api/trash/{pid}").status_code == 200
    assert rows_of(page_ids) == []
    # the now-unreferenced PDF stays for 30 days (gamma/upload_gc.py); take it
    # away as the purge eventually would, so the import has to bring it back
    (ws_uploads_dir(workspace_of("gdonor")) / pdf_name).unlink()

    imp = gdonor.post("/api/import-data", params={"mode": "merge"},
                      files={"file": ("rt.zip", exp.content, "application/zip")})
    assert imp.status_code == 200, imp.text
    assert imp.json()["pages_added"] == 2

    after = rows_of(page_ids)
    assert len(after) == len(before)
    by_id_before = {r[0]: r for r in before}
    for row in after:
        want = by_id_before[row[0]]
        if row[1] == "root":
            # merge appends root pages after the existing ones and stamps them
            # now, so the change feed sees them — position and updated_at are
            # the fields allowed to change
            assert row[:2] == want[:2] and row[3:6] == want[3:6] and row[6] >= want[6]
        else:
            assert row == want
    # the PDF is back byte-identical, and the chat survived the round trip
    assert (ws_uploads_dir(workspace_of("gdonor")) / pdf_name).read_bytes() == pdf_bytes_before
    chat = gdonor.get(f"/api/chats/{paper['id']}").json()
    assert chat["messages"][0]["content"] == "rt chat"


def test_gamma_single_page_export(gdonor):
    page = make_page(gdonor, "Gx single", properties={"labels": [make_label(gdonor, "solo")]})
    r = gdonor.get(f"/api/pages/{page['id']}/export", params={"mode": "gamma"})
    assert r.status_code == 200, r.text
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert "pages.db" in z.namelist()
    scope = json.loads(z.read("manifest.json"))["scope"]
    assert scope == {"folder": None, "pages": 1}
    with tempfile.TemporaryDirectory() as td:
        with open(f"{td}/pages.db", "wb") as out:
            out.write(z.read("pages.db"))
        with closing(sqlite3.connect(f"{td}/pages.db")) as conn:
            trees = conn.execute("SELECT content FROM unified_blocks WHERE page_id IN ('folders', 'labels')").fetchall()
    assert trees == [("solo",)]  # its label travels, no folder


def test_unknown_mode_rejected(gdonor):
    page = make_page(gdonor, "Gx mode check")
    r = gdonor.get(f"/api/pages/{page['id']}/export", params={"mode": "nonsense"})
    assert r.status_code == 400
