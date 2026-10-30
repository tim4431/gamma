"""The Zotero link's engine (gamma/zotero_sync.py) and its API
(routers/zotero_sync.py) against the fake Zotero (zotero_fake.py): the
registry and its refusals (a workspace is a clone, a publication or a
link, never two; one link per library, made in one step even when asked
twice at once), the settings and what they start over, detach, relink,
force and remove (a transfer stopped at once, the PDF marks that go with
the link), the owner-only routes and their answers (a workspace no longer
its owner's personal one uses no key), the dialog's counts (bounded, the
last ones answered again while nothing changed, pairing as a first link
does), the conflicts and the log (a resolved conflict taken once), the
clock it registers (its pool, its debounce, background rounds coalesced),
the status and its error kinds (a fill shown only while a round of it
follows, a page waiting for quiet no unsent edit), Zotero's listing and
Gamma's change feed into the queue, and the bounded round with its
backoff. Collections ↔ folders are test_zotero_sync_folders's. Rounds run
through ``POST …/sync?wait=1`` with the budget lifted; background rounds
are stubbed. Accounts are ``zs_…``."""

import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from conftest import account_of, login, make_user
from zotero_engine import (GROUP, LIB, _by_name, _collection_rows, _edit, _folder_ops, _link, _page,  # noqa: F401
                           _paper, _paths, _queue_entries, _rows, _state, _stored_pdf, _sync, _writes, background,
                           drop_link_row, drop_pending, fake, held, idle, link_row, made, me, owner_id)
from zotero_fake import FULL_ACCESS, FakeZotero
from zotero_fixtures import pdf_bytes, standard_library
from zotero_idle import assert_idle

from gamma import (auth, config, ops, publish, storage, sync_clock, sync_engine, workspaces, ws_backup, zotero_account,
                   zotero_local, zotero_stream, zotero_sync, zotero_sync_blocks, zotero_sync_items, zotero_sync_rows,
                   zotero_web)
from gamma import zotero_remote as zr
from gamma.blocks_store import FOLDERS, LABELS, label_names
from gamma.db import connect_pages_db, connect_users_db, page_now
from gamma.zotero_web import MAX_RETRIES, ZoteroError

USER, PASSWORD = "zs_owner", "pw-zs_owner-123"
OTHER, OTHER_PASSWORD = "zs_other", "pw-zs_other-123"
INFO_KEYS = {"kind", "workspace_id", "name", "library", "library_name", "remote_name", "remote_url", "mode",
             "download", "upload", "auto_tags", "send_unrecorded", "poll_s", "on_change", "status", "created_at",
             "conflicts_open", "conflicts_newest", "pending_local", "detached", "interval_s"}
REAL_BACKGROUND = zotero_sync.sync_in_background  # before ``background`` stubs it


@pytest.fixture(scope="module")
def other_id():
    make_user(OTHER, OTHER_PASSWORD)
    return account_of(OTHER)


def _clear_queue(ws, kinds=None):
    with connect_pages_db(ws) as conn:
        if kinds:
            conn.execute(f"DELETE FROM zotero_queue WHERE kind IN ({','.join('?' * len(kinds))})", kinds)
        else:
            conn.execute("DELETE FROM zotero_queue")
        conn.commit()


# --- the registry and the API ---------------------------------------------------------

def test_a_link_makes_a_workspace_and_answers_its_info(me, made, background, owner_id):
    ws = _link(me)
    assert background == [ws]  # its first round starts in the background
    info = me.get(f"/api/zotero/links/{ws}").json()
    assert set(info) == INFO_KEYS
    assert info["kind"] == "zotero" and info["name"] == "Zotero" and info["library"] == LIB
    assert info["library_name"] == info["remote_name"] == "My Library"
    assert info["remote_url"] == "https://www.zotero.test/tim/library"
    assert (info["mode"], info["download"], info["upload"], info["auto_tags"], info["send_unrecorded"]) == (
        "two-way", "all", True, False, False)
    assert (info["poll_s"], info["on_change"], info["detached"], info["pending_local"]) == (5, True, False, False)
    assert info["status"]["mode"] == "two-way" and info["status"]["username"] == "tim"
    assert info["status"]["access"] == {"write": True, "files": True, "notes": True}
    assert me.get("/api/zotero/links").json() == {"links": [info]}
    mine = next(w for w in me.get("/api/workspaces/mine").json()["workspaces"] if w["id"] == ws)
    assert mine["zotero_of"] == "My Library" and mine["mirror_of"] == "" and mine["personal"]
    assert _state(ws)["library"] == LIB and _state(ws)["first"] == "new"
    # a group is named after itself, and the page on zotero.org is the group's
    group = me.post("/api/zotero/links", json={"library": GROUP, "name": "Lab papers"}).json()
    assert group["name"] == "Lab papers" and group["library_name"] == "Lab"
    assert group["remote_url"] == "https://www.zotero.test/groups/9/library"


def test_linking_is_refused_without_a_connection_a_readable_library_or_twice(me, made, owner_id, other_id, guest):
    zotero_account.disconnect(owner_id, revoke=False)
    r = me.post("/api/zotero/links", json={"library": LIB})
    assert r.status_code == 409 and r.json()["detail"] == zotero_account.NOT_CONNECTED
    zotero_account.connect(owner_id, "zkey-good")
    assert me.post("/api/zotero/links", json={"library": "groups/999"}).status_code == 400
    assert me.post("/api/zotero/links", json={"library": "users/abc"}).status_code == 422
    assert me.post("/api/zotero/links", json={"library": LIB, "mode": "off"}).status_code == 422
    other_ws = workspaces.default_workspace(other_id)
    r = me.post("/api/zotero/links", json={"library": LIB, "workspace_id": other_ws})
    assert r.status_code == 400 and "not a workspace of yours" in r.json()["detail"]
    _link(me)
    r = me.post("/api/zotero/links", json={"library": LIB})
    assert r.status_code == 400 and "already link" in r.json()["detail"]
    assert guest.get("/api/zotero/links").status_code == 403
    assert guest.post("/api/zotero/links", json={"library": LIB}).status_code == 403


def test_two_links_of_one_library_asked_at_once_make_one(me, made, owner_id, monkeypatch):
    ready = threading.Barrier(2, timeout=10)
    real = zotero_sync._library_entry

    def together(client_, library):
        found = real(client_, library)
        ready.wait()  # both asked Zotero before either checks for a link
        return found

    monkeypatch.setattr(zotero_sync, "_library_entry", together)
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(zotero_sync.create_link, owner_id, LIB) for _ in range(2)]
    outcomes = []
    for future in futures:
        try:
            outcomes.append(future.result()["workspace_id"])
        except ValueError as e:
            outcomes.append(str(e))
    made_links = [link["workspace_id"] for link in zotero_sync.list_links(owner_id)]
    assert len(made_links) == 1 and "you already link that Zotero library to a workspace" in outcomes


def test_a_link_that_fails_once_its_workspace_is_made_takes_the_workspace_with_it(me, made, owner_id, monkeypatch):
    before = {w["id"] for w in workspaces.list_for_user(owner_id)}

    def broken(conn, **fields):
        raise RuntimeError("the disk is full")

    monkeypatch.setattr(zr, "set_state", broken)
    with pytest.raises(RuntimeError):
        zotero_sync.create_link(owner_id, LIB, name="Doomed")
    assert {w["id"] for w in workspaces.list_for_user(owner_id)} == before
    assert zotero_sync.list_links(owner_id) == []


