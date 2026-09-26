"""Connecting a self-hosted Gamma server (gammacloud/connect.py): the
approval page and its refusals, the one-time code and its PKCE check, the
client it makes (kind ``server``, owned, registered for the server's
callback), reconnecting (same id, new secret, grants kept), disconnecting,
and the portal's one list of servers with their sign-ins."""

import base64
from contextlib import closing
from urllib.parse import parse_qs, urlsplit

from conftest import register, verify
from test_oidc import authorize_params, pkce
from test_profile import SERVER, SERVER_CALLBACK, bearer, desktop_tokens, lab_tokens

from gammacloud import connect, db, oidc

RETURN = SERVER + "/api/auth/cloud/connect/callback"


def alice(client):
    register(client)
    verify(client)


def approve(client, server=SERVER):
    """The approval page, then Connect: (code, verifier)."""
    verifier, challenge = pkce()
    params = {"server": server, "state": "cs-1", "code_challenge": challenge}
    page = client.get("/connect-server", params=params)
    assert page.status_code == 200 and "Connect lab.example.org" in page.text, page.text
    r = client.post("/connect-server/continue", json={"server": server, "state": "cs-1", "code_challenge": challenge})
    assert r.status_code == 200, r.text
    redirect = urlsplit(r.json()["redirect"])
    assert f"{redirect.scheme}://{redirect.netloc}{redirect.path}" == RETURN
    q = parse_qs(redirect.query)
    assert q["state"] == ["cs-1"]
    return q["code"][0], verifier


def fetch(client, code, verifier, server=SERVER):
    # the server calls from its own address
    return client.post("/api/servers/connect/token", data={"code": code, "code_verifier": verifier, "server": server},
                       headers={"Sec-Fetch-Site": "cross-site"})


def connected(client):
    code, verifier = approve(client)
    r = fetch(client, code, verifier)
    assert r.status_code == 200, r.text
    return r.json()["client_id"], r.json()["client_secret"]


def test_discovery_names_the_connect_endpoints(client):
    doc = client.get("/.well-known/openid-configuration").json()
    assert doc["gamma_server_connect_endpoint"] == "http://testserver/connect-server"
    assert doc["gamma_server_connect_token_endpoint"] == "http://testserver/api/servers/connect/token"


def test_an_unconnected_server_is_told_what_to_do(client):
    _, challenge = pkce()
    r = client.get("/authorize", params=authorize_params(challenge, redirect=SERVER_CALLBACK))
    assert r.status_code == 400 and "not connected to Gamma Cloud yet" in r.text


def test_connecting_a_server(client):
    alice(client)
    client_id, secret = connected(client)
    with closing(db.connect()) as conn:
        made = oidc.get_client(conn, client_id)
        owner = conn.execute("SELECT id FROM accounts WHERE username = 'alice'").fetchone()["id"]
    assert made["kind"] == "server" and made["owner_account_id"] == owner
    assert made["redirect_uris"] == [SERVER_CALLBACK] and made["name"] == "lab.example.org"
    # the server signs people in with it, and lists itself on their account
    tokens = lab_tokens(client, client_id, secret)
    assert tokens["refresh_token"]
    assert client.post("/api/me/servers", headers=bearer(tokens), json={"url": SERVER, "name": "Lab"}).status_code == 200
    page = client.get("/devices").text
    assert "Servers you connected" in page and f"data-disconnect='{client_id}'" in page
    assert page.count(f"<a href='{SERVER}'") == 2      # the signed-in server and the connection


def test_a_code_works_once_for_its_own_server_and_verifier(client):
    alice(client)
    code, verifier = approve(client)
    assert fetch(client, code, "x" * 43).status_code == 400
    assert fetch(client, code, verifier).status_code == 400          # spent by the wrong try
    code, verifier = approve(client)
    assert fetch(client, code, verifier, server="https://elsewhere.example.org").status_code == 400
    code, verifier = approve(client)
    with closing(db.connect()) as conn:
        conn.execute("UPDATE server_connects SET expires_at = ?", (db.after(-1),))
        conn.commit()
    assert fetch(client, code, verifier).status_code == 400
    code, verifier = approve(client)
    assert fetch(client, code, verifier).status_code == 200
    assert fetch(client, code, verifier).status_code == 400


