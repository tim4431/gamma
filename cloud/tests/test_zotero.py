"""The Zotero broker (gammacloud/zotero.py) with zotero.org faked: off until
both app credentials are set, the round trip and the signatures Zotero gets,
who may ask, the code's single use and PKCE, no key made until the server
redeems the code, the keys one server receives an hour (only a key Zotero
made counts), the per-IP limits as the broker's own page, failures at
Zotero, nothing replayable in cloud.db, the purge, the Admin page's entry
and the step-14 upgrade."""

import io
import sqlite3
import urllib.error
from contextlib import closing
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
from conftest import make_admin, register, steps_after
from test_oidc import pkce

from gammacloud import app as server
from gammacloud import config, db, hosted, oidc, zotero
from gammacloud.accounts import Problem

APP_KEY, APP_SECRET = "zotero-app-key", "zotero-app-secret"
VERIFIER = "zotero-verifier-789"
API_KEY = "AbCdEfGhIjKlMnOpQrStUvWx"
SIDECAR = "http://127.0.0.1:9001"
LAB = "https://lab.example.org"


class FakeZotero:
    """zotero.org's two OAuth endpoints, in place of ``zotero._post``: a new
    request token per request, each call's signature checked against the
    secrets Zotero would hold, and a verifier other than the one Zotero
    gave refused as Zotero refuses it (its 401, which ``_post`` makes a
    502)."""

    def __init__(self):
        self.calls = []
        self.secrets = {}   # request token -> its secret
        self.access = {"oauth_token": API_KEY, "oauth_token_secret": API_KEY, "userID": "12345",
                       "username": "alice-z"}

    def __call__(self, url, authorization):
        assert authorization.startswith("OAuth ")
        params = {k: unquote(v.strip('"')) for k, v in
                  (part.split("=", 1) for part in authorization[len("OAuth "):].split(", "))}
        self.calls.append((url, params))
        signed = {k: v for k, v in params.items() if k != "oauth_signature"}
        assert params["oauth_consumer_key"] == APP_KEY and params["oauth_signature_method"] == "HMAC-SHA1"
        assert params["oauth_version"] == "1.0" and params["oauth_nonce"] and params["oauth_timestamp"].isdigit()
        if url == "https://www.zotero.org/oauth/request":
            assert params["oauth_callback"] == "http://testserver/zotero/callback"
            assert params["oauth_signature"] == zotero.signature("POST", url, signed, APP_SECRET)
            token = f"req-token-{len(self.secrets)}"
            self.secrets[token] = f"req-secret-{len(self.secrets)}"
            return {"oauth_token": token, "oauth_token_secret": self.secrets[token], "oauth_callback_confirmed": "true"}
        assert url == "https://www.zotero.org/oauth/access"
        assert params["oauth_signature"] == zotero.signature("POST", url, signed, APP_SECRET,
                                                             self.secrets[params["oauth_token"]])
        if params["oauth_verifier"] != VERIFIER:
            raise Problem(502, "Zotero did not accept the request. Start again from Gamma.")
        return self.access


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(config, "ZOTERO_CLIENT_KEY", APP_KEY)
    monkeypatch.setattr(config, "ZOTERO_CLIENT_SECRET", APP_SECRET)


@pytest.fixture
def fake(monkeypatch):
    fake = FakeZotero()
    monkeypatch.setattr(zotero, "_post", fake)
    return fake


def ask(client, server=SIDECAR, state="st-1"):
    """The browser at the connect page as a Gamma server sends it:
    (response, verifier)."""
    verifier, challenge = pkce()
    r = client.get("/zotero/connect", params={"server": server, "state": state, "code_challenge": challenge},
                   follow_redirects=False)
    return r, verifier


def token_of(r):
    """The request token the browser carries to Zotero's authorize page."""
    assert r.status_code == 302, r.text
    return parse_qs(urlsplit(r.headers["location"]).query)["oauth_token"][0]


def come_back(client, **params):
    """Zotero sends the browser back."""
    return client.get("/zotero/callback", params=params, follow_redirects=False)


def returned(r, server=SIDECAR):
    """The query of a redirect back to the server's Zotero callback."""
    assert r.status_code == 302, r.text
    to = urlsplit(r.headers["location"])
    assert f"{to.scheme}://{to.netloc}{to.path}" == server + "/api/zotero/oauth/callback"
    return {k: v[0] for k, v in parse_qs(to.query).items()}


