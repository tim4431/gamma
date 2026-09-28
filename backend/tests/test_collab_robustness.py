"""The collaboration write path under trouble: concurrency conflicts that
name the op they refuse, a retried batch applied once, a re-sent insert
that never undoes someone's newer edit, text a browser can send but SQLite
can't store, and the page socket's room bookkeeping — a tab reconnecting,
the log position in the hello, access revoked while the socket is open."""

import json
import time

import pytest
from fastapi.testclient import TestClient
from fractional_indexing import generate_key_between
from starlette.websockets import WebSocketDisconnect

from conftest import guest_name, login, make_page, make_user, recv, workspace_of
from gamma import collab, ops
from gamma.app import app


def _ops(client, page_id, ops, client_id="t", batch="", **kw):
    body = {"client": client_id, "ops": ops, **({"batch": batch} if batch else {})}
    return client.post(f"/api/pages/{page_id}/ops", json=body, **kw)


def _keys(n):
    keys, prev = [], None
    for _ in range(n):
        prev = generate_key_between(prev, None)
        keys.append(prev)
    return keys


def _log(client, page_id):
    return client.get(f"/api/pages/{page_id}/ops", params={"since": 0}).json()["batches"]


# --- conflicts ------------------------------------------------------------------

def test_a_conflict_names_its_kind_and_op_and_writes_nothing(guest):
    page = make_page(guest, "Conflict codes")
    other = make_page(guest, "Conflict elsewhere")
    k = _keys(5)
    assert _ops(guest, page["id"], [
        {"op": "insert", "id": "cfP", "parent": page["id"], "position": k[0], "content": "P"},
        {"op": "insert", "id": "cfQ", "parent": page["id"], "position": k[1], "content": "Q"},
        {"op": "insert", "id": "cfC", "parent": page["id"], "position": k[2], "content": "C"},
        {"op": "insert", "id": "cfD", "parent": page["id"], "position": k[3], "content": "D"},
        {"op": "insert", "id": "cfX", "parent": page["id"], "position": k[4], "content": "X"},
    ]).status_code == 200
    unrelated = [{"op": "set", "id": "cfX", "base": "X", "content": "X + what A typed"}]

    # someone put Q under P; our batch moves P under Q
    assert _ops(guest, page["id"], [{"op": "move", "id": "cfQ", "parent": "cfP"}], client_id="B").status_code == 200
    r = _ops(guest, page["id"], unrelated + [{"op": "move", "id": "cfP", "parent": "cfQ"}], client_id="A")
    assert r.status_code == 400 and r.json()["conflict"] == "cycle" and r.json()["index"] == 1
    # someone moved C to another page; our batch edits it
    assert guest.post("/api/blocks/cfC/reorder", json={"parent_id": other["id"]}).status_code == 200
    r = _ops(guest, page["id"], unrelated + [{"op": "set", "id": "cfC", "base": "C", "content": "C!"}], client_id="A")
    assert r.status_code == 403 and r.json()["conflict"] == "moved" and r.json()["index"] == 1
    assert "outside this page" in r.json()["detail"]  # what an older mirror engine matches
    # someone deleted D; our batch edits it
    assert _ops(guest, page["id"], [{"op": "delete", "id": "cfD"}], client_id="B").status_code == 200
    r = _ops(guest, page["id"], unrelated + [{"op": "set", "id": "cfD", "base": "D", "content": "D!"}], client_id="A")
    assert r.status_code == 404 and r.json() == {
        "detail": "no such block: cfD", "missing": "cfD", "conflict": "missing", "index": 1}
    # still one batch, one transaction: none of it was written
    assert guest.get("/api/blocks/cfX").json()["content"] == "X"
    # the client drops the refused op and sends the rest: it lands
    assert _ops(guest, page["id"], unrelated, client_id="A").status_code == 200
    assert guest.get("/api/blocks/cfX").json()["content"] == "X + what A typed"

    # a malformed batch is no conflict
    r = _ops(guest, page["id"], [{"op": "insert", "id": "bad id!", "parent": page["id"], "content": "x"}])
    assert r.status_code == 400 and "conflict" not in r.json()
    r = _ops(guest, page["id"], [{"op": "insert", "id": "cfR", "parent": "root", "content": "x"}])
    assert r.status_code == 403 and "conflict" not in r.json()


# --- a batch applied once -------------------------------------------------------

