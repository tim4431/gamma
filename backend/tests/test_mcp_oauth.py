import base64
import hashlib
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from conftest import account_of, make_user
from gamma.ai_tools import mcp_tools
from gamma.db import connect_users_db
from gamma.integrations import resolve_token

BASE = "http://localhost"
REDIRECT = "http://127.0.0.1:1455/auth/callback"
VERIFIER = "v" * 64
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip("=")


@pytest.fixture
def browser(client):
    from gamma.app import app
    ws = make_user("oauth-reader", "pw")
    with TestClient(app, base_url=BASE) as c:
        assert c.post("/api/login", json={"username": "oauth-reader", "password": "pw"}).status_code == 200
        yield c, ws


def start(c, **overrides):
    registered = c.post("/oauth/register", json={"client_name": "Codex test", "redirect_uris": [REDIRECT],
                                                  "token_endpoint_auth_method": "none"})
    assert registered.status_code == 201, registered.text
    client_id = registered.json()["client_id"]
    params = {"client_id": client_id, "redirect_uri": REDIRECT, "response_type": "code", "state": "test-state",
              "scope": "gamma:read", "code_challenge": CHALLENGE, "code_challenge_method": "S256", "resource": BASE + "/mcp"}
    response = c.get("/oauth/authorize", params={**params, **overrides}, follow_redirects=False)
    return client_id, response


def pending(c, **overrides):
    client_id, value, _ = asked(c, **overrides)
    return client_id, value


def asked(c, **overrides):
    """A sign-in waiting on the consent page: the client's id, what the
    consent posts back, and what the page shows."""
    client_id, response = start(c, **overrides)
    assert response.status_code == 302, response.text
    request_id = parse_qs(urlsplit(response.headers["location"]).query)["gamma_oauth"][0]
    details = c.get("/api/integrations/oauth/request", params={"request_id": request_id})
    assert details.status_code == 200, details.text
    return client_id, {"request_id": request_id, "csrf": details.json()["csrf"]}, details.json()


def approve(c, ws):
    client_id, value = pending(c)
    result = c.post("/api/integrations/oauth/consent", json={**value, "approve": True, "workspaces": [ws]})
    assert result.status_code == 200, result.text
    query = parse_qs(urlsplit(result.json()["redirect_url"]).query)
    assert query["state"] == ["test-state"]
    return client_id, query["code"][0]


def exchange(c, registered_id, auth_code, **overrides):
    return c.post("/oauth/token", data={"grant_type": "authorization_code", "client_id": registered_id,
                                       "code": auth_code, "redirect_uri": REDIRECT, "code_verifier": VERIFIER,
                                       "resource": BASE + "/mcp", **overrides})


