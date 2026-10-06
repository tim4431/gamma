from contextlib import closing

from conftest import invite, make_admin, register
from fastapi.testclient import TestClient

from gammacloud import accounts, config, db, mail


def test_admin_only(client):
    register(client)
    assert client.get("/api/admin/accounts").status_code == 403
    make_admin("alice")
    r = client.get("/api/admin/accounts")
    assert r.status_code == 200 and r.json()["total"] == 1


def test_accounts_admin_flow(client):
    register(client, "alice")
    make_admin("alice")
    with TestClient(client.app, base_url="http://testserver") as other_browser:
        bob = register(other_browser, "bob")
    r = client.get("/api/admin/accounts", params={"q": "bob"})
    assert [a["username"] for a in r.json()["accounts"]] == ["bob"]
    r = client.patch(f"/api/admin/accounts/{bob['id']}", json={"plan": "pro", "verified": True, "username": "robert"})
    assert r.status_code == 200 and r.json()["account"]["plan"] == "pro" and r.json()["account"]["email_verified"]
    assert r.json()["account"]["username"] == "robert"
    assert client.patch(f"/api/admin/accounts/{bob['id']}", json={"username": "alice"}).status_code == 409
    assert client.patch(f"/api/admin/accounts/{bob['id']}", json={"plan": "gold"}).status_code == 400
    r = client.get(f"/api/admin/accounts/{bob['id']}")
    assert r.status_code == 200 and any(a["event"] == "account.plan" for a in r.json()["audit"])
    n = len(mail.outbox)
    assert client.post(f"/api/admin/accounts/{bob['id']}/resend-verify").json()["already"] is True
    assert len(mail.outbox) == n
    me = client.get("/api/me").json()["account"]
    assert client.patch(f"/api/admin/accounts/{me['id']}", json={"is_admin": False}).status_code == 400
    assert client.post(f"/api/admin/accounts/{me['id']}/delete").status_code == 400
    assert client.post(f"/api/admin/accounts/{bob['id']}/delete").status_code == 200
    assert client.get(f"/api/admin/accounts/{bob['id']}").json()["account"]["deleted_at"]
    # restore brings the row back; purge only takes a deleted account and frees the name and address
    assert client.post(f"/api/admin/accounts/{bob['id']}/restore").json()["account"]["username"] == "robert"
    assert client.post(f"/api/admin/accounts/{bob['id']}/restore").status_code == 409
    assert client.post(f"/api/admin/accounts/{bob['id']}/purge").status_code == 409
    assert client.post(f"/api/admin/accounts/{bob['id']}/delete").status_code == 200
    assert client.post(f"/api/admin/accounts/{bob['id']}/purge").status_code == 200
    assert client.get(f"/api/admin/accounts/{bob['id']}").status_code == 404
    assert client.post("/api/admin/accounts/nope/restore").status_code == 404
    with closing(db.connect()) as conn:
        accounts.create(conn, email="bob@example.org", username="robert", password=None)


def test_invites_and_clients(client):
    register(client)
    make_admin("alice")
    r = client.post("/api/admin/invites", json={"uses": 3, "plan": "plus", "note": "beta"})
    assert r.status_code == 200
    code = r.json()["invite"]["code"]
    assert any(i["code"] == code for i in client.get("/api/admin/invites").json()["invites"])
    assert client.delete(f"/api/admin/invites/{code}").status_code == 200
    assert client.delete(f"/api/admin/invites/{code}").status_code == 404
    r = client.post("/api/admin/clients", json={"name": "share host", "kind": "share-host",
                                                "redirect_uris": ["https://share.gammapdf.com/api/auth/cloud/callback"]})
    assert r.status_code == 200 and r.json()["client_id"].startswith("gc_")
    client_id = r.json()["client_id"]
    listed = client.get("/api/admin/clients").json()["clients"]
    assert listed[0]["client_id"] == client_id and "secret" not in str(listed)
    r = client.post("/api/admin/clients", json={"name": "bad", "kind": "container", "redirect_uris": ["http://x.example/cb"]})
    assert r.status_code == 400
    assert client.delete(f"/api/admin/clients/{client_id}").status_code == 200
    assert client.get("/api/admin/audit").status_code == 200