def test_a_second_link_of_a_library_the_index_stops_is_refused_as_the_check_refuses_it(me, made, owner_id, monkeypatch):
    other = workspaces.create("Linked elsewhere", owner_id)["id"]
    real = zr.set_state

    def raced(conn, **fields):  # another link of the library lands between the check and this one's row
        monkeypatch.setattr(zr, "set_state", real)
        link_row(other, owner_id)
        return real(conn, **fields)

    try:
        before = {w["id"] for w in workspaces.list_for_user(owner_id)}
        monkeypatch.setattr(zr, "set_state", raced)
        r = me.post("/api/zotero/links", json={"library": LIB})
        assert r.status_code == 400, r.text
        assert r.json()["detail"] == "you already link that Zotero library to a workspace"
        assert {w["id"] for w in workspaces.list_for_user(owner_id)} == before  # the workspace made for it went
        assert [link["workspace_id"] for link in zotero_sync.list_links(owner_id)] == [other]
    finally:
        drop_link_row(other)
        workspaces.delete(other)


def test_a_workspace_is_a_clone_a_publication_or_a_link_never_two(me, made, owner_id, monkeypatch):
    existing = workspaces.create("Existing papers", owner_id)["id"]
    ws = _link(me, workspace_id=existing, adopt="mine")
    assert ws == existing and _state(ws)["first"] == "existing"
    assert zotero_sync.get_link(ws)["status"]["adopt"] == "mine"
    r = me.post("/api/zotero/links", json={"library": GROUP, "workspace_id": ws})
    assert r.status_code == 400 and "linked to a Zotero library already" in r.json()["detail"]
    # a clone's workspace cannot be linked...
    clone = workspaces.create("A clone", owner_id)["id"]
    with connect_users_db() as conn:
        conn.execute("INSERT INTO mirrors (workspace_id, remote_url, remote_ws, token, owner, created_at) "
                     "VALUES (?, 'http://remote.test', 'rws', 'x', ?, ?)", (clone, owner_id, page_now()))
        conn.commit()
    r = me.post("/api/zotero/links", json={"library": GROUP, "workspace_id": clone})
    assert r.status_code == 400 and "clone" in r.json()["detail"]
    # ...nor a linked one cloned, detached or not
    monkeypatch.setattr(sync_engine, "_check_remote", lambda url, token, mode, fetch: (
        url, {"user": "u", "role": "owner", "workspace": {"id": "rws", "name": "R"}}, mode))
    for _ in range(2):
        with pytest.raises(ValueError, match="Zotero"):
            sync_engine.create_mirror(owner_id, "http://another.test", "gamma_token", workspace_id=ws)
        zotero_sync.detach_link(ws)
    with connect_users_db() as conn:
        conn.execute("DELETE FROM mirrors WHERE workspace_id = ?", (clone,))
        conn.commit()
    # ...nor does it publish: refused before any token is minted, and the button says why
    page = _page(ws, "zsPub1", "To publish")
    monkeypatch.setattr(publish, "_mirror_for", lambda *a, **k: pytest.fail("no mirror for a linked workspace"))
    with pytest.raises(publish.PublishError) as e:
        publish.publish(owner_id, ws, page)
    assert e.value.status == 409 and e.value.message == publish.ZOTERO_LINKED
    state = publish.state(owner_id, ws, page)
    assert state["can_publish"] is False and state["reason"] == publish.ZOTERO_LINKED


def test_a_key_that_cannot_write_links_receive_only(owner_id, monkeypatch):
    access = {"user": {"library": True, "files": True, "notes": True, "write": False}, "groups": {}}
    fake = FakeZotero(access=access).install(monkeypatch)
    standard_library(fake)
    zotero_account.connect(owner_id, fake.api_key)
    c = login(USER, PASSWORD)
    try:
        ws = _link(c)
        info = c.get(f"/api/zotero/links/{ws}").json()
        assert info["mode"] == "two-way" and info["status"]["mode"] == "pull"
        _folder_ops(ws, owner_id, [{"op": "insert", "id": "zsRoNew", "parent": FOLDERS, "content": "Mine only"}])
        status = _sync(c, ws)
        assert status["mode"] == "pull" and status["access"]["write"] is False
        assert _writes(fake) == [] and "Mine only" not in _by_name(fake)
        assert _state(ws)["local_cursor"] == ""  # a receive-only round keeps Gamma's changes for later
        assert c.post(f"/api/zotero/links/{ws}/force", json={"direction": "push"}).status_code == 400
        assert c.get(f"/api/zotero/links/{ws}").json()["pending_local"] is False
        assert_idle(fake, ws)
    finally:
        for link in zotero_sync.list_links(owner_id):
            zotero_sync.remove_link(link["workspace_id"])
        zotero_account.disconnect(owner_id, revoke=False)


def test_settings_change_what_travels_and_start_the_feed_over_when_more_may_go(me, made):
    ws = _link(me)
    _sync(me, ws)
    _sync(me, ws)  # the folders the first round made are in the feed by now
    cursor = _state(ws)["local_cursor"]
    assert cursor
    r = me.patch(f"/api/zotero/links/{ws}", json={"poll_s": 300, "on_change": False, "download": "open"})
    info = r.json()
    assert (info["poll_s"], info["on_change"], info["download"]) == (300, False, "open")
    assert _state(ws)["local_cursor"] == cursor
    me.patch(f"/api/zotero/links/{ws}", json={"upload": False})
    assert _state(ws)["local_cursor"] == cursor
    me.patch(f"/api/zotero/links/{ws}", json={"upload": True})  # uploads newly allowed: every page again
    assert _state(ws)["local_cursor"] == ""
    _sync(me, ws)
    me.patch(f"/api/zotero/links/{ws}", json={"mode": "pull"})
    assert _state(ws)["local_cursor"] and me.get(f"/api/zotero/links/{ws}").json()["mode"] == "pull"
    me.patch(f"/api/zotero/links/{ws}", json={"mode": "two-way"})
    assert _state(ws)["local_cursor"] == ""
    _sync(me, ws)
    me.patch(f"/api/zotero/links/{ws}", json={"send_unrecorded": True})
    assert _state(ws)["local_cursor"] == "" and me.get(f"/api/zotero/links/{ws}").json()["send_unrecorded"]
    for bad in ({"download": "some"}, {"poll_s": -1}, {"mode": "off"}):
        assert me.patch(f"/api/zotero/links/{ws}", json=bad).status_code == 422
    # automatic tags turned on or off: every item is looked at again
    with connect_pages_db(ws) as conn:
        zotero_sync_rows.write_rows(conn, [zotero_sync_rows.new_row("ITEM2345", "item", version=7)])
        conn.commit()
    _clear_queue(ws)
    me.patch(f"/api/zotero/links/{ws}", json={"auto_tags": True})
    assert _queue_entries(ws)["ITEM2345"]["kind"] == "top"
    # a settings change lifts a wait on Zotero's full storage
    zotero_sync_rows.patch_status(ws, {"waiting": {"files": {"quota": 2}, "quota_until": "2026-10-29T00:00:00Z"}})
    me.patch(f"/api/zotero/links/{ws}", json={"on_change": True})
    assert "quota_until" not in me.get(f"/api/zotero/links/{ws}").json()["status"]["waiting"]
    with connect_pages_db(ws) as conn:  # the stand-in item goes; Zotero's automatic tags come as labels
        zotero_sync_rows.drop_rows(conn, ["ITEM2345"])
        conn.execute("DELETE FROM zotero_queue WHERE id = 'ITEM2345'")
        conn.commit()
    _sync(me, ws)
    with connect_pages_db(ws) as conn:
        assert "attention" in label_names(conn).values()


