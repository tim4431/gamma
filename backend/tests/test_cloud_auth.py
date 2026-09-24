"""Sign in with Gamma Cloud (gamma/cloud_auth.py, gamma/cloud_sync.py): the
OIDC client against a fake account server that signs real Ed25519 ID
tokens, the identity policies (refuse / claim / provision), linking a
signed-in account, the admin subject, the settings API and the CLI helpers;
then what the server does with the grant: access tokens by refresh with
rotation, the grant check and its sign-out, the preference profile sync
and the server list."""

import base64
import hashlib
import json
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from conftest import login, make_user
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi.testclient import TestClient

from gamma import cloud_auth, cloud_sync
from gamma.db import connect_users_db, get_profile, set_profile
from gamma.server_settings import _set_raw

ISSUER = "https://account.test"


def _ms_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class FakeAccountServer:
    """Answers the HTTP calls the client makes: discovery, JWKS, the token
    endpoint (code and refresh grants, rotating), revocation, and the API a
    Gamma server calls with an access token: the preference profile, the
    server list and the username lookup. ``person`` is who the next sign-in
    is; ``revoked`` the refresh tokens the client gave back; ``calls`` every
    request as (method, path); ``offline`` makes every call fail as if the
    network were down."""

    def __init__(self):
        self.key = ed25519.Ed25519PrivateKey.generate()
        self.kid = "k1"
        self.person = {"sub": "sub-ca_alice", "preferred_username": "ca_alice", "email": "ca_alice@example.org", "email_verified": True,
                       "name": "Alice", "plan": "free"}
        self.token_calls = []
        self.token_agents = []
        self.aud = None  # override the audience of the next ID token
        self.refresh = "rt-1"
        self.revoked = []
        self.revoke_fails = False
        self.calls = []
        self.offline = False
        self.live = {}        # refresh token -> subject, while its grant lives
        self.access = {}      # access token -> subject
        self.minted = 0
        self.prefs = {}       # subject -> {key: {value, updated_at}}
        self.servers = {}     # subject -> {url: name}
        self.directory = {}   # cloud username -> subject

    def _mint_access(self, subject):
        self.minted += 1
        token = f"at-{self.minted}"
        self.access[token] = subject
        return token

    def _bearer(self, headers):
        auth = (headers or {}).get("Authorization", "")
        subject = self.access.get(auth.removeprefix("Bearer "))
        if not subject:
            raise cloud_auth.CloudAuthError("invalid_token", status=401, error="invalid_token")
        return subject

    def jwk(self):
        raw = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return {"kty": "OKP", "crv": "Ed25519", "x": base64.urlsafe_b64encode(raw).rstrip(b"=").decode(),
                "kid": self.kid, "alg": "EdDSA", "use": "sig"}

    def id_token(self, client_id, nonce):
        now = int(time.time())
        claims = {"iss": ISSUER, "aud": self.aud or client_id, "iat": now, "exp": now + 600, "nonce": nonce,
                  **self.person}
        return jwt.encode(claims, self.key, algorithm="EdDSA", headers={"kid": self.kid})

    def http(self, url, data=None, headers=None, method=None, timeout=None):
        method = method or ("POST" if data is not None else "GET")
        self.calls.append((method, url.removeprefix(ISSUER)))
        if self.offline:
            raise cloud_auth.CloudAuthError("cannot reach the account server: offline")
        if url.startswith(ISSUER + "/api/"):
            return self.api(method, url.removeprefix(ISSUER), json.loads(data) if data else None, headers)
        if url == ISSUER + "/.well-known/openid-configuration":
            return {"issuer": ISSUER, "authorization_endpoint": ISSUER + "/authorize", "token_endpoint": ISSUER + "/token",
                    "jwks_uri": ISSUER + "/jwks", "revocation_endpoint": ISSUER + "/revoke"}
        if url == ISSUER + "/revoke":
            if self.revoke_fails:
                raise cloud_auth.CloudAuthError("cannot reach the account server")
            token = parse_qs(data.decode())["token"][0]
            self.revoked.append(token)
            self.live.pop(token, None)
            return {}
        if url == ISSUER + "/jwks":
            return {"keys": [self.jwk()]}
        if url == ISSUER + "/token":
            form = {k: v[0] for k, v in parse_qs(data.decode()).items()}
            self.token_calls.append(form)
            self.token_agents.append((headers or {}).get("User-Agent", ""))
            if form["grant_type"] == "refresh_token":
                subject = self.live.pop(form["refresh_token"], None)
                if not subject:
                    raise cloud_auth.CloudAuthError("invalid_grant", status=400, error="invalid_grant")
                new = f"{form['refresh_token']}+"
                self.live[new] = subject
                return {"access_token": self._mint_access(subject), "token_type": "Bearer", "expires_in": 3600,
                        "refresh_token": new, "scope": "openid email profile offline_access prefs"}
            code = json.loads(base64.urlsafe_b64decode(form["code"] + "==").decode())
            challenge = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).rstrip(b"=").decode()
            if challenge != code["challenge"] or form["redirect_uri"] != code["redirect_uri"]:
                raise cloud_auth.CloudAuthError("pkce or redirect mismatch")
            out = {"access_token": self._mint_access(self.person["sub"]), "token_type": "Bearer", "expires_in": 3600,
                   "id_token": self.id_token(form["client_id"], code["nonce"])}
            if "offline_access" in code["scope"]:
                out["refresh_token"] = self.refresh
                self.live[self.refresh] = self.person["sub"]
            return out
        raise AssertionError(f"unexpected url {url}")

    def api(self, method, path, body, headers):
        subject = self._bearer(headers)
        if path.startswith("/api/me/prefs/"):
            key = path.removeprefix("/api/me/prefs/")
            stored = self.prefs.setdefault(subject, {}).get(key)
            if method == "GET":
                if not stored:
                    raise cloud_auth.CloudAuthError("not found", status=404, body={"detail": "not found"})
                return dict(stored)
            if method == "PUT":
                ts = body.get("updated_at") or _ms_now()
                ts = min(datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                         _ms_now())
                if stored and ts < stored["updated_at"]:
                    raise cloud_auth.CloudAuthError("A newer value is stored.", status=409,
                                                    body={"detail": "A newer value is stored.", **stored})
                self.prefs[subject][key] = {"value": body["value"], "updated_at": ts}
                return {"updated_at": ts}
        if path == "/api/me/servers":
            mine = self.servers.setdefault(subject, {})
            if method == "POST":
                mine[body["url"]] = body["name"]
                return {"server": {"url": body["url"], "name": body["name"]}}
            if method == "DELETE":
                return {"ok": True, "removed": mine.pop(body["url"], None) is not None}
        if path.startswith("/api/lookup/username?u="):
            name = path.split("u=", 1)[1]
            if name not in self.directory:
                raise cloud_auth.CloudAuthError("no such account", status=404, body={"detail": "no such account"})
            return {"sub": self.directory[name], "username": name}
        raise AssertionError(f"unexpected call {method} {path}")

    def get_json(self, url, headers):
        """``workspaces._get_json``'s shape: (status, body)."""
        try:
            return 200, self.http(url, headers=headers)
        except cloud_auth.CloudAuthError as e:
            return e.status, e.body