def test_an_invite_from_creation_to_spent(client):
    register(client)
    make_admin("alice")
    r = client.post("/api/admin/invites", json={"uses": 2, "plan": "plus", "note": "lab", "expires_days": 30,
                                                "grant_days": 90})
    inv = r.json()["invite"]
    assert (inv["uses_total"], inv["used"], inv["state"], inv["grant_days"]) == (2, 0, "active", 90)
    assert inv["expires_at"][:10] == db.after(30 * 86400)[:10]
    code = inv["code"]
    assert client.post("/api/admin/invites", json={"plan": "free", "grant_days": 5}).status_code == 400
    assert client.post("/api/admin/invites", json={"plan": "plus", "expires_days": 0}).status_code == 400
    # a registration with it keeps the code and dates the grant
    with TestClient(client.app, base_url="http://testserver") as other:
        bob = register(other, "bob", code=code)
    assert bob["plan"] == "plus" and bob["granted_until"][:10] == db.after(90 * 86400)[:10]
    listed = {i["code"]: i for i in client.get("/api/admin/invites").json()["invites"]}
    assert listed[code]["used"] == 1 and listed[code]["state"] == "active"
    who = client.get(f"/api/admin/invites/{code}").json()
    assert [(a["username"], a["plan"]) for a in who["accounts"]] == [("bob", "plus")]
    # turned off it is refused like an unknown code; on again it works
    assert client.patch(f"/api/admin/invites/{code}", json={"disabled": True}).json()["invite"]["state"] == "off"
    with TestClient(client.app, base_url="http://testserver") as other:
        r = other.post("/api/register", json={"email": "carol@example.org", "username": "carol",
                                              "password": "correct horse battery", "invite": code})
        assert r.status_code == 403 and "not valid" in r.json()["detail"]
    assert client.patch(f"/api/admin/invites/{code}", json={"disabled": False}).json()["invite"]["state"] == "active"
    with TestClient(client.app, base_url="http://testserver") as other:
        register(other, "carol", code=code)
    assert client.get(f"/api/admin/invites/{code}").json()["invite"]["state"] == "spent"
    events = [a["event"] for a in client.get("/api/admin/audit").json()["audit"]]
    assert "invite.disable" in events and "invite.enable" in events
    assert client.patch("/api/admin/invites/nope", json={"disabled": True}).status_code == 404
    assert client.get("/api/admin/invites/nope").status_code == 404
    page = client.get("/admin").text
    assert "Copy link" in page and "Who used it" in page and "Grant ends…" in page and "id=invdone" in page


def test_the_configuration_never_shows_a_secret(client, monkeypatch):
    for key, value in (("STRIPE_SECRET", "sk_live_hidden1"), ("STRIPE_WEBHOOK_SECRET", "whsec_hidden2"),
                       ("SMTP_PASSWORD", "hidden3"), ("SMTP_USER", "hidden4"), ("GOOGLE_CLIENT_ID", "gid"),
                       ("GOOGLE_CLIENT_SECRET", "hidden5"), ("GITHUB_CLIENT_ID", ""),
                       ("GITHUB_CLIENT_SECRET", "hidden6")):
        monkeypatch.setattr(config, key, value)
    register(client)
    assert client.get("/api/admin/config").status_code == 403
    make_admin("alice")
    r = client.get("/api/admin/config")
    assert r.status_code == 200 and "hidden" not in r.text
    items = {(g["name"], i["name"]): i for g in r.json()["groups"] for i in g["items"]}
    assert items["Stripe", "Billing"]["state"] == "on" and items["Stripe", "Mode"]["value"] == "live"
    assert items["Stripe", "Webhook secret"] == {"name": "Webhook secret", "env": "GAMMA_CLOUD_STRIPE_WEBHOOK_SECRET",
                                                 "state": "set"}
    assert items["Mail", "SMTP password"]["state"] == "set" and "value" not in items["Mail", "SMTP password"]
    assert items["Stripe", "Pro yearly price"]["env"] == "GAMMA_CLOUD_STRIPE_PRICE_PRO_YEAR"
    public = items["Server", "Public URL"]
    assert public["value"] == "http://testserver" and "local run" in public["note"]
    assert items["Server", "Schema version"]["value"] == str(db.SCHEMA_VERSION)
    assert items["Mail", "Backend"]["value"] == "memory"
    assert items["Sign-in", "Google"]["state"] == "on"
    assert items["Sign-in", "GitHub"] == {"name": "GitHub", "env": "GAMMA_CLOUD_GITHUB_CLIENT_ID + _SECRET",
                                          "state": "off", "note": "needs both the client id and the secret"}


def test_send_test_mail(client, monkeypatch):
    register(client)
    assert client.post("/api/admin/test-mail").status_code == 403
    make_admin("alice")
    mail.outbox.clear()
    r = client.post("/api/admin/test-mail")
    assert r.status_code == 200 and r.json()["to"] == "alice@example.org" and "sends nothing" in r.json()["detail"]
    assert mail.outbox[-1]["to"] == "alice@example.org" and mail.outbox[-1]["subject"] == "Gamma Cloud test message"
    monkeypatch.setattr(config, "MAIL_BACKEND", "console")
    assert "console" in client.post("/api/admin/test-mail").json()["detail"]
    monkeypatch.setattr(config, "MAIL_BACKEND", "smtp")
    monkeypatch.setattr(config, "SMTP_HOST", "")
    r = client.post("/api/admin/test-mail")
    assert r.status_code == 502 and "GAMMA_CLOUD_SMTP_HOST is not set" in r.json()["detail"]
    assert [client.post("/api/admin/test-mail").status_code for _ in range(3)] == [502, 502, 429]


def test_bearer_token_cannot_admin(client):
    """A Gamma server's access token must never reach the admin API."""
    from test_oidc import pkce, signed_in_code, CALLBACK
    from conftest import verify
    from gammacloud import config
    register(client)
    make_admin("alice")
    verify(client)
    verifier, challenge = pkce()
    code = signed_in_code(client, challenge)
    tokens = client.post("/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": CALLBACK,
                                         "client_id": config.DESKTOP_CLIENT_ID, "code_verifier": verifier}).json()
    client.post("/api/logout")
    headers = {"Authorization": "Bearer " + tokens["access_token"]}
    assert client.get("/api/me", headers=headers).status_code == 200
    assert client.get("/api/admin/accounts", headers=headers).status_code == 401
    assert client.post("/api/me/password", json={"current": "x", "new": "yyyyyyyyy"}, headers=headers).status_code == 401
    assert invite()  # helper still works