def test_a_retried_batch_is_answered_again_not_applied_twice(guest):
    page = make_page(guest, "Retry once")
    assert _ops(guest, page["id"], [{"op": "insert", "id": "rtA", "parent": page["id"],
                                     "content": "The results are clear."}]).status_code == 200
    batch = [{"op": "set", "id": "rtA", "base": "The results are clear.", "content": "The results are very clear."}]
    first = _ops(guest, page["id"], batch, client_id="tabA", batch="b-1")
    assert first.status_code == 200
    # the answer was lost; someone else edits the block meanwhile
    assert _ops(guest, page["id"], [{"op": "set", "id": "rtA", "base": "The results are very clear.",
                                     "content": "The results are very clear. See Fig. 2."}],
                client_id="tabB").status_code == 200
    seqs = [b["seq"] for b in _log(guest, page["id"])]
    again = _ops(guest, page["id"], batch, client_id="tabA", batch="b-1")
    assert again.status_code == 200
    assert again.json()["seq"] == first.json()["seq"] and again.json()["ops"] == first.json()["ops"]
    # nothing re-applied: no "very very", no new log row
    assert guest.get("/api/blocks/rtA").json()["content"] == "The results are very clear. See Fig. 2."
    assert [b["seq"] for b in _log(guest, page["id"])] == seqs
    # the id is the client's own: another tab using it is another batch
    other = _ops(guest, page["id"], [{"op": "set", "id": "rtA", "content": "tab C"}], client_id="tabC", batch="b-1")
    assert other.json()["seq"] == seqs[-1] + 1
    # a refused batch is not remembered: fixed and sent again, it applies
    r = _ops(guest, page["id"], [{"op": "set", "id": "nope", "content": "x"}], client_id="tabA", batch="b-2")
    assert r.status_code == 404
    r = _ops(guest, page["id"], [{"op": "set", "id": "rtA", "content": "second"}], client_id="tabA", batch="b-2")
    assert r.status_code == 200 and guest.get("/api/blocks/rtA").json()["content"] == "second"


def test_a_resent_insert_never_undoes_a_newer_edit(guest):
    page = make_page(guest, "Create if absent")
    k = _keys(2)
    ins = [{"op": "insert", "id": "caA", "parent": page["id"], "position": k[0], "content": "draft", "props": {}}]
    assert _ops(guest, page["id"], ins, client_id="tabA").status_code == 200
    assert _ops(guest, page["id"], [{"op": "insert", "id": "caH", "parent": page["id"], "position": k[1],
                                     "content": "holder"}], client_id="tabB").status_code == 200
    assert _ops(guest, page["id"], [
        {"op": "set", "id": "caA", "base": "draft", "content": "draft, expanded by B", "props": {"color": "yellow"}},
        {"op": "move", "id": "caA", "parent": "caH"}], client_id="tabB").status_code == 200
    # tab A's insert arrives again (a retry without a batch id, a rescue)
    r = _ops(guest, page["id"], ins, client_id="tabA")
    assert r.status_code == 200
    blk = guest.get("/api/blocks/caA").json()
    assert (blk["content"], blk["properties"], blk["parent_id"]) == ("draft, expanded by B", {"color": "yellow"}, "caH")
    # the echo carries the block as it is, so the sender converges on it
    echo = r.json()["ops"][0]
    assert (echo["op"], echo["parent"], echo["content"], echo["props"]) == (
        "insert", "caH", "draft, expanded by B", {"color": "yellow"})


# --- text SQLite can't store -----------------------------------------------------

def test_a_lone_surrogate_is_stored_as_a_replacement_character(guest):
    page = make_page(guest, "Half an emoji")
    for bid in ("suA", "suB"):
        assert _ops(guest, page["id"], [{"op": "insert", "id": bid, "parent": page["id"], "content": bid}]).status_code == 200

    def raw(path, body, method="POST"):  # json.dumps escapes a lone surrogate as the browser does
        return guest.request(method, path, content=json.dumps(body), headers={"Content-Type": "application/json"})

    r = raw(f"/api/pages/{page['id']}/ops", {"client": "t", "ops": [
        {"op": "set", "id": "suB", "content": "good edit in another block"},
        {"op": "set", "id": "suA", "content": "cut in half: \ud83d", "props": {"note \udc00": ["x\ud83d"]}},
    ]})
    assert r.status_code == 200, r.text
    assert guest.get("/api/blocks/suB").json()["content"] == "good edit in another block"
    a = guest.get("/api/blocks/suA").json()
    assert a["content"] == "cut in half: �" and a["properties"] == {"note �": ["x�"]}
    # both halves sent as escapes make the one character again
    assert raw(f"/api/pages/{page['id']}/ops", {"client": "t", "ops": [
        {"op": "set", "id": "suA", "content": "whole 😀"}]}).status_code == 200
    assert guest.get("/api/blocks/suA").json()["content"] == "whole \U0001f600"
    # the single-block writers too
    assert raw("/api/blocks/suA", {"content": "put \ud83d"}, method="PUT").status_code == 200
    assert guest.get("/api/blocks/suA").json()["content"] == "put �"
    r = raw("/api/blocks", {"parent_id": page["id"], "content": "new \udc00"})
    assert r.status_code == 200 and guest.get(f"/api/blocks/{r.json()['id']}").json()["content"] == "new �"