def fetch(client, code, verifier, server=SIDECAR, ip=""):
    # the server calls from its own address (``ip``, else the test client's)
    headers = {"Sec-Fetch-Site": "cross-site", **({"CF-Connecting-IP": ip} if ip else {})}
    return client.post("/api/zotero/connect/token", data={"code": code, "code_verifier": verifier, "server": server},
                       headers=headers)


def code_for(client, server=SIDECAR):
    """A whole round trip up to the code: (code, verifier)."""
    r, verifier = ask(client, server)
    q = returned(come_back(client, oauth_token=token_of(r), oauth_verifier=VERIFIER), server)
    assert q["state"] == "st-1"
    return q["code"], verifier


def dump():
    with closing(db.connect()) as conn:
        return "\n".join(conn.iterdump())


def test_off_until_both_app_credentials_are_set(client, monkeypatch, fake):
    for key, secret in (("", ""), (APP_KEY, ""), ("", APP_SECRET)):
        monkeypatch.setattr(config, "ZOTERO_CLIENT_KEY", key)
        monkeypatch.setattr(config, "ZOTERO_CLIENT_SECRET", secret)
        r, verifier = ask(client)
        assert r.status_code == 404 and "Paste a Zotero API key" in r.text
        assert come_back(client, oauth_token="req-token-0", oauth_verifier=VERIFIER).status_code == 404
        assert fetch(client, "code", verifier).status_code == 404
        doc = client.get("/.well-known/openid-configuration").json()
        assert "gamma_zotero_connect_endpoint" not in doc and "gamma_zotero_token_endpoint" not in doc
    assert fake.calls == []


def test_discovery_names_the_broker_when_set_up(client):
    doc = client.get("/.well-known/openid-configuration").json()
    assert doc["gamma_zotero_connect_endpoint"] == "http://testserver/zotero/connect"
    assert doc["gamma_zotero_token_endpoint"] == "http://testserver/api/zotero/connect/token"


def test_connecting_zotero_from_a_desktop_sidecar(client, fake):
    r, verifier = ask(client)
    assert r.status_code == 302, r.text
    to = urlsplit(r.headers["location"])
    assert f"{to.scheme}://{to.netloc}{to.path}" == "https://www.zotero.org/oauth/authorize"
    assert {k: v[0] for k, v in parse_qs(to.query).items()} == {
        "oauth_token": "req-token-0", "library_access": "1", "notes_access": "1", "write_access": "1",
        "all_groups": "write", "name": "Gamma desktop app"}
    q = returned(come_back(client, oauth_token="req-token-0", oauth_verifier=VERIFIER))
    assert q["state"] == "st-1" and q["code"]
    assert [url.rsplit("/", 1)[1] for url, _ in fake.calls] == ["request"]   # no key made yet
    # the request is spent: Zotero's callback works once
    again = come_back(client, oauth_token="req-token-0", oauth_verifier=VERIFIER)
    assert again.status_code == 400 and "expired or was already used" in again.text
    r = fetch(client, q["code"], verifier)
    assert r.status_code == 200, r.text
    assert r.json() == {"api_key": API_KEY, "user_id": 12345, "username": "alice-z"}
    assert r.headers["cache-control"] == "no-store"
    assert [url.rsplit("/", 1)[1] for url, _ in fake.calls] == ["request", "access"]   # the key, made now
    assert fetch(client, q["code"], verifier).status_code == 400          # the code works once
    with closing(db.connect()) as conn:
        audit = conn.execute("SELECT * FROM audit WHERE event = 'zotero.connect'").fetchall()
    assert [(a["actor"], a["detail"]) for a in audit] == [("zotero:12345", SIDECAR)]


def test_the_key_is_named_after_a_public_server(client, fake):
    with closing(db.connect()) as conn:
        oidc.create_client(conn, name="Lab", kind="server", redirect_uris=[LAB + "/api/auth/cloud/callback"])
        conn.commit()
    r, _ = ask(client, LAB)
    assert parse_qs(urlsplit(r.headers["location"]).query)["name"] == ["Gamma at lab.example.org"]


