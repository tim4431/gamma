"""The readers of a library that holds a work folder (docs/dev/
work_folders.md "The readers, classified"): the library listing carries
the pages the library lists and the pinned files (``SQL_IN_LISTING``), with
each directory's count of files (``work_counts``) and, ``within`` a folder,
that folder's pages whatever the library lists; a directory reads page by
page (GET /api/folders/{id}/pages); POST /api/pages/summaries answers the
pages the listing leaves out. Then the library-wide jobs over a populated
work folder, which take a figure for no paper: the lookup by document and
its 409, the clipper's match, its folder picker and the paper metadata."""

import secrets
from types import SimpleNamespace

import pytest

from conftest import account_of, login, make_folder, make_page, make_user, mark_work_folder, work_props, workspace_of
from gamma.auth import DISK_CLIENT
from gamma.blocks_store import create_page, page_for_doc
from gamma.db import connect_pages_db
from gamma.documents.model import document
from gamma.ops import SYNC_CLIENT
from gamma.routers.clip import find_page
from gamma.routers.metadata import NO_RECORD

USER, PASSWORD = "wfr_owner", "pw-wfr-1"
PINNED = "2026-10-01T00:00:00.000Z"


@pytest.fixture(scope="module")
def r():
    """A work folder W that pulls, its directory D, a library folder L. In
    W: ``a.pdf`` (1,000 bytes) and ``main.pdf`` (2,000 bytes, in the
    library, filed in L as well), the notes ``notes.md`` and ``todo.md``
    (pinned); in D ``fig.pdf`` with a sub-page a mirror brought. Beside
    them a library page in L, a stored paper and an opened PDF read in
    place. ``updated_at`` runs a, main, notes, todo, fig."""
    make_user(USER, PASSWORD)
    owner = login(USER, PASSWORD)
    ws, account = workspace_of(USER), account_of(USER)
    ids = {name: make_folder(owner, path) for name, path in (("W", "wfr-W"), ("D", "wfr-W/dir"), ("L", "wfr-L"))}
    mark_work_folder(owner, ids["W"], pull=True)
    W, D, L = ids["W"], ids["D"], ids["L"]
    h = {k: secrets.token_hex(32) for k in ("a", "main", "fig", "fig2", "paper", "opened")}

    def pdf(key, name, size=None):
        return document(h[key], name=name, at="path", size=size)

    with connect_pages_db(ws) as conn:
        def vault(title, props):
            return create_page(conn, title, props, actor=account, client=DISK_CLIENT)["id"]

        pages = {
            "a": vault("a", work_props(W, "a.pdf", doc=pdf("a", "a.pdf", 1000))),
            "main": vault("Attention", {**work_props(W, "main.pdf", library=True, doc=pdf("main", "main.pdf", 2000)),
                                        "folders": [W, L]}),
            "notes": vault("notes", work_props(W, "notes.md")),
            "todo": vault("todo", work_props(W, "todo.md")),
            "fig": vault("fig", {**work_props(W, "fig.pdf", doc=pdf("fig", "fig.pdf", 300), directory=D),
                                 "meta": {"doi": "10.5555/wfr.fig"}}),
            "fig2": vault("fig2", work_props(W, "fig2.pdf", doc=pdf("fig2", "fig2.pdf"), directory=D)),
        }
        pages["under"] = create_page(conn, "under fig", {"parent_page": pages["fig"]}, actor=account,
                                     client=SYNC_CLIENT)["id"]
    pages["library"] = make_page(owner, "a library page", properties={"folders": [L]})["id"]
    pages["paper"] = make_page(owner, "a stored paper", properties={
        "doc": document(h["paper"], name="paper.pdf"), "meta": {"doi": "10.5555/wfr.paper"}})["id"]
    pages["opened"] = make_page(owner, "an opened PDF", properties={"doc": pdf("opened", "opened.pdf")})["id"]
    assert owner.put(f"/api/blocks/{pages['todo']}", json={"properties": {"pinned": PINNED}}).status_code == 200
    with connect_pages_db(ws) as conn:
        for day, key in enumerate(("a", "main", "notes", "todo", "fig", "fig2"), 1):
            conn.execute("UPDATE unified_blocks SET updated_at = ? WHERE id = ?",
                         (f"2026-01-0{day}T00:00:00.000Z", pages[key]))
        conn.commit()
    return SimpleNamespace(owner=owner, ws=ws, h=h, p=pages, **ids)


