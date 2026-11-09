"""Sub-pages through shares and publications (docs/research/sub-pages.md
"Sharing and publishing"): the share settings name the pages a link
opens beyond its page (``pages_inside``) and the shares that open a page
from around it (``via``), and ``GET /pages/{id}/reach`` says what a move
into a page exposes the moved page to; an edit share's link makes a page
inside the shared page; a sub-page a publication carries reads as carried
(``carried_by``), counts in the publication's pages, can be left out of it
and stays off the share host (``sync_engine.leave_out``); a sub-page the
plan has no room for waits without a round error (``CapDeferred``); a page
made inside a published page there comes here (``_made_there``); and a
round deletes nothing there before its ``whoami`` said it may write."""

import pytest

from conftest import login, make_folder, make_user, workspace_of
from test_mirror import _transport  # noqa: F401 — every call to "another server" goes through a TestClient
from test_publish import anonymous, bound, cloud, linked, page_ids, publishing  # noqa: F401
from test_sub_pages_lifecycle import _filtered_pair, _move, _page, _row, _sync_ok
from gamma import sync_engine
from gamma.blocks_store import page_ancestors
from gamma.db import connect_pages_db
from gamma.integrations import create_token

USER, PASSWORD = "sps_owner", "pw-sps-1"


@pytest.fixture(scope="module")
def owner():
    make_user(USER, PASSWORD)
    return login(USER, PASSWORD)


def _share(client, page_id, **settings):
    r = client.post(f"/api/share/{page_id}", json=settings or None)
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_the_share_settings_name_the_pages_inside_and_the_shares_around(owner):
    folder = make_folder(owner, "Sps shared folder")
    top = _page(owner, "Sps top", folders=[folder])
    kid = _page(owner, "Sps kid", top)
    grand = _page(owner, "Sps grand", kid)
    alone = _page(owner, "Sps alone")
    own = owner.get(f"/api/share-settings/{top}").json()
    assert own["token"] is None and own["pages_inside"] == 2 and own["via"] == []
    _share(owner, top, audience="anyone", role="edit")
    r = owner.post(f"/api/share/folder/{folder}", json={"audience": "users", "role": "view"})
    assert r.status_code == 200, r.text
    # a page inside the shared page: not shared itself, opened through the page's link and the folder's
    s = owner.get(f"/api/share-settings/{grand}").json()
    assert s["token"] is None and s["pages_inside"] == 0
    assert [(v["page_id"], v["folder"], v["title"], v["audience"], v["role"]) for v in s["via"]] == [
        (top, "", "Sps top", "anyone", "edit"), ("", folder, "Sps shared folder", "users", "view")]
    # the page's own share is not among them, a page outside both has none
    assert [v["page_id"] for v in owner.get(f"/api/share-settings/{top}").json()["via"]] == [""]
    assert owner.get(f"/api/share-settings/{alone}").json()["via"] == []
    # the nearest page share first
    _share(owner, kid)
    assert [v["page_id"] for v in owner.get(f"/api/share-settings/{grand}").json()["via"]] == [kid, top, ""]

    # what a move into a page exposes the moved page to: its own share first, no publication here
    reach = owner.get(f"/api/pages/{kid}/reach").json()
    assert [s["page_id"] for s in reach["shares"]] == [kid, top, ""] and reach["published"] is None
    assert owner.get(f"/api/pages/{alone}/reach").json() == {"shares": [], "published": None}
    assert owner.get("/api/pages/nosuchpage/reach").status_code == 404


def test_an_edit_share_makes_a_page_inside_the_shared_page(owner):
    top = _page(owner, "Spe top")
    kid = _page(owner, "Spe kid", top)
    other = _page(owner, "Spe other")
    token = _share(owner, top, audience="anyone", role="edit")
    anon = anonymous()
    q = {"share": token}
    named = {"X-Gamma-Name": "Otter"}
    r = anon.post("/api/pages", params=q, headers=named, json={"title": "Made by a share editor", "parent_page": kid})
    assert r.status_code == 200, r.text
    made = r.json()["id"]
    with connect_pages_db(workspace_of(USER)) as conn:
        assert page_ancestors(conn, made) == [top, kid]
        actor = conn.execute("SELECT actor FROM page_changes WHERE page_id = ?", (made,)).fetchone()[0]
    assert actor == "link:Otter"
    # the link opens it, like any page inside the shared page
    assert anon.get(f"/api/blocks/{made}", params=q).status_code == 200
    # an id the link names, as /page makes one
    r = anon.post("/api/pages", params=q, json={"id": "spe_linked_page", "title": "", "parent_page": top})
    assert r.status_code == 200 and r.json()["id"] == "spe_linked_page"
    # nowhere else: no parent, a page outside the share, page properties
    for body in ({"title": "Loose"}, {"title": "Out", "parent_page": other}, {"title": "Missing", "parent_page": "nosuch"},
                 {"title": "Props", "parent_page": top, "properties": {"pinned": "2026-01-01"}}):
        assert anon.post("/api/pages", params=q, json=body).status_code == 403, body
    # an id a page in Recently deleted has: refused, not given way (that page need not be in the share)
    gone = _page(owner, "Spe gone")
    assert owner.delete(f"/api/blocks/{gone}").status_code == 200
    r = anon.post("/api/pages", params=q, json={"id": gone, "title": "hijack", "parent_page": top})
    assert r.status_code == 409, r.text
    assert owner.post(f"/api/trash/{gone}/restore").status_code == 200
    # a view link makes nothing
    view = _share(owner, other, audience="anyone", role="view")
    r = anon.post("/api/pages", params={"share": view}, json={"title": "x", "parent_page": other})
    assert r.status_code == 403