def test_a_code_works_once_for_its_own_server_and_verifier(client, fake):
    assert fetch(client, "no-such-code", pkce()[0]).status_code == 400
    code, verifier = code_for(client)
    assert fetch(client, code, pkce()[0]).status_code == 400              # another verifier
    assert fetch(client, code, verifier).status_code == 400               # spent by the wrong try
    code, verifier = code_for(client)
    assert fetch(client, code, verifier, server="http://127.0.0.1:9002").status_code == 400
    code, verifier = code_for(client)
    with closing(db.connect()) as conn:
        conn.execute("UPDATE zotero_codes SET expires_at = ?", (db.after(-1),))
        conn.commit()
    assert fetch(client, code, verifier).status_code == 400
    assert "access" not in [url.rsplit("/", 1)[1] for url, _ in fake.calls]   # no wrong try made a key
    code, verifier = code_for(client)
    assert fetch(client, code, verifier, server=SIDECAR + "/").status_code == 200   # the same address, normalized
    with closing(db.connect()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM zotero_codes").fetchone()[0] == 0


def test_only_a_known_gamma_server_may_ask(client, fake, monkeypatch):
    r, _ = ask(client, "https://evil.example.org")
    assert r.status_code == 403 and "which https://evil.example.org is not" in r.text
    assert fake.calls == []                                               # zotero.org is not asked
    for origin in ("http://localhost:5000", "http://[::1]:9001"):        # every sidecar is on loopback
        assert ask(client, origin)[0].status_code == 302, origin
    # a server connected to Gamma Cloud: its client's redirect origin
    with closing(db.connect()) as conn:
        oidc.create_client(conn, name="Lab", kind="server", redirect_uris=[LAB + "/api/auth/cloud/callback"])
        conn.commit()
    assert ask(client, LAB)[0].status_code == 302
    assert ask(client, "https://lab.example.org:8443")[0].status_code == 403    # another origin on that host
    assert ask(client, "https://sub.lab.example.org")[0].status_code == 403
    # a hosted server that exists, even before its client does
    monkeypatch.setattr(config, "HOSTED_DOMAIN", "gammapdf.test")
    account = register(client)
    with closing(db.connect()) as conn:
        conn.execute("INSERT INTO hosted_servers (id, account_id, label, state, state_changed_at, created_at) "
                     "VALUES ('s1', ?, 'alice', 'provisioning', ?, ?)", (account["id"], db.now(), db.now()))
        conn.commit()
    assert ask(client, hosted.url_of("alice"))[0].status_code == 302
    with closing(db.connect()) as conn:
        conn.execute("UPDATE hosted_servers SET state = 'deleted'")
        conn.commit()
    assert ask(client, hosted.url_of("alice"))[0].status_code == 403


def _visit(client, server, ip, state="s", challenge=""):
    """A browser at ``ip`` at the connect page for ``server``."""
    return client.get("/zotero/connect",
                      params={"server": server, "state": state, "code_challenge": challenge or pkce()[1]},
                      headers={"CF-Connecting-IP": ip}, follow_redirects=False)


def _approve(client, server, ip, oauth_verifier=VERIFIER):
    """A browser at ``ip`` goes to Zotero for ``server`` and comes back
    with ``oauth_verifier`` (Zotero's own when a person approved there):
    the broker's answer to the callback, and the PKCE verifier the code is
    redeemed with."""
    verifier, challenge = pkce()
    token = token_of(_visit(client, server, ip, challenge=challenge))
    r = client.get("/zotero/callback", params={"oauth_token": token, "oauth_verifier": oauth_verifier},
                   headers={"CF-Connecting-IP": ip}, follow_redirects=False)
    return r, verifier


def _connect(client, server, ip):
    """A person at ``ip`` approves at Zotero for ``server``, and the server
    redeems the code: the token endpoint's answer."""
    r, verifier = _approve(client, server, ip)
    return fetch(client, returned(r, server)["code"], verifier, server)


def test_visits_that_never_come_back_lock_no_server_out(client, fake):
    with closing(db.connect()) as conn:
        oidc.create_client(conn, name="Lab", kind="server", redirect_uris=[LAB + "/api/auth/cloud/callback"])
        conn.commit()
    # one anonymous address, a made-up state, never at Zotero: it uses up only its own limit
    for _ in range(30):
        assert _visit(client, LAB, "198.51.100.66", "x").status_code == 302
    r = _visit(client, LAB, "198.51.100.66", "x")
    assert r.status_code == 429 and r.headers["content-type"].startswith("text/html")   # the broker's page, not JSON
    assert "Cannot connect Zotero" in r.text and "Too many attempts" in r.text and int(r.headers["retry-after"]) > 0
    assert r.headers["cache-control"] == "no-store"
    # a person of that server, a moment later, gets their key
    assert _connect(client, LAB, "203.0.113.7").status_code == 200


def test_approvals_zotero_never_made_lock_no_server_out(client, fake):
    with closing(db.connect()) as conn:
        oidc.create_client(conn, name="Lab", kind="server", redirect_uris=[LAB + "/api/auth/cloud/callback"])
        conn.commit()
    # one anonymous address, never at Zotero, as often as its limits allow: it reads the
    # request token off the redirect, calls back with a made-up verifier, redeems the code
    for _ in range(30):
        r, verifier = _approve(client, LAB, "198.51.100.66", oauth_verifier="made-up")
        r = fetch(client, returned(r, LAB)["code"], verifier, LAB, ip="198.51.100.66")
        assert r.status_code == 502 and "Zotero did not accept the request" in r.json()["detail"]
    assert _visit(client, LAB, "198.51.100.66").status_code == 429      # its own limit is spent
    assert sum(url.endswith("/oauth/access") for url, _ in fake.calls) == 30   # each refused at Zotero
    # a person of that server, a moment later, gets their key
    r = _connect(client, LAB, "203.0.113.7")
    assert r.status_code == 200 and r.json()["api_key"] == API_KEY


def test_one_server_receives_only_so_many_keys_an_hour(client, fake, monkeypatch):
    from gammacloud.routers import zotero as routes
    monkeypatch.setattr(routes, "PER_SERVER_HOURLY", 3)
    share_host = "https://app.gammapdf.test"
    with closing(db.connect()) as conn:
        oidc.create_client(conn, name="Lab", kind="server", redirect_uris=[LAB + "/api/auth/cloud/callback"])
        oidc.create_client(conn, name="Share host", kind="share-host",
                           redirect_uris=[share_host + "/api/auth/cloud/callback"])
        conn.commit()
    for i in range(3):   # each person from an address of their own: only the limit per server is at work
        assert _connect(client, LAB, f"203.0.113.{i}").status_code == 200
    # the next one gets to Zotero and back, but the server is refused before Zotero makes a key
    r, verifier = _approve(client, LAB, "203.0.113.50")
    refused = fetch(client, returned(r, LAB)["code"], verifier, LAB)
    assert refused.status_code == 429 and int(refused.headers["retry-after"]) > 3500
    assert refused.json()["detail"].startswith(
        "lab.example.org has received as many Zotero connections through Gamma Cloud as it may in an hour.")
    assert sum(url.endswith("/oauth/access") for url, _ in fake.calls) == 3
    with closing(db.connect()) as conn:   # the refused code is spent
        assert conn.execute("SELECT COUNT(*) FROM zotero_codes").fetchone()[0] == 0
    # visiting, and declining at Zotero, still work: neither makes a key
    token = token_of(_visit(client, LAB, "203.0.113.51"))
    assert returned(come_back(client, oauth_token=token), LAB) == {"error": "access_denied", "state": "s"}

    # no cap for a sidecar (the person's own computer) or for a server that runs Gamma Cloud's own code
    monkeypatch.setattr(config, "HOSTED_DOMAIN", "gammapdf.test")
    account = register(client)
    with closing(db.connect()) as conn:
        conn.execute("INSERT INTO hosted_servers (id, account_id, label, state, state_changed_at, created_at) "
                     "VALUES ('s1', ?, 'alice', 'running', ?, ?)", (account["id"], db.now(), db.now()))
        conn.commit()
    for origin in (SIDECAR, share_host, hosted.url_of("alice")):
        for i in range(4):
            assert _connect(client, origin, f"198.51.100.{i}").status_code == 200, origin


def test_the_connect_page_refuses_a_bad_request(client, fake):
    _, challenge = pkce()
    for params in ({"server": "http://lab.example.org", "state": "s", "code_challenge": challenge},
                   {"server": SIDECAR + "/gamma", "state": "s", "code_challenge": challenge},
                   {"server": "", "state": "s", "code_challenge": challenge},
                   {"server": SIDECAR, "state": "s", "code_challenge": "short"},
                   {"server": SIDECAR, "state": "", "code_challenge": challenge},
                   {"server": SIDECAR, "state": "s" * 513, "code_challenge": challenge}):
        r = client.get("/zotero/connect", params=params, follow_redirects=False)
        assert r.status_code == 400 and "Cannot connect Zotero" in r.text, params
    assert fake.calls == []


def test_refused_at_zotero_goes_back_with_access_denied(client, fake):
    token = token_of(ask(client)[0])
    assert returned(come_back(client, oauth_token=token)) == {"error": "access_denied", "state": "st-1"}
    token = token_of(ask(client)[0])
    assert returned(come_back(client, denied=token)) == {"error": "access_denied", "state": "st-1"}
    assert [url.rsplit("/", 1)[1] for url, _ in fake.calls] == ["request", "request"]    # no key asked for
    with closing(db.connect()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM zotero_requests").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM zotero_codes").fetchone()[0] == 0


def test_an_unknown_or_expired_request_shows_a_page(client, fake):
    r = come_back(client, oauth_token="never-issued", oauth_verifier=VERIFIER)
    assert r.status_code == 400 and "Start again from Gamma" in r.text
    assert come_back(client).status_code == 400
    token = token_of(ask(client)[0])
    with closing(db.connect()) as conn:
        conn.execute("UPDATE zotero_requests SET expires_at = ?", (db.after(-1),))
        conn.commit()
    assert come_back(client, oauth_token=token, oauth_verifier=VERIFIER).status_code == 400
    assert [url.rsplit("/", 1)[1] for url, _ in fake.calls] == ["request"]


def test_when_zotero_fails(client, fake, monkeypatch):
    def down(url, authorization):
        raise Problem(502, "We could not reach Zotero. Try again in a moment.")

    monkeypatch.setattr(zotero, "_post", down)
    r, _ = ask(client)
    assert r.status_code == 502 and "could not reach Zotero" in r.text
    # at the access step, which the server's redemption makes, the server gets the error
    monkeypatch.setattr(zotero, "_post", fake)
    code, verifier = code_for(client)
    monkeypatch.setattr(zotero, "_post", down)
    r = fetch(client, code, verifier)
    assert r.status_code == 502 and r.json() == {"detail": "We could not reach Zotero. Try again in a moment."}
    # an answer without a key is a failure too
    monkeypatch.setattr(zotero, "_post", fake)
    fake.access = {"oauth_token": API_KEY}
    code, verifier = code_for(client)
    r = fetch(client, code, verifier)
    assert r.status_code == 502 and "without an API key" in r.json()["detail"]
    with closing(db.connect()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit WHERE event = 'zotero.connect'").fetchone()[0] == 0


def test_an_approval_its_server_never_redeems_makes_no_key(client, fake):
    # the server could not finish (its own wait ran out, the account is gone,
    # the person did not confirm): the code expires unredeemed
    code_for(client)
    with closing(db.connect()) as conn:
        conn.execute("UPDATE zotero_codes SET expires_at = ?", (db.after(-1),))
        conn.commit()
    server.purge()
    assert [url.rsplit("/", 1)[1] for url, _ in fake.calls] == ["request"]
    # and the broker never sends back a browser the server already gave up on
    assert config.ZOTERO_REQUEST_TTL < 600   # backend/gamma/routers/zotero.py PENDING_TTL
    assert config.ZOTERO_CODE_TTL > 240      # its CONFIRM_TTL: a confirmed code is still good


def test_cloud_db_holds_nothing_replayable(client, fake):
    r, verifier = ask(client)
    token = token_of(r)
    held = dump()
    assert 'INSERT INTO "zotero_requests"' in held
    assert token not in held and fake.secrets[token] not in held
    q = returned(come_back(client, oauth_token=token, oauth_verifier=VERIFIER))
    held = dump()
    assert 'INSERT INTO "zotero_codes"' in held
    for secret in (q["code"], token, fake.secrets[token], VERIFIER):
        assert secret not in held
    with closing(db.connect()) as conn:
        row = conn.execute("SELECT * FROM zotero_codes").fetchone()
    assert row["code_hash"] == db.token_hash(q["code"]) and row["server"] == SIDECAR
    assert fetch(client, q["code"], verifier).json()["api_key"] == API_KEY
    assert API_KEY not in dump()


def test_the_hourly_purge_drops_what_expired(client, fake):
    code_for(client)
    ask(client)
    with closing(db.connect()) as conn:
        conn.execute("UPDATE zotero_codes SET expires_at = ?", (db.after(-1),))
        conn.commit()
    server.purge()
    with closing(db.connect()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM zotero_codes").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM zotero_requests").fetchone()[0] == 1   # still in time
        conn.execute("UPDATE zotero_requests SET expires_at = ?", (db.after(-1),))
        conn.commit()
    server.purge()
    with closing(db.connect()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM zotero_requests").fetchone()[0] == 0


def test_the_admin_page_says_whether_zotero_is_set_up(client, monkeypatch):
    register(client)
    make_admin("alice")

    def item():
        groups = client.get("/api/admin/config").json()["groups"]
        return next(i for g in groups if g["name"] == "Zotero" for i in g["items"])

    assert item()["state"] == "on" and "note" not in item()
    assert APP_SECRET not in client.get("/api/admin/config").text
    monkeypatch.setattr(config, "ZOTERO_CLIENT_SECRET", "")
    assert item() == {"name": "Connect for Gamma servers", "env": "GAMMA_CLOUD_ZOTERO_CLIENT_KEY + _SECRET",
                      "state": "off", "note": "needs both the client key and the secret"}
    monkeypatch.setattr(config, "ZOTERO_CLIENT_KEY", "")
    assert "pasted API key" in item()["note"]


def test_the_signature_matches_a_published_example():
    # The worked example of an HMAC-SHA1 signature in Twitter's OAuth 1.0a
    # documentation ("Creating a signature").
    params = {"status": "Hello Ladies + Gentlemen, a signed OAuth request!", "include_entities": "true",
              "oauth_consumer_key": "xvz1evFS4wEEPTGEFPHBog",
              "oauth_nonce": "kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg",
              "oauth_signature_method": "HMAC-SHA1", "oauth_timestamp": "1318622958",
              "oauth_token": "370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb", "oauth_version": "1.0"}
    assert zotero.signature("POST", "https://api.twitter.com/1.1/statuses/update.json", params,
                            "kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw",
                            "LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE") == "hCtSmYh+iHYCEqBWrE7C7hYmtUk="


def test_the_wire(monkeypatch):
    seen = {}

    class Answer(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def urlopen(req, timeout):
        seen.update(url=req.full_url, method=req.get_method(), agent=req.get_header("User-agent"),
                    auth=req.get_header("Authorization"), timeout=timeout)
        return Answer(b"oauth_token=t&oauth_token_secret=s&oauth_callback_confirmed=true")

    monkeypatch.setattr(zotero.urllib.request, "urlopen", urlopen)
    assert zotero.request_token() == ("t", "s")
    assert seen["url"] == "https://www.zotero.org/oauth/request" and seen["method"] == "POST"
    assert seen["agent"] == "gamma-cloud" and seen["timeout"] == 15
    assert seen["auth"].startswith('OAuth oauth_consumer_key="zotero-app-key", ')
    assert 'oauth_callback="http%3A%2F%2Ftestserver%2Fzotero%2Fcallback"' in seen["auth"]

    for error in (urllib.error.URLError("down"),
                  urllib.error.HTTPError("https://www.zotero.org/oauth/request", 401, "no", {}, io.BytesIO())):
        def failing(req, timeout, error=error):
            raise error
        monkeypatch.setattr(zotero.urllib.request, "urlopen", failing)
        with pytest.raises(Problem) as e:
            zotero.request_token()
        assert e.value.status == 502


def test_step_14_adds_the_broker_tables(client):
    with closing(db.connect()) as conn:
        conn.execute("DROP TABLE zotero_requests")
        conn.execute("DROP TABLE zotero_codes")
        conn.execute("PRAGMA user_version = 13")
        conn.commit()
    assert steps_after(13)[0] == "zotero"  # later steps follow
    assert db.ensure_current() == steps_after(13)
    assert db.ensure_current() == []
    with closing(sqlite3.connect(str(config.DB_PATH))) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        db._step_zotero(conn)  # re-runnable, as every step must be
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"zotero_requests", "zotero_codes"} <= tables
