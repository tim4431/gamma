"""Administrator-confirmed public origins, including TLS-terminating proxies."""
import socket

import pytest
from fastapi.testclient import TestClient

from conftest import make_user
from gamma import host_scope
from gamma.db import connect_users_db
from gamma.server_settings import public_url_settings, validate_public_url
import test_mcp_oauth as oauth


@pytest.fixture
def admin(client, monkeypatch, request):
    """A signed-in admin's client at http://gamma.example, or at the
    address an indirect parameter names."""
    from gamma.app import app
    base_url = getattr(request, "param", "http://gamma.example")
    monkeypatch.delenv("GAMMA_PUBLIC_URL", raising=False)
    monkeypatch.delenv("GAMMA_MCP_ALLOWED_HOSTS", raising=False)
    ws = make_user("origin-admin", "pw", is_admin=1)
    with connect_users_db() as conn:
        conn.execute("DELETE FROM settings WHERE key = 'public_url'")
        conn.execute("UPDATE users SET is_admin = 1 WHERE username = 'origin-admin'")
    with TestClient(app, base_url=base_url) as c:
        assert c.post("/api/login", json={"username": "origin-admin", "password": "pw"}).status_code == 200
        yield c, ws
    with connect_users_db() as conn:
        conn.execute("DELETE FROM settings WHERE key = 'public_url'")
        conn.execute("UPDATE users SET is_admin = 0 WHERE username = 'origin-admin'")


def test_confirmed_origin_enables_live_oauth_and_transport(admin, monkeypatch):
    c, ws = admin
    assert not c.get("/api/integrations/tokens").json()["oauth_available"]
    result = c.put("/api/admin/settings", json={"public_url": " https://GAMMA.example/ "},
                   headers={"Origin": "https://gamma.example"})
    assert result.status_code == 200, result.text
    assert result.json()["public_url"] == "https://gamma.example"
    assert public_url_settings()["public_url_source"] == "saved"
    # Read via a fresh application instance to verify persistence, not a cache.
    from gamma.app import create_app
    with TestClient(create_app(), base_url="http://gamma.example") as fresh:
        assert fresh.get("/.well-known/oauth-authorization-server").json()["issuer"] == "https://gamma.example"
    listing = c.get("/api/integrations/tokens").json()
    assert listing["oauth_available"] and listing["mcp_url"] == "https://gamma.example/mcp"
    assert "https://gamma.example/.well-known" in c.get("/mcp").headers["www-authenticate"]
    monkeypatch.setattr(oauth, "BASE", "https://gamma.example")
    client_id, code = oauth.approve(c, ws)
    token = oauth.exchange(c, client_id, code).json()["access_token"]
    manual = c.post("/api/integrations/tokens", json={"name": "Manual test"}).json()["token"]
    def rpc(bearer, host="gamma.example"):
        return c.post("/mcp", headers={"Host": host, "Authorization": f"Bearer {bearer}",
                      "Accept": "application/json, text/event-stream"},
                      json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert rpc(token).status_code == 200  # Same manager, created BEFORE confirmation.
    assert rpc(manual, "evil.example").status_code == 421
    assert c.put("/api/admin/settings", json={"public_url": "https://new.example"}).status_code == 200
    assert rpc(manual).status_code == 421  # Old host removed from the live allowlist.
    assert rpc(manual, "new.example").status_code == 200
    assert rpc(token, "new.example").status_code == 401  # Old OAuth resource cannot be retargeted.
    assert c.put("/api/admin/settings", json={"public_url": ""}).status_code == 200
    assert rpc(manual, "new.example").status_code == 421
    assert not c.get("/api/integrations/tokens").json()["oauth_available"]


NAS = "http://192.168.1.10:8000"


@pytest.mark.parametrize("admin", [NAS], indirect=True)
def test_a_nas_on_this_network_serves_assistants_over_plain_http(admin, monkeypatch):
    # No certificate, no domain: its address on this network is confirmed,
    # and an assistant signs in through it and connects, as at https.
    c, ws = admin
    assert not c.get("/api/integrations/tokens").json()["oauth_available"]
    result = c.put("/api/admin/settings", json={"public_url": NAS + "/"}, headers={"Origin": NAS})
    assert result.status_code == 200, result.text
    assert result.json()["public_url"] == NAS
    assert c.get("/.well-known/oauth-authorization-server").json()["issuer"] == NAS
    listing = c.get("/api/integrations/tokens").json()
    assert listing["oauth_available"] and listing["mcp_url"] == NAS + "/mcp"
    monkeypatch.setattr(oauth, "BASE", NAS)
    client_id, code = oauth.approve(c, ws)
    token = oauth.exchange(c, client_id, code).json()["access_token"]
    manual = c.post("/api/integrations/tokens", json={"name": "Manual test"}).json()["token"]
    for bearer in (token, manual):
        r = c.post("/mcp", headers={"Host": "192.168.1.10:8000", "Authorization": f"Bearer {bearer}",
                                    "Accept": "application/json, text/event-stream"},
                   json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert r.status_code == 200, r.text


def test_a_name_of_this_network_that_dns_puts_on_the_internet_is_refused(admin, monkeypatch):
    # A search domain or a resolver's rewrite can make nas.lan public:
    # assistants would then sign in in the clear across the internet.
    c, _ = admin
    answers = {"nas.lan": "203.0.113.5", "nas.home.arpa": "192.168.1.5"}

    def resolve(host, port, *args, **kwargs):
        if host not in answers:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (answers[host], 0))]
    monkeypatch.setattr(host_scope, "_resolved", {})
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    refused = c.put("/api/admin/settings", json={"public_url": "http://nas.lan:8000"})
    assert refused.status_code == 400 and "resolves to an address on the internet" in refused.text
    assert public_url_settings()["public_url"] == ""
    assert c.put("/api/admin/settings", json={"public_url": "http://nas.home.arpa:8000"}).status_code == 200
    # a name that does not resolve here is taken by its form: the NAS's own mDNS name, say
    assert c.put("/api/admin/settings", json={"public_url": "http://gamma-nas.local"}).json()["public_url"] == "http://gamma-nas.local"
    # a lookup that did not answer in time says nothing of where the name is
    monkeypatch.setattr(host_scope, "resolved", lambda host, timeout=2.0: ("lan", True))
    slow = c.put("/api/admin/settings", json={"public_url": "http://slow.lan:8000"})
    assert slow.status_code == 400 and "could not be looked up in time" in slow.text
    assert public_url_settings()["public_url"] == "http://gamma-nas.local"