def _listing(c, **params):
    got = c.get("/api/blocks/root/children", params=params)
    assert got.status_code == 200, got.text
    return got.json()


def _ids(r, *keys):
    return {r.p[k] for k in keys}


# ---- the library listing ------------------------------------------------------------------

def test_the_listing_leaves_out_the_files_the_library_does_not_list(r):
    out = _listing(r.owner)
    listed = {p["id"]: p for p in out["children"]}
    assert _ids(r, "main", "todo", "library", "paper", "opened") <= listed.keys()
    assert not _ids(r, "a", "notes", "fig", "fig2", "under") & listed.keys()  # a sub-page goes by its top
    pinned = listed[r.p["todo"]]["properties"]
    assert pinned["work_folder"] == r.W and pinned["pinned"] == PINNED and "library" not in pinned
    main = listed[r.p["main"]]["properties"]
    assert (main["work_folder"], main["library"], main["file_name"]) == (r.W, True, "main.pdf")
    tree = {f["id"]: f for f in out["folders"]["children"]}
    assert tree[r.W]["properties"]["vault"] == {"pull": True, "library": False}


def test_work_counts_count_each_directorys_own_files(r):
    assert _listing(r.owner)["work_counts"] == {r.W: 4, r.D: 2}


def test_within_lists_a_folders_pages_whatever_the_library_lists(r):
    def within(folder):
        return [p["id"] for p in _listing(r.owner, within=folder)["children"]]

    assert set(within(r.W)) == _ids(r, "a", "main", "notes", "todo", "fig", "fig2", "under")
    assert set(within(r.D)) == _ids(r, "fig", "fig2", "under")
    assert set(within(r.L)) == _ids(r, "main", "library")
    assert within("no-such-folder") == []
    ordered = within(r.D)
    assert ordered.index(r.p["under"]) == ordered.index(r.p["fig"]) + 1  # each page then the pages inside it


def test_a_folder_share_of_a_work_folder_lists_its_files(r, anon):
    made = r.owner.post(f"/api/share/folder/{r.W}", json={"audience": "anyone", "role": "view"})
    assert made.status_code == 200, made.text
    share = {"share": made.json()["token"]}
    out = _listing(anon, **share)
    ids = {p["id"] for p in out["children"]}
    assert _ids(r, "a", "main", "notes", "todo", "fig", "fig2") <= ids
    assert not _ids(r, "library", "paper", "opened") & ids
    assert out["work_counts"] == {r.W: 4, r.D: 2}
    assert anon.get("/api/blocks/root/children", params={**share, "within": r.D}).status_code == 400
    assert anon.get(f"/api/folders/{r.D}/pages", params=share).status_code == 200
    other = r.owner.post(f"/api/share/folder/{r.L}", json={"audience": "anyone", "role": "view"}).json()["token"]
    assert anon.get(f"/api/folders/{r.D}/pages", params={"share": other}).status_code == 403


# ---- a directory, page by page ------------------------------------------------------------

def _names(r, folder, **params):
    got = r.owner.get(f"/api/folders/{folder}/pages", params=params)
    assert got.status_code == 200, got.text
    return [p["properties"]["file_name"] for p in got.json()["pages"]]


