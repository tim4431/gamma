"""The Zotero account routes (gamma/routers/zotero.py): the status, a pasted
key checked with Zotero, a disconnect that revokes what Gamma had made,
the libraries a key reaches, guests refused, and signing in at zotero.org
end to end in both modes — with this server's own Zotero app, and through
Gamma Cloud's broker (a fake account server) — including the callback
that needs no session for a round trip started on this computer, the
binding to the browser that started anywhere else (its cookie, a refusal
for another account, the page that asks before connecting, also for a
browser holding only the starting account's session, as a login CSRF
plants one, or one started with a loopback Host header from another
machine), the addresses signing in is offered at, and the status only the
starting account may read."""

import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import pytest
from conftest import account_of, fresh_client, make_user
from test_cloud_auth import callback as cloud_callback
from test_cloud_auth import cloud  # noqa: F401  (the fake account server fixture)
from test_cloud_auth import start as cloud_start
from zotero_fake import FULL_ACCESS, GOOD_VERIFIER, REQUEST_TOKEN, FakeZotero

from gamma import cloud_auth, zotero_account, zotero_web
from gamma.routers import zotero as zotero_routes

USER, PASSWORD = "zr_alice", "pw-zr_alice-123"
OTHER, OTHER_PASSWORD = "zr_bob", "pw-zr_bob-123"
ISSUER = "https://account.test"
# A server on this computer (a desktop sidecar) with no public URL: the
# callback base is the page's own loopback origin.
BASE = "http://127.0.0.1:9100"
PUBLIC = "https://gamma.example"  # a public URL, for the round trips off loopback
# Where a request's connection comes from: this computer (a desktop
# sidecar's own window, a browser on the server's machine), or another one.
HERE, ELSEWHERE = ("127.0.0.1", 50000), ("192.168.1.20", 50000)
NOT_SIGNING_IN = {"available": False, "mode": None}
COOKIE, CALLBACK = zotero_routes.COOKIE, zotero_routes.CALLBACK_PATH