def test_the_approval_page_refuses_what_it_cannot_connect(client):
    _, challenge = pkce()
    for params in ({"server": "http://127.0.0.1:9001", "state": "s", "code_challenge": challenge},
                   {"server": "http://lab.example.org", "state": "s", "code_challenge": challenge},
                   {"server": "https://lab.example.org/gamma", "state": "s", "code_challenge": challenge},
                   {"server": SERVER, "state": "s", "code_challenge": "short"},
                   {"server": SERVER, "state": "", "code_challenge": challenge}):
        assert client.get("/connect-server", params=params).status_code == 400, params
    # signed out: the login page, then back here
    params = {"server": SERVER, "state": "s", "code_challenge": challenge}
    r = client.get("/connect-server", params=params, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("/login?next=%2Fconnect-server%3Fserver%3D")
    # an unconfirmed e-mail cannot connect
    register(client)
    page = client.get("/connect-server", params=params).text
    assert "Confirm your e-mail first" in page and f"{RETURN}?error=access_denied" in page.replace("&amp;", "&")
    assert client.post("/connect-server/continue", json=params).status_code == 403


def test_the_approval_comes_from_the_portal_page(client):
    alice(client)
    _, challenge = pkce()
    r = client.post("/connect-server/continue", json={"server": SERVER, "state": "s", "code_challenge": challenge},
                    headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


def test_reconnecting_rotates_the_secret_and_keeps_sign_ins(client):
    alice(client)
    client_id, secret = connected(client)
    tokens = lab_tokens(client, client_id, secret)
    again, new_secret = connected(client)
    assert again == client_id and new_secret != secret
    old = client.post("/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
                                      "client_id": client_id, "client_secret": secret})
    assert old.status_code == 401
    basic = base64.b64encode(f"{client_id}:{new_secret}".encode()).decode()
    r = client.post("/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]},
                    headers={"Authorization": "Basic " + basic})
    assert r.status_code == 200, r.text


def test_an_account_connects_a_limited_number_of_servers(client, monkeypatch):
    alice(client)
    monkeypatch.setattr(connect, "MAX_CLIENTS", 1)
    connected(client)
    verifier, challenge = pkce()
    other = "https://other.example.org"
    r = client.post("/connect-server/continue", json={"server": other, "state": "s", "code_challenge": challenge})
    code = parse_qs(urlsplit(r.json()["redirect"]).query)["code"][0]
    r = fetch(client, code, verifier, server=other)
    assert r.status_code == 400 and "at most 1" in r.json()["detail"]


def test_disconnecting_a_server(client):
    alice(client)
    client_id, secret = connected(client)
    tokens = lab_tokens(client, client_id, secret)
    register(client, "bob")
    verify(client)                                                    # bob's browser now
    assert client.post(f"/api/connected/{client_id}/disconnect", json={}).status_code == 404
    client.post("/api/logout")
    client.post("/api/login", json={"login": "alice", "password": "correct horse battery"})
    assert client.post(f"/api/connected/{client_id}/disconnect", json={}).status_code == 200
    with closing(db.connect()) as conn:
        assert oidc.get_client(conn, client_id) is None
    assert client.get("/api/me/prefs", headers=bearer(tokens)).status_code == 401
    assert "Servers you connected" not in client.get("/devices").text


def test_one_list_of_servers_with_their_sign_ins(client):
    alice(client)
    desktop = desktop_tokens(client)
    client.post("/api/me/servers", headers=bearer(desktop), json={"url": "http://127.0.0.1:9001", "name": "TimPC"})
    # a server that registered with a token holding no grant: listed, signed out
    client.post("/api/me/servers", headers=bearer(desktop_tokens(client, scope="openid")), json={"url": SERVER})
    page = client.get("/devices").text
    assert "2 listed" in page and page.count("class=dev") == 2 + 1          # two servers, one browser
    assert "TimPC" in page and "Gamma desktop app" in page and "Signed out" in page
    assert f"data-remove='{SERVER}'" in page
    # signing the desktop app out takes its server off the list
    grant = client.get("/api/devices").json()["devices"][0]["id"]
    assert client.post(f"/api/devices/{grant}/revoke", json={}).status_code == 200
    assert "TimPC" not in client.get("/devices").text
    # a signed-out server is removed by hand
    assert client.post("/api/servers/remove", json={"url": SERVER}).status_code == 200
    assert client.post("/api/servers/remove", json={"url": SERVER}).status_code == 404
    assert "No Gamma server is signed in" in client.get("/devices").text