def test_a_sub_page_carried_by_its_parent_s_publication_reads_so(publishing):
    local, local_ws = linked(publishing, "sps_car")
    top = _page(local, "Carrier top")
    kid = _page(local, "Carrier kid", top)
    grand = _page(local, "Carrier grand", kid)
    state = local.get(f"/api/pages/{top}/publish").json()
    assert state["pages_inside"] == 2 and not state["published"]
    r = local.post(f"/api/pages/{top}/publish")
    assert r.status_code == 200, r.text
    assert set(r.json()["mirror"]["published_pages"]) == {top, kid, grand}
    url = r.json()["url"]
    state = local.get(f"/api/pages/{kid}/publish").json()
    assert state["published"] is False and state["carried_by"] == {"page_id": top, "title": "Carrier top"}
    assert state["url"] == f"{url}&page={kid}" and state["mirror"]["page_filter"] == [top]
    # the publication's pages in its listing: every page it puts there, for the pill and Settings
    listed = local.get(f"/api/mirrors/{local_ws}").json()
    assert listed["page_filter"] == [top] and set(listed["published_pages"]) == {top, kid, grand}
    assert local.get(f"/api/pages/{grand}/reach").json()["published"] == {"page_id": top, "title": "Carrier top"}


def test_a_sub_page_unpublished_on_its_own_stays_off_the_share_host(publishing):
    local, local_ws = linked(publishing, "sps_off")
    host = bound(login("sps_off", "pw"), workspace_of("sps_off"))
    top = _page(local, "Off top")
    kid = _page(local, "Off kid", top)
    grand = _page(local, "Off grand", kid)
    sibling = _page(local, "Off sibling", top)
    assert local.post(f"/api/pages/{top}/publish").status_code == 200
    assert {top, kid, grand, sibling} <= page_ids(host)
    # carried, not published on its own: unpublishing it leaves it out of the page around it
    r = local.delete(f"/api/pages/{kid}/publish")
    assert r.status_code == 200, r.text
    assert set(r.json()["mirror"]["published_pages"]) == {top, sibling}
    assert not {kid, grand} & page_ids(host) and {top, sibling} <= page_ids(host)
    for _ in range(2):
        _sync_ok(local_ws)
    assert not {kid, grand} & page_ids(host)
    _page(local, "Off great-grandchild", grand)  # a page made inside it later stays off too
    _sync_ok(local_ws)
    assert len(page_ids(host) & {top, kid, grand, sibling}) == 2 and len(page_ids(host)) == 2
    # typing in it asks for no round: the reach passes it by
    sync_engine._dirty.pop(local_ws, None)
    r = local.post(f"/api/pages/{grand}/ops", json={"client": "t", "ops": [
        {"op": "insert", "id": "sps_off1", "parent": grand, "content": "typed in a page left out"}]})
    assert r.status_code == 200, r.text
    assert not sync_engine.has_local_changes(local_ws)
    # a settings change of the page around it keeps it left out
    r = local.post(f"/api/pages/{top}/publish", json={"audience": "anyone"})
    assert r.status_code == 200, r.text
    assert sync_engine.left_out(sync_engine.get_mirror(local_ws)) == [kid]
    _sync_ok(local_ws)
    assert not {kid, grand} & page_ids(host) and top in page_ids(host)
    state = local.get(f"/api/pages/{kid}/publish").json()
    assert state["published"] is False and "carried_by" not in state
    assert state["left_out_of"] == {"page_id": top, "title": "Off top"}
    assert _row(kid, local_ws)[1]["parent_page"] == top  # here it stays where it is
    assert local.delete(f"/api/pages/{kid}/publish").status_code == 409

    # published again: back there with the pages inside it, no longer left out
    assert local.post(f"/api/pages/{kid}/publish").status_code == 200
    assert {kid, grand} <= page_ids(host)
    assert not sync_engine.left_out(sync_engine.get_mirror(local_ws))
    # published on its own as well, then unpublished: off again, the page around it still published
    assert local.delete(f"/api/pages/{kid}/publish").status_code == 200
    _sync_ok(local_ws)
    assert not {kid, grand} & page_ids(host) and top in page_ids(host)
    assert sync_engine.get_mirror(local_ws)["page_filter"] == [top]
    # moved out of the published page, it is nobody's to leave out any more
    _move(local, kid, None)
    _sync_ok(local_ws)
    assert not sync_engine.left_out(sync_engine.get_mirror(local_ws))
    assert kid not in page_ids(host)


