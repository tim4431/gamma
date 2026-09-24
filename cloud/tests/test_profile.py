"""The API Gamma servers call with a person's access token: the preference
profile (scope, last-writer-wins, caps), refresh tokens for a confidential
client with the prefs scope, the linked-server list and the Overview's
list of it, the username lookup, deletion, the upgrade."""

import base64
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

from conftest import register, verify
from test_oidc import CALLBACK, authorize_params, pkce, request_id_from, signed_in_code

from gammacloud import accounts, config, db, oidc

SERVER = "https://lab.example.org"
SERVER_CALLBACK = SERVER + "/api/auth/cloud/callback"


def alice(client):
    register(client)
    verify(client)


def desktop_tokens(client, scope="openid offline_access prefs", **extra):
    verifier, challenge = pkce()
    code = signed_in_code(client, challenge, scope=scope)
    r = client.post("/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": CALLBACK,
                                    "client_id": config.DESKTOP_CLIENT_ID, "code_verifier": verifier, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def lab_client(name="The lab's server"):
    with closing(db.connect()) as conn:
        client_id, secret = oidc.create_client(conn, name=name, kind="container", redirect_uris=[SERVER_CALLBACK])
        conn.commit()
    return client_id, secret


def lab_tokens(client, client_id, secret, scope="openid offline_access prefs", **extra):
    verifier, challenge = pkce()
    r = client.get("/authorize", params=authorize_params(challenge, client_id=client_id, redirect=SERVER_CALLBACK,
                                                        scope=scope))
    assert r.status_code == 200, r.text
    redirect = client.post("/authorize/continue", json={"request_id": request_id_from(r.text)}).json()["redirect"]
    code = parse_qs(urlsplit(redirect).query)["code"][0]
    r = client.post("/token", data={"grant_type": "authorization_code", "code": code, "code_verifier": verifier,
                                    "redirect_uri": SERVER_CALLBACK, "client_id": client_id, "client_secret": secret,
                                    **extra})
    assert r.status_code == 200, r.text
    return r.json()


def bearer(tokens):
    return {"Authorization": "Bearer " + tokens["access_token"]}


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# --- the preference profile ---------------------------------------------------

def test_prefs_round_trip_with_a_prefs_token(client):
    alice(client)
    h = bearer(desktop_tokens(client))
    assert client.get("/api/me/prefs", headers=h).json() == {"prefs": []}
    assert client.get("/api/me/prefs/appearance", headers=h).status_code == 404
    r = client.put("/api/me/prefs/appearance", headers=h, json={"value": {"theme": "dark", "flip": True}})
    assert r.status_code == 200, r.text
    ts = r.json()["updated_at"]
    assert len(ts) == 24 and ts.endswith("Z")
    assert client.get("/api/me/prefs/appearance", headers=h).json() == {"value": {"theme": "dark", "flip": True},
                                                                        "updated_at": ts}
    client.put("/api/me/prefs/reading", headers=h, json={"value": ["a", 1, None]})
    listed = client.get("/api/me/prefs", headers=h).json()["prefs"]
    assert [p["key"] for p in listed] == ["appearance", "reading"] and listed[0]["updated_at"] == ts
    assert client.delete("/api/me/prefs/appearance", headers=h).json() == {"ok": True, "removed": True}
    assert client.delete("/api/me/prefs/appearance", headers=h).json() == {"ok": True, "removed": False}
    assert client.get("/api/me/prefs/appearance", headers=h).status_code == 404


def test_prefs_need_the_scope_or_the_portal_session(client):
    alice(client)
    plain = bearer(desktop_tokens(client, scope="openid email offline_access"))
    r = client.get("/api/me/prefs", headers=plain)
    assert r.status_code == 403 and "insufficient_scope" in r.headers["www-authenticate"]
    assert client.put("/api/me/prefs/x", headers=plain, json={"value": 1}).status_code == 403
    assert client.get("/api/me", headers=plain).status_code == 200        # /api/me takes any token
    assert client.get("/api/me/prefs", headers={"Authorization": "Bearer nope"}).status_code == 401
    # the portal session reads and writes the same profile
    assert client.put("/api/me/prefs/x", json={"value": 1}).status_code == 200
    h = bearer(desktop_tokens(client))
    assert client.get("/api/me/prefs/x", headers=h).json()["value"] == 1
    client.post("/api/logout")
    assert client.get("/api/me/prefs").status_code == 401
    # a portal write still has to come from the portal's pages
    assert client.put("/api/me/prefs/x", json={"value": 2}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403


def test_prefs_are_last_writer_wins_by_updated_at(client):
    alice(client)
    h = bearer(desktop_tokens(client))
    t0 = datetime.now(timezone.utc) - timedelta(hours=2)
    r = client.put("/api/me/prefs/k", headers=h, json={"value": "second", "updated_at": iso(t0 + timedelta(minutes=10))})
    assert r.json()["updated_at"] == iso(t0 + timedelta(minutes=10))
    # an older write loses and gets the stored side
    r = client.put("/api/me/prefs/k", headers=h, json={"value": "first", "updated_at": iso(t0)})
    assert r.status_code == 409
    assert r.json()["value"] == "second" and r.json()["updated_at"] == iso(t0 + timedelta(minutes=10))
    # the same time (a retry) and a newer one win
    assert client.put("/api/me/prefs/k", headers=h,
                      json={"value": "again", "updated_at": iso(t0 + timedelta(minutes=10))}).status_code == 200
    later = (t0 + timedelta(minutes=20)).replace(microsecond=0)
    r = client.put("/api/me/prefs/k", headers=h, json={"value": "third", "updated_at": later.isoformat()})  # +00:00
    assert r.status_code == 200 and r.json()["updated_at"] == iso(later)
    # a future time is clamped to now, so it cannot pin the value
    r = client.put("/api/me/prefs/k", headers=h, json={"value": "future", "updated_at": "2099-01-01T00:00:00Z"})
    assert r.json()["updated_at"] <= db.now()
    assert client.put("/api/me/prefs/k", headers=h, json={"value": "now"}).status_code == 200
    assert client.get("/api/me/prefs/k", headers=h).json()["value"] == "now"
    for bad in ("yesterday", 12, "2026-13-01T00:00:00Z"):
        assert client.put("/api/me/prefs/k", headers=h, json={"value": 1, "updated_at": bad}).status_code == 400, bad


def test_prefs_caps(client):
    alice(client)
    h = bearer(desktop_tokens(client))
    for key in ("Theme", "a_b", "x" * 41, "a.b"):
        assert client.put(f"/api/me/prefs/{key}", headers=h, json={"value": 1}).status_code == 400, key
        assert client.get(f"/api/me/prefs/{key}", headers=h).status_code == 400, key
    assert client.put("/api/me/prefs/" + "x" * 40, headers=h, json={"value": 1}).status_code == 200
    big = "x" * (64 * 1024)
    assert client.put("/api/me/prefs/big", headers=h, json={"value": big}).status_code == 413
    assert client.put("/api/me/prefs/big", headers=h, json={"value": big[:-10]}).status_code == 200
    r = client.put("/api/me/prefs/k", headers={**h, "Content-Type": "application/json"}, content=b"not json")
    assert r.status_code == 400
    assert client.put("/api/me/prefs/k", headers=h, json={"updated_at": db.now()}).status_code == 400
    assert client.put("/api/me/prefs/k", headers=h, json=[1]).status_code == 400
    for i in range(18):
        assert client.put(f"/api/me/prefs/k{i}", headers=h, json={"value": i}).status_code == 200
    assert len(client.get("/api/me/prefs", headers=h).json()["prefs"]) == 20
    r = client.put("/api/me/prefs/one-more", headers=h, json={"value": 1})
    assert r.status_code == 400 and "20" in r.json()["detail"]
    assert client.put("/api/me/prefs/k0", headers=h, json={"value": "changed"}).status_code == 200


def test_pref_writes_are_rate_limited_per_account(client):
    alice(client)
    h = bearer(desktop_tokens(client))
    codes = [client.put("/api/me/prefs/k", headers=h, json={"value": i}).status_code for i in range(121)]
    assert codes[:120] == [200] * 120 and codes[120] == 429
    assert client.get("/api/me/prefs/k", headers=h).status_code == 200    # reads are not limited


# --- scopes and grants --------------------------------------------------------

def test_discovery_lists_the_prefs_scope(client):
    assert "prefs" in client.get("/.well-known/openid-configuration").json()["scopes_supported"]


def test_a_server_with_the_prefs_scope_gets_a_refresh_token(client):
    alice(client)
    client_id, secret = lab_client()
    # offline_access alone is still refused for a server
    _, challenge = pkce()
    r = client.get("/authorize", params=authorize_params(challenge, client_id=client_id, redirect=SERVER_CALLBACK,
                                                        scope="openid offline_access"), follow_redirects=False)
    assert r.status_code == 302 and "invalid_scope" in r.headers["location"]
    tokens = lab_tokens(client, client_id, secret, device_id="lab-server-0001", device_name="lab box")
    assert tokens["refresh_token"] and tokens["scope"] == "openid offline_access prefs"
    assert client.get("/api/me/prefs", headers=bearer(tokens)).status_code == 200
    # the Devices page shows the grant under the machine's name and the client's
    devices = client.get("/api/devices").json()["devices"]
    assert len(devices) == 1 and devices[0]["client"] == "The lab's server" and devices[0]["device_name"] == "lab box"
    assert "The lab&#x27;s server" in client.get("/devices").text
    # the same rotation rules: the client authenticates, the token rotates
    r = client.post("/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
                                    "client_id": client_id})
    assert r.status_code == 401
    basic = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
    r = client.post("/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]},
                    headers={"Authorization": "Basic " + basic})
    assert r.status_code == 200, r.text
    fresh = r.json()
    assert fresh["refresh_token"] != tokens["refresh_token"] and fresh["scope"] == "openid offline_access prefs"
    assert client.get("/api/me/prefs", headers=bearer(fresh)).status_code == 200
    # one grant per device_id, and the portal revokes it
    again = lab_tokens(client, client_id, secret, device_id="lab-server-0001", device_name="lab box")
    devices = client.get("/api/devices").json()["devices"]
    assert len(devices) == 1
    client.post(f"/api/devices/{devices[0]['id']}/revoke", json={})
    r = client.post("/token", data={"grant_type": "refresh_token", "refresh_token": again["refresh_token"],
                                    "client_id": client_id, "client_secret": secret})
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    assert client.get("/api/me/prefs", headers=bearer(again)).status_code == 401


def test_a_server_may_ask_for_prefs_without_a_refresh_token(client):
    alice(client)
    client_id, secret = lab_client()
    tokens = lab_tokens(client, client_id, secret, scope="openid prefs")
    assert "refresh_token" not in tokens
    assert client.get("/api/me/prefs", headers=bearer(tokens)).status_code == 200
    assert client.get("/api/devices").json()["devices"] == []


# --- the server list ----------------------------------------------------------

def test_linked_servers(client):
    alice(client)
    h = bearer(desktop_tokens(client, scope="openid"))                       # any access token will do
    assert client.get("/api/me", headers=h).json()["servers"] == []
    r = client.post("/api/me/servers", headers=h, json={"url": "HTTPS://Lab.Example.org:443/", "name": "  Lab\n server "})
    assert r.status_code == 200, r.text
    server = r.json()["server"]
    assert server["url"] == SERVER and server["name"] == "Lab server" and server["kind"] == "linked"
    assert server["local"] is False and server["linked_at"] == server["last_seen_at"]
    # the same address again refreshes it, one row
    r = client.post("/api/me/servers", headers=h, json={"url": SERVER, "name": "Lab"})
    assert r.json()["server"]["linked_at"] == server["linked_at"] and r.json()["server"]["name"] == "Lab"
    # the desktop sidecar's loopback address; a name defaults to the host
    local = client.post("/api/me/servers", headers=h, json={"url": "http://127.0.0.1:9001"}).json()["server"]
    assert local["local"] is True and local["name"] == "127.0.0.1:9001"
    long = client.post("/api/me/servers", headers=h, json={"url": "https://x.example.org", "name": "n" * 200})
    assert len(long.json()["server"]["name"]) == 80
    for bad in ("http://lab.example.org", "https://lab.example.org/gamma", "https://lab.example.org?x=1",
                "ftp://lab.example.org", "https://user@lab.example.org", "lab.example.org", ""):
        assert client.post("/api/me/servers", headers=h, json={"url": bad}).status_code == 400, bad
    me = client.get("/api/me", headers=h).json()
    assert {s["url"] for s in me["servers"]} == {SERVER, "http://127.0.0.1:9001", "https://x.example.org"}
    assert all(s["kind"] == "linked" for s in me["servers"])
    # the portal session sees the list too, but cannot register a server
    assert len(client.get("/api/me").json()["servers"]) == 3
    assert client.post("/api/me/servers", json={"url": SERVER}).status_code == 401
    # the Overview lists them: the name links, the loopback one is this computer
    page = client.get("/").text
    assert f"<a href='{SERVER}' target=_blank rel='noopener noreferrer'>Lab</a>" in page
    assert "This computer" in page and "href='http://127.0.0.1:9001'" not in page
    # unlink, by body or by query
    r = client.request("DELETE", "/api/me/servers", headers=h, json={"url": SERVER + "/"})
    assert r.json() == {"ok": True, "removed": True}
    r = client.delete("/api/me/servers", headers=h, params={"url": "http://127.0.0.1:9001"})
    assert r.json() == {"ok": True, "removed": True}
    assert client.request("DELETE", "/api/me/servers", headers=h, json={"url": SERVER}).json()["removed"] is False
    assert [s["url"] for s in client.get("/api/me", headers=h).json()["servers"]] == ["https://x.example.org"]


def test_a_server_client_registers_only_its_own_address(client):
    alice(client)
    client_id, secret = lab_client()
    h = bearer(lab_tokens(client, client_id, secret, scope="openid"))
    assert client.post("/api/me/servers", headers=h, json={"url": SERVER, "name": "Lab"}).status_code == 200
    r = client.post("/api/me/servers", headers=h, json={"url": "https://elsewhere.example.org"})
    assert r.status_code == 403
    desktop = bearer(desktop_tokens(client, scope="openid"))
    client.post("/api/me/servers", headers=desktop, json={"url": "https://elsewhere.example.org"})
    assert client.request("DELETE", "/api/me/servers", headers=h,
                          json={"url": "https://elsewhere.example.org"}).status_code == 403


def test_the_overview_with_no_servers(client):
    alice(client)
    assert "No Gamma server lists this account yet" in client.get("/").text


# --- username lookup ----------------------------------------------------------

def test_username_lookup(client):
    register(client, "bob")
    verify(client)
    register(client, "carol")                                                # never confirmed
    with closing(db.connect()) as conn:
        dave = accounts.create(conn, email="dave@example.org", username="dave", password=None, verified=True)
        accounts.delete(conn, dave["id"])
        bob_id = accounts.by_username(conn, "bob")["id"]
        conn.commit()
    alice(client)
    h = bearer(desktop_tokens(client, scope="openid"))
    assert client.get("/api/lookup/username", params={"u": "bob"}, headers=h).json() == {"sub": bob_id,
                                                                                         "username": "bob"}
    assert client.get("/api/lookup/username", params={"u": " Bob "}, headers=h).status_code == 200
    for u in ("bo", "carol", "dave", "bob@example.org", "nobody", "", "b%"):
        assert client.get("/api/lookup/username", params={"u": u}, headers=h).status_code == 404, u
    # a token is required; the portal session is not one
    assert client.get("/api/lookup/username", params={"u": "bob"}).status_code == 401


def test_username_lookup_is_rate_limited(client):
    alice(client)
    h = bearer(desktop_tokens(client, scope="openid"))
    codes = [client.get("/api/lookup/username", params={"u": f"user{i}"}, headers=h).status_code for i in range(21)]
    assert codes[:20] == [404] * 20 and codes[20] == 429
    # another token of the same account gets its own window, the IP's is shared
    other = bearer(desktop_tokens(client, scope="openid"))
    codes = [client.get("/api/lookup/username", params={"u": "alice"}, headers=other).status_code for _ in range(10)]
    assert codes[:9] == [200] * 9 and codes[9] == 429


# --- deletion and the upgrade -------------------------------------------------

def test_deleting_the_account_purges_its_profile_and_servers(client):
    alice(client)
    h = bearer(desktop_tokens(client))
    client.put("/api/me/prefs/k", headers=h, json={"value": 1})
    client.post("/api/me/servers", headers=h, json={"url": SERVER})
    assert client.post("/api/me/delete", json={"password": "correct horse battery"}).status_code == 200
    with closing(db.connect()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM prefs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM servers_linked").fetchone()[0] == 0
        conn.execute("UPDATE accounts SET deleted_at = '2000-01-01T00:00:00.000Z'")
        assert accounts.purge_deleted(conn, 30) == 1
        conn.commit()


def test_upgrade_to_profile(client):
    alice(client)
    with closing(db.connect()) as conn:
        conn.execute("DROP TABLE prefs")
        conn.execute("DROP TABLE servers_linked")
        conn.execute("PRAGMA user_version = 3")
        conn.commit()
    assert db.ensure_current() == ["profile"]
    assert db.ensure_current() == []
    with closing(sqlite3.connect(str(config.DB_PATH))) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION == 4
    h = bearer(desktop_tokens(client))
    assert client.put("/api/me/prefs/k", headers=h, json={"value": 1}).status_code == 200


def test_the_share_host_is_named_in_me_and_discovery(client, monkeypatch):
    alice(client)
    h = bearer(desktop_tokens(client, scope="openid"))
    assert client.get("/api/me", headers=h).json()["share_host"] == ""
    assert client.get("/.well-known/openid-configuration").json()["gamma_share_host"] == ""
    monkeypatch.setattr(config, "SHARE_HOST_URL", "https://share.example.org")
    assert client.get("/api/me", headers=h).json()["share_host"] == "https://share.example.org"
    assert client.get("/api/me").json()["share_host"] == "https://share.example.org"   # the portal session too
    assert client.get("/.well-known/openid-configuration").json()["gamma_share_host"] == "https://share.example.org"
