"""The other servers this computer's links name (gamma/remote_servers.py,
routers/remote_servers.py; docs/dev/https.md): moving an account's links to
a server's new address, what plain http may be used for, and the
certificates the shell pins.

One process plays both sides: the "other server" is this app's TestClient,
reached through ``gamma_sync.default_open_url`` and the mirror's transport,
both of which see the address asked, so ``http://nas`` and
``https://nas:5001`` are told apart. Another server is played by
another workspace there, or a token refused there (``answers``)."""

import io
import json
import socket
import time

import pytest

from conftest import account_of, fresh_client, login, make_folder, make_user, workspace_of
from gamma import folder_links, gamma_sync, remote_servers, sync_engine, workspaces
from gamma.computers import channel
from gamma.db import connect_users_db, page_now
from gamma.integrations import create_token
from gamma.publisher_sessions import seal

PASSWORD = "rs-pass-1234"
SECRET = "rs-shell-secret"
SHELL = {"X-Gamma-Shell": SECRET}
NAS, MOVED = "http://nas", "https://nas:5001"
PIN = {"origin": "https://nas.lan:5001", "sha256": "ab" * 32, "subject": "CN=nas.lan", "issuer": "CN=nas.lan",
       "not_before": "2026-01-01T00:00:00+00:00", "not_after": "2036-01-01T00:00:00+00:00"}


@pytest.fixture(scope="module")
def owner():
    make_user("rs_owner", PASSWORD)
    return login("rs_owner", PASSWORD)


@pytest.fixture
def nas(owner, tmp_path, monkeypatch):
    """This server as the desktop app runs it (links kept here, its shell's
    secret set), every other server this app's TestClient (redirects not
    followed), reached through the URL-aware seams: ``calls`` the
    ``(origin, path, Authorization)`` of each request, ``bodies`` its
    ``(origin, path, JSON body)``, ``answers`` an answer by ``(origin,
    path)`` (``(status, a JSON value or bytes)``), ``down`` the origins
    not reached. No channel's thread runs
    (``started`` the ones a move restarts). What a test made goes with it."""
    monkeypatch.setenv("GAMMA_FOLDER_LINKS", "1")
    monkeypatch.setenv("GAMMA_SHELL_SECRET", SECRET)
    monkeypatch.setattr(folder_links, "run_in_background", lambda link_id, **kw: None)
    started = []
    monkeypatch.setattr(channel, "start", lambda link_id: started.append(link_id))
    http, calls, bodies, answers, down = fresh_client(follow_redirects=False), [], [], {}, set()

    def answer(url, method, path, headers, body=None):
        origin, bare = remote_servers.origin_of(url), path.split("?")[0]
        calls.append((origin, bare, headers.get("Authorization", "")))
        content = body if body is None or isinstance(body, bytes) else b"".join(body)
        if content and headers.get("Content-Type", "").startswith("application/json"):
            bodies.append((origin, bare, json.loads(content)))
        if origin in down:
            raise gamma_sync.Unreachable(f"cannot reach {url}: refused")
        if (origin, bare) in answers:
            status, value = answers[(origin, bare)]
            return status, {}, io.BytesIO(value if isinstance(value, bytes) else json.dumps(value).encode())
        r = http.request(method, path, headers=headers, content=content)
        return r.status_code, r.headers, io.BytesIO(r.content)

    def fetch(self, method, path, body, headers):
        status, _headers, data = answer(self.url, method, path, headers, body)
        return status, data.read()

    monkeypatch.setattr(gamma_sync, "default_open_url", answer)
    monkeypatch.setattr(sync_engine, "default_fetch", None)
    monkeypatch.setattr(sync_engine.Remote, "_urllib_fetch", fetch)
    me = account_of("rs_owner")
    yield {"calls": calls, "bodies": bodies, "answers": answers, "down": down, "started": started, "dir": tmp_path, "me": me}
    for link in folder_links.all_links():
        if link["created_by"] == me:
            folder_links._forget(link)
    with connect_users_db() as conn:
        conn.execute("DELETE FROM computer_links WHERE created_by = ?", (me,))
        conn.execute("DELETE FROM mirrors WHERE owner = ?", (me,))
        conn.execute("DELETE FROM server_addresses")
        conn.execute("DELETE FROM tls_pins")
        conn.execute("DELETE FROM integration_tokens WHERE user_id = ?", (me,))