@pytest.fixture
def cloud(monkeypatch):
    fake = FakeAccountServer()
    monkeypatch.setattr(cloud_auth, "_http", fake.http)
    # revocation runs on a thread in the server; here it runs inline so the asserts see it
    monkeypatch.setattr(cloud_auth, "revoke_later", lambda tokens: [cloud_auth.revoke_refresh(t) for t in tokens if t])
    monkeypatch.setattr(cloud_sync, "_background", lambda fn: fn())
    monkeypatch.setenv("GAMMA_CLOUD_ISSUER", ISSUER)
    monkeypatch.delenv("GAMMA_CLOUD_CLIENT_ID", raising=False)
    monkeypatch.delenv("GAMMA_CLOUD_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("GAMMA_CLOUD_POLICY", "refuse")
    monkeypatch.delenv("GAMMA_CLOUD_ADMIN_SUBJECT", raising=False)
    cloud_auth._discovery_cache.clear()
    cloud_auth._jwks_cache.clear()
    cloud_auth._access.clear()
    yield fake
    for timer in list(cloud_sync._timers.values()):
        timer.cancel()
    cloud_sync._timers.clear()
    cloud_sync._status.clear()
    cloud_auth._access.clear()
    _set_raw("cloud_server_url", "")
    with connect_users_db() as conn:
        conn.execute("DELETE FROM identities")
        conn.commit()


def browser(base_url="http://testserver"):
    from gamma.app import app
    return TestClient(app, base_url=base_url)


def start(c, **params):
    """GET /start; returns the authorize params the browser was sent with."""
    r = c.get("/api/auth/cloud/start", params=params, follow_redirects=False)
    assert r.status_code == 302, r.text
    url = urlsplit(r.headers["location"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == ISSUER + "/authorize"
    return {k: v[0] for k, v in parse_qs(url.query).items()}


def callback(c, auth, fake=None, **extra):
    """The account server sends the browser back: the "code" carries what the
    fake token endpoint needs to check PKCE."""
    code = base64.urlsafe_b64encode(json.dumps({"challenge": auth["code_challenge"], "nonce": auth.get("nonce", ""),
                                                "redirect_uri": auth["redirect_uri"], "scope": auth["scope"]}).encode()).decode().rstrip("=")
    return c.get("/api/auth/cloud/callback", params={"code": code, "state": auth["state"], **extra}, follow_redirects=False)


def error_of(r):
    assert r.status_code == 302
    return parse_qs(urlsplit(r.headers["location"]).query).get("cloud_error", [""])[0]


def test_server_config_and_start(cloud):
    c = browser()
    assert c.get("/api/server-config").json()["cloud"] == {"enabled": True, "issuer": ISSUER}
    auth = start(c, next="/?page=abc")
    assert auth["client_id"] == "gamma-desktop" and auth["code_challenge_method"] == "S256"
    assert auth["redirect_uri"] == "http://testserver/api/auth/cloud/callback"
    assert auth["scope"].split() == ["openid", "email", "profile", "offline_access", "prefs"]
    assert len(auth["code_challenge"]) == 43


def test_refuse_policy(cloud):
    c = browser()
    r = callback(c, start(c))
    assert "not linked" in error_of(r)
    assert c.get("/api/session").json()["user"] is None
    # the refused sign-in's refresh token went back: no device is left at the account server
    assert cloud.revoked == ["rt-1"]


def test_provision_policy_creates_account(cloud, monkeypatch):
    monkeypatch.setenv("GAMMA_CLOUD_POLICY", "provision")
    c = browser()
    r = callback(c, start(c, next="/?page=abc"))
    assert r.status_code == 302 and r.headers["location"] == "/?page=abc"
    s = c.get("/api/session").json()
    assert s["user"] == "ca_alice" and s["is_admin"] is False and s["default_workspace"]
    status = c.get("/api/auth/cloud/status").json()["identity"]
    assert status["username"] == "ca_alice" and status["plan"] == "free" and status["offline"] is True
    # only the cloud can sign this account in
    r = c.post("/api/login", json={"username": "ca_alice", "password": ""})
    assert r.status_code == 401
    assert c.post("/api/auth/cloud/unlink").status_code == 400  # would lock it out
    # a second sign-in finds the identity, whatever the policy says
    monkeypatch.setenv("GAMMA_CLOUD_POLICY", "refuse")
    c2 = browser()
    assert callback(c2, start(c2)).headers["location"] == "/"
    assert c2.get("/api/session").json()["user"] == "ca_alice"
    assert cloud_auth.refresh_token_of("ca_alice") == "rt-1" and cloud.revoked == []
    # a newer sign-in replaces the refresh token and gives the old one back
    cloud.refresh = "rt-2"
    c3 = browser()
    assert callback(c3, start(c3)).headers["location"] == "/"
    assert cloud_auth.refresh_token_of("ca_alice") == "rt-2" and cloud.revoked == ["rt-1"]


def test_sign_in_names_this_install(cloud, monkeypatch):
    monkeypatch.setenv("GAMMA_CLOUD_POLICY", "provision")
    cloud.person.update({"sub": "sub-ca_uma", "preferred_username": "ca_uma", "email": "ca_uma@example.org"})
    for _ in range(2):
        c = browser()
        callback(c, start(c))
    first, second = cloud.token_calls[-2:]
    assert first["device_id"] == second["device_id"] and len(first["device_id"]) >= 16  # stable per install
    assert first["device_name"]
    assert cloud.token_agents[-1].startswith("Gamma/") and "; http://testserver)" in cloud.token_agents[-1]


def test_claim_policy_links_existing_username(cloud, monkeypatch):
    make_user("ca_bob", "pw-ca_bob-123")
    cloud.person.update({"sub": "sub-ca_bob", "preferred_username": "ca_bob", "email": "ca_bob@example.org"})
    c = browser()
    assert "not linked" in error_of(callback(c, start(c)))
    monkeypatch.setenv("GAMMA_CLOUD_POLICY", "claim")
    r = callback(c, start(c))
    assert r.headers["location"] == "/"
    assert c.get("/api/session").json()["user"] == "ca_bob"
    # ca_bob has a password, so unlinking is allowed and signs the identity off
    assert c.post("/api/auth/cloud/unlink").json()["ok"] is True
    assert c.get("/api/auth/cloud/status").json()["identity"] is None
    assert cloud.revoked == ["rt-1", "rt-1"]  # the refused first try's token, then the unlinked one's
    # a different cloud account with the same username cannot claim a linked or taken name
    r = callback(c, start(c))
    assert r.headers["location"] == "/"  # ca_bob re-claims (unlinked, claim policy)
    cloud.person["sub"] = "sub-other-ca_bob"
    assert "belongs to someone else" in error_of(callback(browser(), start(browser())))


def test_claim_is_case_insensitive_when_unambiguous(cloud, monkeypatch):
    monkeypatch.setenv("GAMMA_CLOUD_POLICY", "claim")
    make_user("CA_Hank", "pw-ca_hank-123")
    cloud.person.update({"sub": "sub-ca_hank", "preferred_username": "ca_hank", "email": "ca_hank@example.org"})
    c = browser()
    assert callback(c, start(c)).headers["location"] == "/"
    assert c.get("/api/session").json()["user"] == "CA_Hank"
    # two usernames that only differ in case: nobody is claimed
    make_user("CA_Ivy", "pw-ca_ivy-123")
    make_user("ca_ivY", "pw-ca_ivy-123")
    cloud.person.update({"sub": "sub-ca_ivy", "preferred_username": "ca_ivy", "email": "ca_ivy@example.org"})
    assert "not linked" in error_of(callback(browser(), start(browser())))


def test_link_signed_in_account(cloud):
    make_user("ca_carol", "pw-ca_carol-123")
    c = login("ca_carol", "pw-ca_carol-123")
    cloud.person.update({"sub": "sub-ca_carol", "preferred_username": "ca_carol-cloud", "email": "ca_carol@example.org"})
    auth = start(c, link="1")
    r = callback(c, auth)
    assert r.headers["location"] == "/"
    status = c.get("/api/auth/cloud/status").json()["identity"]
    assert status["username"] == "ca_carol-cloud"
    # the same cloud account cannot be linked to a second local account
    make_user("ca_dave", "pw-ca_dave-123")
    d = login("ca_dave", "pw-ca_dave-123")
    assert "already linked" in error_of(callback(d, start(d, link="1")))
    # and ca_carol cannot link a second cloud account
    cloud.person["sub"] = "sub-ca_carol-2"
    assert "Unlink it first" in error_of(callback(c, start(c, link="1")))
    # an account server that cannot be reached does not stop an unlink
    cloud.revoke_fails = True
    assert c.post("/api/auth/cloud/unlink").json()["ok"] is True
    # linking needs a session
    assert browser().get("/api/auth/cloud/start", params={"link": "1"}, follow_redirects=False).status_code == 401


def test_admin_subject_provisions_admin(cloud, monkeypatch):
    monkeypatch.setenv("GAMMA_CLOUD_ADMIN_SUBJECT", "sub-owner")
    cloud.person.update({"sub": "sub-owner", "preferred_username": "owner", "email": "owner@example.org"})
    c = browser()
    assert callback(c, start(c)).headers["location"] == "/"
    s = c.get("/api/session").json()
    assert s["user"] == "owner" and s["is_admin"] is True


def test_token_checks(cloud, monkeypatch):
    monkeypatch.setenv("GAMMA_CLOUD_POLICY", "provision")
    cloud.person.update({"sub": "sub-gwen", "preferred_username": "gwen", "email": "gwen@example.org"})
    c = browser()
    cloud.person["email_verified"] = False
    assert "Confirm your e-mail" in error_of(callback(c, start(c)))
    cloud.person["email_verified"] = True
    cloud.aud = "someone-else"
    assert "not valid" in error_of(callback(c, start(c)))
    cloud.aud = None
    # a state is single use
    auth = start(c)
    assert callback(c, auth).headers["location"] == "/"
    assert "expired or was already used" in error_of(callback(browser(), auth))
    # the account server saying no
    r = c.get("/api/auth/cloud/callback", params={"error": "access_denied", "state": "x"}, follow_redirects=False)
    assert error_of(r) == "Sign-in cancelled."


def test_disabled_without_issuer(monkeypatch):
    monkeypatch.delenv("GAMMA_CLOUD_ISSUER", raising=False)
    c = browser()
    assert c.get("/api/server-config").json()["cloud"]["enabled"] is False
    assert c.get("/api/auth/cloud/start", follow_redirects=False).status_code == 503


def test_admin_settings_roundtrip(monkeypatch):
    monkeypatch.delenv("GAMMA_CLOUD_ISSUER", raising=False)
    make_user("ca_root", "pw-ca_root-123", is_admin=1)
    c = login("ca_root", "pw-ca_root-123")
    r = c.put("/api/admin/settings", json={"cloud_issuer": "https://account.example", "cloud_client_id": "gc_abc",
                                           "cloud_client_secret": "shh", "cloud_policy": "claim"})
    assert r.status_code == 200, r.text
    cfg = r.json()["cloud"]
    assert cfg == {"issuer": "https://account.example", "client_id": "gc_abc", "policy": "claim", "has_secret": True,
                   "enabled": True, "share_host": False, "source": "saved"}
    assert cloud_auth.client_secret() == "shh"
    # the share host switch (gamma/publish.py), off again for the rest of the suite
    assert c.put("/api/admin/settings", json={"cloud_share_host": True}).json()["cloud"]["share_host"] is True
    assert c.put("/api/admin/settings", json={"cloud_share_host": False}).json()["cloud"]["share_host"] is False
    assert "shh" not in json.dumps(c.get("/api/admin/settings").json())
    assert c.put("/api/admin/settings", json={"cloud_policy": "anything"}).status_code == 400
    assert c.put("/api/admin/settings", json={"cloud_issuer": "http://not-https.example"}).status_code == 400
    r = c.put("/api/admin/settings", json={"cloud_issuer": "", "cloud_client_secret": ""})
    assert r.json()["cloud"]["enabled"] is False and r.json()["cloud"]["has_secret"] is False
    monkeypatch.setenv("GAMMA_CLOUD_ISSUER", ISSUER)
    assert c.get("/api/admin/settings").json()["cloud"]["source"] == "environment"
    assert c.put("/api/admin/settings", json={"cloud_policy": "claim"}).status_code == 400


def test_rename_and_delete_follow_identities(cloud, monkeypatch):
    # a name no other test file creates: the suite shares one data directory per worker
    monkeypatch.setenv("GAMMA_CLOUD_POLICY", "provision")
    cloud.person.update({"sub": "sub-ynez", "preferred_username": "ynez", "email": "ynez@example.org"})
    c = browser()
    callback(c, start(c))
    make_user("ca_root", "pw-ca_root-123", is_admin=1)
    admin = login("ca_root", "pw-ca_root-123")
    assert admin.post("/api/admin/users/ynez/rename", json={"new_username": "ynez2"}).status_code == 200
    assert cloud_auth.status_of("ynez2")["username"] == "ynez"
    assert admin.delete("/api/admin/users/ynez2").status_code == 200
    with connect_users_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM identities WHERE username = 'ynez2'").fetchone()[0] == 0
    assert cloud.revoked == ["rt-1"]


def test_cli_link_and_unlink(cloud, capsys):
    import manage
    make_user("ca_frank", "pw-ca_frank-123")
    manage.link_identity("ca_frank", "sub-ca_frank", "ca_frank", "ca_frank@example.org")
    manage.list_identities()
    assert "ca_frank" in capsys.readouterr().out
    assert cloud_auth.status_of("ca_frank")["username"] == "ca_frank"
    manage.unlink_identity("ca_frank")
    assert cloud_auth.status_of("ca_frank") is None


# --- the grant: access tokens, the grant check, the profile, the server list ---

def link_account(cloud, name, base_url="http://testserver"):
    """A password account ``name`` that links its Gamma Cloud account (subject
    ``sub-<name>``) from a signed-in browser; returns that browser, whose
    session the cloud sign-in minted."""
    make_user(name, f"pw-{name}-123")
    c = browser(base_url)
    assert c.post("/api/login", json={"username": name, "password": f"pw-{name}-123"}).status_code == 200
    cloud.person.update({"sub": f"sub-{name}", "preferred_username": name.replace("_", "-"), "email": f"{name}@example.org"})
    assert callback(c, start(c, link="1")).headers["location"] == "/"
    return c


def until(check, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.02)
    return check()


def test_access_token_refresh_rotation_and_cache(cloud, monkeypatch):
    import threading
    link_account(cloud, "ca_jo")
    grants = lambda: [f for f in cloud.token_calls if f["grant_type"] == "refresh_token"]  # noqa: E731
    # the sign-in's own access token is cached: no refresh
    first = cloud_auth.access_token_for("ca_jo")
    assert first and grants() == []
    # past its lifetime the grant is refreshed, the rotated token saved, the new access token cached
    cloud_auth._access.clear()
    second = cloud_auth.access_token_for("ca_jo")
    assert second != first and len(grants()) == 1 and grants()[0]["refresh_token"] == "rt-1"
    assert cloud_auth.refresh_token_of("ca_jo") == "rt-1+"
    assert cloud_auth.access_token_for("ca_jo") == second and len(grants()) == 1
    # fresh (the hourly check) refreshes regardless, from the newest token
    assert cloud_auth.access_token_for("ca_jo", fresh=True) not in (first, second)
    assert grants()[-1]["refresh_token"] == "rt-1+" and cloud_auth.refresh_token_of("ca_jo") == "rt-1++"

    # one refresh at a time: callers waiting on the lock take the cached token
    def slow_http(url, *a, **kw):
        if url.endswith("/token"):
            time.sleep(0.1)
        return cloud.http(url, *a, **kw)

    monkeypatch.setattr(cloud_auth, "_http", slow_http)
    cloud_auth._access.clear()
    got = []
    threads = [threading.Thread(target=lambda: got.append(cloud_auth.access_token_for("ca_jo"))) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(got)) == 1 and got[0] and len(grants()) == 3
    # no identity, no token
    assert cloud_auth.access_token_for("nobody-here") is None


def test_revoked_grant_ends_cloud_sessions_only(cloud):
    cloud_session = link_account(cloud, "ca_kim")
    password_session = login("ca_kim", "pw-ca_kim-123")
    assert cloud_session.get("/api/session").json()["user"] == "ca_kim"
    with connect_users_db() as conn:
        vias = sorted(r[0] for r in conn.execute("SELECT via FROM sessions WHERE username = 'ca_kim'"))
    assert vias == ["", "", "cloud"]  # the linking browser's password login, the second login, the cloud sign-in
    # the person signs this server out on the Devices page
    cloud.live.clear()
    assert cloud_sync.check_all() == {"ca_kim": ""}
    assert cloud_session.get("/api/session").json()["user"] is None
    assert password_session.get("/api/session").json()["user"] == "ca_kim"
    status = cloud_auth.status_of("ca_kim")
    assert status["offline"] is False and status["revoked_at"]
    # no token left: the next check asks nothing
    before = len(cloud.calls)
    assert cloud_sync.check_all() == {} and len(cloud.calls) == before
    # signing in again holds a new grant and clears the mark
    c = browser()
    assert callback(c, start(c)).headers["location"] == "/"
    assert c.get("/api/session").json()["user"] == "ca_kim"
    assert cloud_auth.status_of("ca_kim")["revoked_at"] == "" and cloud_auth.refresh_token_of("ca_kim") == "rt-1"


def test_offline_check_changes_nothing(cloud, monkeypatch):
    c = link_account(cloud, "ca_lee")
    cloud.offline = True
    assert cloud_sync.check_all() == {"ca_lee": ""}
    assert c.get("/api/session").json()["user"] == "ca_lee"
    assert cloud_auth.refresh_token_of("ca_lee") == "rt-1" and cloud_auth.status_of("ca_lee")["revoked_at"] == ""
    # an answer that is not a refusal changes nothing either
    cloud.offline = False

    def unavailable(url, *a, **kw):
        if url.endswith("/token"):
            raise cloud_auth.CloudAuthError("busy", status=503, error="temporarily_unavailable")
        return cloud.http(url, *a, **kw)

    monkeypatch.setattr(cloud_auth, "_http", unavailable)
    assert cloud_sync.check_all() == {"ca_lee": ""}
    assert c.get("/api/session").json()["user"] == "ca_lee" and cloud_auth.refresh_token_of("ca_lee") == "rt-1"
    # back online: the check refreshes, syncs and checks in
    monkeypatch.setattr(cloud_auth, "_http", cloud.http)
    assert cloud_sync.check_all() == {"ca_lee": "ok"}
    assert cloud_auth.refresh_token_of("ca_lee") == "rt-1+"


def test_profile_pull_push_and_conflict(cloud, monkeypatch):
    make_user("ca_mia", "pw-ca_mia-123")
    set_profile("ca_mia", {"theme": "light"})  # older than the cloud's; no identity yet, so nothing is pushed
    time.sleep(0.01)
    cloud.prefs["sub-ca_mia"] = {"profile": {"value": {"theme": "dark"}, "updated_at": _ms_now()}}
    # the sign-in pulls the newer profile before the browser loads
    c = link_account(cloud, "ca_mia")
    value, at = get_profile("ca_mia")
    assert value == {"theme": "dark"}
    assert at == cloud.prefs["sub-ca_mia"]["profile"]["updated_at"][:-1] + "000Z"
    assert c.get("/api/prefs/profile").json()["value"] == {"theme": "dark"}
    assert cloud_sync.sync_profile("ca_mia") == "same"

    # a change here is pushed once it settles: three quick changes, one PUT
    monkeypatch.setattr(cloud_sync, "PUSH_DELAY", 0.2)
    puts = lambda: [x for x in cloud.calls if x == ("PUT", "/api/me/prefs/profile")]  # noqa: E731
    for theme in ("sepia", "gray", "night"):
        assert c.put("/api/prefs/profile", json={"value": {"theme": theme}}).status_code == 200
    assert until(lambda: cloud.prefs["sub-ca_mia"]["profile"]["value"] == {"theme": "night"})
    time.sleep(0.3)
    assert len(puts()) == 1 and not cloud_sync._timers
    assert cloud_sync.sync_profile("ca_mia") == "same"

    # the hourly check: a newer copy elsewhere is pulled, a newer one here is pushed
    time.sleep(0.01)
    cloud.prefs["sub-ca_mia"]["profile"] = {"value": {"theme": "dark", "enterNewNote": True}, "updated_at": _ms_now()}
    assert cloud_sync.check("ca_mia") == "ok" and get_profile("ca_mia")[0] == {"theme": "dark", "enterNewNote": True}
    monkeypatch.setattr(cloud_sync, "profile_changed", lambda username: None)  # no timer: pushed by hand below
    set_profile("ca_mia", {"theme": "gray"})
    assert cloud_sync.sync_profile("ca_mia") == "pushed"
    assert cloud.prefs["sub-ca_mia"]["profile"]["value"] == {"theme": "gray"}

    # a push the account server refuses as older takes its value (409)
    set_profile("ca_mia", {"theme": "stale"})
    time.sleep(0.01)
    cloud.prefs["sub-ca_mia"]["profile"] = {"value": {"theme": "sepia"}, "updated_at": _ms_now()}
    assert cloud_sync.push_profile("ca_mia") == "pulled"
    assert get_profile("ca_mia")[0] == {"theme": "sepia"}

    # the cloud has none: the local one goes up
    cloud.prefs["sub-ca_mia"].clear()
    assert cloud_sync.sync_profile("ca_mia") == "pushed"
    assert cloud.prefs["sub-ca_mia"]["profile"]["value"] == {"theme": "sepia"}
    # only the profile travels: never the provider entries or the active entry
    assert {path for _, path in cloud.calls if path.startswith("/api/me/prefs")} == {"/api/me/prefs/profile"}


def test_edit_right_after_a_pull_counts_as_newer(cloud, monkeypatch):
    # a synced copy may carry a time ahead of this server's clock: a local
    # edit is stamped after it, so it wins the next sync instead of a 409
    link_account(cloud, "ca_pia")
    monkeypatch.setattr(cloud_sync, "profile_changed", lambda username: None)
    ahead = "2999-01-01T00:00:00.000000Z"
    set_profile("ca_pia", {"theme": "dark"}, updated_at=ahead)
    assert set_profile("ca_pia", {"theme": "sepia"}) == "2999-01-01T00:00:00.001000Z"
    # a synced copy never replaces a newer one here
    assert set_profile("ca_pia", {"theme": "old"}, updated_at="2020-01-01T00:00:00.000000Z") == "2999-01-01T00:00:00.001000Z"
    assert get_profile("ca_pia")[0] == {"theme": "sepia"}
    # pushed with that time, the account server clamps it to its now, and this server keeps the clamped time
    assert cloud_sync.push_profile("ca_pia") == "pushed"
    stored = cloud.prefs["sub-ca_pia"]["profile"]["updated_at"]
    assert stored < "2999" and get_profile("ca_pia")[1] == stored[:-1] + "000Z"
    assert cloud_sync.sync_profile("ca_pia") == "same"


def test_no_network_without_identity(cloud, monkeypatch):
    # cloud sign-in on, an account without a linked identity: nothing is ever sent
    make_user("ca_nia", "pw-ca_nia-123")
    c = login("ca_nia", "pw-ca_nia-123")
    assert c.put("/api/prefs/profile", json={"value": {"theme": "dark"}}).status_code == 200
    assert cloud_sync._timers == {}
    assert cloud_auth.access_token_for("ca_nia") is None
    assert cloud_sync.check_all() == {}
    assert cloud_sync.sync_profile("ca_nia") == "" and cloud_sync.register_server("ca_nia", url="https://x.example") is False
    assert cloud.calls == []
    # cloud sign-in off, even with an identity row: no call at all
    link_account(cloud, "ca_nell")
    monkeypatch.delenv("GAMMA_CLOUD_ISSUER")
    monkeypatch.setattr(cloud_auth, "_http", lambda *a, **kw: pytest.fail("no network without cloud sign-in"))
    nell = login("ca_nell", "pw-ca_nell-123")
    assert nell.put("/api/prefs/profile", json={"value": {"theme": "dark"}}).status_code == 200
    assert cloud_sync._timers == {} and cloud_sync.check_all() == {}
    assert cloud_auth.access_token_for("ca_nell") is None


def test_server_registration(cloud, monkeypatch):
    import socket
    # a sidecar: its loopback origin, named after the machine
    local = "http://127.0.0.1:9123"
    c = link_account(cloud, "ca_ola", base_url=local)
    assert cloud.servers["sub-ca_ola"] == {local: socket.gethostname()[:80]}
    # the hourly check refreshes the entry (an upsert), from the remembered address
    posts = lambda: [x for x in cloud.calls if x == ("POST", "/api/me/servers")]  # noqa: E731
    before = len(posts())
    assert cloud_sync.check_all() == {"ca_ola": "ok"} and len(posts()) == before + 1
    # unlinking takes it off the list, then revokes the grant (refreshed first when no access token is cached)
    cloud_auth._access.clear()
    assert c.post("/api/auth/cloud/unlink").json()["ok"] is True
    assert cloud.servers["sub-ca_ola"] == {}
    assert cloud.revoked == ["rt-1++"] and cloud.live == {}
    # a server with a confirmed public URL registers that, named by its host
    monkeypatch.setenv("GAMMA_PUBLIC_URL", "https://gamma.example.org")
    link_account(cloud, "ca_pat")
    assert cloud.servers["sub-ca_pat"] == {"https://gamma.example.org": "gamma.example.org"}
    # a LAN address that is neither: not listed
    monkeypatch.delenv("GAMMA_PUBLIC_URL")
    _set_raw("cloud_server_url", "")
    link_account(cloud, "ca_quin", base_url="http://192.168.1.20:9001")
    assert cloud.servers.get("sub-ca_quin", {}) == {}
    # a failure is a warning, never an error to the person
    cloud.offline = True
    assert cloud_sync.register_server("ca_pat", url="https://gamma.example.org") is False


def test_sync_status(cloud, monkeypatch):
    status = lambda c: c.get("/api/auth/cloud/sync-status").json()  # noqa: E731
    assert browser().get("/api/auth/cloud/sync-status").status_code == 401
    # a password account without an identity: off, nothing asked
    make_user("ca_sam", "pw-ca_sam-123")
    sam = login("ca_sam", "pw-ca_sam-123")
    assert status(sam) == {"profile": {"state": "off", "at": "", "error": ""}, "identity": {"linked": False}}
    assert cloud.calls == []
    # linked: the sign-in's pull agreed
    c = link_account(cloud, "ca_tia")
    got = status(c)
    assert got["identity"] == {"linked": True, "username": "ca-tia"}
    assert got["profile"]["state"] == "synced" and got["profile"]["at"].endswith("Z") and not got["profile"]["error"]
    # a change here is pending until its push lands
    monkeypatch.setattr(cloud_sync, "PUSH_DELAY", 0.2)
    assert c.put("/api/prefs/profile", json={"value": {"theme": "dark"}}).status_code == 200
    assert status(c)["profile"]["state"] == "pending"
    assert until(lambda: status(c)["profile"]["state"] == "synced")
    assert cloud.prefs["sub-ca_tia"]["profile"]["value"] == {"theme": "dark"}
    # the account server unreachable: the check's failure is an error with its message; reading it asks nothing
    cloud.offline = True
    assert cloud_sync.sync_profile("ca_tia") == ""
    before = len(cloud.calls)
    got = status(c)["profile"]
    assert got["state"] == "error" and "offline" in got["error"] and len(cloud.calls) == before
    # a failed push stays pending, with the reason, for the next check
    assert c.put("/api/prefs/profile", json={"value": {"theme": "sepia"}}).status_code == 200
    assert until(lambda: status(c)["profile"]["error"] != "" and not cloud_sync._timers)
    assert status(c)["profile"]["state"] == "pending"
    # back online, the check pushes it
    cloud.offline = False
    assert cloud_sync.check("ca_tia") == "ok"
    assert status(c)["profile"]["state"] == "synced" and cloud.prefs["sub-ca_tia"]["profile"]["value"] == {"theme": "sepia"}
    # cloud sign-in off: off, whatever was last recorded
    monkeypatch.delenv("GAMMA_CLOUD_ISSUER")
    assert status(c)["profile"]["state"] == "off"

def test_grant_check_runs_at_startup(monkeypatch):
    import asyncio
    seen = []
    monkeypatch.setattr(cloud_sync, "check_all", lambda: seen.append(1))

    async def run():
        async with cloud_sync.lifespan():
            for _ in range(100):
                if seen:
                    break
                await asyncio.sleep(0.01)

    asyncio.run(run())
    assert seen == [1]
