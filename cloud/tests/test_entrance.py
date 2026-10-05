"""The entrance (gammacloud/entrance.py): where the authorize step sends a
signed-in person when the shared server asks, which sign-ins skip the
confirm card, the chooser, the portal's /open, a hosted server's address
and a client's second redirect URI."""

from contextlib import closing
from urllib.parse import parse_qs, urlsplit

import pytest
from conftest import register, verify
from fastapi.testclient import TestClient
from test_oidc import authorize_params, pkce

from gammacloud import config, db, entrance, hosted, oidc

DOMAIN = "gammapdf.test"
APP = "https://app.gammapdf.test"
APP_CALLBACK = APP + "/api/auth/cloud/callback"


@pytest.fixture
def cloud(monkeypatch):
    """Hosting and a shared server; answers the shared server's client id."""
    monkeypatch.setattr(config, "HOSTED_DOMAIN", DOMAIN)
    monkeypatch.setattr(config, "APP_URL", APP)
    with closing(db.connect()) as conn:
        client_id, _ = oidc.create_client(conn, name="Gamma Cloud", kind="share-host", redirect_uris=[APP_CALLBACK])
        conn.commit()
    return client_id


def signed_up(client, username="alice"):
    account = register(client, username)
    verify(client, username)
    return account


def ask(client, client_id, redirect):
    """GET /authorize as the server at ``redirect`` would send the browser."""
    _, challenge = pkce()
    scope = "openid email profile offline_access" + (" prefs" if client_id else "")   # what each kind of server asks
    return client.get("/authorize", params=authorize_params(challenge, client_id=client_id, redirect=redirect,
                                                            scope=scope), follow_redirects=False)


def a_server(account_id, label, state="running"):
    """A hosted server of ``account_id`` and its OIDC client; returns
    (client id, callback)."""
    callback = f"{hosted.url_of(label)}/api/auth/cloud/callback"
    with closing(db.connect()) as conn:
        client_id, _ = oidc.create_client(conn, name=f"Hosted: {label}", kind="container", redirect_uris=[callback],
                                          owner=account_id)
        conn.execute("INSERT INTO hosted_servers (id, account_id, label, client_id, state, state_changed_at, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)", (f"s_{label}", account_id, label, client_id, state, db.now(), db.now()))
        conn.commit()
    return client_id, callback


def link(account_id, url):
    """The account signed in on the server at ``url`` (its list entry)."""
    with closing(db.connect()) as conn:
        conn.execute("INSERT INTO servers_linked (account_id, url, name, linked_at, last_seen_at) VALUES (?, ?, ?, ?, ?)",
                     (account_id, url, urlsplit(url).hostname, db.now(), db.now()))
        conn.commit()


def places(account_id):
    with closing(db.connect()) as conn:
        return [(p["kind"], p["url"]) for p in entrance.destinations(conn, account_id)]


def test_a_hosted_server_answers_at_its_owners_name_with_the_suffix(client, cloud):
    assert hosted.url_of("alice") == f"https://alice-user.{DOMAIN}"
    alice = signed_up(client)
    a_server(alice["id"], "alice")
    with closing(db.connect()) as conn:
        assert hosted.is_server_url(conn, f"https://alice-user.{DOMAIN}")
        assert not hosted.is_server_url(conn, f"https://alice.{DOMAIN}")           # a service name's shape
        assert not hosted.is_server_url(conn, f"https://nobody-user.{DOMAIN}")
        assert not hosted.is_server_url(conn, f"https://alice-user.{DOMAIN}/x")
        assert not hosted.is_server_url(conn, "https://alice-user.example.org")


