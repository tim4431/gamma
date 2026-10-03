"""The workspace change feed (routers/sync.py) over the change log
(``page_changes``): the pages whose row moved past a cursor, in seq order —
live ones with their op-log seq, deleted ones as tombstones — paginated,
workspace-scoped and exact: a change is listed once, and a page written
again comes again at its new place."""

from fractional_indexing import generate_key_between

from conftest import account_of, guest_name, login, make_page, make_user, workspace_of
from gamma import ops
from gamma.db import connect_pages_db


def _feed(client, since="", limit=500):
    r = client.get("/api/sync/changes", params={"since": since, "limit": limit})
    assert r.status_code == 200, r.text
    return r.json()


def _ids(feed, key="pages"):
    return [p["id"] for p in feed[key]]


def _caught_up(client):
    """The cursor at the end of the feed now."""
    feed = _feed(client, limit=2000)
    while feed["more"]:
        feed = _feed(client, feed["cursor"], limit=2000)
    return feed["cursor"]


def _insert(client, page_id, block_id):
    r = client.post(f"/api/pages/{page_id}/ops", json={"client": "t", "ops": [
        {"op": "insert", "id": block_id, "parent": page_id, "position": generate_key_between(None, None),
         "content": block_id}]})
    assert r.status_code == 200, r.text
    return r.json()["seq"]


def test_feed_lists_changed_and_deleted_pages_with_their_seq(guest):
    make_page(guest, "Old")
    cursor = _caught_up(guest)
    page = make_page(guest, "Fresh")
    feed = _feed(guest, cursor)
    assert _ids(feed) == [page["id"]] and feed["deleted"] == [] and feed["more"] is False
    assert feed["since"] == cursor and int(feed["cursor"]) > int(cursor)
    entry = feed["pages"][0]
    assert entry["seq"] == 0 and entry["created_at"] == entry["updated_at"]

    seq = _insert(guest, page["id"], "sfA")
    after = _feed(guest, cursor)
    assert _ids(after) == [page["id"]]
    assert after["pages"][0]["seq"] == seq and after["pages"][0]["updated_at"] > entry["created_at"]

    guest.delete(f"/api/blocks/{page['id']}").raise_for_status()
    gone = _feed(guest, cursor)
    assert _ids(gone) == [] and [(d["id"], d["actor"]) for d in gone["deleted"]] == [
        (page["id"], account_of(guest_name()))]


def test_a_change_is_listed_once(guest):
    a, b = make_page(guest, "Once A"), make_page(guest, "Once B")
    cursor = _caught_up(guest)
    assert _feed(guest, cursor) == {"since": cursor, "cursor": cursor, "more": False, "pages": [], "deleted": []}
    _insert(guest, a["id"], "sfOnce1")
    once = _feed(guest, cursor)
    assert _ids(once) == [a["id"]]
    assert _feed(guest, once["cursor"])["pages"] == []  # caught up: nothing again, ever
    # b written, then a again: each once, in the order they were last written
    _insert(guest, b["id"], "sfOnce2")
    _insert(guest, a["id"], "sfOnce3")
    assert _ids(_feed(guest, once["cursor"])) == [b["id"], a["id"]]
    assert _ids(_feed(guest, cursor)) == [b["id"], a["id"]]


def test_feed_paginates_in_seq_order_and_a_page_written_meanwhile_comes_again_later(guest):
    cursor = _caught_up(guest)
    pages = [make_page(guest, f"P{i}")["id"] for i in range(5)]
    first = _feed(guest, cursor, limit=2)
    assert _ids(first) == pages[:2] and first["more"] is True
    _insert(guest, pages[0], "sfPaged")  # written while the walk goes on: its row moves past the walk
    seen, cursors, feed = _ids(first), [int(first["cursor"])], first
    while feed["more"]:
        feed = _feed(guest, feed["cursor"], limit=2)
        assert len(feed["pages"]) + len(feed["deleted"]) <= 2
        seen += _ids(feed)
        cursors.append(int(feed["cursor"]))
    assert seen == pages + [pages[0]]
    assert cursors == sorted(set(cursors)) and len(cursors) == 3  # strictly forward, 2 + 2 + 2
    assert feed["cursor"] == _caught_up(guest)


def test_a_page_made_again_under_a_deleted_id_is_live(guest):
    ws = workspace_of(guest_name())
    page = make_page(guest, "Gone for good")
    cursor = _caught_up(guest)
    with connect_pages_db(ws) as conn:
        ops.delete_page(ws, conn, page["id"], actor="sf_t")
    gone = _feed(guest, cursor)
    assert _ids(gone) == [] and _ids(gone, "deleted") == [page["id"]]
    r = guest.post("/api/pages", json={"id": page["id"], "title": "Back again"})
    assert r.status_code == 200, r.text
    for since in (cursor, gone["cursor"]):  # one row per page: the newest says what it is
        back = _feed(guest, since)
        assert _ids(back) == [page["id"]] and back["deleted"] == []


def test_a_cursor_this_log_never_gave_out_lists_from_the_start(guest):
    make_page(guest, "Listed whatever")
    everything = _feed(guest, "", limit=2000)
    assert everything["more"] is False
    for since in ("0", "2026-01-01T00:00:00.000000Z|abc", "-3", "x", "１２", str(int(everything["cursor"]) + 1)):
        again = _feed(guest, since, limit=2000)
        assert (again["pages"], again["deleted"], again["cursor"]) == (
            everything["pages"], everything["deleted"], everything["cursor"]), since


def test_feed_is_workspace_scoped_and_needs_a_session(client, guest, anon):
    make_user("feed_other", "pw")
    other = login("feed_other", "pw")
    mine = make_page(guest, "Mine")
    theirs = make_page(other, "Theirs")
    assert mine["id"] in _ids(_feed(guest, limit=2000)) and theirs["id"] not in _ids(_feed(guest, limit=2000))
    assert theirs["id"] in _ids(_feed(other)) and mine["id"] not in _ids(_feed(other))
    assert anon.get("/api/sync/changes").status_code == 401
    assert guest.get("/api/sync/changes?limit=0").status_code == 200


def test_wholesale_rewrites_reach_the_feed(guest):
    """A subtree replace on a nested block logs a reload — and touches the
    page, so the feed lists it."""
    page = make_page(guest, "Rewritten")
    child = guest.post("/api/blocks", json={"parent_id": page["id"], "content": "c"}).json()
    cursor = _caught_up(guest)
    r = guest.put(f"/api/blocks/{child['id']}/children", json={"blocks": [{"content": "grandchild"}]})
    assert r.status_code == 200, r.text
    feed = _feed(guest, cursor)
    assert _ids(feed) == [page["id"]] and feed["pages"][0]["seq"] >= 2