def _login(username, password, base_url=BASE, peer=HERE):
    """A fresh client signed in as the account, at ``base_url``, its
    connections coming from ``peer``."""
    c = fresh_client(base_url=base_url, client=peer)
    r = c.post("/api/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture(autouse=True)
def sidecar(monkeypatch):
    """This server is the desktop app's own (``BASE``): the app's sidecar
    sets GAMMA_FOLDER_LINKS. A test of another server on loopback unsets it."""
    monkeypatch.setenv("GAMMA_FOLDER_LINKS", "1")


@pytest.fixture(scope="module")
def alice_id():
    make_user(USER, PASSWORD)
    return account_of(USER)


@pytest.fixture(scope="module")
def bob_id():
    make_user(OTHER, OTHER_PASSWORD)
    return account_of(OTHER)


@pytest.fixture
def alice(alice_id):
    yield _login(USER, PASSWORD)
    zotero_account.disconnect(alice_id, revoke=False)


@pytest.fixture
def browser():
    """A browser at this server's address with no Gamma session: the
    desktop app's system browser."""
    return fresh_client(base_url=BASE, client=HERE)


@pytest.fixture
def public(monkeypatch, alice_id, bob_id):
    """This server at a public URL: Alice's and Bob's browsers there."""
    monkeypatch.setenv("GAMMA_PUBLIC_URL", PUBLIC)
    yield _login(USER, PASSWORD, PUBLIC), _login(OTHER, OTHER_PASSWORD, PUBLIC)
    zotero_account.disconnect(alice_id, revoke=False)
    zotero_account.disconnect(bob_id, revoke=False)


@pytest.fixture
def fake(monkeypatch):
    """A fake Zotero, and neither a Zotero app nor cloud sign-in here
    unless the test sets one up."""
    for name in ("GAMMA_ZOTERO_CLIENT_KEY", "GAMMA_ZOTERO_CLIENT_SECRET", "GAMMA_CLOUD_ISSUER",
                 "GAMMA_CLOUD_DEFAULT_ISSUER"):
        monkeypatch.delenv(name, raising=False)
    zotero_routes._probe_failed.clear()
    yield FakeZotero().install(monkeypatch)
    zotero_routes._probe_failed.clear()


@pytest.fixture
def zotero_app(monkeypatch, fake):
    """This server has a Zotero app of its own."""
    monkeypatch.setenv("GAMMA_ZOTERO_CLIENT_KEY", "gamma-consumer")
    monkeypatch.setenv("GAMMA_ZOTERO_CLIENT_SECRET", "gamma-consumer-secret")
    return fake


class FakeBroker:
    """The account server as the cloud mode sees it: discovery, with the
    broker's two endpoints when ``offers`` (its admin set up a Zotero app),
    and the token endpoint, which trades ``good-code`` for ``key`` when
    the verifier is the challenge's and the server the one it was sent
    back to. ``offline`` fails every call as an unreachable server does."""

    def __init__(self, key: str):
        self.key = key
        self.offers = True
        self.offline = False
        self.calls = []
        self.challenge = ""  # the challenge the connect page was opened with
        self.server = BASE  # the address it was opened with
        self.forms = []

    def http(self, url, data=None, headers=None, method=None, timeout=None):
        self.calls.append(url.removeprefix(ISSUER))
        if self.offline:
            raise cloud_auth.CloudAuthError("cannot reach the account server: offline")
        if url == ISSUER + "/.well-known/openid-configuration":
            doc = {"issuer": ISSUER, "authorization_endpoint": ISSUER + "/authorize",
                   "token_endpoint": ISSUER + "/token", "jwks_uri": ISSUER + "/jwks"}
            if self.offers:
                doc.update(gamma_zotero_connect_endpoint=ISSUER + "/zotero/connect",
                           gamma_zotero_token_endpoint=ISSUER + "/api/zotero/connect/token")
            return doc
        if url == ISSUER + "/api/zotero/connect/token":
            form = {k: v[0] for k, v in parse_qs(data.decode()).items()}
            self.forms.append(form)
            verified = _s256(form.get("code_verifier", "")) == self.challenge
            if form.get("code") != "good-code" or not verified or form.get("server") != self.server:
                raise cloud_auth.CloudAuthError("unknown or used code", status=400)
            return {"api_key": self.key, "user_id": 123, "username": "tim"}
        raise AssertionError(f"unexpected url {url}")


@pytest.fixture
def broker(monkeypatch, fake):
    """Cloud sign-in on, with Gamma Cloud offering the Zotero broker; the
    key it hands out is one the fake Zotero made."""
    fake.keys["zkey-broker"] = dict(FULL_ACCESS)
    account_server = FakeBroker("zkey-broker")
    monkeypatch.setattr(cloud_auth, "_http", account_server.http)
    monkeypatch.setenv("GAMMA_CLOUD_ISSUER", ISSUER)
    cloud_auth._discovery_cache.clear()
    yield account_server
    cloud_auth._discovery_cache.clear()


def _s256(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def _query(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def _status(client, state):
    return client.post("/api/zotero/oauth/status", json={"state": state})


def _start(client) -> dict:
    r = client.post("/api/zotero/oauth/start", json={})
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    return r.json()


def _down(fake, path: str) -> None:
    """Zotero answers 500 to ``path`` every time, its retries included."""
    for _ in range(zotero_web.MAX_RETRIES + 1):
        fake.fail_next(500, path)


def _approved(fake, url: str) -> str:
    """zotero.org approves: the callback address it sends the browser to."""
    approval = fake.handle("GET", url, {}, b"")
    assert approval.status == 302
    return approval.headers["Location"]


def test_the_status_before_and_after_connecting_a_key(fake, alice):
    assert alice.get("/api/zotero/account").json() == {"connected": False, "can_edit": True,
                                                        "oauth": NOT_SIGNING_IN}
    r = alice.put("/api/zotero/account", json={"api_key": " zkey-good "})
    assert r.status_code == 200, r.text
    connected = r.json()
    assert {k: v for k, v in connected.items() if k != "connected_at"} == {
        "connected": True, "user_id": 123, "username": "tim", "hint": "…good", "via": "key",
        "access": {"library": True, "notes": True, "write": True, "files": True, "groups": "write"},
        "can_edit": True, "oauth": NOT_SIGNING_IN}
    assert alice.get("/api/zotero/account").json() == connected
    assert "zkey-good" not in r.text


def test_a_key_zotero_refuses_or_that_cannot_read_the_library_is_not_kept(fake, alice):
    r = alice.put("/api/zotero/account", json={"api_key": "zkey-unknown"})
    assert r.status_code == 400 and "does not accept this key" in r.json()["detail"]
    fake.keys["zkey-groups-only"] = {"user": {"library": False}, "groups": {"all": {"library": True}}}
    r = alice.put("/api/zotero/account", json={"api_key": "zkey-groups-only"})
    assert r.status_code == 400 and "Allow library access" in r.json()["detail"]
    assert alice.put("/api/zotero/account", json={"api_key": "two words"}).status_code == 400
    assert alice.put("/api/zotero/account", json={"api_key": "x" * 300}).status_code == 422
    _down(fake, "/keys/current")
    r = alice.put("/api/zotero/account", json={"api_key": "zkey-good"})
    assert r.status_code == 502 and "Zotero answered 500" in r.json()["detail"]
    assert alice.get("/api/zotero/account").json()["connected"] is False


def test_disconnecting_revokes_a_key_gamma_had_made_and_keeps_a_pasted_one(fake, alice, alice_id):
    alice.put("/api/zotero/account", json={"api_key": "zkey-good"})
    assert alice.delete("/api/zotero/account").json() == {"ok": True}
    assert alice.get("/api/zotero/account").json()["connected"] is False
    assert "zkey-good" in fake.keys  # the person's own key, perhaps used elsewhere
    fake.keys["zkey-made"] = dict(FULL_ACCESS)
    zotero_account.connect(alice_id, "zkey-made", via="oauth")
    assert alice.delete("/api/zotero/account").json() == {"ok": True}
    assert "zkey-made" not in fake.keys
    assert fake.calls[-1][:2] == ("DELETE", "/keys/current")
    assert alice.delete("/api/zotero/account").json() == {"ok": True}  # nothing left: still fine


def test_the_libraries_are_my_library_and_the_groups(fake, alice):
    r = alice.get("/api/zotero/libraries")
    assert r.status_code == 409 and r.json()["detail"] == zotero_account.NOT_CONNECTED
    fake.add_group(9, "Lab")
    fake.add_group(10, "Department", write=False)
    alice.put("/api/zotero/account", json={"api_key": "zkey-good"})
    mine = {"user_id": 123, "read": True, "notes": True}
    assert alice.get("/api/zotero/libraries").json() == {"libraries": [
        {"id": "users/123", "name": "My Library", "kind": "user", **mine, "write": True, "files": True},
        {"id": "groups/9", "name": "Lab", "kind": "group", **mine, "write": True, "files": True},
        {"id": "groups/10", "name": "Department", "kind": "group", **mine, "write": False, "files": False}]}
    _down(fake, "/keys/current")
    assert alice.get("/api/zotero/libraries").status_code == 502
    del fake.keys["zkey-good"]  # deleted at zotero.org since: the person reconnects
    r = alice.get("/api/zotero/libraries")
    assert r.status_code == 409 and "reconnect" in r.json()["detail"]


def test_a_stored_key_this_server_can_no_longer_read_asks_for_a_reconnect(fake, alice, monkeypatch):
    from cryptography.fernet import Fernet

    alice.put("/api/zotero/account", json={"api_key": "zkey-good"})
    monkeypatch.setenv("GAMMA_PUBLISHER_SESSION_KEY", Fernet.generate_key().decode("ascii"))
    r = alice.get("/api/zotero/libraries")
    assert r.status_code == 409 and r.json()["detail"] == zotero_account.UNREADABLE


def test_a_guest_sees_no_way_to_connect_and_every_change_is_refused(zotero_app, guest):
    assert guest.get("/api/zotero/account").json() == {"connected": False, "can_edit": False,
                                                        "oauth": NOT_SIGNING_IN}
    refusals = [guest.put("/api/zotero/account", json={"api_key": "zkey-good"}),
                guest.delete("/api/zotero/account"),
                guest.get("/api/zotero/libraries"),
                guest.post("/api/zotero/oauth/start", json={}),
                guest.post("/api/zotero/oauth/status", json={"state": "anything"})]
    for r in refusals:
        assert r.status_code == 403 and r.json()["detail"] == zotero_routes.GUESTS_REFUSED
    assert not [call for call in zotero_app.calls if call[1] == "/keys/current"]
    assert not zotero_app.oauth


def test_a_signed_out_browser_gets_no_answer(fake, anon):
    assert anon.get("/api/zotero/account").status_code == 401
    assert anon.post("/api/zotero/oauth/start", json={}).status_code == 401


@pytest.mark.parametrize("scope", ["read", "write"])
def test_an_integration_token_never_reaches_the_accounts_zotero(fake, alice, alice_id, scope):
    """The connection is the account's, not a workspace's: a token bound
    to one workspace is refused, whatever its scope."""
    from conftest import workspace_of

    from gamma.integrations import create_token

    alice.put("/api/zotero/account", json={"api_key": "zkey-good"})
    token = create_token(alice_id, workspace_of(USER), f"zr-{scope}", 90, scope=scope)["token"]
    c = fresh_client(base_url=BASE, client=HERE)
    c.headers["Authorization"] = f"Bearer {token}"
    for method, path, body in (("GET", "/api/zotero/account", None),
                               ("PUT", "/api/zotero/account", {"api_key": "zkey-other"}),
                               ("DELETE", "/api/zotero/account", None),
                               ("GET", "/api/zotero/libraries", None),
                               ("POST", "/api/zotero/oauth/start", {}),
                               ("POST", "/api/zotero/oauth/status", {"state": "s"})):
        r = c.request(method, path, json=body)
        assert r.status_code == 403, (method, path, r.status_code, r.text)
    assert zotero_account.api_key(alice_id) == "zkey-good"


def test_the_prefs_api_never_hands_out_or_replaces_the_stored_key(fake, alice, alice_id):
    alice.put("/api/zotero/account", json={"api_key": "zkey-good"})
    assert alice.get(f"/api/prefs/{zotero_account.PREF_KEY}").status_code == 400
    assert alice.put(f"/api/prefs/{zotero_account.PREF_KEY}", json={"value": {"key": "x"}}).status_code == 400
    assert zotero_account.api_key(alice_id) == "zkey-good"


def test_signing_in_with_this_servers_own_zotero_app(zotero_app, alice, browser):
    fake = zotero_app
    anon = browser
    assert alice.get("/api/zotero/account").json()["oauth"] == {"available": True, "mode": "server"}
    r = alice.post("/api/zotero/oauth/start", json={})
    assert COOKIE not in r.cookies  # started from this computer on loopback: the round trip needs no cookie
    started = r.json()
    state, url = started["state"], started["url"]
    assert url.startswith(fake.web_base + "/oauth/authorize?")
    q = _query(url)
    assert q["oauth_token"] == REQUEST_TOKEN and q["library_access"] == "1" and q["all_groups"] == "write"
    assert q["name"] == f"Gamma ({cloud_auth.server_name(BASE)})"
    request_step = fake.oauth[-1]
    assert request_step["params"]["oauth_consumer_key"] == "gamma-consumer"
    assert request_step["params"]["oauth_callback"] == f"{BASE}/api/zotero/oauth/callback?state={state}"
    assert _status(alice, state).json() == {"state": "pending"}

    # zotero.org approves and sends the browser back: no Gamma session there (the desktop's system browser)
    approval = fake.handle("GET", url, {}, b"")
    assert approval.status == 302
    callback = approval.headers["Location"]
    assert _query(callback) == {"state": state, "oauth_token": REQUEST_TOKEN, "oauth_verifier": GOOD_VERIFIER}
    page = anon.get(callback)
    assert page.status_code == 200 and "Zotero is connected to Gamma" in page.text
    assert "You can close this tab." in page.text and "window.close()" in page.text
    assert f"the Gamma account “{USER}”" in page.text
    assert page.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in page.headers["content-security-policy"]
    assert fake.oauth[-1]["path"] == "/oauth/access" and fake.oauth[-1]["params"]["oauth_verifier"] == GOOD_VERIFIER

    done = _status(alice, state).json()
    assert done["state"] == "connected"
    assert done["account"]["username"] == "tim" and done["account"]["via"] == "oauth"
    account = alice.get("/api/zotero/account").json()
    assert account["connected"] and account["via"] == "oauth" and account["hint"] == "…th-1"
    assert alice.get("/api/zotero/libraries").json()["libraries"][0]["id"] == "users/123"

    # the state is single-use, its outcome stays for the status, and only for the account that started it
    again = anon.get(callback)
    assert again.status_code == 400 and "expired or was already used" in again.text
    assert _status(alice, state).json()["state"] == "connected"
    make_user(OTHER, OTHER_PASSWORD)
    assert _status(_login(OTHER, OTHER_PASSWORD), state).status_code == 404


def test_an_unknown_state_or_an_answer_that_does_not_match_its_start_connects_nothing(zotero_app, alice, browser):
    anon = browser
    fake = zotero_app
    r = anon.get("/api/zotero/oauth/callback", params={"state": "never-made", "oauth_token": REQUEST_TOKEN,
                                                       "oauth_verifier": GOOD_VERIFIER})
    assert r.status_code == 400 and "expired or was already used" in r.text
    assert anon.get("/api/zotero/oauth/callback").status_code == 400
    assert _status(alice, "never-made").status_code == 404
    assert _status(alice, "").status_code == 404

    state = _start(alice)["state"]
    r = anon.get("/api/zotero/oauth/callback", params={"state": state, "oauth_token": "another-token",
                                                       "oauth_verifier": GOOD_VERIFIER})
    assert r.status_code == 400 and "does not belong to this connection" in r.text
    assert "window.close" not in r.text
    assert _status(alice, state).json() == {"state": "error",
                                            "error": "Zotero's answer does not belong to this connection: start again"}

    state = _start(alice)["state"]
    r = anon.get("/api/zotero/oauth/callback", params={"state": state, "oauth_token": REQUEST_TOKEN})
    assert r.status_code == 400 and "Zotero did not approve the connection" in r.text

    state = _start(alice)["state"]
    r = anon.get("/api/zotero/oauth/callback", params={"state": state, "oauth_token": REQUEST_TOKEN,
                                                       "oauth_verifier": "forged"})
    assert r.status_code == 400 and "Zotero refused the connection" in r.text
    assert _status(alice, state).json()["state"] == "error"

    state = _start(alice)["state"]
    r = anon.get("/api/zotero/oauth/callback", params={"state": state, "error": "<b>bad</b>"})
    assert "&lt;b&gt;bad&lt;/b&gt;" in r.text and "<b>bad" not in r.text
    assert alice.get("/api/zotero/account").json()["connected"] is False
    assert not [step for step in fake.oauth if step["params"].get("oauth_verifier") == GOOD_VERIFIER]


def test_signing_in_through_gamma_clouds_broker(broker, alice, browser):
    anon = browser
    assert alice.get("/api/zotero/account").json()["oauth"] == {"available": True, "mode": "cloud"}
    started = _start(alice)
    state, url = started["state"], started["url"]
    assert url.startswith(ISSUER + "/zotero/connect?")
    q = _query(url)
    assert q["server"] == BASE and q["state"] == state and len(q["code_challenge"]) == 43
    broker.challenge = q["code_challenge"]
    assert _status(alice, state).json() == {"state": "pending"}

    # the broker sends the browser back with a one-time code
    page = anon.get("/api/zotero/oauth/callback", params={"code": "good-code", "state": state})
    assert page.status_code == 200 and "Zotero is connected to Gamma" in page.text
    assert broker.forms[-1]["server"] == BASE and broker.forms[-1]["code"] == "good-code"
    done = _status(alice, state).json()
    assert done["state"] == "connected" and done["account"]["via"] == "oauth"
    assert alice.get("/api/zotero/account").json()["hint"] == "…oker"

    # a code the broker does not know, and a refusal at zotero.org
    state = _start(alice)["state"]
    page = anon.get("/api/zotero/oauth/callback", params={"code": "bad-code", "state": state})
    assert page.status_code == 400 and "unknown or used code" in page.text
    assert _status(alice, state).json() == {
        "state": "error", "error": "Gamma Cloud could not finish connecting Zotero: unknown or used code"}
    state = _start(alice)["state"]
    anon.get("/api/zotero/oauth/callback", params={"error": "access_denied", "state": state})
    assert _status(alice, state).json() == {"state": "error", "error": "You cancelled connecting Zotero."}
    assert alice.get("/api/zotero/account").json()["hint"] == "…oker"  # the earlier connection stays


def test_signing_in_is_offered_only_with_a_zotero_app_or_the_broker(broker, fake, alice, monkeypatch):
    # the account server sets up no Zotero app
    broker.offers = False
    assert alice.get("/api/zotero/account").json()["oauth"] == NOT_SIGNING_IN
    r = alice.post("/api/zotero/oauth/start", json={})
    assert r.status_code == 400 and r.json()["detail"] == cloud_auth.NO_ZOTERO_BROKER

    # unreachable: the account answer still comes, and it does not ask again within the minute
    broker.offers, broker.offline = True, True
    cloud_auth._discovery_cache.clear()
    zotero_routes._probe_failed.clear()
    assert alice.get("/api/zotero/account").json()["oauth"] == NOT_SIGNING_IN
    asked = len(broker.calls)
    assert alice.get("/api/zotero/account").json()["oauth"] == NOT_SIGNING_IN
    assert len(broker.calls) == asked
    r = alice.post("/api/zotero/oauth/start", json={})
    assert r.status_code == 502 and "cannot reach the account server" in r.json()["detail"]

    # no cloud sign-in and no Zotero app: paste a key
    monkeypatch.delenv("GAMMA_CLOUD_ISSUER")
    zotero_routes._probe_failed.clear()
    assert alice.get("/api/zotero/account").json()["oauth"] == NOT_SIGNING_IN
    r = alice.post("/api/zotero/oauth/start", json={})
    assert r.status_code == 400 and r.json()["detail"] == zotero_routes.NO_SIGN_IN

    # this server's own app wins over the broker
    monkeypatch.setenv("GAMMA_CLOUD_ISSUER", ISSUER)
    monkeypatch.setenv("GAMMA_ZOTERO_CLIENT_KEY", "gamma-consumer")
    monkeypatch.setenv("GAMMA_ZOTERO_CLIENT_SECRET", "gamma-consumer-secret")
    assert alice.get("/api/zotero/account").json()["oauth"] == {"available": True, "mode": "server"}


def test_off_loopback_the_browser_that_started_connects_by_its_cookie(zotero_app, public, alice_id):
    alice, _ = public
    r = alice.post("/api/zotero/oauth/start", json={})
    assert r.status_code == 200, r.text
    cookie = next(c for c in r.headers.get_list("set-cookie") if c.startswith(COOKIE + "="))
    for part in ("HttpOnly", "SameSite=lax", "Secure", f"Path={CALLBACK}", "Max-Age=600"):
        assert part.lower() in cookie.lower(), cookie
    assert _query(zotero_app.oauth[-1]["params"]["oauth_callback"])["state"] == r.json()["state"]
    assert zotero_app.oauth[-1]["params"]["oauth_callback"].startswith(PUBLIC + CALLBACK)
    callback = _approved(zotero_app, r.json()["url"])

    # the cookie alone (a browser whose session ended meanwhile) is enough
    signed_out = fresh_client(base_url=PUBLIC)
    signed_out.cookies.set(COOKIE, alice.cookies.get(COOKIE), domain="gamma.example", path=CALLBACK)
    page = signed_out.get(callback)
    assert page.status_code == 200 and "Zotero is connected to Gamma" in page.text, page.text
    assert f"the Gamma account “{USER}” on gamma.example" in page.text
    assert any(c.startswith(COOKIE + "=") and "max-age=0" in c.lower() for c in page.headers.get_list("set-cookie"))
    assert zotero_account.status(alice_id)["via"] == "oauth"
    zotero_account.disconnect(alice_id, revoke=False)

    # the starting account's session without the cookie is not: it can be planted (the next test), so it is asked
    started = _start(alice)
    alice.cookies.delete(COOKIE)
    page = alice.get(_approved(zotero_app, started["url"]))
    assert page.status_code == 200 and "Connect Zotero to Gamma?" in page.text
    assert f"the Gamma account “{USER}” on gamma.example" in page.text
    assert not zotero_account.status(alice_id)["connected"]
    token = page.text.split('name="token" value="', 1)[1].split('"', 1)[0]
    done = alice.post(zotero_routes.CONFIRM_PATH, data={"token": token})
    assert done.status_code == 200 and "Zotero is connected to Gamma" in done.text
    assert _status(alice, started["state"]).json()["state"] == "connected"


def test_a_session_planted_by_a_login_csrf_is_asked_before_connecting(zotero_app, cloud, monkeypatch):  # noqa: F811
    # a public server with Gamma Cloud sign-in that makes an account for anyone who signs in
    cloud.person.update({"sub": "sub-zr_mallory", "preferred_username": "zr_mallory",
                         "email": "zr_mallory@example.org"})
    monkeypatch.setenv("GAMMA_CLOUD_CLIENT_ID", "lab-server")
    monkeypatch.setenv("GAMMA_CLOUD_POLICY", "provision")
    monkeypatch.setenv("GAMMA_PUBLIC_URL", PUBLIC)
    mallory = fresh_client(base_url=PUBLIC, client=ELSEWHERE)
    assert cloud_callback(mallory, cloud_start(mallory)).status_code == 302
    mallory_id = account_of("zr_mallory")
    try:
        # Mallory keeps the callback of another sign-in of hers and has Bob's browser open it: it holds her session
        bob = fresh_client(base_url=PUBLIC)
        assert cloud_callback(bob, cloud_start(fresh_client(base_url=PUBLIC))).status_code == 302
        assert bob.get("/api/session").json()["user"] == "zr_mallory"
        # she starts connecting Zotero in her own browser, and Bob approves the link she sends
        started = _start(mallory)
        page = bob.get(_approved(zotero_app, started["url"]))
        assert page.status_code == 200 and "Connect Zotero to Gamma?" in page.text
        assert "the Gamma account “zr_mallory” on gamma.example" in page.text
        assert not zotero_account.status(mallory_id)["connected"]
        assert not [step for step in zotero_app.oauth if step["path"] == "/oauth/access"]  # no key was made
        assert _status(mallory, started["state"]).json() == {"state": "pending"}
    finally:
        zotero_account.disconnect(mallory_id, revoke=False)


def test_off_loopback_a_browser_signed_in_to_another_account_is_refused(zotero_app, public, alice_id, bob_id):
    alice, bob = public
    started = _start(alice)
    # Alice passes the approval link to Bob, who approves at zotero.org in his own browser
    page = bob.get(_approved(zotero_app, started["url"]))
    assert page.status_code == 400 and "a different Gamma account" in page.text
    assert not zotero_account.status(alice_id)["connected"] and not zotero_account.status(bob_id)["connected"]
    assert not [step for step in zotero_app.oauth if step["path"] == "/oauth/access"]  # no key was made
    assert _status(alice, started["state"]).json() == {"state": "error", "error": zotero_routes.OTHER_ACCOUNT}
    # even with Alice's cookie in that browser
    started = _start(alice)
    bob.cookies.set(COOKIE, alice.cookies.get(COOKIE), domain="gamma.example", path=CALLBACK)
    assert bob.get(_approved(zotero_app, started["url"])).status_code == 400
    assert not zotero_account.status(alice_id)["connected"]


def test_off_loopback_a_browser_with_neither_is_asked_before_connecting(zotero_app, public, alice_id, bob_id):
    alice, bob = public
    started = _start(alice)
    callback = _approved(zotero_app, started["url"])
    # the desktop app's system browser on a remote server: no cookie, no session
    system_browser = fresh_client(base_url=PUBLIC)
    page = system_browser.get(callback)
    assert page.status_code == 200 and "Connect Zotero to Gamma?" in page.text
    assert f"the Gamma account “{USER}” on gamma.example" in page.text
    assert f'action="{zotero_routes.CONFIRM_PATH}"' in page.text and "form-action 'self'" in page.headers[
        "content-security-policy"]
    assert "window.close" not in page.text
    token = page.text.split('name="token" value="', 1)[1].split('"', 1)[0]
    assert not zotero_account.status(alice_id)["connected"]
    assert not [step for step in zotero_app.oauth if step["path"] == "/oauth/access"]
    assert _status(alice, started["state"]).json() == {"state": "pending"}
    assert system_browser.get(callback).status_code == 400  # the state is used up by the page

    done = system_browser.post(zotero_routes.CONFIRM_PATH, data={"token": token})
    assert done.status_code == 200 and "Zotero is connected to Gamma" in done.text
    assert zotero_account.status(alice_id)["via"] == "oauth"
    assert _status(alice, started["state"]).json()["state"] == "connected"
    again = system_browser.post(zotero_routes.CONFIRM_PATH, data={"token": token})
    assert again.status_code == 400 and "expired or was already used" in again.text
    assert len([step for step in zotero_app.oauth if step["path"] == "/oauth/access"]) == 1
    assert system_browser.post(zotero_routes.CONFIRM_PATH, data={"token": "never-made"}).status_code == 400

    # Connect pressed in a browser signed in to someone else connects no one
    zotero_account.disconnect(alice_id, revoke=False)
    started = _start(alice)
    page = fresh_client(base_url=PUBLIC).get(_approved(zotero_app, started["url"]))
    token = page.text.split('name="token" value="', 1)[1].split('"', 1)[0]
    refused = bob.post(zotero_routes.CONFIRM_PATH, data={"token": token})
    assert refused.status_code == 400 and "a different Gamma account" in refused.text
    assert not zotero_account.status(alice_id)["connected"] and not zotero_account.status(bob_id)["connected"]


def test_without_a_public_url_signing_in_is_offered_only_on_loopback(zotero_app, alice_id, monkeypatch):
    # a LAN address, or any Host a request names: the approval could be sent anywhere
    lan = _login(USER, PASSWORD, "http://gamma.lan:9001")
    assert lan.get("/api/zotero/account").json()["oauth"] == NOT_SIGNING_IN
    r = lan.post("/api/zotero/oauth/start", json={})
    assert r.status_code == 400 and r.json()["detail"] == zotero_routes.NO_SIGN_IN
    assert not zotero_app.oauth  # zotero.org was not asked
    # a round trip started on loopback does not finish through another Host
    started = _start(_login(USER, PASSWORD))
    callback = urlsplit(_approved(zotero_app, started["url"]))
    page = fresh_client(base_url="http://gamma.lan:9001").get(f"{CALLBACK}?{callback.query}")
    assert page.status_code == 400 and "expired or was already used" in page.text
    assert not zotero_account.status(alice_id)["connected"]
    # with a public URL the same page signs in, and the approval comes back to that URL
    monkeypatch.setenv("GAMMA_PUBLIC_URL", PUBLIC)
    assert lan.get("/api/zotero/account").json()["oauth"] == {"available": True, "mode": "server"}
    _start(lan)
    assert zotero_app.oauth[-1]["params"]["oauth_callback"].startswith(PUBLIC + CALLBACK + "?")


def test_a_loopback_address_binds_nothing_unless_the_start_came_from_this_computer(zotero_app, alice_id):
    # a LAN server with no public URL: at the address Alice uses, signing in is not offered
    lan = _login(USER, PASSWORD, "http://192.168.1.5:9001", peer=ELSEWHERE)
    assert lan.post("/api/zotero/oauth/start", json={}).status_code == 400
    # her session with a Host header that names loopback: the round trip is bound to her client as anywhere else
    r = lan.post("/api/zotero/oauth/start", json={}, headers={"Host": "localhost:9001"})
    assert r.status_code == 200, r.text
    assert any(c.startswith(COOKIE + "=") for c in r.headers.get_list("set-cookie"))
    callback = _approved(zotero_app, r.json()["url"])
    assert callback.startswith("http://localhost:9001" + CALLBACK + "?")
    # the owner approves in a browser on the server's machine with no Gamma session there: asked, not connected
    page = fresh_client(base_url="http://localhost:9001", client=HERE).get(callback)
    assert page.status_code == 200 and "Connect Zotero to Gamma?" in page.text
    assert f"the Gamma account “{USER}” on" in page.text
    assert not zotero_account.status(alice_id)["connected"]
    assert not [step for step in zotero_app.oauth if step["path"] == "/oauth/access"]

    # a Docker install opened at localhost: its connections come from the container's gateway, and the browser
    # that started connects by its cookie
    docker = _login(USER, PASSWORD, "http://localhost:9001", peer=("172.17.0.1", 50000))
    r = docker.post("/api/zotero/oauth/start", json={})
    assert any(c.startswith(COOKIE + "=") for c in r.headers.get_list("set-cookie"))
    page = docker.get(_approved(zotero_app, r.json()["url"]))
    assert page.status_code == 200 and "Zotero is connected to Gamma" in page.text, page.text
    assert zotero_account.status(alice_id)["via"] == "oauth"
    zotero_account.disconnect(alice_id, revoke=False)


def test_a_server_reached_on_loopback_that_is_not_the_desktop_apps_binds_the_browser_and_asks(zotero_app,
                                                                                               alice_id,
                                                                                               monkeypatch):
    # behind an SSH tunnel or a port forward every person's requests come from loopback at localhost: the
    # round trip is bound to the browser that started it, as anywhere else
    monkeypatch.delenv("GAMMA_FOLDER_LINKS")
    alice = _login(USER, PASSWORD)
    r = alice.post("/api/zotero/oauth/start", json={})
    assert r.status_code == 200, r.text
    assert any(c.startswith(COOKIE + "=") for c in r.headers.get_list("set-cookie"))
    callback = _approved(zotero_app, r.json()["url"])
    # another person's browser through the same tunnel, holding no cookie: asked, never connected
    page = fresh_client(base_url=BASE, client=HERE).get(callback)
    assert page.status_code == 200 and "Connect Zotero to Gamma?" in page.text
    assert f"the Gamma account “{USER}” on" in page.text
    assert not zotero_account.status(alice_id)["connected"]
    assert not [step for step in zotero_app.oauth if step["path"] == "/oauth/access"]
    # the browser that started it connects by its cookie
    r = alice.post("/api/zotero/oauth/start", json={})
    page = alice.get(_approved(zotero_app, r.json()["url"]))
    assert page.status_code == 200 and "Zotero is connected to Gamma" in page.text
    zotero_account.disconnect(alice_id, revoke=False)


def test_cloud_mode_is_offered_only_where_the_broker_takes_this_server(broker, alice_id, monkeypatch):
    # a public server saved with Gamma Cloud but never connected to it: the broker does not know it
    monkeypatch.setenv("GAMMA_PUBLIC_URL", PUBLIC)
    page = _login(USER, PASSWORD, PUBLIC)
    assert page.get("/api/zotero/account").json()["oauth"] == NOT_SIGNING_IN
    r = page.post("/api/zotero/oauth/start", json={})
    assert r.status_code == 400 and r.json()["detail"] == cloud_auth.NOT_CONNECTED
    # connected (a client of its own): cloud mode, the broker sending the browser back to the public URL
    monkeypatch.setenv("GAMMA_CLOUD_CLIENT_ID", "lab-server")
    assert page.get("/api/zotero/account").json()["oauth"] == {"available": True, "mode": "cloud"}
    assert _query(_start(page)["url"])["server"] == PUBLIC
    # a plain-HTTP address off loopback, which the broker refuses
    monkeypatch.setenv("GAMMA_PUBLIC_URL", "http://192.168.1.5:9001")
    lan = _login(USER, PASSWORD, "http://192.168.1.5:9001")
    assert lan.get("/api/zotero/account").json()["oauth"] == NOT_SIGNING_IN
    r = lan.post("/api/zotero/oauth/start", json={})
    assert r.status_code == 400 and r.json()["detail"] == zotero_routes.NO_SIGN_IN
    assert not broker.forms


def test_the_brokers_error_description_is_shown(broker, alice, browser):
    state = _start(alice)["state"]
    sentence = "Zotero did not accept the request. Start again from Gamma."
    page = browser.get(CALLBACK, params={"error": "server_error", "error_description": sentence, "state": state})
    assert page.status_code == 400 and sentence in page.text
    assert _status(alice, state).json() == {"state": "error", "error": sentence}
    state = _start(alice)["state"]
    browser.get(CALLBACK, params={"error": "server_error", "state": state})
    assert _status(alice, state).json() == {"state": "error", "error": "Zotero was not connected (server_error)."}


def test_a_made_key_zotero_cannot_check_is_revoked_not_left_behind(zotero_app, alice, alice_id, browser,
                                                                    monkeypatch):
    def unreachable(self):
        raise zotero_web.ZoteroError("Zotero answered 500")

    callback = _approved(zotero_app, _start(alice)["url"])
    monkeypatch.setattr(zotero_web.ZoteroClient, "key_info", unreachable)
    page = browser.get(callback)
    assert page.status_code == 400 and "Zotero answered 500" in page.text
    made = [k for k in zotero_app.keys if k.startswith("zkey-oauth-")]
    assert made == [] and zotero_app.calls[-1][:2] == ("DELETE", "/keys/current")
    assert not zotero_account.status(alice_id)["connected"]