def test_the_shared_server_signs_its_own_people_in_with_no_card(client, cloud):
    """Free, Lite and Plus live on the shared server: asked by it, the
    account server answers the code at once."""
    alice = signed_up(client)
    assert places(alice["id"]) == [("shared", APP)]
    r = ask(client, cloud, APP_CALLBACK)
    assert r.status_code == 302 and r.headers["location"].startswith(APP_CALLBACK + "?code=")
    assert parse_qs(urlsplit(r.headers["location"]).query)["state"] == ["st-1"]
    # the desktop app and a server somebody runs keep the card
    r = ask(client, None, "http://127.0.0.1:9001/api/auth/cloud/callback")
    assert r.status_code == 200 and "Continue" in r.text
    # an unconfirmed address is refused as before, whoever asks
    with TestClient(client.app, base_url="http://testserver") as other:
        register(other, "bob")
        r = ask(other, cloud, APP_CALLBACK)
        assert r.status_code == 200 and "not\n" not in r.text and "confirmed yet" in r.text


def test_a_pro_owner_is_forwarded_to_their_own_server(client, cloud):
    alice = signed_up(client)
    own_client, own_callback = a_server(alice["id"], "alice")
    assert places(alice["id"]) == [("own", f"https://alice-user.{DOMAIN}")]
    r = ask(client, cloud, APP_CALLBACK)
    assert r.status_code == 302
    assert r.headers["location"] == f"https://alice-user.{DOMAIN}/api/auth/cloud/start?next=/"
    with closing(db.connect()) as conn:                      # the shared server's request is dropped, not left pending
        assert conn.execute("SELECT COUNT(*) FROM oauth_requests").fetchone()[0] == 0
    # there, the owner is signed in at once
    r = ask(client, own_client, own_callback)
    assert r.status_code == 302 and r.headers["location"].startswith(own_callback + "?code=")
    # a server that does not answer yet is no destination: the shared server it is
    with closing(db.connect()) as conn:
        conn.execute("UPDATE hosted_servers SET state = 'provisioning'")
        conn.commit()
    assert places(alice["id"]) == [("shared", APP)]
    assert ask(client, cloud, APP_CALLBACK).headers["location"].startswith(APP_CALLBACK)


def test_several_libraries_get_the_chooser(client, cloud):
    """An owner who also has a library on the shared server chooses; the
    shared server's row finishes the request it started."""
    alice = signed_up(client)
    a_server(alice["id"], "alice")
    link(alice["id"], APP)
    assert places(alice["id"]) == [("own", f"https://alice-user.{DOMAIN}"), ("shared", APP)]
    r = ask(client, cloud, APP_CALLBACK)
    assert r.status_code == 200 and "Where to?" in r.text and "id=stay" in r.text
    assert f"href='https://alice-user.{DOMAIN}/api/auth/cloud/start?next=/'" in r.text
    marker = 'request_id: "'
    request_id = r.text[r.text.index(marker) + len(marker):].split('"', 1)[0]
    redirect = client.post("/authorize/continue", json={"request_id": request_id}).json()["redirect"]
    assert redirect.startswith(APP_CALLBACK + "?code=")
    link(alice["id"], "https://lab.example.org")                 # a server someone runs is never listed
    assert places(alice["id"]) == [("own", f"https://alice-user.{DOMAIN}"), ("shared", APP)]


def test_a_member_of_a_hosted_server_chooses_and_is_then_signed_in_there_at_once(client, cloud):
    owner = signed_up(client, "olga")
    team_client, team_callback = a_server(owner["id"], "olga")
    team = f"https://olga-user.{DOMAIN}"
    with TestClient(client.app, base_url="http://testserver") as other:
        bob = signed_up(other, "bob")
        r = ask(other, team_client, team_callback)                     # the first visit: the card that names the server
        assert r.status_code == 200 and "Continue" in r.text and "Where to?" not in r.text
        link(bob["id"], team)
        with closing(db.connect()) as conn:                            # ... and the grant that sign-in left
            conn.execute("INSERT INTO grants (id, account_id, client_id, scope, created_at, rotated_at, last_used_at, "
                         "expires_at) VALUES ('g1', ?, ?, 'openid', ?, ?, ?, ?)",
                         (bob["id"], team_client, db.now(), db.now(), db.now(), db.after(86400)))
            conn.commit()
        assert places(bob["id"]) == [("shared", APP), ("team", team)]
        assert ask(other, team_client, team_callback).status_code == 302
        r = ask(other, cloud, APP_CALLBACK)
        assert r.status_code == 200 and "Where to?" in r.text and "olga-user.gammapdf.test" in r.text
        # signed out there (the grant revoked): the card is back
        with closing(db.connect()) as conn:
            conn.execute("UPDATE grants SET revoked_at = ?", (db.now(),))
            conn.commit()
        assert ask(other, team_client, team_callback).status_code == 200


