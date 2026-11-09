"""The library's bulk actions in one request (POST /api/blocks/bulk,
ops.trash_pages / apply_each): many pages to Recently deleted, or many
properties patches, in one transaction, each page checked and answered as
its own DELETE or PUT /blocks/{id} would be, one refused leaving the others
to go."""

import pytest

from conftest import login, make_folder, make_page, make_user, workspace_of
from gamma import collab
from gamma.blocks_store import TRASH
from gamma.db import connect_pages_db
from gamma.routers.blocks import MAX_BULK


@pytest.fixture(scope="module")
def owner():
    make_user("bulk_owner", "pw")
    return login("bulk_owner", "pw")


def _bulk(c, body, status=200, **kw):
    r = c.post("/api/blocks/bulk", json=body, **kw)
    assert r.status_code == status, r.text
    return r.json()


def _row(ws, block_id):
    with connect_pages_db(ws) as conn:
        return conn.execute("SELECT parent_id, properties FROM unified_blocks WHERE id = ?", (block_id,)).fetchone()


def _library(c):
    return {b["id"] for b in c.get("/api/blocks/root/children").json()["children"]}


def test_bulk_trash_moves_each_page_and_answers_each_in_order(owner, monkeypatch):
    ws = workspace_of("bulk_owner")
    pages = [make_page(owner, f"Bulk {i}")["id"] for i in range(3)]
    inner = make_page(owner, "Inside Bulk 0", properties={"parent_page": pages[0]})["id"]
    note = owner.post("/api/blocks", json={"parent_id": pages[1], "content": "a note"}).json()["id"]
    heard = []
    monkeypatch.setattr(collab, "publish", lambda w, page, msg: heard.append((page, msg.get("t"))))

    asked = [pages[0], "nosuchblockid0", note, pages[1], pages[1], inner, pages[2]]
    results = _bulk(owner, {"trash": asked})["results"]
    assert [r["id"] for r in results] == asked
    assert [r["ok"] for r in results] == [True, False, False, True, False, False, True]
    # what DELETE /blocks/{id} answers, page by page
    assert results[0]["trashed"]["id"] == pages[0] and results[0]["trashed"]["deleted_by"] == "bulk_owner"
    assert (results[1]["status"], results[1]["detail"]) == (404, "block not found")
    assert (results[2]["status"], results[2]["detail"]) == (400, "not a page")
    # the page asked twice, and the one inside a page that went before it, are gone already
    assert results[4]["status"] == 404 and results[5]["status"] == 404
    for page in (*pages, inner):
        assert _row(ws, page)[0] == TRASH
        assert page not in _library(owner)
    assert _row(ws, note)[0] == pages[1], "a block named alone is no page and stays"
    # each room heard it went, the page inside the first one too
    assert {p for p, t in heard if t == "trashed"} == {*pages, inner}
    trashed = {p["id"] for p in owner.get("/api/trash").json()["pages"]}
    assert set(pages) <= trashed


def test_bulk_patches_apply_each_and_one_refused_leaves_the_others(owner):
    ws = workspace_of("bulk_owner")
    folder = make_folder(owner, "bulk/filed")
    a, b, c = (make_page(owner, f"Patch {n}")["id"] for n in "abc")
    sub = make_page(owner, "Patch sub", properties={"parent_page": a})["id"]
    gone = make_page(owner, "Patch gone")["id"]
    owner.delete(f"/api/blocks/{gone}").raise_for_status()

    patches = [
        {"id": a, "properties": {"pinned": "2026-11-08T10:00:00Z", "folders": [folder]}},
        {"id": gone, "properties": {"pinned": "2026-11-08T10:00:00Z"}},
        {"id": b, "properties": {"parent_page": sub}},   # fine: b goes inside a's sub-page
        {"id": a, "properties": {"parent_page": sub}},   # a cycle: refused alone
        {"id": c, "properties": {"pinned": None}},
    ]
    results = _bulk(owner, {"patches": patches})["results"]
    assert [r["id"] for r in results] == [p["id"] for p in patches]
    assert [r["ok"] for r in results] == [True, False, True, False, True]
    assert results[0]["properties"] == patches[0]["properties"] and results[0]["seq"] > 0
    assert results[0]["updated_at"]
    assert (results[1]["status"], results[1]["detail"]) == (404, "block not found")
    assert results[3]["status"] == 400
    props = owner.get(f"/api/blocks/{a}").json()["properties"]
    assert props["pinned"] == "2026-11-08T10:00:00Z" and props["folders"] == [folder]
    assert "parent_page" not in props, "the refused patch wrote nothing"
    assert owner.get(f"/api/blocks/{b}").json()["properties"]["parent_page"] == sub
    # each batch is logged on its own page, as PUT /blocks/{id} logs it
    with connect_pages_db(ws) as conn:
        logged = {r[0] for r in conn.execute("SELECT DISTINCT page_id FROM page_ops WHERE page_id IN (?, ?, ?)",
                                             (a, b, c))}
    assert logged == {a, b, c}