def test_a_directory_reads_its_own_files_sorted(r):
    assert _names(r, r.W) == ["a.pdf", "main.pdf", "notes.md", "todo.md"]
    assert _names(r, r.W, order="desc") == ["todo.md", "notes.md", "main.pdf", "a.pdf"]
    assert _names(r, r.W, sort="format") == ["notes.md", "todo.md", "a.pdf", "main.pdf"]  # a note is markdown
    assert _names(r, r.W, sort="size") == ["a.pdf", "main.pdf", "notes.md", "todo.md"]   # none last
    assert _names(r, r.W, sort="size", order="desc") == ["main.pdf", "a.pdf", "notes.md", "todo.md"]
    assert _names(r, r.W, sort="modified") == ["todo.md", "notes.md", "main.pdf", "a.pdf"]  # the newest first
    assert _names(r, r.W, sort="modified", order="asc") == ["a.pdf", "main.pdf", "notes.md", "todo.md"]
    assert _names(r, r.D) == ["fig.pdf", "fig2.pdf"]  # the sub-page is no file of it


def test_a_directory_reads_page_by_page(r):
    first = r.owner.get(f"/api/folders/{r.W}/pages", params={"limit": 3}).json()
    assert (first["folder"], first["offset"], first["next"], first["total"]) == (r.W, 0, 3, 4)
    rest = r.owner.get(f"/api/folders/{r.W}/pages", params={"limit": 3, "offset": first["next"]}).json()
    assert (rest["offset"], rest["next"], rest["total"]) == (3, None, 4)
    assert [p["id"] for p in first["pages"] + rest["pages"]] == [
        r.p[k] for k in ("a", "main", "notes", "todo")]
    row = first["pages"][1]
    assert row["content"] == "Attention" and row["properties"]["doc"]["size"] == 2000
    assert row["preview"] == "" and row["parent"] == "" and row["open_comments"] == 0


def test_a_rows_open_comments_count_its_open_threads(r):
    def thread(page_id, body):
        made = r.owner.post(f"/api/pages/{page_id}/comments", json={"block_id": page_id, "body": body})
        assert made.status_code == 201, made.text
        return made.json()["thread"]["id"]

    thread(r.p["notes"], "still open")
    thread(r.p["notes"], "open too")
    assert r.owner.post(f"/api/comments/{thread(r.p['notes'], 'settled')}/resolve", json={}).status_code == 200
    assert r.owner.delete(f"/api/comments/{thread(r.p['a'], 'taken back')}").status_code == 200
    rows = {p["id"]: p["open_comments"] for p in r.owner.get(f"/api/folders/{r.W}/pages").json()["pages"]}
    assert rows[r.p["notes"]] == 2 and rows[r.p["a"]] == 0 and rows[r.p["todo"]] == 0


def test_a_directory_read_refuses_what_is_no_directory(r):
    for folder in (r.L, "no-such-folder"):
        got = r.owner.get(f"/api/folders/{folder}/pages")
        assert got.status_code == 400 and got.json()["detail"] == "the library lists this folder's pages"
    assert r.owner.get(f"/api/folders/{r.W}/pages", params={"sort": "kind"}).status_code == 400
    assert r.owner.get(f"/api/folders/{r.W}/pages", params={"order": "up"}).status_code == 400
    assert r.owner.get(f"/api/folders/{r.W}/pages", params={"offset": -1}).status_code == 400


# ---- summaries -----------------------------------------------------------------------------

def test_summaries_answer_the_live_pages_asked_in_order(r):
    gone = make_page(r.owner, "gone soon")["id"]
    assert r.owner.delete(f"/api/blocks/{gone}").status_code == 200
    child = r.owner.post("/api/blocks", json={"parent_id": r.p["notes"], "content": "a note's block"}).json()["id"]
    asked = [r.p["notes"], "no-such-page", gone, child, r.p["fig"], r.p["library"], r.p["notes"]]
    got = r.owner.post("/api/pages/summaries", json={"ids": asked})
    assert got.status_code == 200, got.text
    pages = got.json()["pages"]
    assert [p["id"] for p in pages] == [r.p["notes"], r.p["fig"], r.p["library"]]
    assert pages[0]["preview"] == "a note's block" and pages[0]["properties"]["file_name"] == "notes.md"
    under = r.owner.post("/api/pages/summaries", json={"ids": [r.p["under"]]}).json()["pages"]
    assert under[0]["parent"] == r.p["fig"]
    too_many = r.owner.post("/api/pages/summaries", json={"ids": [f"p{i}" for i in range(501)]})
    assert too_many.status_code == 413