def test_a_sub_page_with_no_room_on_the_plan_waits_without_a_round_error(publishing, caplog):
    local, local_ws = linked(publishing, "sps_cap")
    host = bound(login("sps_cap", "pw"), workspace_of("sps_cap"))
    top = _page(local, "Room top")
    for n in range(3):
        _page(local, f"Room kid {n}", top)
    other = _page(local, "Room other")
    assert local.post(f"/api/pages/{top}/publish").status_code == 200
    assert local.post(f"/api/pages/{other}/publish").status_code == 200
    assert len(page_ids(host)) == 5  # the free plan's five: full
    late = _page(local, "Room late kid", top)
    caplog.clear()
    for _ in range(3):
        status = _sync_ok(local_ws)  # no round error
        assert list(status["capped"]) == [late] and "5 published pages" in status["capped"][late]
        assert late in status["retry"] and late not in page_ids(host)
    # said once, not every round
    assert sum("no room on the remote" in m for m in caplog.messages) == 1
    # a slot freed: it goes at the next round and waits no more
    assert local.delete(f"/api/pages/{other}/publish").status_code == 200
    status = _sync_ok(local_ws)
    assert late in page_ids(host) and "capped" not in status and late not in status["retry"]


def test_the_cap_refusal_counts_the_pages_inside(publishing):
    local, _ = linked(publishing, "sps_cnt")
    big = _page(local, "Counted big")
    for n in range(5):
        _page(local, f"Counted kid {n}", big)
    r = local.post(f"/api/pages/{big}/publish")
    assert r.status_code == 409 and r.json()["going"] == 6 and r.json()["limit"]["max"] == 5


def test_a_page_made_inside_a_published_page_there_comes_here(publishing):
    local, local_ws = linked(publishing, "sps_mt")
    top = _page(local, "Made-there top")
    kid = _page(local, "Made-there kid", top)
    r = local.post(f"/api/pages/{top}/publish", json={"audience": "anyone", "role": "edit"})
    assert r.status_code == 200, r.text
    token = r.json()["share"]["token"]
    r = anonymous().post("/api/pages", params={"share": token}, json={"title": "Made by a share editor", "parent_page": kid})
    assert r.status_code == 200, r.text
    made = r.json()["id"]
    _sync_ok(local_ws)
    with connect_pages_db(local_ws) as conn:
        assert page_ancestors(conn, made) == [top, kid]
    assert made in local.get(f"/api/mirrors/{local_ws}").json()["published_pages"]
    _sync_ok(local_ws)
    assert _row(made, local_ws)[0] == "root"


def test_a_round_deletes_nothing_there_before_whoami_says_it_may_write(monkeypatch):
    remote, local, top = _filtered_pair("sps_wh")
    kid = _page(local.client, "sps_wh kid", top)
    _sync_ok(local.ws)
    assert _row(kid, remote.ws)[0] == "root"
    _move(local.client, kid, None)  # out of the published page: the next round would delete it there
    sync_engine.replace_token(local.ws, create_token(remote.id, remote.ws, "read", 30, scope="read")["token"])
    sync_engine._whoami_seen.pop(local.ws, None)
    deletes = []
    fetch = sync_engine.default_fetch

    def watched(method, path, body, headers):
        if method == "DELETE":
            deletes.append(path)
        return fetch(method, path, body, headers)

    monkeypatch.setattr(sync_engine, "default_fetch", watched)
    status = sync_engine.sync_workspace(local.ws)
    monkeypatch.setattr(sync_engine, "default_fetch", fetch)
    assert "read-only" in status["last_error"] and not deletes
    assert _row(kid, remote.ws)[0] == "root"
    # a write token again: the page leaves there
    sync_engine.replace_token(local.ws, create_token(remote.id, remote.ws, "write", 30, scope="write")["token"])
    _sync_ok(local.ws)
    assert _row(kid, remote.ws) is None or _row(kid, remote.ws)[0] != "root"