@pytest.mark.parametrize("admin", ["http://nas.lan:8000"], indirect=True)
def test_an_allowed_host_over_plain_http_signs_in_only_once_confirmed(admin, monkeypatch):
    # GAMMA_MCP_ALLOWED_HOSTS lets manual tokens through a name; browser
    # sign-in over plain http is the confirmed address's alone.
    c, _ = admin
    monkeypatch.setenv("GAMMA_MCP_ALLOWED_HOSTS", "nas.lan:8000")
    assert c.get("/.well-known/oauth-authorization-server").status_code == 421
    assert not c.get("/api/integrations/tokens").json()["oauth_available"]
    manual = c.post("/api/integrations/tokens", json={"name": "Manual test"}).json()["token"]
    r = c.post("/mcp", headers={"Host": "nas.lan:8000", "Authorization": f"Bearer {manual}",
                                "Accept": "application/json, text/event-stream"},
               json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 200, r.text


def test_public_origin_permissions_and_override(admin, monkeypatch):
    c, _ = admin
    assert c.put("/api/admin/settings", json={"public_url": "https://gamma.example"},
                 headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.put("/api/admin/settings", json={"public_url": "https://gamma.example"},
                 headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert public_url_settings()["public_url"] == ""
    monkeypatch.setenv("GAMMA_PUBLIC_URL", "https://managed.example")
    assert c.get("/api/admin/settings").json()["public_url_source"] == "environment"
    assert c.put("/api/admin/settings", json={"public_url": "https://gamma.example"}).status_code == 400
    make_user("origin-reader", "pw")
    c.post("/api/login", json={"username": "origin-reader", "password": "pw"})
    assert c.put("/api/admin/settings", json={"public_url": "https://gamma.example"}).status_code == 403
    assert c.get("/api/admin/settings").status_code == 403


@pytest.mark.parametrize("value", ["http://gamma.example", "http://203.0.113.5:8000", "http://nas.example.com",
    "http://[2001:db8::1]", "http://evil\u3002com", "http://203\u30020\u3002113\u30025", "http://3405803781",
    "http://0xcb007105", "https://gamma.example/path", "https://gamma.example?",
    "https://gamma.example#", "https://user:pw@gamma.example", "https://*.example", "https://gamma.example:bad",
    "https://gamma.example:0", "https://gamma.example:65536", "https://good.example\\@evil.example",
    "https://gamma.\nexample", "javascript:alert(1)", "https://"])
def test_unsafe_origins_rejected(value):
    with pytest.raises(ValueError):
        validate_public_url(value)


@pytest.mark.parametrize("value,expected", [("https://Gamma.example:443/", "https://gamma.example"),
    ("https://gamma.example:8443", "https://gamma.example:8443"), ("http://localhost:9001", "http://localhost:9001"),
    ("http://[::1]:9001/", "http://[::1]:9001"), ("http://192.168.1.10:8000/", "http://192.168.1.10:8000"),
    ("http://NAS.local:80", "http://nas.local"), ("http://nas:8000", "http://nas:8000"),
    ("http://100.101.102.103:8000", "http://100.101.102.103:8000"), ("http://[fd00::5]:9001/", "http://[fd00::5]:9001"),
    ("https://[2001:db8::1]:8443/", "https://[2001:db8::1]:8443"), ("", "")])
def test_public_origin_normalization(value, expected):
    assert validate_public_url(value) == expected


def test_invalid_setting_does_not_partially_save(admin):
    c, _ = admin
    before = c.get("/api/admin/settings").json()
    assert c.put("/api/admin/settings", json={"public_url": "http://gamma.example", "quota_mb": 123}).status_code == 400
    assert c.get("/api/admin/settings").json() == before