def test_summaries_through_a_share_leave_out_what_it_does_not_reach(r, anon):
    token = r.owner.post(f"/api/share/folder/{r.W}", json={"audience": "anyone", "role": "view"}).json()["token"]
    got = anon.post("/api/pages/summaries", params={"share": token},
                    json={"ids": [r.p["library"], r.p["fig"], r.p["paper"], r.p["main"]]})
    assert got.status_code == 200, got.text
    assert [p["id"] for p in got.json()["pages"]] == [r.p["fig"], r.p["main"]]


# ---- the library-wide jobs over a work folder ----------------------------------------------

def test_a_file_not_in_the_library_is_no_page_of_its_pdf(r):
    with connect_pages_db(r.ws) as conn:
        assert page_for_doc(conn, r.h["fig"]) is None and page_for_doc(conn, r.h["opened"]) is None
        assert page_for_doc(conn, r.h["main"])[0] == r.p["main"]
        assert page_for_doc(conn, r.h["paper"])[0] == r.p["paper"]
    found = r.owner.post("/api/pages/by-docs", json={"doc_ids": [r.h[k] for k in ("a", "fig", "main", "paper")]})
    assert set(found.json()["pages"]) == {r.h["main"], r.h["paper"]}
    assert r.owner.get(f"/api/blocks/by-doc/{r.h['fig']}").status_code == 404
    refused = r.owner.post(f"/api/blocks/by-doc/{secrets.token_hex(32)}", json={"default_title": "x", "folder": r.D})
    assert refused.status_code == 403 and refused.json()["conflict"] == "locked"  # a PDF is its vault's to file there


def test_attaching_a_files_pdf_elsewhere_is_no_conflict(r):
    held = r.owner.post(f"/api/pages/{make_page(r.owner, 'takes main')['id']}/attachment",
                        json={"doc_id": r.h["main"]})
    assert held.status_code == 409 and held.json()["page_id"] == r.p["main"]
    free = make_page(r.owner, "takes fig2")["id"]
    attached = r.owner.post(f"/api/pages/{free}/attachment", json={"doc_id": r.h["fig2"]})
    assert attached.status_code == 200, attached.text
    with connect_pages_db(r.ws) as conn:
        assert page_for_doc(conn, r.h["fig2"])[0] == free  # the library's one page of those bytes


def test_the_clipper_matches_library_documents_only(r):
    with connect_pages_db(r.ws) as conn:
        assert find_page(conn, doi="10.5555/wfr.fig") is None
        assert find_page(conn, doi="10.5555/wfr.paper")["id"] == r.p["paper"]


def test_the_clippers_folders_are_the_filing_targets(r):
    got = r.owner.get("/api/library/folders")
    assert got.status_code == 200, got.text
    offered = {f["id"] for f in got.json()["folders"]}
    assert r.L in offered and not {r.W, r.D} & offered


def test_paper_metadata_leaves_a_file_out_until_it_is_in_the_library(r):
    papers = {p["id"] for p in r.owner.get("/api/metadata/status").json()["papers"]}
    assert _ids(r, "main", "paper") <= papers
    assert not _ids(r, "a", "fig", "fig2", "notes", "opened") & papers
    refused = r.owner.post("/api/metadata/fetch", json={"block_id": r.p["fig"]})
    assert refused.status_code == 409 and refused.json()["detail"] == NO_RECORD