def test_detach_keeps_everything_and_relink_goes_on(me, made, fake, background, owner_id):
    ws = _link(me)
    _sync(me, ws)
    rows = _collection_rows(ws)
    info = me.post(f"/api/zotero/links/{ws}/detach").json()
    assert info["detached"] is True and info["mode"] == "off"
    assert info["status"]["detached_mode"] == "two-way" and info["status"]["detached_at"]
    mine = next(w for w in me.get("/api/workspaces/mine").json()["workspaces"] if w["id"] == ws)
    assert mine["zotero_of"] == ""
    assert me.patch(f"/api/zotero/links/{ws}", json={"mode": "pull"}).status_code == 400
    calls = len(fake.calls)
    assert me.post(f"/api/zotero/links/{ws}/sync?wait=1").status_code == 200
    assert len(fake.calls) == calls  # a detached link asks Zotero nothing
    assert me.post(f"/api/zotero/links/{ws}/force", json={"direction": "pull"}).status_code == 400
    zotero_account.disconnect(owner_id, revoke=False)
    assert me.post(f"/api/zotero/links/{ws}/relink", json={}).status_code == 409  # connect Zotero first
    zotero_account.connect(owner_id, fake.api_key)
    background.clear()
    info = me.post(f"/api/zotero/links/{ws}/relink", json={}).json()
    assert info["detached"] is False and info["mode"] == "two-way" and background == [ws]
    assert "detached_at" not in info["status"] and "detached_mode" not in info["status"]
    assert set(_collection_rows(ws)) == set(rows)


def test_a_force_is_noted_then_runs_as_a_first_link(me, made, fake, background):
    ws = _link(me)
    _sync(me, ws)
    assert me.post(f"/api/zotero/links/{ws}/force", json={"direction": "sideways"}).status_code == 422
    background.clear()
    info = me.post(f"/api/zotero/links/{ws}/force", json={"direction": "pull"}).json()
    assert info["status"]["force"] == "pull" and background == [ws]
    calls = len(fake.calls)
    status = _sync(me, ws)
    # every pair decided in the one round, the force is spent: nothing of it stays on the status
    assert not {"force", "adopt", "forced"} & set(status)
    listed = [path for m, path, _ in fake.calls[calls:] if m == "GET"]
    assert any(path.startswith(f"/{LIB}/collections?since=0") for path in listed)  # both sides from the start
    assert _state(ws)["version"] == fake.version(LIB)
    with connect_pages_db(ws) as conn:  # the collections and the items were decided
        assert not any(row["detail"].get("first") for row in zotero_sync_rows.read_rows(conn).values())
    assert _writes(fake, calls) == []  # nothing differed