def test_bulk_patch_of_a_page_nothing_else_wrote_matches_a_put(owner):
    page = make_page(owner, "Same as a PUT")["id"]
    other = make_page(owner, "Same as a PUT, twin")["id"]
    body = {"properties": {"labels": None, "pinned": "2026-11-08T11:00:00Z"}}
    one = owner.put(f"/api/blocks/{other}", json=body).json()
    many = _bulk(owner, {"patches": [{"id": page, **body}]})["results"][0]
    assert many["properties"] == one["properties"]
    assert owner.get(f"/api/blocks/{page}").json()["properties"] == owner.get(f"/api/blocks/{other}").json()["properties"]


def test_bulk_refusals(owner, anon):
    page = make_page(owner, "Bulk refusals")["id"]
    _bulk(owner, {}, 400)
    _bulk(owner, {"trash": [page], "patches": []}, 400)
    _bulk(owner, {"trash": ["x"] * (MAX_BULK + 1)}, 413)
    r = owner.post("/api/blocks/bulk", content='{"patches": [{"id": "%s", "properties": {"n": NaN}}]}' % page,
                   headers={"Content-Type": "application/json"})
    assert r.status_code == 400 and "not a finite number" in r.text
    assert anon.post("/api/blocks/bulk", json={"trash": [page]}).status_code == 401
    assert _bulk(owner, {"trash": []})["results"] == []

    # a share editor may delete no page and change no page root's properties: the request is refused
    make_user("bulk_outsider", "pw")
    outsider = login("bulk_outsider", "pw")
    token = owner.post(f"/api/share/{page}").json()["token"]
    owner.put(f"/api/share-settings/{page}",
              json={"audience": "list", "users": [{"name": "bulk_outsider", "role": "edit"}]}).raise_for_status()
    q = {"share": token}
    assert outsider.post("/api/blocks/bulk", params=q, json={"trash": [page]}).status_code == 403
    assert outsider.post("/api/blocks/bulk", params=q,
                         json={"patches": [{"id": page, "properties": {"pinned": "x"}}]}).status_code == 403
    assert page in _library(owner)


def test_a_viewer_is_refused(owner):
    make_user("bulk_admin", "pw", is_admin=1)
    make_user("bulk_viewer", "pw")
    r = login("bulk_admin", "pw").post("/api/workspaces", json={"name": "Bulk lab", "kind": "shared",
                                                                "owner": "bulk_owner"})
    ws = r.json()["id"]
    owner.put(f"/api/workspaces/{ws}/members/bulk_viewer", json={"role": "viewer"}).raise_for_status()
    h = {"X-Gamma-Workspace": ws}
    page = owner.post("/api/blocks", json={"parent_id": "root", "content": "Lab"}, headers=h).json()["id"]
    viewer = login("bulk_viewer", "pw")
    assert viewer.post("/api/blocks/bulk", headers=h, json={"trash": [page]}).status_code == 403
    assert _bulk(owner, {"trash": [page]}, headers=h)["results"][0]["ok"]


def test_a_share_host_deletes_for_good(owner, monkeypatch):
    from gamma import cloud_auth

    real = cloud_auth.settings
    monkeypatch.setattr(cloud_auth, "settings", lambda: {**real(), "share_host": True})
    ws = workspace_of("bulk_owner")
    pages = [make_page(owner, f"Published {i}")["id"] for i in range(2)]
    results = _bulk(owner, {"trash": pages})["results"]
    assert results == [{"id": p, "ok": True} for p in pages]
    assert all(_row(ws, p) is None for p in pages)


def test_a_share_host_deletes_in_one_transaction_and_answers_a_page_already_gone(owner, monkeypatch):
    from gamma import cloud_auth, ops

    real = cloud_auth.settings
    monkeypatch.setattr(cloud_auth, "settings", lambda: {**real(), "share_host": True})
    ws = workspace_of("bulk_owner")
    top = make_page(owner, "Published top")["id"]
    inner = make_page(owner, "Published inner", properties={"parent_page": top})["id"]
    other = make_page(owner, "Published other")["id"]
    # the page inside goes with its top; it and the top asked again are no library pages by their turn
    results = _bulk(owner, {"trash": [top, inner, top, other]})["results"]
    assert results == [
        {"id": top, "ok": True},
        {"id": inner, "ok": False, "status": 404, "detail": "block not found"},
        {"id": top, "ok": False, "status": 404, "detail": "block not found"},
        {"id": other, "ok": True},
    ]
    assert all(_row(ws, p) is None for p in (top, inner, other))
    with connect_pages_db(ws) as conn:
        kinds = dict(conn.execute("SELECT page_id, kind FROM page_changes WHERE page_id IN (?, ?)", (top, inner)))
    assert kinds == {top: "deleted", inner: "deleted"}

    # one failing purge undoes the whole set: nothing is gone for good behind a 500
    first, second = make_page(owner, "Kept A")["id"], make_page(owner, "Kept B")["id"]
    purge = ops._purge

    def failing(conn, page_id, actor):
        if page_id == second:
            raise RuntimeError("database is locked")
        return purge(conn, page_id, actor)

    monkeypatch.setattr(ops, "_purge", failing)
    with pytest.raises(RuntimeError):
        owner.post("/api/blocks/bulk", json={"trash": [first, second]})
    assert _row(ws, first) is not None and _row(ws, second) is not None