# --- the socket ------------------------------------------------------------------

def _hello(ws):
    msg = ws.receive_json()
    assert msg["t"] == "hello"
    return msg


PRESENCE = ("join", "leave", "cursor")  # all that may come before the message a test waits for


def test_a_tab_reconnecting_keeps_its_place_and_the_others_room(client, guest):
    ws_id = workspace_of(guest_name())
    page = make_page(guest, "Reconnect page")
    old = client.websocket_connect(f"/api/ws/page/{page['id']}?client=TAB1")
    old.__enter__()
    color = _hello(old)["color"]
    with client.websocket_connect(f"/api/ws/page/{page['id']}?client=OTHER") as watcher:
        _hello(watcher)
        # the tab reconnects (same client id) before the server noticed its old socket drop
        with client.websocket_connect(f"/api/ws/page/{page['id']}?client=TAB1") as new:
            hello = _hello(new)
            assert hello["color"] == color and sorted(p["client"] for p in hello["peers"]) == ["OTHER", "TAB1"]
            # the server closes the replaced socket; its teardown removes nothing
            try:
                with pytest.raises(WebSocketDisconnect) as closed:
                    while True:
                        assert old.receive_json()["t"] in ("join", "leave", "cursor")
                assert closed.value.code == collab.CLOSE_REPLACED
            finally:
                old.__exit__(None, None, None)
            time.sleep(0.2)
            assert sorted(collab.room_for(ws_id, page["id"]).peers) == ["OTHER", "TAB1"]
            assert recv(watcher, "join", PRESENCE)["peer"]["client"] == "TAB1"  # the reconnect, not a leave
            assert _ops(guest, page["id"], [{"op": "insert", "id": "rcA", "parent": page["id"], "content": "x"}],
                        client_id="W").status_code == 200
            assert recv(new, "ops", PRESENCE)["ops"][0]["id"] == "rcA"
            assert recv(watcher, "ops", PRESENCE)["ops"][0]["id"] == "rcA"
        # a plain leave still announces
        assert recv(watcher, "leave", PRESENCE)["client"] == "TAB1"


def test_a_stale_room_never_takes_the_current_one_down(client, guest):
    ws_id = workspace_of(guest_name())
    page = make_page(guest, "Cascade page")
    a = client.websocket_connect(f"/api/ws/page/{page['id']}?client=A1")
    a.__enter__()
    _hello(a)
    room = collab.room_for(ws_id, page["id"])
    a.__exit__(None, None, None)
    time.sleep(0.2)
    assert collab.room_for(ws_id, page["id"]) is None
    # an old handler still holding the emptied room must not unregister the new one
    with client.websocket_connect(f"/api/ws/page/{page['id']}?client=Z") as z:
        _hello(z)
        current = collab.room_for(ws_id, page["id"])
        assert current is not room
        ghost = collab.Peer(ws=None, client="A1", user="", name="", color=0, can_edit=True)
        room.peers["A1"] = ghost
        assert collab.leave(room, ghost) is True
        assert collab.room_for(ws_id, page["id"]) is current
        assert _ops(guest, page["id"], [{"op": "insert", "id": "csA", "parent": page["id"], "content": "x"}],
                    client_id="W").status_code == 200
        assert recv(z, "ops", PRESENCE)["ops"][0]["id"] == "csA"