def test_removing_a_link_forgets_what_it_kept_and_its_marks(me, made, owner_id):
    ws = _link(me)
    _sync(me, ws)
    page = _page(ws, "zsMarked", "Marked", zotero_pdf={"key": "ATTA2345", "state": "remote"})
    ops.commit_ops(ws, page, [{"op": "insert", "id": "zsLossy1", "parent": page, "content": "a note",
                               "props": {"zotero_readonly": "lossy"}}], actor=owner_id, client="t")
    zotero_sync_rows.record_conflict(ws, page, "zsLossy1", "zotero_lossy", mine="a note")
    assert me.delete(f"/api/zotero/links/{ws}").json() == {"ok": True}
    assert zotero_sync.get_link(ws) is None and me.get(f"/api/zotero/links/{ws}").status_code == 404
    with connect_pages_db(ws) as conn:
        for table in ("zotero_objects", "zotero_queue", "zotero_state", "sync_log", "sync_conflicts"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
        props = {bid: json.loads(raw) for bid, raw in conn.execute(
            "SELECT id, properties FROM unified_blocks WHERE id IN (?, ?)", (page, "zsLossy1"))}
    assert "zotero_pdf" not in props[page] and "zotero_readonly" not in props["zsLossy1"]
    assert set(_paths(ws)) == {("ML",), ("ML", "Transformers"), ("ML", "Transformers", "Efficient"), ("Reading",)}
    assert workspaces.get(ws) is not None


def test_removing_a_link_mid_download_stops_the_transfer_at_once(me, made, fake, idle, monkeypatch):
    idle.skip("the link is removed in the middle of its round")
    started = threading.Event()

    def slow(self, library, key, sink, *, md5="", tick=None):
        started.set()
        end = time.monotonic() + 10
        while time.monotonic() < end:  # a large file at a slow pace: the client beats as it streams
            if self.heartbeat:
                self.heartbeat()
            time.sleep(0.02)
        raise zotero_web.ZoteroError("the test's transfer ran out", 500)

    monkeypatch.setattr(zotero_web.ZoteroClient, "download", slow)
    ws = _link(me)
    round_ = ThreadPoolExecutor(1).submit(zotero_sync.sync_workspace, ws)
    assert started.wait(10)
    at = time.monotonic()
    zotero_sync.remove_link(ws)
    assert time.monotonic() - at < 5
    assert zotero_sync.get_link(ws) is None
    round_.result(10)


MARK = {"key": "ABCD2345", "state": "remote", "filename": "x.pdf"}


def _marked_page(name: str) -> tuple:
    ws = make_user(name, "pw-" + name)
    c = login(name, "pw-" + name)
    c.headers["X-Gamma-Workspace"] = ws
    r = c.post("/api/pages", json={"title": "Item", "properties": {"zotero_pdf": dict(MARK)}})
    assert r.status_code == 200, r.text
    return ws, c, r.json()["id"]


def test_a_mark_left_on_a_page_in_recently_deleted_goes_with_the_link(client):
    ws, c, page = _marked_page("zs_marks1")
    link_row(ws, account_of("zs_marks1"))
    assert c.delete(f"/api/blocks/{page}").status_code == 200  # to Recently deleted
    zotero_sync.remove_link(ws)
    assert c.post(f"/api/trash/{page}/restore").status_code == 200
    with connect_pages_db(ws) as conn:
        props = json.loads(conn.execute("SELECT properties FROM unified_blocks WHERE id = ?", (page,)).fetchone()[0])
    assert "zotero_pdf" not in props
    r = c.post(f"/api/pages/{page}/attachment", json={"source_url": "https://example.org/a.pdf"})
    assert r.status_code == 200, r.text


def test_a_mark_restored_from_a_backup_without_its_link_lets_a_pdf_be_attached(client):
    ws, c, page = _marked_page("zs_marks2")
    link_row(ws, account_of("zs_marks2"))
    made = ws_backup.create(ws, label="while-linked")
    zotero_sync.remove_link(ws)
    ws_backup.restore_zip(ws, ws_backup.backup_path(ws, made["name"]), "replace")
    r = c.post(f"/api/pages/{page}/attachment", json={"source_url": "https://example.org/b.pdf"})
    assert r.status_code == 200, r.text
    assert "zotero_pdf" not in r.json()["properties"]  # the mark goes with the attach


def test_a_detached_link_and_a_clone_still_refuse_a_pdf_on_a_marked_page(client):
    ws, c, page = _marked_page("zs_marks3")
    link_row(ws, account_of("zs_marks3"), mode="off")  # a reattach would bring Zotero's file over it
    r = c.post(f"/api/pages/{page}/attachment", json={"source_url": "https://example.org/c.pdf"})
    assert r.status_code == 409
    clone, cc, cpage = _marked_page("zs_marks4")
    with connect_users_db() as conn:  # a whole-workspace clone of a linked origin: the attach would reach it
        conn.execute("INSERT INTO mirrors (workspace_id, remote_url, remote_ws, token, owner, created_at) "
                     "VALUES (?, 'http://testserver', 'origin', '', ?, ?)",
                     (clone, account_of("zs_marks4"), page_now()))
        conn.commit()
    try:
        r = cc.put(f"/api/pages/{cpage}/attachment", json={"source_url": "https://example.org/d.pdf"})
        assert r.status_code == 409
    finally:
        with connect_users_db() as conn:
            conn.execute("DELETE FROM mirrors WHERE workspace_id = ?", (clone,))
            conn.commit()
    zotero_sync.remove_link(ws)


def test_only_the_owner_reaches_a_link(me, made, other_id, guest):
    ws = _link(me)
    other = login(OTHER, OTHER_PASSWORD)
    assert other.get("/api/zotero/links").json() == {"links": []}
    for method, path, body in (
            ("GET", "", None), ("PATCH", "", {"poll_s": 0}), ("POST", "/sync?wait=1", None),
            ("POST", "/detach", None), ("POST", "/relink", {}), ("POST", "/force", {"direction": "pull"}),
            ("GET", "/log", None), ("GET", "/conflicts", None), ("POST", "/conflicts/1", {"choice": "keep"}),
            ("POST", "/fetch", {"page_id": "zsNoPage"}), ("DELETE", "", None)):
        r = other.request(method, f"/api/zotero/links/{ws}{path}", json=body)
        assert r.status_code == 404, (method, path, r.status_code)
    assert guest.get(f"/api/zotero/links/{ws}").status_code == 403
    assert zotero_sync.get_link(ws)["mode"] == "two-way"


def test_a_link_whose_workspace_is_no_longer_its_owners_personal_one_uses_no_key(me, made, fake):
    ws = _link(me)
    _sync(me, ws)
    with connect_users_db() as conn:
        conn.execute("UPDATE zotero_links SET poll_s = 5 WHERE workspace_id = ?", (ws,))
        conn.execute("UPDATE workspaces SET kind = 'shared' WHERE id = ?", (ws,))
        conn.commit()
    try:
        assert ws not in [row[0] for row in zotero_sync._rows()]
        assert ws not in [link.workspace_id for link in zotero_stream.live_links()]
        calls = len(fake.calls)
        status = _sync(me, ws, ok=False)
        assert status["error_kind"] == "not_connected" and fake.calls[calls:] == []
    finally:
        with connect_users_db() as conn:
            conn.execute("UPDATE workspaces SET kind = 'personal' WHERE id = ?", (ws,))
            conn.commit()
    _sync(me, ws)


def test_fetch_puts_a_pages_pdf_first_and_asks_for_a_round(me):
    ws = _link(me)
    _clear_queue(ws)
    page = _page(ws, "zsOpened", "Opened", zotero_pdf={"key": "ATTA2345", "state": "remote", "size": 10})
    _page(ws, "zsOther", "Other")
    with connect_pages_db(ws) as conn:
        zr.enqueue(conn, [zr.page_entry("zsOther"), {"id": "ITEM2345", "kind": "top", "version": 99}])
        conn.commit()
    drop_pending(ws)
    assert me.post(f"/api/zotero/links/{ws}/fetch", json={"page_id": page}).json() == {"queued": True}
    with connect_pages_db(ws) as conn:
        order = [e.id for e in zr.take(conn, now=page_now(), limit=10)]
    assert order[:2] == ["file:ATTA2345", f"page:{page}"]
    assert ws in zotero_sync._clock.pending
    assert me.post(f"/api/zotero/links/{ws}/fetch", json={"page_id": "zsNoSuchPage"}).status_code == 404


def test_the_preview_counts_what_linking_would_move(me, made, owner_id):
    r = me.get(f"/api/zotero/links/preview?library={LIB}")
    assert r.status_code == 200, r.text
    size = (len(pdf_bytes()) + len(pdf_bytes("Supplementary material."))
            + len(pdf_bytes("A standalone handout without metadata.")))
    counts = {"items": 7, "pdfs": 3, "pdf_bytes": size, "linked_files": 1, "notes": 3, "attachments": 7,
              "partial": False}
    assert r.json() == counts
    into = workspaces.create("Preview target", owner_id)["id"]
    doc_id, _ = storage.store_pdf(into, pdf_bytes("A paper of mine"))
    _page(into, "zsPrev1", "Attention",
          meta={"title": "Attention is all you need", "doi": "10.1038/s41586-000-00000-0"})
    _page(into, "zsPrev2", "Mine", meta={"title": "A paper of mine", "year": "2020"}, doc_id=doc_id)
    _page(into, "zsPrev3", "Loose notes")
    from gamma.highlights import position
    ops.commit_ops(into, "zsPrev2", [{"op": "insert", "id": "zsPrevMark", "parent": "zsPrev2", "content": "",
                                      "props": {"color": "#ffd400", "quote": "A paper",
                                                "pdf_position": position(1, 612, 792, [(72, 60, 300, 80)])}}],
                   actor=owner_id, client="t")
    r = me.get(f"/api/zotero/links/preview?library={LIB}&workspace_id={into}")
    # (the new paper's highlight goes once its PDF does: only with uploads on, which the dialog adds)
    assert r.json() == {**counts, "matches": 1, "to_send": 1, "to_send_bytes": len(pdf_bytes("A paper of mine")),
                        "collections_to_add": 0, "tags_to_add": 0, "notes_to_send": 0, "highlights_to_send": 0,
                        "highlights_new": 1}
    assert me.get("/api/zotero/links/preview?library=groups/999").status_code == 400
    assert me.get(f"/api/zotero/links/preview?library={LIB}&workspace_id=zsNotMine").status_code == 400
    zotero_account.disconnect(owner_id, revoke=False)
    assert me.get(f"/api/zotero/links/preview?library={LIB}").status_code == 409


def _many(fake, n: int) -> None:
    lib = fake.library
    for i in range(n):
        key = fake.add_item(lib, {"itemType": "journalArticle", "title": f"Paper {i}", "DOI": f"10.5555/zb.{i}"})
        fake.add_item(lib, {"itemType": "attachment", "parentItem": key, "linkMode": "imported_file",
                            "contentType": "application/pdf", "filename": f"zb-{i}.pdf"})


def test_the_counts_of_a_large_library_read_a_bounded_part_of_it(me, fake, owner_id):
    _many(fake, 2500)
    into = workspaces.create("Counts target", owner_id)["id"]
    calls = len(fake.calls)
    r = me.get(f"/api/zotero/links/preview?library={LIB}&workspace_id={into}")
    assert r.status_code == 200, r.text
    out = r.json()
    gets = [path for method, path, _ in fake.calls[calls:] if method == "GET"]
    assert len(gets) <= 30, len(gets)
    assert out["items"] == 2500 and out["attachments"] == 2500 and out["partial"] is True
    assert out["matches"] is None and out["to_send"] is None


def test_the_same_counts_again_are_answered_from_the_last_while_nothing_changed(me, made, fake):
    assert me.get(f"/api/zotero/links/preview?library={LIB}").status_code == 200
    calls = len(fake.calls)
    again = me.get(f"/api/zotero/links/preview?library={LIB}")
    assert again.status_code == 200
    paths = [path for method, path, _ in fake.calls[calls:] if method == "GET"]
    assert not any("itemType=attachment" in p for p in paths) and len(paths) <= 4, paths
    fake.edit(LIB, made["keys"]["thesis"], title="Changed in Zotero")
    calls = len(fake.calls)
    assert me.get(f"/api/zotero/links/preview?library={LIB}").status_code == 200
    assert any("itemType=attachment" in p for _, p, _ in fake.calls[calls:])  # counted afresh


def test_a_count_runs_once_per_account_and_waits_on_zotero_a_bounded_time(me, made, fake, owner_id, monkeypatch):
    inside, release = threading.Event(), threading.Event()
    fake.on_request("/items/top", lambda: (inside.set(), release.wait(5)))
    first = ThreadPoolExecutor(1).submit(zotero_sync.preview, owner_id, LIB)
    assert inside.wait(5)
    try:
        r = me.get(f"/api/zotero/links/preview?library={LIB}")
        assert r.status_code == 429
    finally:
        release.set()
    assert first.result(5)["items"] == 7
    zotero_sync._previews.clear()
    monkeypatch.setattr(zotero_sync, "PREVIEW_DEADLINE_S", 0)
    fake.fail_next(503, "/items/top", headers={"Retry-After": "30"})
    r = me.get(f"/api/zotero/links/preview?library={LIB}")
    assert r.status_code == 503 and r.json()["detail"] == zotero_sync.PREVIEW_BUSY


def test_the_preview_pairs_as_the_first_link_does_by_md5_and_standalone_key(me, made, fake, owner_id):
    from gamma.zotero_web import item_uri
    keys = made["keys"]
    ws = workspaces.create("Preview by file", owner_id)["id"]
    zotero_local.create_page(ws, page_id="zfxByFile", title="attention copy.pdf", actor="zi", client="t",
                             props={"doc_id": _stored_pdf(ws, pdf_bytes())})  # Zotero's very file, no record
    zotero_local.create_page(ws, page_id="zfxByKey", title="handout", actor="zi", client="t",
                             props={"zotero_key": item_uri(LIB, keys["standalone_pdf"])})
    preview = zotero_sync.preview(owner_id, LIB, ws)
    assert (preview["matches"], preview["to_send"]) == (2, 0)
    _link(me, workspace_id=ws)
    _sync(me, ws)
    assert _rows(ws)[keys["attention"]]["page_id"] == "zfxByFile"
    assert _rows(ws)[keys["standalone_pdf"]]["page_id"] == "zfxByKey"


def test_the_preview_counts_the_folders_labels_notes_and_highlights_a_first_link_sends(me, made, fake, owner_id):
    from gamma import ops
    from gamma.blocks_store import FOLDERS, LABELS
    from gamma.highlights import position
    from gamma.zotero_web import item_uri
    keys = made["keys"]
    ws = workspaces.create("Preview of the writes", owner_id)["id"]
    ops.commit_ops(ws, FOLDERS, [{"op": "insert", "id": "zfxFolderML", "parent": FOLDERS, "content": "ML"},
                                 {"op": "insert", "id": "zfxFolderNew", "parent": FOLDERS, "content": "Brand new"}],
                   actor=owner_id, client="t")
    ops.commit_ops(ws, LABELS, [{"op": "insert", "id": "zfxLabelA", "parent": LABELS, "content": "Transformers"},
                                {"op": "insert", "id": "zfxLabelB", "parent": LABELS, "content": "to read"}],
                   actor=owner_id, client="t")
    _paper(ws, "zfxPrevA", "Attention", doc_id=_stored_pdf(ws, pdf_bytes()),
           zotero_key=item_uri(LIB, keys["attention"]), labels=["zfxLabelA", "zfxLabelB"],
           meta={"title": "Attention is all you need", "year": "2017"})
    _edit(ws, owner_id, "zfxPrevA", [
        {"op": "insert", "id": "zfxPrevNote", "parent": "zfxPrevA", "content": "My own reading notes"},
        {"op": "insert", "id": "zfxPrevMark", "parent": "zfxPrevA", "content": "",
         "props": {"color": "#ffd400", "quote": "Attention",
                   "pdf_position": position(1, 612, 792, [(72, 60, 300, 80)])}}])
    # a new paper with a PDF, and a paper whose item has none in Zotero: their highlights go once their PDF does
    _paper(ws, "zfxPrevNew", "A paper of mine", doc_id=_stored_pdf(ws, pdf_bytes("A paper of mine")),
           meta={"title": "A paper of mine", "year": "2020"})
    _paper(ws, "zfxPrevBook", "Deep Learning", doc_id=_stored_pdf(ws, pdf_bytes("Deep Learning")),
           zotero_key=item_uri(LIB, keys["deep_learning"]), meta={"title": "Deep Learning", "year": "2016"})
    for page, n in (("zfxPrevNew", 2), ("zfxPrevBook", 1)):
        _edit(ws, owner_id, page, [{"op": "insert", "id": f"{page}Mark{i}", "parent": page, "content": "",
                                    "props": {"color": "#ffd400", "quote": "Deep",
                                              "pdf_position": position(1, 612, 792, [(72, 60 + 30 * i, 300, 80)])}}
                                   for i in range(n)])
    preview = zotero_sync.preview(owner_id, LIB, ws)
    assert (preview["matches"], preview["to_send"]) == (2, 1)
    assert preview["collections_to_add"] == 1  # "ML" pairs with Zotero's ML; "Brand new" is made there
    assert preview["tags_to_add"] == 1  # "Transformers" is the item's tag already; "to read" is not
    # on the paper whose PDF Zotero has, whatever the uploads; on those whose PDF the link sends, once it has
    assert preview["notes_to_send"] == 1 and (preview["highlights_to_send"], preview["highlights_new"]) == (1, 3)


def test_conflicts_are_listed_kept_once_per_block_and_resolved_by_kind(me, made, monkeypatch):
    ws = _link(me)
    _sync(me, ws)
    zotero_sync_rows.record_conflict(ws, "zsPg", "zsBlk", "zotero_lossy", mine="first edit")
    zotero_sync_rows.record_conflict(ws, "zsPg", "zsBlk", "zotero_lossy", mine="second edit")  # refreshed in place
    zotero_sync_rows.record_conflict(ws, "zsPg", "zsPg", "zotero_field", mine="Year: 2017", theirs="Year: 2018")
    zotero_sync_rows.record_conflict(ws, "zsPg", "zsPg", "zotero_field", mine="Year: 2016", theirs="Year: 2015",
                                     resolved=True)
    with pytest.raises(ValueError):
        zotero_sync_rows.record_conflict(ws, "zsPg", "zsPg", "diverged")
    rows = me.get(f"/api/zotero/links/{ws}/conflicts").json()["conflicts"]
    assert [(c["kind"], c["mine"]) for c in rows] == [("zotero_field", "Year: 2017"), ("zotero_lossy", "second edit")]
    assert [c["mine"] for c in me.get(f"/api/zotero/links/{ws}/conflicts?resolved=1").json()["conflicts"]] == [
        "Year: 2016"]
    info = me.get(f"/api/zotero/links/{ws}").json()
    assert info["conflicts_open"] == 2 and info["conflicts_newest"] == rows[0]["id"]
    field_id, lossy_id = rows[0]["id"], rows[1]["id"]
    r = me.post(f"/api/zotero/links/{ws}/conflicts/{lossy_id}", json={"choice": "theirs"})
    assert r.status_code == 409  # no way to take that side yet: the row stays open
    taken = []
    monkeypatch.setitem(zotero_sync.RESOLVERS, "zotero_field", lambda ws_, row, choice: taken.append((row, choice)))
    assert me.post(f"/api/zotero/links/{ws}/conflicts/{field_id}", json={"choice": "theirs"}).json() == {
        "id": field_id, "resolved": True}
    assert taken[0][1] == "theirs" and taken[0][0]["theirs"] == "Year: 2018"
    assert me.post(f"/api/zotero/links/{ws}/conflicts/{lossy_id}", json={"choice": "keep"}).json()["resolved"]
    assert me.post(f"/api/zotero/links/{ws}/conflicts/{lossy_id + 99}", json={"choice": "keep"}).status_code == 404
    assert me.post(f"/api/zotero/links/{ws}/conflicts/{lossy_id}", json={"choice": "both"}).status_code == 422
    assert me.get(f"/api/zotero/links/{ws}").json()["conflicts_open"] == 0
    log = me.get(f"/api/zotero/links/{ws}/log?limit=50").json()["changes"]
    assert [(e["action"], e["stats"]["add"]) for e in log if e["page_id"] == FOLDERS] == [("pulled", 4)]


def test_a_resolved_conflict_takes_no_side_again(me, made, monkeypatch):
    ws = _link(me)
    _sync(me, ws)
    zotero_sync_rows.record_conflict(ws, "zbPg", "zbPg", "zotero_field", mine="Year: 2017", theirs="Year: 2018",
                                     resolved=True)
    (row,) = me.get(f"/api/zotero/links/{ws}/conflicts?resolved=1").json()["conflicts"]
    taken = []
    monkeypatch.setitem(zotero_sync.RESOLVERS, "zotero_field", lambda ws_, row_, choice: taken.append(choice))
    r = me.post(f"/api/zotero/links/{ws}/conflicts/{row['id']}", json={"choice": "theirs"})
    assert r.status_code == 409 and taken == []
    assert me.post(f"/api/zotero/links/{ws}/conflicts/{row['id']}", json={"choice": "keep"}).status_code == 200


def test_a_conflict_written_resolved_marks_its_own_row_whatever_was_written_meanwhile(me, monkeypatch):
    ws = _link(me)
    _sync(me, ws)
    real = sync_engine._conflict

    def other_writer():  # another thread: another connection
        with connect_pages_db(ws) as other:
            other.execute("INSERT INTO sync_conflicts (page_id, block_id, kind, mine, theirs, result, base, at) "
                          "VALUES ('zbOther', 'zbOther', 'zotero_lossy', 'theirs', '', '', '', ?)", (page_now(),))
            other.commit()

    def and_another(conn, *args, **kw):
        real(conn, *args, **kw)
        writer = threading.Thread(target=other_writer)  # its row lands between the insert and the resolve
        writer.start()
        writer.join(10)

    monkeypatch.setattr(sync_engine, "_conflict", and_another)
    zotero_sync_rows.record_conflict(ws, "zbPg", "zbPg", "zotero_field", mine="ours", theirs="x", resolved=True)
    with connect_pages_db(ws) as conn:
        rows = dict(conn.execute("SELECT mine, resolved FROM sync_conflicts WHERE page_id IN ('zbPg', 'zbOther')"))
    assert rows == {"ours": 1, "theirs": 0}


def test_the_rows_layer_checks_a_conflicts_kind_without_loading_the_engine(tmp_path):
    code = ("import sys\nfrom gamma import zotero_sync_rows as r\n"
            "try:\n    r.record_conflict('w', 'p', 'b', 'nope')\nexcept ValueError:\n    pass\n"
            "print('gamma.zotero_sync' in sys.modules)")
    env = {**__import__("os").environ, "GAMMA_DATA_DIR": str(tmp_path)}
    out = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split()[-1] == "False"


# --- the clock and the status ----------------------------------------------------------

def test_the_link_registers_its_own_clock(me, owner_id):
    engine = sync_clock.get("zotero")
    assert engine is zotero_sync._clock and engine.client == zotero_sync_rows.CLIENT == "zotero"
    assert engine.debounce == zotero_sync_rows.DEBOUNCE_S and engine.lock("zsAny") is engine.lock("zsAny")
    assert zotero_local.CLIENT == zotero_sync_rows.CLIENT and zotero_local.ACTOR == auth.ZOTERO_ACTOR == "zotero"
    assert auth.actor_names(["zotero"]) == {"zotero": "Zotero"}
    ws = _link(me)
    assert (ws, "two-way", 5, 1) in [tuple(row) for row in engine.refresh()] and engine.wants_change[ws]
    now = time.monotonic()
    engine.pending.pop(ws, None)
    engine.last_run[ws] = now - 29
    assert not engine.due_now(ws, "two-way", 5, now)  # Live: the stream's, and a safety round every 30 s
    engine.last_run[ws] = now - 31
    assert engine.due_now(ws, "two-way", 5, now) and not engine.due_now(ws, "two-way", 300, now)
    assert not engine.due_now(ws, "off", 5, now)
    # the link's own writes do not count as local edits; anyone else's do (a clone's pushes too)
    engine.dirty.pop(ws, None)
    ops.notify_commit(ws, "zotero", "zsPage")
    assert not engine.has_local_changes(ws)
    ops.notify_commit(ws, "sync", "zsPage")
    assert engine.has_local_changes(ws) and engine.pending[ws] > time.monotonic() + 2
    assert me.get(f"/api/zotero/links/{ws}").json()["pending_local"] is True
    me.patch(f"/api/zotero/links/{ws}", json={"mode": "pull"})
    assert me.get(f"/api/zotero/links/{ws}").json()["pending_local"] is False
    me.patch(f"/api/zotero/links/{ws}", json={"mode": "two-way"})
    _sync(me, ws)
    assert not engine.has_local_changes(ws)  # a clean round settles it
    # a round already running is never waited on: the loop looks again shortly
    engine.pending[ws] = time.monotonic() - 1
    with engine.lock(ws):
        engine.tick()
    assert engine.pending[ws] > time.monotonic() + 3
    # a link whose workspace is gone gets no rounds; one left running is marked interrupted at startup
    with connect_users_db() as conn:
        conn.execute("INSERT INTO zotero_links (workspace_id, owner, library, status, created_at) "
                     "VALUES ('zsGoneWs', ?, 'groups/77', ?, ?)", (owner_id, json.dumps({"running": True}),
                                                                  page_now()))
        conn.commit()
    try:
        assert "zsGoneWs" not in [row[0] for row in engine.refresh()]
        zotero_sync_rows.patch_status(ws, {"running": True, "progress": {"phase": "sync", "done": 1, "total": 2}})
        zotero_sync.reset_interrupted()
        status = zotero_sync.get_link(ws)["status"]
        assert status["running"] is False and status["interrupted"] is True and "progress" not in status
        _sync(me, ws)
        assert "interrupted" not in zotero_sync.get_link(ws)["status"]
    finally:
        with connect_users_db() as conn:
            conn.execute("DELETE FROM zotero_links WHERE workspace_id = 'zsGoneWs'")
            conn.commit()


def test_the_link_hands_its_rounds_to_a_pool_of_its_own():
    assert zotero_sync._clock._workers == zotero_sync_rows.WORKERS > 1
    assert sync_engine._clock._workers == 1  # the mirror runs its rounds on its loop, as before


def test_the_links_debounce_is_its_named_quiet_time():
    assert zotero_sync._clock.debounce == zotero_sync_rows.DEBOUNCE_S == 3.0


def test_many_background_rounds_asked_while_one_waits_start_one_thread(me, made, monkeypatch):
    ws = _link(me)
    ran = []
    monkeypatch.setattr(zotero_sync, "_held_round", lambda ws_: ran.append(ws_))
    with zotero_sync._clock.lock(ws):
        for _ in range(50):
            REAL_BACKGROUND(ws)
        waiting = [t for t in threading.enumerate() if t.name == f"zotero-{ws}" and t.is_alive()]
        assert len(waiting) == 1
    waiting[0].join(5)
    assert ran == [ws]
    monkeypatch.setattr(zotero_sync._clock, "looping", lambda: True)  # the clock runs: its own request
    drop_pending(ws)
    REAL_BACKGROUND(ws)
    assert ws in zotero_sync._clock.pending
    assert not [t for t in threading.enumerate() if t.name == f"zotero-{ws}" and t.is_alive()]
    drop_pending(ws)


def test_a_rounds_error_is_worded_by_its_kind_and_a_clean_round_clears_it(me, made, fake, owner_id):
    ws = _link(me)
    status = _sync(me, ws)
    assert status["last_error"] == "" and status["error_kind"] == ""
    calls = len(fake.calls)
    _sync(me, ws)
    assert not any(path == "/keys/current" for _, path, _ in fake.calls[calls:])  # what the key may do: cached
    key = fake.keys.pop(fake.api_key)  # revoked on zotero.org
    status = _sync(me, ws, ok=False)
    assert status["error_kind"] == "auth" and status["last_error"] and status["last_attempt"]
    assert status["running"] is False
    fake.keys[fake.api_key] = key
    assert _sync(me, ws)["error_kind"] == ""
    fake.fail_next(503, f"/{LIB}/items", times=MAX_RETRIES + 1)
    assert _sync(me, ws, ok=False)["error_kind"] == "busy"
    fake.fail_next(502, f"/{LIB}/items", times=MAX_RETRIES + 1)
    assert _sync(me, ws, ok=False)["error_kind"] == "offline"
    zotero_account.disconnect(owner_id, revoke=False)
    status = _sync(me, ws, ok=False)
    assert status["error_kind"] == "not_connected" and status["last_error"] == zotero_account.NOT_CONNECTED
    zotero_account.connect(owner_id, fake.api_key)
    assert _sync(me, ws)["last_error"] == ""
    # an error found outside a round (the stream: Zotero refuses this client), cleared by the next clean round
    status = zotero_sync.record_error(ws, "Zotero refused this client (4403)", kind="auth")
    assert status["error_kind"] == "auth" and status["last_error"] == "Zotero refused this client (4403)"
    assert ws not in zotero_sync._access_seen and zotero_sync.record_error(ws, "x", kind="odd")["error_kind"] == "other"
    assert _sync(me, ws)["error_kind"] == ""
    # a group the key no longer reaches (the stream says so: the cache is forgotten)
    group = me.post("/api/zotero/links", json={"library": GROUP}).json()["workspace_id"]
    _sync(me, group)
    fake.keys[fake.api_key] = {**FULL_ACCESS, "groups": {}}
    zotero_sync.forget_access(group)
    status = _sync(me, group, ok=False)
    assert status["error_kind"] == "library_gone" and "Lab" in status["last_error"]


def test_a_page_held_back_for_its_notes_is_no_unsent_edit(me, made):
    from datetime import datetime, timedelta, timezone
    ws = _link(me)
    _sync(me, ws)
    zotero_sync._clock.dirty.pop(ws, None)
    with connect_pages_db(ws) as conn:
        zr.enqueue(conn, [zr.page_entry("zbQuiet", next_at=datetime.now(timezone.utc) + timedelta(seconds=60),
                                        reason=zotero_sync_blocks.NOTES_WAIT)])
        conn.commit()
    assert me.get(f"/api/zotero/links/{ws}").json()["pending_local"] is False
    with connect_pages_db(ws) as conn:
        zr.enqueue(conn, [zr.page_entry("zbDue", next_at="")])
        conn.commit()
    assert me.get(f"/api/zotero/links/{ws}").json()["pending_local"] is True
    with connect_pages_db(ws) as conn:
        zr.drop(conn, ["page:zbQuiet", "page:zbDue"])
        conn.commit()


def test_a_fill_shows_only_while_a_round_of_it_follows(me, made, monkeypatch):
    ws = _link(me)
    _sync(me, ws)
    zotero_sync_rows.patch_status(ws, {"fill": {"units": 3, "bytes": 10}})
    assert "fill" not in me.get(f"/api/zotero/links/{ws}").json()["status"]  # nothing runs it: no spinner
    monkeypatch.setattr(config, "sync_interval_s", lambda: 30)
    zotero_sync._clock.request(ws, 0)
    try:
        assert me.get(f"/api/zotero/links/{ws}").json()["status"]["fill"] == {"units": 3, "bytes": 10}
    finally:
        drop_pending(ws)
    zotero_sync.reset_interrupted()  # the loops run: the fill's next round at the clock's first look
    assert ws in zotero_sync._clock.pending and zotero_sync.get_link(ws)["status"]["fill"]
    drop_pending(ws)
    monkeypatch.setattr(config, "sync_interval_s", lambda: 0)
    zotero_sync.reset_interrupted()  # no loop: nothing follows, and the count goes
    assert "fill" not in zotero_sync.get_link(ws)["status"] and ws not in zotero_sync._clock.pending


# --- the listing and the local feed ---------------------------------------------------

def test_zotero_changes_go_into_the_queue_and_the_listing_moves_the_version(me, made, fake, held):
    keys = made["keys"]
    ws = _link(me)
    status = _sync(me, ws)
    state, queue = _state(ws), _queue_entries(ws)
    assert state["version"] == fake.version(LIB)
    tops = {key for key, entry in queue.items() if entry["kind"] == "top"}
    assert tops == {keys[n] for n in ("attention", "ppo", "deep_learning", "thesis", "conference", "standalone_pdf",
                                      "standalone_note", "trashed")}
    children = {key for key, entry in queue.items() if entry["kind"] == "child"}
    assert {keys[n] for n in ("pdf", "highlight", "ink_annotation", "note", "image", "linked_pdf")} <= children
    assert not any(entry["kind"] == "collection" for entry in queue.values())  # the collections pass took them
    assert status["backlog"] == len(queue) and "progress" not in status
    # nothing changed in Zotero: one small call answers 304 and nothing is listed
    calls = len(fake.calls)
    _sync(me, ws)
    listed = [path for m, path, _ in fake.calls[calls:] if m == "GET"]
    assert listed == [f"/{LIB}/items?limit=1&format=versions"]
    # an edit is queued at its new version; a removal for good leaves the queue
    fake.edit(LIB, keys["ppo"], title="PPO")
    fake.delete(LIB, keys["thesis"])
    _sync(me, ws)
    queue = _queue_entries(ws)
    assert queue[keys["ppo"]]["version"] == fake.data(LIB, keys["ppo"])["version"]
    assert keys["thesis"] not in queue and _state(ws)["version"] == fake.version(LIB)


def test_gamma_changes_go_into_the_queue_in_two_way_rounds_only(me, made, owner_id, held):
    ws = _link(me)
    _sync(me, ws)
    _page(ws, "zsFeed1", "Edited here")
    _sync(me, ws)
    # the tree went with the pass
    assert "page:zsFeed1" in _queue_entries(ws) and "page:folders" not in _queue_entries(ws)
    me.patch(f"/api/zotero/links/{ws}", json={"mode": "pull"})
    cursor = _state(ws)["local_cursor"]
    _page(ws, "zsFeed2", "Edited while receiving only")
    _sync(me, ws)
    assert "page:zsFeed2" not in _queue_entries(ws) and _state(ws)["local_cursor"] == cursor
    me.patch(f"/api/zotero/links/{ws}", json={"mode": "two-way"})
    _sync(me, ws)
    assert "page:zsFeed2" in _queue_entries(ws)
    # a label renamed here (one Zotero brought too) has every page carrying it looked at again
    ops.commit_ops(ws, LABELS, [{"op": "insert", "id": "zsLabel1", "parent": LABELS, "content": "qc"}],
                   actor=owner_id, client="zotero")
    _page(ws, "zsTagged", "Tagged", labels=["zsLabel1"])
    _sync(me, ws)
    assert _state(ws)["labels"] == {"zsLabel1": "qc"}
    _clear_queue(ws, ("page",))
    ops.commit_ops(ws, LABELS, [{"op": "set", "id": "zsLabel1", "content": "quantum"}], actor=owner_id, client="t")
    _sync(me, ws)
    queue = _queue_entries(ws)
    assert "page:zsTagged" in queue and "page:labels" not in queue and _state(ws)["labels"] == {"zsLabel1": "quantum"}


# --- the bounded round ---------------------------------------------------------------

def _consume(r, entry):
    with connect_pages_db(r.ws) as conn:
        zr.done(conn, [entry.id])
        conn.commit()


def test_a_round_is_bounded_asks_for_a_follow_up_and_never_for_held_work(me, made, monkeypatch, held):
    ws = _link(me)
    monkeypatch.setattr(zotero_sync_items, "unit_item", _consume)
    monkeypatch.setattr(zotero_sync, "ROUND_UNITS", 3)
    engine = zotero_sync._clock
    engine.pending.pop(ws, None)
    _sync(me, ws)  # the collections pass and two items
    tops = [k for k, e in _queue_entries(ws).items() if e["kind"] == "top"]
    assert len(tops) == 6 and ws in engine.pending  # the rest next round, at once
    engine.pending.pop(ws, None)
    while [k for k, e in _queue_entries(ws).items() if e["kind"] == "top"]:
        _sync(me, ws)
    engine.pending.pop(ws, None)
    status = _sync(me, ws)  # only the children are left, and they are not taken yet
    assert ws not in engine.pending and status["backlog"] == len(_queue_entries(ws)) > 0
    monkeypatch.setattr(zotero_sync, "ROUND_BUDGET_S", 0)
    with connect_pages_db(ws) as conn:
        zr.enqueue(conn, [{"id": "ITEM2345", "kind": "top", "version": 1}])
        conn.commit()
    _sync(me, ws)
    # past the budget: nothing taken, a follow-up asked
    assert "ITEM2345" in _queue_entries(ws) and ws in engine.pending


def test_a_unit_that_fails_backs_off_and_is_given_up_after_its_attempts(me, made, fake, monkeypatch, held):
    keys = made["keys"]
    ws = _link(me)

    def broken(r, entry):
        if entry.id == keys["attention"]:
            raise RuntimeError("this item is broken")
        return zotero_sync_rows.HELD

    monkeypatch.setattr(zotero_sync_items, "unit_item", broken)
    status = _sync(me, ws, ok=False)
    assert status["last_error"] == "this item is broken" and status["error_kind"] == "other"
    entry = _queue_entries(ws)[keys["attention"]]
    assert entry["attempts"] == 1 and entry["next_at"] > page_now() and entry["reason"] == "this item is broken"
    with connect_pages_db(ws) as conn:  # seven tries done, the wait over
        conn.execute("UPDATE zotero_queue SET attempts = 7, next_at = '' WHERE id = ?", (keys["attention"],))
        conn.commit()
    _sync(me, ws, ok=False)
    assert keys["attention"] not in _queue_entries(ws)
    row = _rows(ws, "item")[keys["attention"]]
    assert row["state"] == "rejected" and row["version"] == fake.data(LIB, keys["attention"])["version"]
    [conflict] = me.get(f"/api/zotero/links/{ws}/conflicts").json()["conflicts"]
    assert conflict["kind"] == "zotero_rejected" and conflict["block_id"] == keys["attention"]
    assert "after 8 tries" in conflict["result"]
    # listed again at the version it failed at, it stays out; a change in Zotero brings it back
    with connect_pages_db(ws) as conn:
        zr.set_state(conn, version=0)
        conn.commit()
    _sync(me, ws)
    assert keys["attention"] not in _queue_entries(ws)
    fake.edit(LIB, keys["attention"], title="Attention, again")
    monkeypatch.setattr(zotero_sync_items, "unit_item", lambda r, entry: zotero_sync_rows.HELD)
    _sync(me, ws)
    assert keys["attention"] in _queue_entries(ws)
    # Zotero being busy is no unit's fault: the round stops, the unit is not counted
    monkeypatch.setattr(zotero_sync_items, "unit_item", lambda r, entry: (_ for _ in ()).throw(
        ZoteroError("Zotero is busy", 503)))
    status = _sync(me, ws, ok=False)
    assert status["error_kind"] == "busy" and _queue_entries(ws)[keys["attention"]]["attempts"] == 0


def test_a_deleted_workspace_or_account_takes_its_links(me, fake, owner_id):
    ws = _link(me)
    workspaces.delete(ws)
    assert zotero_sync.get_link(ws) is None and ws not in [row[0] for row in zotero_sync._rows()]
    make_user("zs_gone", "pw-zs_gone-123")
    gone = account_of("zs_gone")
    zotero_account.connect(gone, fake.api_key)
    linked = zotero_sync.create_link(gone, LIB)["workspace_id"]
    assert zotero_sync.list_links(gone)
    workspaces.delete_account(gone, release_now=True)
    assert zotero_sync.list_links(gone) == [] and zotero_sync.get_link(linked) is None
