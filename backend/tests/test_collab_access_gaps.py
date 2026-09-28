"""The page socket's access, and its room bookkeeping, at the edges the
revalidation used to miss: a page refiled out of a shared folder by an op
batch, an account deleted by an admin, a share stopped while a visitor's
handshake is in flight; a client gone while the hello is sent; a peer
dropped on a failed send (the others must hear it left)."""

import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from conftest import login, make_page, make_user, recv
from gamma import collab, workspaces
from gamma.app import app
from gamma.db import connect_users_db
from gamma.routers import collab as rcollab

PRESENCE = ("join", "leave", "cursor")


def _hello(sock):
    msg = sock.receive_json()
    assert msg["t"] == "hello", msg
    return msg


def _closed(sock):
    """Read until the socket closes; its close code. A batch arriving
    before the close fails the test."""
    with pytest.raises(WebSocketDisconnect) as closed:
        while True:
            msg = sock.receive_json()
            assert msg["t"] != "ops", f"a peer without access received a batch: {msg}"
    return closed.value.code


def test_a_page_refiled_out_of_a_shared_folder_closes_the_folder_visitor():
    make_user("cag_folder", "cagfolderpw1")
    owner = login("cag_folder", "cagfolderpw1")
    page = make_page(owner, "Filed page", {"folder": "Shared"})
    token = owner.post("/api/share/folder", params={"name": "Shared"},
                       json={"audience": "anyone", "role": "view"}).json()["token"]
    with TestClient(app, cookies=owner.cookies) as ow:
        anon = TestClient(app)
        anon.portal = ow.portal
        with ow.websocket_connect(f"/api/ws/page/{page['id']}?client=OWN") as o, \
                anon.websocket_connect(f"/api/ws/page/{page['id']}?client=VIS&share={token}") as v:
            _hello(o), _hello(v)
            assert recv(o, "join", PRESENCE)["peer"]["client"] == "VIS"
            # the owner files the page elsewhere: the visitor leaves and is closed
            r = ow.post(f"/api/pages/{page['id']}/ops", json={"client": "OWN", "ops": [
                {"op": "set", "id": page["id"], "props": {"folder": "Private"}}]})
            assert r.status_code == 200, r.text
            assert recv(o, "leave", PRESENCE + ("ops",))["client"] == "VIS"
            with pytest.raises(WebSocketDisconnect) as closed:
                while True:  # the refiling batch itself may reach it before the close
                    msg = v.receive_json()
                    assert msg["t"] != "ops" or not any(op.get("content") for op in msg["ops"])
            assert closed.value.code == collab.CLOSE_REVOKED
            ow.post(f"/api/pages/{page['id']}/ops", json={"client": "OWN", "ops": [
                {"op": "insert", "id": "cagA", "parent": page["id"], "content": "private now"}]}).raise_for_status()
            assert recv(o, "ops", PRESENCE)["ops"][0]["id"] == "cagA"


def test_deleting_an_account_closes_its_sockets_and_those_of_its_workspaces():
    make_user("cag_admin", "cagadminpw12", is_admin=1)
    make_user("cag_member", "cagmemberpw1")
    admin = login("cag_admin", "cagadminpw12")
    member = login("cag_member", "cagmemberpw1")
    team = admin.post("/api/workspaces", json={"name": "Deleted-account team", "kind": "shared",
                                               "owner": "cag_admin"}).json()["id"]
    assert admin.put(f"/api/workspaces/{team}/members/cag_member", json={"role": "editor"}).status_code == 200
    team_page = admin.post("/api/blocks", params={"ws": team}, json={"parent_id": "root", "content": "Team"}).json()
    # the member's own page, open to a signed-in visitor (the admin) through a share link
    own_page = make_page(member, "Member's own")
    token = member.post(f"/api/share/{own_page['id']}").json()["token"]
    member.put(f"/api/share-settings/{own_page['id']}", json={"audience": "users", "role": "view"}).raise_for_status()
    with TestClient(app, cookies=admin.cookies) as ad:
        mem = TestClient(app, cookies=member.cookies)
        mem.portal = ad.portal
        team_url = f"/api/ws/page/{team_page['id']}?ws={team}"
        with ad.websocket_connect(team_url + "&client=ADM") as a, \
                mem.websocket_connect(team_url + "&client=MEM") as m, \
                ad.websocket_connect(f"/api/ws/page/{own_page['id']}?client=VIS&share={token}") as visitor:
            _hello(a), _hello(m), _hello(visitor)
            assert recv(a, "join", PRESENCE)["peer"]["client"] == "MEM"
            assert ad.delete("/api/admin/users/cag_member").status_code == 200
            assert recv(a, "leave", PRESENCE)["client"] == "MEM"
            assert _closed(m) == collab.CLOSE_REVOKED
            # the member's personal workspace went with it: its visitor is closed too
            assert _closed(visitor) == collab.CLOSE_REVOKED