def test_discovery_and_browser_signin_roundtrip(browser):
    c, ws = browser
    discovery = c.get("/mcp")
    assert discovery.status_code == 401
    assert BASE + "/.well-known/oauth-protected-resource/mcp" in discovery.headers["www-authenticate"]
    assert c.get("/.well-known/oauth-protected-resource/mcp").json()["resource"] == BASE + "/mcp"
    assert c.get("/.well-known/oauth-authorization-server").json()["code_challenge_methods_supported"] == ["S256"]
    client_id, code = approve(c, ws)
    result = exchange(c, client_id, code)
    assert result.status_code == 200, result.text
    token = result.json()["access_token"]
    assert result.json()["expires_in"] == 90 * 86400
    assert resolve_token(token, BASE + "/mcp") == (account_of("oauth-reader"), ws)
    assert resolve_token(token, "https://other.example/mcp") is None
    assert result.headers["cache-control"] == "no-store"
    with connect_users_db() as conn:
        values = conn.execute("SELECT value FROM mcp_oauth").fetchall()
        assert not any(code in row[0] or token in row[0] for row in values)
    assert exchange(c, client_id, code).status_code == 400  # single use
    tools = c.post("/mcp", headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"},
                   json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert tools.status_code == 200, tools.text
    assert {tool["name"] for tool in tools.json()["result"]["tools"]} == mcp_tools() | {"list_workspaces", "read_gamma_link", "export_page"}
    listing = c.get("/api/integrations/tokens").json()
    connection = next(t for t in listing["tokens"] if t["name"] == "Codex test (OAuth)")
    c.delete("/api/integrations/tokens/" + connection["id"])
    assert resolve_token(token, BASE + "/mcp") is None


@pytest.mark.parametrize("changes", [{"code_verifier": "x" * 64}, {"resource": "https://wrong.example/mcp"},
                                     {"redirect_uri": "http://127.0.0.1:2222/callback"}, {"client_id": "unregistered"}])
def test_exchange_rejects_wrong_proof_resource_redirect_and_client(browser, changes):
    c, ws = browser
    client_id, code = approve(c, ws)
    assert exchange(c, client_id, code, **changes).status_code in (400, 401)
    assert exchange(c, client_id, code).status_code == 200


def test_consent_requires_matching_browser_session_and_membership(browser):
    from gamma.app import app
    c, ws = browser
    _, value = pending(c)
    payload = {**value, "approve": True, "workspaces": [ws]}
    assert c.post("/api/integrations/oauth/consent", json={**payload, "csrf": "forged"}).status_code == 403
    assert c.post("/api/integrations/oauth/consent", json=payload, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post("/api/integrations/oauth/consent", json={**payload, "workspaces": ["other"]}).status_code == 403
    with TestClient(app, base_url=BASE) as second:
        second.post("/api/login", json={"username": "oauth-reader", "password": "pw"})
        assert second.post("/api/integrations/oauth/consent", json=payload).status_code == 403
    assert c.post("/api/integrations/oauth/consent", json=payload, headers={"Origin": BASE}).status_code == 200
    assert c.post("/api/integrations/oauth/consent", json=payload).status_code in (400, 403)


def test_cancellation_does_not_create_token(browser):
    c, _ = browser
    before = c.get("/api/integrations/tokens").json()["tokens"]
    _, value = pending(c)
    result = c.post("/api/integrations/oauth/consent", json={**value, "approve": False})
    assert parse_qs(urlsplit(result.json()["redirect_url"]).query)["error"] == ["access_denied"]
    assert c.get("/api/integrations/tokens").json()["tokens"] == before


@pytest.mark.parametrize("configured_by", ["saved", "environment"])
@pytest.mark.parametrize("approve_request", [True, False])
def test_consent_behind_https_proxy(browser, monkeypatch, configured_by, approve_request):
    from gamma.app import app
    from gamma import server_settings

    _, ws = browser
    public = "https://gamma.example"
    monkeypatch.delenv("GAMMA_PUBLIC_URL", raising=False)
    if configured_by == "environment":
        monkeypatch.setenv("GAMMA_PUBLIC_URL", public)
    else:
        server_settings.set_public_url(public)
    try:
        # The proxy preserves Host but its connection to Gamma uses HTTP.
        with TestClient(app, base_url="http://gamma.example") as c:
            assert c.post("/api/login", json={"username": "oauth-reader", "password": "pw"}).status_code == 200
            client_id, response = start(c, resource=public + "/mcp")
            assert response.status_code == 302, response.text
            request_id = parse_qs(urlsplit(response.headers["location"]).query)["gamma_oauth"][0]
            details = c.get("/api/integrations/oauth/request", params={"request_id": request_id},
                            headers={"Origin": public})
            assert details.status_code == 200, details.text
            payload = {"request_id": request_id, "csrf": details.json()["csrf"],
                       "workspaces": [ws], "approve": approve_request}
            for origin in ("https://evil.example", "http://gamma.example", "https://gamma.example:444", "null"):
                denied = c.post("/api/integrations/oauth/consent", json=payload,
                                headers={"Origin": origin, "X-Forwarded-Host": "evil.example",
                                         "X-Forwarded-Proto": "https"})
                assert denied.status_code == 403, denied.text
            result = c.post("/api/integrations/oauth/consent", json=payload, headers={"Origin": public})
            assert result.status_code == 200, result.text
            query = parse_qs(urlsplit(result.json()["redirect_url"]).query)
            assert query["state"] == ["test-state"]
            if approve_request:
                tokens = exchange(c, client_id, query["code"][0], resource=public + "/mcp")
                assert tokens.status_code == 200, tokens.text
                assert resolve_token(tokens.json()["access_token"], public + "/mcp") == (account_of("oauth-reader"), ws)
            else:
                assert query["error"] == ["access_denied"]
    finally:
        if configured_by == "saved":
            server_settings.set_public_url("")


@pytest.mark.parametrize("uri", ["javascript:alert(1)", "http://evil.example/callback", "https://good.example/#fragment", "https://user:pass@example.com/cb"])
def test_unsafe_callbacks_rejected(browser, uri):
    c, _ = browser
    result = c.post("/oauth/register", json={"redirect_uris": [uri]})
    assert result.status_code == 400


def test_host_and_transport_boundaries(browser, monkeypatch):
    c, _ = browser
    assert c.get("/.well-known/oauth-authorization-server", headers={"Host": "evil.example"}).status_code == 400
    monkeypatch.setenv("GAMMA_PUBLIC_URL", "https://gamma.example")
    monkeypatch.setenv("GAMMA_MCP_ALLOWED_HOSTS", "gamma.example")
    assert c.get("/.well-known/oauth-authorization-server").status_code == 421
    result = c.get("/.well-known/oauth-authorization-server", headers={"Host": "gamma.example"})
    assert result.json()["issuer"] == "https://gamma.example"


def test_expired_request_and_code_fail(browser):
    c, ws = browser
    client_id, code = approve(c, ws)
    with connect_users_db() as conn:
        conn.execute("UPDATE mcp_oauth SET expires_at = ? WHERE kind = 'code'", (int(time.time()) - 1,))
    assert exchange(c, client_id, code).status_code == 400
    _, value = pending(c)
    with connect_users_db() as conn:
        conn.execute("UPDATE mcp_oauth SET expires_at = ? WHERE kind = 'request'", (int(time.time()) - 1,))
    assert c.get("/api/integrations/oauth/request", params={"request_id": value["request_id"]}).status_code == 400


def test_body_limit_and_guest_denial(browser):
    c, _ = browser
    assert c.post("/oauth/register", content="x" * 20000).status_code == 413
    _, response = start(c, resource="https://wrong.example/mcp")
    assert "error=" in response.headers["location"]
    c.post("/api/login-guest")
    assert c.get("/api/integrations/oauth/request?request_id=anything").status_code == 403


@pytest.mark.parametrize("path", ["/oauth/register", "/oauth/token"])
def test_oauth_endpoints_reject_oversized_bodies(browser, path):
    c, _ = browser
    response = c.post(path, content="x" * 16385,
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert response.status_code == 413


def test_bounded_request_replays_chunks_and_stops_at_limit():
    import asyncio
    from fastapi import HTTPException, Request
    from gamma.mcp_oauth import bounded_request

    async def check():
        scope = {"type": "http", "method": "POST", "path": "/oauth/token",
                 "query_string": b"", "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
                 "state": {"user": "reader"}}
        # Finish the stream explicitly, as a chunked ASGI request does.
        messages = iter([
            {"type": "http.request", "body": b"code=", "more_body": True},
            {"type": "http.request", "body": b"x" * (16384 - 5), "more_body": False},
        ])

        async def receive_at_limit():
            return next(messages)

        replay = await bounded_request(Request(scope, receive_at_limit))
        assert replay.state.user == "reader"
        assert len(await replay.body()) == 16384
        assert (await replay.form())["code"] == "x" * (16384 - 5)
        assert len(await replay.body()) == 16384  # SDK can reread after form parsing

        calls = 0

        async def receive_oversized():
            nonlocal calls
            calls += 1
            assert calls <= 2, "must stop before reading the remaining stream"
            return {"type": "http.request", "body": b"x" * 8193, "more_body": True}

        with pytest.raises(HTTPException) as exc:
            await bounded_request(Request(scope, receive_oversized))
        assert exc.value.status_code == 413
        assert calls == 2

    asyncio.run(check())


def test_logout_invalidates_an_unexchanged_code(browser):
    c, ws = browser
    client_id, code = approve(c, ws)
    c.post("/api/logout")
    assert exchange(c, client_id, code).status_code == 400


def test_official_sdk_discovers_registers_and_completes_oauth(browser):
    """The SDK generates PKCE/state/resource parameters, not hand-built forms."""
    import asyncio
    import httpx
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientMetadata
    from gamma.app import app

    c, ws = browser

    class MemoryStorage:
        tokens = None
        client = None

        async def get_tokens(self):
            return self.tokens

        async def set_tokens(self, value):
            self.tokens = value

        async def get_client_info(self):
            return self.client

        async def set_client_info(self, value):
            self.client = value

    storage = MemoryStorage()
    callback = {}

    async def redirect(url):
        response = c.get(url, follow_redirects=False)
        assert response.status_code == 302
        request_id = parse_qs(urlsplit(response.headers["location"]).query)["gamma_oauth"][0]
        details = c.get("/api/integrations/oauth/request", params={"request_id": request_id}).json()
        result = c.post("/api/integrations/oauth/consent", json={"request_id": request_id,
                        "csrf": details["csrf"], "workspaces": [ws], "approve": True})
        callback.update(parse_qs(urlsplit(result.json()["redirect_url"]).query))

    async def receive_callback():
        return callback["code"][0], callback["state"][0]

    async def run():
        oauth = OAuthClientProvider(BASE + "/mcp", OAuthClientMetadata(redirect_uris=[REDIRECT],
                                   client_name="SDK test", token_endpoint_auth_method="none"),
                                   storage, redirect, receive_callback)
        # ASGITransport doesn't propagate lifespan state; after successful auth
        # /mcp returns 503 here. The browser suite checks actual tool execution.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), auth=oauth) as http:
            response = await http.get(BASE + "/mcp")
            assert response.status_code == 503

    asyncio.run(run())
    assert storage.tokens is not None
    assert resolve_token(storage.tokens.access_token, BASE + "/mcp") == (account_of("oauth-reader"), ws)


def test_signin_approves_several_workspaces_or_all(browser):
    from gamma import workspaces
    from gamma.integrations import resolve_connection

    c, ws = browser
    lab = workspaces.create("OAuth lab", account_of("oauth-reader"))["id"]
    try:
        client_id, value = pending(c)
        assert c.post("/api/integrations/oauth/consent", json={**value, "approve": True}).status_code == 400
        result = c.post("/api/integrations/oauth/consent", json={**value, "approve": True, "workspaces": [lab, ws]})
        code = parse_qs(urlsplit(result.json()["redirect_url"]).query)["code"][0]
        token = exchange(c, client_id, code).json()["access_token"]
        assert resolve_connection(token, BASE + "/mcp")[:2] == (account_of("oauth-reader"), [lab, ws])
        client_id, value = pending(c)
        result = c.post("/api/integrations/oauth/consent", json={**value, "approve": True, "all_workspaces": True})
        code = parse_qs(urlsplit(result.json()["redirect_url"]).query)["code"][0]
        every = exchange(c, client_id, code).json()["access_token"]
        later = workspaces.create("OAuth later", account_of("oauth-reader"))["id"]
        assert {ws, lab, later} <= set(resolve_connection(every, BASE + "/mcp")[1])
        listing = c.get("/api/integrations/tokens").json()["tokens"]
        mine = [t for t in listing if t["name"] == "Codex test (OAuth)"]
        assert any(t["workspaces"] == [lab, ws] for t in mine) and any(t["all_workspaces"] for t in mine)
        assert not any(t["edit_all"] or t["edit_workspaces"] for t in mine)
        workspaces.delete(later)
        # of those it reads, those it may edit: one, or every one; never one it does not read
        client_id, value = pending(c)
        assert c.post("/api/integrations/oauth/consent", json={**value, "approve": True, "workspaces": [ws],
                                                               "edit_workspaces": [lab]}).status_code == 400
        client_id, value = pending(c)
        result = c.post("/api/integrations/oauth/consent", json={**value, "approve": True, "workspaces": [lab, ws],
                                                                 "edit_workspaces": [lab]})
        code = parse_qs(urlsplit(result.json()["redirect_url"]).query)["code"][0]
        editor = exchange(c, client_id, code).json()["access_token"]
        assert resolve_connection(editor, BASE + "/mcp").editable == (lab,)
        assert "edit_block" in _tool_names(c, editor)
        client_id, value = pending(c)
        result = c.post("/api/integrations/oauth/consent", json={**value, "approve": True, "all_workspaces": True,
                                                                 "edit_all": True})
        code = parse_qs(urlsplit(result.json()["redirect_url"]).query)["code"][0]
        everywhere = exchange(c, client_id, code).json()["access_token"]
        assert {ws, lab} <= set(resolve_connection(everywhere, BASE + "/mcp").editable)
        assert "edit_block" not in _tool_names(c, every)
    finally:
        for t in c.get("/api/integrations/tokens").json()["tokens"]:
            c.delete("/api/integrations/tokens/" + t["id"])
        workspaces.delete(lab)


def _tool_names(c, token):
    listed = c.post("/mcp", headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"},
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert listed.status_code == 200, listed.text
    return {tool["name"] for tool in listed.json()["result"]["tools"]}


@pytest.mark.parametrize("ticked", [True, False])
def test_signin_may_allow_answering_feedback(browser, ticked):
    """A client asking for gamma:feedback has the consent page's "answer my
    comments" ticked; the person decides, and the token's scope follows
    what they approved: a feedback token with the two answering tools, or
    a read one."""
    from gamma.integrations import resolve_connection

    c, ws = browser
    assert c.get("/.well-known/oauth-protected-resource/mcp").json()["scopes_supported"] == [
        "gamma:read", "gamma:feedback"]
    assert c.get("/.well-known/oauth-authorization-server").json()["scopes_supported"] == [
        "gamma:read", "gamma:feedback"]
    assert 'scope="gamma:read"' in c.get("/mcp").headers["www-authenticate"]  # the challenge still asks to read
    assert asked(c)[2]["feedback_asked"] is False
    client_id, value, shown = asked(c, scope="gamma:feedback gamma:read")
    assert shown["feedback_asked"] is True
    result = c.post("/api/integrations/oauth/consent", json={**value, "approve": True, "workspaces": [ws],
                                                             "feedback": ticked})
    assert result.status_code == 200, result.text
    tokens = exchange(c, client_id, parse_qs(urlsplit(result.json()["redirect_url"]).query)["code"][0])
    assert tokens.status_code == 200, tokens.text
    assert tokens.json()["scope"] == ("gamma:read gamma:feedback" if ticked else "gamma:read")
    token = tokens.json()["access_token"]
    assert resolve_connection(token, BASE + "/mcp").scope == ("feedback" if ticked else "read")
    names = _tool_names(c, token)
    assert ({"reply_feedback", "resolve_feedback"} <= names) is ticked and "list_feedback" in names
    # a read sign-in may be given the answering too
    if ticked:
        client_id, value = pending(c)
        result = c.post("/api/integrations/oauth/consent", json={**value, "approve": True, "workspaces": [ws],
                                                                 "feedback": True})
        tokens = exchange(c, client_id, parse_qs(urlsplit(result.json()["redirect_url"]).query)["code"][0])
        assert tokens.json()["scope"] == "gamma:read gamma:feedback"
        assert "reply_feedback" in _tool_names(c, tokens.json()["access_token"])
    for t in c.get("/api/integrations/tokens").json()["tokens"]:
        c.delete("/api/integrations/tokens/" + t["id"])


def test_other_scopes_are_refused(browser):
    c, _ = browser
    for scope in ("gamma:write", "gamma:read gamma:write", "", "gamma:read  gamma:feedback"):
        registered = c.post("/oauth/register", json={"client_name": "Writer", "redirect_uris": [REDIRECT],
                                                     "token_endpoint_auth_method": "none", "scope": scope})
        assert registered.status_code == 400 and registered.json()["error"] == "invalid_client_metadata", scope
    for scope in ("gamma:read gamma:feedback", "gamma:feedback"):
        registered = c.post("/oauth/register", json={"client_name": "Answerer", "redirect_uris": [REDIRECT],
                                                     "token_endpoint_auth_method": "none", "scope": scope})
        assert registered.status_code == 201 and registered.json()["scope"] == "gamma:read gamma:feedback"
    _, response = start(c, scope="gamma:write")
    assert response.status_code == 302 and "error=invalid_scope" in response.headers["location"]