def test_the_hello_counts_a_batch_committed_while_joining(client, guest, monkeypatch):
    page = make_page(guest, "Hello gap")
    tree_seq = guest.get(f"/api/blocks/{page['id']}/subtree").json()["seq"]
    real_join = collab.join
    fired = []

    def join_after_a_write(ws, page_id, peer):
        if not fired:
            fired.append(1)
            ops.commit_ops(ws, page_id, [{"op": "insert", "id": "hgA", "parent": page_id,
                                          "content": "written during the handshake"}], actor="other")
        return real_join(ws, page_id, peer)

    monkeypatch.setattr(collab, "join", join_after_a_write)
    with client.websocket_connect(f"/api/ws/page/{page['id']}?client=late") as s:
        # the log position is read after joining: the client sees it is behind
        assert _hello(s)["seq"] == tree_seq + 1


def test_revoking_access_closes_the_open_socket(client):
    make_user("cr_owner", "crownerpw123")
    owner = login("cr_owner", "crownerpw123")
    page = make_page(owner, "Revoked page")
    token = owner.post(f"/api/share/{page['id']}").json()["token"]
    assert owner.put(f"/api/share-settings/{page['id']}", json={"audience": "anyone", "role": "view"}).status_code == 200
    with TestClient(app, cookies=owner.cookies) as ow:
        anon = TestClient(ow.app)
        anon.portal = ow.portal  # the visitor's socket on the same loop as the owner's requests
        with ow.websocket_connect(f"/api/ws/page/{page['id']}?client=OWN") as o, \
                anon.websocket_connect(f"/api/ws/page/{page['id']}?client=VIS&share={token}") as v:
            _hello(o), _hello(v)
            assert recv(o, "join", PRESENCE)["peer"]["client"] == "VIS"
            # upgrading the link to edit re-announces the visitor with its new right
            assert ow.put(f"/api/share-settings/{page['id']}", json={"audience": "anyone", "role": "edit"}).status_code == 200
            peer = recv(o, "join", PRESENCE)["peer"]
            assert (peer["client"], peer["can_edit"]) == ("VIS", True)
            # stop sharing: the visitor is dropped and its socket closed
            assert ow.delete(f"/api/share-settings/{page['id']}").status_code == 200
            assert recv(o, "leave", PRESENCE)["client"] == "VIS"
            with pytest.raises(WebSocketDisconnect) as closed:
                while True:
                    msg = v.receive_json()
                    assert msg["t"] != "ops", "a revoked visitor still received a batch"
            assert closed.value.code == collab.CLOSE_REVOKED
            assert ow.post(f"/api/pages/{page['id']}/ops", json={"client": "OWN", "ops": [
                {"op": "insert", "id": "rvA", "parent": page["id"], "content": "private again"}]}).status_code == 200
            assert recv(o, "ops", PRESENCE)["ops"][0]["id"] == "rvA"


def test_a_removed_member_is_closed_and_a_new_viewer_loses_edit(client):
    make_user("cr_admin", "cradminpw123", is_admin=1)
    make_user("cr_member", "crmemberpw12")
    admin = login("cr_admin", "cradminpw123")
    ws_id = admin.post("/api/workspaces", json={"name": "Revalidate team", "kind": "shared", "owner": "cr_admin"}).json()["id"]
    assert admin.put(f"/api/workspaces/{ws_id}/members/cr_member", json={"role": "editor"}).status_code == 200
    page = admin.post("/api/blocks", params={"ws": ws_id}, json={"parent_id": "root", "content": "Team page"}).json()
    member = login("cr_member", "crmemberpw12")
    with TestClient(app, cookies=admin.cookies) as ad:
        mem = TestClient(app, cookies=member.cookies)
        mem.portal = ad.portal
        url = f"/api/ws/page/{page['id']}?ws={ws_id}"
        with ad.websocket_connect(url + "&client=ADM") as a, mem.websocket_connect(url + "&client=MEM") as m:
            _hello(a)
            assert next(p for p in _hello(m)["peers"] if p["client"] == "MEM")["can_edit"] is True
            assert recv(a, "join", PRESENCE)["peer"]["client"] == "MEM"
            assert ad.put(f"/api/workspaces/{ws_id}/members/cr_member", json={"role": "viewer"}).status_code == 200
            peer = recv(a, "join", PRESENCE)["peer"]
            assert (peer["client"], peer["can_edit"]) == ("MEM", False)
            assert ad.delete(f"/api/workspaces/{ws_id}/members/cr_member").status_code == 200
            assert recv(a, "leave", PRESENCE)["client"] == "MEM"
            with pytest.raises(WebSocketDisconnect) as closed:
                while True:
                    m.receive_json()
            assert closed.value.code == collab.CLOSE_REVOKED
            assert "MEM" not in collab.room_for(ws_id, page["id"]).peers