def test_a_share_stopped_during_the_handshake_is_caught_after_the_join(monkeypatch):
    make_user("cag_hs", "caghspassw1")
    owner = login("cag_hs", "caghspassw1")
    ws = workspaces.default_workspace("cag_hs")
    page = make_page(owner, "Handshake page")
    token = owner.post(f"/api/share/{page['id']}").json()["token"]
    owner.put(f"/api/share-settings/{page['id']}", json={"audience": "anyone", "role": "view"}).raise_for_status()
    real = rcollab._socket_access

    def admitted_then_revoked(sock, page_id):
        out = real(sock, page_id)
        if out and sock.query_params.get("share"):
            with connect_users_db() as conn:  # what stopping the share does ...
                conn.execute("DELETE FROM shares WHERE token = ?", (token,))
                conn.commit()
            collab.revalidate(ws)  # ... before the visitor is in the room
        return out

    monkeypatch.setattr(rcollab, "_socket_access", admitted_then_revoked)
    with TestClient(app, cookies=owner.cookies) as ow:
        anon = TestClient(app)
        anon.portal = ow.portal
        with ow.websocket_connect(f"/api/ws/page/{page['id']}?client=OWN") as o:
            _hello(o)
            with anon.websocket_connect(f"/api/ws/page/{page['id']}?client=VIS&share={token}") as v:
                _hello(v)  # still the first message; the close follows at once
                assert _closed(v) == collab.CLOSE_REVOKED
            assert "VIS" not in collab.room_for(ws, page["id"]).peers


class _Sock:
    """Just enough of a starlette WebSocket for the page handler."""

    def __init__(self, fail_send=False):
        self.query_params, self.cookies = {"client": "GONE"}, {}
        self.fail_send, self.sent, self.closed = fail_send, [], None

    async def accept(self):
        pass

    async def send_text(self, text):
        if self.fail_send:
            raise WebSocketDisconnect(code=1006)
        self.sent.append(text)

    async def receive_json(self):
        raise WebSocketDisconnect(code=1000)

    async def close(self, code=1000):
        self.closed = code


def test_a_client_gone_while_the_hello_is_sent_is_an_ordinary_close(monkeypatch):
    peer = collab.Peer(ws=None, client="", user="u", name="u", color=0, can_edit=True, account="u")

    def admitted(sock, page_id):
        peer.ws = sock
        return "cag-ws", peer

    monkeypatch.setattr(rcollab, "_socket_access", admitted)
    monkeypatch.setattr(rcollab, "_log_position", lambda ws, page_id: 0)
    monkeypatch.setattr(rcollab, "_still_admitted", lambda ws, page_id, peer: True)
    # no exception escapes the handler (uvicorn would log it as an ASGI error)
    asyncio.run(rcollab.page_socket(_Sock(fail_send=True), "cag-page"))
    assert collab.room_for("cag-ws", "cag-page") is None


def test_a_peer_dropped_on_a_failed_send_is_announced_as_gone():
    async def main():
        alive, dead = _Sock(), _Sock(fail_send=True)
        room = collab.join("cag-ghost", "page", collab.Peer(ws=alive, client="W", user="u", name="u",
                                                               color=0, can_edit=True))
        dead_peer = collab.Peer(ws=dead, client="X", user="v", name="v", color=1, can_edit=True)
        collab.join("cag-ghost", "page", dead_peer)
        await room.broadcast('{"t": "ops"}')
        for _ in range(5):
            await asyncio.sleep(0)
        assert "X" not in room.peers
        assert collab.leave(room, dead_peer) is False  # its handler's teardown has nothing to announce
        return alive.sent

    sent = asyncio.run(main())
    assert sent == ['{"t": "ops"}', '{"t": "leave", "client": "X"}']
    assert collab.room_for("cag-ghost", "page").peers.keys() == {"W"}