def _vault(nas, url=NAS, **fields) -> dict:
    """A vault of a folder of the "other server", its row alone (no
    directory, no round), with a token of it."""
    ws, folder = workspace_of("rs_owner"), fields.pop("folder_id", "root")
    token = create_token(nas["me"], ws, "rs-vault", 1)
    path = str(nas["dir"] / f"vault-{len(folder_links.all_links())}-{token['id'][:6]}")
    return folder_links._insert(folder_links._new(ws, path, nas["me"], remote_url=url, token_id=token["id"],
                                                  folder_id=folder, **fields), token["token"])


def _computer(owner, url=NAS) -> dict:
    """This computer shown to the "other server" at ``url`` (the shell's
    route, its announce checked there)."""
    token = create_token(account_of("rs_owner"), workspace_of("rs_owner"), "Gamma desktop: RS", 1, scope="computer")
    r = owner.post("/api/computer-links", json={"remote_url": url, "workspace": workspace_of("rs_owner"),
                                                "token": token["token"], "token_id": token["id"]}, headers=SHELL)
    assert r.status_code == 201, r.text
    return r.json()["link"]


def _mirror(nas, url=NAS) -> str:
    """An offline copy of the "other server"'s workspace, detached (no round
    runs): its local workspace's id."""
    me, there = nas["me"], workspace_of("rs_owner")
    ws = workspaces.create("RS offline copy", me)["id"]
    token = create_token(me, there, "rs-mirror", 1, scope="write")["token"]
    with connect_users_db() as conn:
        conn.execute("INSERT INTO mirrors (workspace_id, remote_url, remote_ws, remote_name, token, owner, mode, status, "
                     "created_at) VALUES (?, ?, ?, 'RS', ?, ?, 'off', '{}', ?)", (ws, url, there, seal(token), me, page_now()))
    return ws


def _move(owner, frm=NAS, to=MOVED, **body):
    return owner.post("/api/remote-servers/move", json={"from": frm, "to": to, **body}, headers=SHELL)


def _sent(nas, origin=MOVED) -> list:
    """The credentials that went to ``origin``."""
    return [auth for at, _path, auth in nas["calls"] if at == origin and auth]


def test_a_move_takes_the_vaults_the_channel_and_the_copies_to_the_new_address(owner, nas):
    folder = make_folder(owner, "RS kept")
    vault = _vault(nas, folder_id=folder)
    link = _computer(owner)
    copy = _mirror(nas)
    before = channel.computer_id(NAS)
    assert channel.of_vault(vault)["id"] == link["id"]

    # the dry run counts and asks nothing
    nas["calls"].clear()
    r = _move(owner, dry_run=True)
    assert r.status_code == 200, r.text
    assert r.json() == {"from": NAS, "to": MOVED, "kind": "upgrade", "plain": False,
                        "moved": {"vaults": 1, "computers": 1, "mirrors": 1}}
    assert nas["calls"] == []

    # the move: the probe without a credential, then each token, the channel's first; what the old
    # address's failure left in the statuses goes with it
    folder_links._write_status(vault["id"], {"last_error": "http://nas now redirects to https://nas:5001",
                                             "failed_at": time.time(), "error_kind": "redirected", "move_to": MOVED,
                                             "upgrade": True})
    r = _move(owner)
    assert r.status_code == 200, r.text
    assert r.json() == {"from": NAS, "to": MOVED, "kind": "upgrade", "revoke": [],
                        "moved": {"vaults": 1, "computers": 1, "mirrors": 1}}
    asked = [(path, bool(auth)) for at, path, auth in nas["calls"] if at == MOVED]
    assert asked == [("/api/health", False), ("/api/computers/announce", True), ("/api/sync/whoami", True),
                     ("/api/sync/whoami", True)]
    # the channel's check announces what the channel does, its vaults included: at the same server (this
    # upgrade) the token's row is updated, and one naming no vault would end its work folders' agents
    announced = [body for at, path, body in nas["bodies"] if path == "/api/computers/announce"]
    assert announced[-1]["vaults"] == [{"kind": "folder", "folder_id": folder, "paused": False, "agent": None}]
    assert announced[-1]["computer"]["id"] == before
    assert folder_links.get_link(vault["id"])["remote_url"] == MOVED
    assert channel.get(link["id"])["remote_url"] == MOVED and nas["started"][-1] == link["id"]
    assert channel.get(link["id"])["status"]["state"] == "connecting"
    assert not {"error_kind", "move_to", "upgrade", "failed_at"} & set(folder_links.get_link(vault["id"])["status"])
    assert sync_engine.get_mirror(copy)["remote_url"] == MOVED
    moved = folder_links.get_link(vault["id"])
    assert channel.of_vault(moved)["id"] == link["id"]                     # vault and channel still a pair

    # this computer is the same one there, and again once moved back
    assert channel.computer_id(MOVED) == before
    r = _move(owner, MOVED, NAS, plain_ok=True)                             # back to http://: the shell asked
    assert r.status_code == 200 and r.json()["moved"] == {"vaults": 1, "computers": 1, "mirrors": 1}
    assert channel.computer_id(NAS) == before

    # nothing at the old address: nothing moves, nothing is asked
    nas["calls"].clear()
    r = _move(owner, "http://gone.lan", MOVED)
    assert r.status_code == 200 and r.json()["moved"] == {"vaults": 0, "computers": 0, "mirrors": 0}
    assert nas["calls"] == []