def test_signing_in_on_the_authorize_page_follows_the_same_rule(client, cloud):
    alice = signed_up(client)
    a_server(alice["id"], "alice")
    with TestClient(client.app, base_url="http://testserver") as fresh:   # signed out: the form, then the forward
        r = ask(fresh, cloud, APP_CALLBACK)
        assert r.status_code == 200 and "Password" in r.text
        marker = 'request_id: "'
        request_id = r.text[r.text.index(marker) + len(marker):].split('"', 1)[0]
        r = fresh.post("/authorize/login", json={"request_id": request_id, "login": "alice",
                                                 "password": "correct horse battery"})
        assert r.json() == {"redirect": f"https://alice-user.{DOMAIN}/api/auth/cloud/start?next=/"}
    link(alice["id"], APP)
    with TestClient(client.app, base_url="http://testserver") as fresh:   # several libraries: back to the chooser
        r = ask(fresh, cloud, APP_CALLBACK)
        request_id = r.text[r.text.index(marker) + len(marker):].split('"', 1)[0]
        r = fresh.post("/authorize/login", json={"request_id": request_id, "login": "alice",
                                                 "password": "correct horse battery"})
        assert r.json()["redirect"] == f"/authorize/resume?request_id={request_id}"
        assert "Where to?" in fresh.get(r.json()["redirect"]).text


def test_open_gamma(client, cloud, monkeypatch):
    with TestClient(client.app, base_url="http://testserver") as anon:
        r = anon.get("/open", follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "/login?next=%2Fopen"
    alice = signed_up(client)
    r = client.get("/open", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == APP + "/api/auth/cloud/start?next=/"
    assert "href='/open'>Open Gamma" in client.get("/").text
    a_server(alice["id"], "alice")
    assert client.get("/open", follow_redirects=False).headers["location"].startswith(f"https://alice-user.{DOMAIN}/")
    link(alice["id"], APP)
    r = client.get("/open")
    assert r.status_code == 200 and "Where to?" in r.text and "id=stay" not in r.text and r.text.count("<a class=dest ") == 2
    # nowhere to go: no shared server and no server of one's own
    monkeypatch.setattr(config, "APP_URL", "")
    with closing(db.connect()) as conn:
        conn.execute("DELETE FROM hosted_servers")
        conn.commit()
    assert client.get("/open", follow_redirects=False).headers["location"] == "/"
    assert "Open Gamma" not in client.get("/").text


def test_a_client_takes_a_second_redirect_uri(client, cloud, capsys):
    """The shared server under a new name beside the old one."""
    import manage
    alice_callback = "https://share.gammapdf.test/api/auth/cloud/callback"
    signed_up(client)
    assert ask(client, cloud, alice_callback).status_code == 400          # not registered yet
    manage.main(["client-redirect", cloud, alice_callback])
    assert capsys.readouterr().out.split() == [APP_CALLBACK, alice_callback]
    assert ask(client, cloud, alice_callback).status_code == 302
    assert ask(client, cloud, APP_CALLBACK).status_code == 302
    manage.main(["client-redirect", cloud, alice_callback])               # again: nothing doubles
    assert capsys.readouterr().out.split() == [APP_CALLBACK, alice_callback]
    with pytest.raises(SystemExit):
        manage.main(["client-redirect", cloud, "http://share.gammapdf.test/cb"])
    with pytest.raises(SystemExit):
        manage.main(["client-redirect", "gc_nobody", alice_callback])
