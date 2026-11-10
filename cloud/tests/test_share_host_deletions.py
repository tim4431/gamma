"""The deleted accounts the shared server asks for (accounts.deletions,
routers/share_host.py): an account deleted in the portal is listed for
the share host's client and no other, a restored one is left out until it
is deleted again, a purged one stays listed, and the list pages by its
cursor."""

import base64
from contextlib import closing

from conftest import register
from test_profile import SERVER_CALLBACK

from gammacloud import accounts, db, oidc

PASSWORD = "correct horse battery"


def basic(client_id, secret):
    return {"Authorization": "Basic " + base64.b64encode(f"{client_id}:{secret}".encode()).decode()}


def new_client(kind):
    with closing(db.connect()) as conn:
        pair = oidc.create_client(conn, name=f"The {kind}", kind=kind, redirect_uris=[SERVER_CALLBACK])
        conn.commit()
    return pair


def listed(client, headers, after=0):
    r = client.get("/api/share-host/deletions", params={"after": after}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_the_share_host_learns_of_each_deleted_account(client):
    share = basic(*new_client("share-host"))
    bob = register(client, "bob")["id"]
    client.cookies.clear()
    alice = register(client, "alice")["id"]
    start = listed(client, share)
    assert start["deletions"] == [] and start["next"] > 0
    assert client.post("/api/me/delete", json={"password": PASSWORD}).status_code == 200
    out = listed(client, share)
    assert [d["account_id"] for d in out["deletions"]] == [alice]
    assert out["deletions"][0]["deleted_at"] and out["next"] >= out["deletions"][0]["id"]
    # from its cursor: nothing new
    assert listed(client, share, out["next"]) == {"deletions": [], "next": out["next"]}
    # an account restored before the share host asked is not listed; deleted again, it is
    with closing(db.connect()) as conn:
        accounts.restore(conn, alice, "test")
        conn.commit()
    assert listed(client, share)["deletions"] == []
    with closing(db.connect()) as conn:
        accounts.delete(conn, alice, "test")
        accounts.delete(conn, bob, "test")
        conn.commit()
    again = listed(client, share, out["next"])
    assert [d["account_id"] for d in again["deletions"]] == [alice, bob]
    # purged, the deletion stays listed: a share host that was down still finds it
    with closing(db.connect()) as conn:
        accounts.purge(conn, alice)
        conn.commit()
    assert [d["account_id"] for d in listed(client, share, out["next"])["deletions"]] == [alice, bob]


def test_the_list_pages_by_its_cursor(client):
    share = basic(*new_client("share-host"))
    ids = []
    with closing(db.connect()) as conn:
        for n in range(5):
            conn.execute("INSERT INTO accounts (id, email, username, created_at, deleted_at) "
                         "VALUES (?, ?, ?, ?, ?)", (f"acc{n}", f"p{n}@example.org", f"p{n}", db.now(), db.now()))
            db.audit(conn, "account.delete", f"acc{n}", f"acc{n}")
            ids.append(f"acc{n}")
        first = accounts.deletions(conn, 0, limit=2)
        assert [d["account_id"] for d in first["deletions"]] == ids[:2]
        second = accounts.deletions(conn, first["next"], limit=2)
        assert [d["account_id"] for d in second["deletions"]] == ids[2:4]
        third = accounts.deletions(conn, second["next"], limit=2)
        assert [d["account_id"] for d in third["deletions"]] == ids[4:]
        assert third["next"] == conn.execute("SELECT MAX(id) FROM audit").fetchone()[0]


def test_only_the_share_hosts_client_may_ask(client):
    share_id, _ = new_client("share-host")
    container = basic(*new_client("container"))
    assert client.get("/api/share-host/deletions").status_code == 401
    assert client.get("/api/share-host/deletions", headers=basic(share_id, "wrong")).status_code == 401
    assert client.get("/api/share-host/deletions", headers={"Authorization": "Basic !!"}).status_code == 401
    assert client.get("/api/share-host/deletions", headers=container).status_code == 403
    # the desktop client is public: it proves nothing, and is no share host
    from gammacloud import config
    assert client.get("/api/share-host/deletions", headers=basic(config.DESKTOP_CLIENT_ID, "")).status_code == 403
    assert client.get("/api/share-host/deletions", params={"after": -1},
                      headers=basic(*new_client("share-host"))).status_code == 422


def test_the_settings_page_says_the_shared_library_goes(client, monkeypatch):
    from gammacloud import config
    register(client, "carol")
    monkeypatch.setattr(config, "APP_URL", "https://app.gammapdf.test")
    page = client.get("/settings").text
    assert ("Your library and published pages on the shared server, app.gammapdf.test, "
            "are deleted within about an hour; other Gamma servers keep their data.") in page
    assert 'SHARED = "app.gammapdf.test"' in page and "are deleted too." in page   # the confirm says it again
    # without a shared server there is none to name
    monkeypatch.setattr(config, "APP_URL", "")
    page = client.get("/settings").text
    assert "Gamma servers keep their data." in page and "shared server" not in page and 'SHARED = ""' in page