def test_a_new_channel_at_an_address_a_server_left_gets_an_id_of_its_own(owner, nas):
    _computer(owner)
    before = channel.computer_id(NAS)
    assert _move(owner).status_code == 200
    with connect_users_db() as conn:
        conn.execute("DELETE FROM computer_links WHERE created_by = ?", (nas["me"],))
    _computer(owner)                                                        # another server now at http://nas
    assert channel.computer_id(NAS) not in (before, channel.computer_id(MOVED))
    assert channel.computer_id(MOVED) == before


def test_a_move_moves_nothing_unless_the_new_address_is_that_server(owner, nas):
    folder = make_folder(owner, "RS checked")
    vault = _vault(nas, folder_id=folder)
    link = _computer(owner)

    # the probe answers no Gamma: no token goes there
    nas["calls"].clear()
    nas["answers"][(MOVED, "/api/health")] = (200, b"<html>sign in</html>")
    r = _move(owner)
    assert r.status_code == 502 and "could not reach https://nas:5001" in r.json()["detail"], r.text
    assert _sent(nas) == []
    nas["answers"].clear()

    # another workspace there for the channel, checked first: the vault's token never goes there
    nas["calls"].clear()
    nas["answers"][(MOVED, "/api/computers/announce")] = (200, {"workspace": {"id": "elsewhere"}, "account": {}})
    r = _move(owner)
    assert (r.status_code, r.json()["detail"]) == (409, "https://nas:5001 opens another workspace than http://nas "
                                                        "for this computer: it is another server. Nothing was moved.")
    assert len(_sent(nas)) == 1 and _sent(nas)[0] == f"Bearer {channel.get(link['id'], with_token=True)['token']}"
    nas["answers"].clear()

    # the vault's token refused there
    nas["answers"][(MOVED, "/api/sync/whoami")] = (401, {"detail": "Invalid token"})
    r = _move(owner)
    assert r.status_code == 409 and "refused the token of the synced vault" in r.json()["detail"], r.text
    nas["answers"].clear()

    # not reached
    nas["down"].add(MOVED)
    assert _move(owner).status_code == 502
    nas["down"].clear()
    assert folder_links.get_link(vault["id"])["remote_url"] == NAS and channel.get(link["id"])["remote_url"] == NAS


def test_a_move_to_plain_http_is_refused_far_and_asked_first_near(owner, nas):
    _vault(nas)
    nas["calls"].clear()
    r = _move(owner, to="http://example.com")
    assert r.status_code == 400 and "is on the internet" in r.json()["detail"] and nas["calls"] == []
    r = _move(owner, to="http://192.168.1.5:9001", dry_run=True)
    assert r.json()["plain"] is True and r.json()["kind"] == "other"
    r = _move(owner, to="http://192.168.1.5:9001")
    assert r.status_code == 400 and "without encryption" in r.json()["detail"] and nas["calls"] == []
    r = _move(owner, to="http://192.168.1.5:9001", plain_ok=True)
    assert r.status_code == 200 and r.json()["moved"]["vaults"] == 1
    assert _move(owner, "http://192.168.1.5:9001", "http://192.168.1.5:9001").status_code == 400   # the same address


def test_a_channel_shown_at_the_new_address_first_gives_its_place_to_the_moved_one(owner, nas):
    link = _computer(owner)
    there = _computer(owner, MOVED)
    token_id = channel.get(there["id"])["token_id"]
    r = _move(owner)
    assert r.status_code == 200, r.text
    assert r.json()["revoke"] == [{"workspace_id": workspace_of("rs_owner"), "token_id": token_id}]
    assert channel.get(there["id"]) is None and channel.get(link["id"])["remote_url"] == MOVED


def test_the_moves_and_the_pins_take_the_shells_word(owner, nas, monkeypatch):
    assert _move(owner, dry_run=True, **{}).status_code == 200
    assert owner.post("/api/remote-servers/move", json={"from": NAS, "to": MOVED}, headers={"X-Gamma-Shell": "wrong"}
                      ).status_code == 403
    assert owner.get("/api/remote-servers/pins").status_code == 403
    monkeypatch.setenv("GAMMA_SHELL_SECRET", "")                          # no secret: refused, never unchecked
    assert _move(owner, dry_run=True).status_code == 403
    assert owner.put("/api/remote-servers/pins", json={"pins": []}, headers=SHELL).status_code == 403
    monkeypatch.setenv("GAMMA_SHELL_SECRET", SECRET)
    monkeypatch.delenv("GAMMA_FOLDER_LINKS")                              # a server that keeps no links
    assert _move(owner, dry_run=True).status_code == 404
    assert owner.get("/api/remote-servers/pins", headers=SHELL).status_code == 404
    assert owner.post("/api/remote-servers/check", json={"url": NAS}).status_code == 404


def test_the_pins_are_the_shells_list_whole(owner, nas, monkeypatch):
    r = owner.put("/api/remote-servers/pins", json={"pins": [PIN]}, headers=SHELL)
    assert r.status_code == 200, r.text
    first = r.json()["pins"]
    assert [(p["origin"], p["sha256"], p["subject"]) for p in first] == [(PIN["origin"], PIN["sha256"], "CN=nas.lan")]
    assert first[0]["pinned_at"]
    assert owner.get("/api/remote-servers/pins", headers=SHELL).json() == {"pins": first}

    other = {**PIN, "origin": "https://192.168.1.5:5001", "sha256": "cd" * 32}
    r = owner.put("/api/remote-servers/pins", json={"pins": [PIN, other]}, headers=SHELL)
    pins = {p["origin"]: p for p in r.json()["pins"]}
    assert set(pins) == {PIN["origin"], other["origin"]} and pins[PIN["origin"]]["pinned_at"] == first[0]["pinned_at"]
    r = owner.put("/api/remote-servers/pins", json={"pins": [other]}, headers=SHELL)   # the whole list, replaced
    assert [p["origin"] for p in r.json()["pins"]] == [other["origin"]]

    r = owner.put("/api/remote-servers/pins", json={"pins": [{**PIN, "origin": "http://nas.lan"}]}, headers=SHELL)
    assert r.status_code == 400 and r.json()["detail"]
    assert owner.put("/api/remote-servers/pins", json={"pins": [{**PIN, "sha256": "ab"}]}, headers=SHELL).status_code == 400
    assert [p["origin"] for p in owner.get("/api/remote-servers/pins", headers=SHELL).json()["pins"]] == [other["origin"]]

    # what a certificate failure rests (a vault's retry wait, a channel's longest rest) tries again at once
    refused, other = _vault(nas, url="https://nas.lan:5001"), _vault(nas, url="https://nas.lan:5002")
    folder_links._write_status(refused["id"], {"last_error": "nas.lan's certificate is not trusted",
                                               "failed_at": time.time(), "error_kind": "certificate"})
    folder_links._write_status(other["id"], {"last_error": "cannot reach", "failed_at": time.time()})
    nudged = []
    monkeypatch.setattr(channel, "nudge", lambda: nudged.append(1))
    assert owner.put("/api/remote-servers/pins", json={"pins": [PIN]}, headers=SHELL).status_code == 200
    assert nudged == [1]
    assert "failed_at" not in folder_links.get_link(refused["id"])["status"]
    assert folder_links.due(folder_links.get_link(refused["id"])) == "both"
    assert "failed_at" in folder_links.get_link(other["id"])["status"]     # another failure keeps its wait

    monkeypatch.setenv("GAMMA_HOSTED", "1")                               # never on a hosted server
    assert owner.get("/api/remote-servers/pins", headers=SHELL).status_code == 404
    assert owner.put("/api/remote-servers/pins", json={"pins": []}, headers=SHELL).status_code == 404


def test_check_says_what_plain_http_is_before_a_token_is_minted(owner, nas):
    def check(url, **renews):
        r = owner.post("/api/remote-servers/check", json={"url": url, **renews})
        assert r.status_code == 200, r.text
        return r.json()

    lan = check("http://nas.lan:9001/")
    assert (lan["origin"], lan["scope"], lan["plain"], lan["existing"], lan["refused"]) == (
        "http://nas.lan:9001", "lan", True, False, "")
    assert lan["warning"].startswith("nas.lan is reached over http://")
    far = check("http://example.com")
    assert (far["scope"], far["plain"], far["warning"]) == ("far", True, "")
    assert far["refused"] == ("example.com is on the internet and this address is http://: Gamma sends its tokens "
                              "there only over https://. Use https://example.com.")
    assert check("https://example.com")["plain"] is False and check("http://127.0.0.1:9001")["plain"] is False

    vault = _vault(nas, url="http://example.com")                           # a link from before: kept serviceable
    far = check("http://example.com", vault=vault["id"])                    # its token renewed
    assert far["existing"] is True and far["refused"] == "" and "the new token travels in clear" in far["warning"]
    # but a new link there is refused as the route that makes it refuses it, before any token is minted in clear:
    # another vault or offline copy, or this computer's channel in a workspace it has none in there
    for renews in ({}, {"workspace": workspace_of("rs_owner")}, {"vault": "another"}):
        far = check("http://example.com", **renews)
        assert far["existing"] is True and far["refused"].startswith("example.com is on the internet"), renews
    with connect_users_db() as conn:
        conn.execute("INSERT INTO computer_links (id, remote_url, workspace_id, created_by, token, token_id, created_at) "
                     "VALUES ('rs-far-channel', 'http://example.com', ?, ?, '', '', ?)",
                     (workspace_of("rs_owner"), nas["me"], page_now()))
    assert check("http://example.com", workspace=workspace_of("rs_owner"))["refused"] == ""   # re-tokened
    assert owner.post("/api/remote-servers/check", json={"url": "ftp://x"}).status_code == 400


def test_a_new_link_over_http_to_the_internet_is_refused_before_any_request(owner, nas):
    ws = workspace_of("rs_owner")
    token = create_token(nas["me"], ws, "rs-far", 1)
    nas["calls"].clear()
    r = owner.post("/api/folder-links", json={"folder": "root", "path": str(nas["dir"] / "Far"), "token": token["token"],
                                              "remote_url": "http://example.com"}, headers=SHELL)
    assert r.status_code == 400 and "is on the internet" in r.json()["detail"], r.text
    r = owner.post("/api/computer-links", json={"remote_url": "http://example.com", "workspace": ws,
                                                "token": token["token"]}, headers=SHELL)
    assert r.status_code == 400 and "is on the internet" in r.json()["detail"], r.text
    r = owner.post("/api/mirrors", json={"remote_url": "http://example.com", "token": token["token"]})
    assert r.status_code == 400 and "is on the internet" in r.json()["detail"], r.text
    assert nas["calls"] == []

    # one kept there from before takes its new token
    vault = _vault(nas, url="http://example.com")
    again = create_token(nas["me"], ws, "rs-far-2", 1)
    r = owner.patch(f"/api/folder-links/{vault['id']}", json={"token": again["token"], "token_id": again["id"]},
                    headers=SHELL)
    assert r.status_code == 200, r.text


def test_the_lists_say_plain_http_without_dns(owner, nas, monkeypatch):
    _vault(nas, url="http://elsewhere.example")
    _vault(nas, url="http://192.168.1.5:9001")
    _computer(owner)
    asked = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: asked.append(a) or [])
    links = {link["remote_url"]: link["plain"] for link in owner.get("/api/folder-links").json()["links"]
             if link["remote_url"]}
    assert links == {"http://elsewhere.example": "name", "http://192.168.1.5:9001": "lan"}
    assert [link["plain"] for link in owner.get("/api/computer-links").json()["links"]] == ["lan"]
    assert asked == []
    assert remote_servers.plain("https://nas.example") == remote_servers.plain("http://localhost:9001") == ""
    assert remote_servers.plain("http://203.0.113.5") == "name"
