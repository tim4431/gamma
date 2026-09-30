"""Publisher credentials stay private to an account and exact HTTPS host."""

import json
import time
from urllib.request import Request

import pytest

from ai_fixtures import FakeResp
from conftest import guest_name, login, make_user, make_page
from gamma import ai_web
from gamma import publisher_sessions as sessions
from gamma.db import connect_users_db
from gamma.net_guard import guarded_urlopen
from test_net_guard import transport  # noqa: F401 -- fake urllib transport fixture
from test_ai_tools_web import _text_pdf

HOST = "journals.aps.org"
SECRET = "private-publisher-session"
COOKIE = {"name": "access", "value": SECRET, "domain": ".aps.org",
          "hostOnly": False, "path": "/"}
AI_SOURCE = "https://doi.org/10.1103/ai-session-test"
AI_PDF = f"https://{HOST}/prl/pdf/10.1103/ai-session-test"
AI_TEXT = "Subscriber-only research findings."


@pytest.fixture
def ai_fetch(accounts, monkeypatch):
    """Real chat/tool/resolver/cookie path; only the model and HTTP are fake."""
    import gamma.routers.ai as ai_mod
    import gamma.routers.pdf as pdf_mod

    for caller in accounts:
        response = caller.post("/api/ai/providers", json={
            "protocol": "anthropic", "base_url": "https://ai-provider.invalid",
            "api_key": "sk-test-key-123", "models": "claude-solo"})
        assert response.status_code == 200, response.text

    def fake_open(messages, system, entry, rt, pdf_b64s=None, **kwargs):
        # Neither the provider nor its tool specs receive the credentials.
        assert SECRET not in json.dumps([messages, system, kwargs])
        assert sessions.current_user.get() is None
        if messages[-1]["role"] == "tool":
            return FakeResp([{"type": "content_block_delta", "delta": {
                "type": "text_delta", "text": "Finished reading."}}])
        return FakeResp([
            {"type": "content_block_start", "content_block": {
                "type": "tool_use", "id": "fetch1", "name": "fetch_paper"}},
            {"type": "content_block_delta", "delta": {"type": "input_json_delta",
                # A model-supplied identity must not override the caller.
                "partial_json": json.dumps({"source": AI_SOURCE, "publisher_user": "pub_alice"})}},
            {"type": "content_block_stop"},
        ])

    monkeypatch.setattr(ai_mod, "_open_ai", fake_open)
    monkeypatch.setattr(ai_mod.ai_catalog, "context_window", lambda *args: (0, ""))
    monkeypatch.setattr(pdf_mod, "_open_access_pdfs", lambda doi: [])
    ai_web.clear_cache()

    def fetch(caller, *, stream=True, permissions=None, wait=False, **kwargs):
        # `wait` off: a blocked fetch answers at once instead of holding the
        # reply open on its card (gamma/ai_agent.PaperWait) — these tests are
        # about whose cookies the fetch used, not about the card.
        response = caller.post("/api/ai/chat", json={
            "prompt": "Read the cited paper", "agent_scope": "folder", "stream": stream,
            "paper_wait": wait, "permissions": permissions or {}}, **kwargs)
        assert response.status_code == 200, response.text
        assert SECRET not in response.text
        actions = ([line["action"] for line in map(json.loads, response.text.splitlines())
                    if "action" in line] if stream else response.json()["actions"])
        action, = actions
        return action

    yield fetch
    ai_web.clear_cache()


@pytest.fixture
def accounts(client):
    for name in ("pub_alice", "pub_bob"):
        make_user(name, "publisher-password")
        with connect_users_db() as conn:
            conn.execute("DELETE FROM publisher_sessions WHERE username=?", (name,))
            conn.commit()
    return login("pub_alice", "publisher-password"), login("pub_bob", "publisher-password")


def connect(client, cookies=None, host=HOST, **kwargs):
    return client.post("/api/publisher-sessions", json={
        "host": host, "cookies": [COOKIE] if cookies is None else cookies,
    }, headers={"x-forwarded-proto": "https", **kwargs})


def cookie_header(username, url):
    token = sessions.current_user.set(username)
    try:
        jar = sessions.cookie_jar()
    finally:
        sessions.current_user.reset(token)
    req = Request(url)
    jar.add_cookie_header(req)
    return req.get_header("Cookie")


def test_connect_encrypted_private_and_disconnect(accounts):
    alice, bob = accounts
    assert connect(alice).status_code == 200
    result = alice.get("/api/publisher-sessions")
    assert result.json()["sessions"][0]["host"] == HOST
    assert SECRET not in result.text and "encrypted" not in result.text
    assert bob.get("/api/publisher-sessions").json()["sessions"] == []
    with connect_users_db() as conn:
        encrypted = conn.execute("SELECT encrypted FROM publisher_sessions WHERE username='pub_alice'").fetchone()[0]
    assert SECRET not in encrypted
    assert cookie_header("pub_alice", f"https://{HOST}/paper.pdf") == "access=" + SECRET
    assert cookie_header("pub_bob", f"https://{HOST}/paper.pdf") is None
    assert cookie_header(None, f"https://{HOST}/paper.pdf") is None
    # Another user's disconnect cannot remove Alice's connection.
    assert bob.delete(f"/api/publisher-sessions/{HOST}").status_code == 200
    assert cookie_header("pub_alice", f"https://{HOST}/paper.pdf")
    assert alice.delete(f"/api/publisher-sessions/{HOST}").status_code == 200
    assert cookie_header("pub_alice", f"https://{HOST}/paper.pdf") is None


def test_no_guest_anonymous_share_or_plain_http_import(accounts, guest, anon):
    alice, _ = accounts
    assert connect(anon).status_code == 401
    assert connect(guest).status_code == 403
    assert alice.post("/api/publisher-sessions", json={"host": HOST, "cookies": [COOKIE]}).status_code == 400
    assert alice.post("/api/publisher-sessions?share=anything", json={"host": HOST, "cookies": [COOKIE]}).status_code == 403
    assert connect(alice, **{"x-gamma-user": "pub_bob"}).status_code == 409


@pytest.mark.parametrize("cookie", [
    {**COOKIE, "domain": ".org"},
    {**COOKIE, "domain": ".stanford.edu"},
    {**COOKIE, "domain": "login.aps.org", "hostOnly": True},
    {**COOKIE, "name": "bad\r\nCookie"},
    {**COOKIE, "value": SECRET + "\r\nCookie: leak"},
    {**COOKIE, "partitionKey": {"topLevelSite": "https://aps.org"}},
    {**COOKIE, "expirationDate": time.time() - 10},
])
def test_reject_bad_cookie_without_echoing_value(accounts, cookie):
    response = connect(accounts[0], [cookie])
    assert response.status_code == 400
    assert SECRET not in response.text


def test_cookie_scope_and_expiry(accounts, monkeypatch):
    alice, _ = accounts
    now = time.time()
    assert connect(alice, [{**COOKIE, "path": "/prl/"}]).status_code == 200
    assert cookie_header("pub_alice", f"https://{HOST}/prl/pdf/a")
    for url in (f"http://{HOST}/prl/pdf/a", "https://link.aps.org/prl/pdf/a",
                f"https://evil.{HOST}/prl/pdf/a", f"https://{HOST}.evil.org/prl/pdf/a",
                f"https://{HOST}/other/pdf/a"):
        assert cookie_header("pub_alice", url) is None
    monkeypatch.setattr(sessions.time, "time", lambda: now + sessions.SESSION_AGE + 10)
    assert cookie_header("pub_alice", f"https://{HOST}/prl/pdf/a") is None
    assert alice.get("/api/publisher-sessions").json()["sessions"] == []


def test_connected_cookies_in_pdf_flow_but_not_share(accounts, transport):
    alice, bob = accounts
    routes, seen = transport
    start = "https://doi.org/10.1103/session-test"
    pdf = f"https://{HOST}/prl/pdf/10.1103/session-test"
    routes[start] = (302, {"Location": pdf}, b"")
    routes[pdf] = (200, {"Content-Type": "application/pdf"}, b"%PDF-1.4 test")
    assert connect(alice).status_code == 200
    response = alice.post("/api/resolve-pdf", json={"source_url": start, "allow_oa": False})
    assert response.status_code == 200, response.text
    assert seen == [(start, None), (pdf, "access=" + SECRET)]
    seen.clear()
    response = alice.get("/api/pdf", params={"source_url": pdf})
    assert response.content.startswith(b"%PDF")
    assert response.headers["cache-control"] == "private, no-store"
    assert seen == [(pdf, "access=" + SECRET)]
    seen.clear()
    bob.get("/api/pdf", params={"source_url": pdf})
    assert seen == [(pdf, None)]
    seen.clear()
    page = make_page(alice, "Session paper", properties={"source_url": pdf, "doc_id": "publisher-test"})
    share = alice.post(f"/api/share/{page['id']}").json()["token"]
    alice.get("/api/pdf", params={"source_url": pdf, "share": share})
    assert seen == [(pdf, None)]


@pytest.mark.parametrize("stream", [True, False])
def test_ai_fetch_uses_callers_cookies_and_private_cache(accounts, transport, ai_fetch, stream):
    alice, bob = accounts
    routes, seen = transport
    routes[AI_SOURCE] = (302, {"Location": AI_PDF}, b"")
    routes[AI_PDF] = (200, {"Content-Type": "application/pdf"}, _text_pdf([AI_TEXT]))
    assert connect(alice).status_code == 200

    action = ai_fetch(alice, stream=stream)
    assert action["kind"] == "fetch" and AI_TEXT in action["result"]
    assert (AI_SOURCE, None) in seen
    assert all(cookie == "access=" + SECRET for url, cookie in seen if url == AI_PDF)
    seen.clear()
    assert AI_TEXT in ai_fetch(alice, stream=stream)["result"]
    assert seen == []  # later windows reuse only this account's document

    routes[AI_PDF] = (403, {}, b"")
    for caller, kwargs in ((bob, {}), (alice, {"params": {"share": "public-view"}})):
        action = ai_fetch(caller, stream=stream, **kwargs)
        assert action["error"] and AI_TEXT not in action["result"]
        assert seen and all(cookie is None for _, cookie in seen)
        seen.clear()


def test_ai_fetch_retries_cached_abstract_after_connect_and_refresh(accounts, transport, ai_fetch):
    alice, _ = accounts
    routes, seen = transport
    routes[AI_SOURCE] = (302, {"Location": AI_PDF}, b"")
    routes[AI_PDF] = (200, {"Content-Type": "text/html"}, b"<p>Only the abstract.</p>")
    assert "Fetched web page" in ai_fetch(alice)["result"]

    assert connect(alice).status_code == 200
    routes[AI_PDF] = (200, {"Content-Type": "application/pdf"}, _text_pdf([AI_TEXT]))
    seen.clear()
    assert AI_TEXT in ai_fetch(alice)["result"]
    assert (AI_PDF, "access=" + SECRET) in seen

    assert connect(alice, [{**COOKIE, "value": "refreshed-access"}]).status_code == 200
    routes[AI_PDF] = (200, {"Content-Type": "application/pdf"}, _text_pdf(["New session content."]))
    seen.clear()
    assert "New session content." in ai_fetch(alice)["result"]
    assert (AI_PDF, "access=refreshed-access") in seen


@pytest.mark.parametrize("stream", [True, False])
def test_ai_fetch_cookie_permission_bypasses_private_cache_without_disconnecting(
        accounts, transport, ai_fetch, stream):
    alice, _ = accounts
    routes, seen = transport
    routes[AI_SOURCE] = (302, {"Location": AI_PDF}, b"")
    routes[AI_PDF] = (200, {"Content-Type": "application/pdf"}, _text_pdf([AI_TEXT]))
    assert connect(alice).status_code == 200
    assert AI_TEXT in ai_fetch(alice, stream=stream)["result"]

    seen.clear()
    routes[AI_PDF] = (200, {"Content-Type": "text/html"}, b"<p>Public abstract.</p>")
    action = ai_fetch(alice, stream=stream, permissions={"publisher_cookies": False})
    assert action["kind"] == "fetch" and "Public abstract." in action["result"]
    assert AI_TEXT not in action["result"]
    assert seen and all(cookie is None for _, cookie in seen)
    assert alice.get("/api/publisher-sessions").json()["sessions"]

    seen.clear()
    assert AI_TEXT in ai_fetch(alice, stream=stream, permissions={"publisher_cookies": True})["result"]
    assert seen == []  # the account's authenticated cache is still available


@pytest.mark.parametrize("revoke", ["disconnect", "expire"])
def test_ai_fetch_does_not_reuse_cache_after_session_revocation(
        accounts, transport, ai_fetch, monkeypatch, revoke):
    alice, _ = accounts
    routes, seen = transport
    now = time.time()
    # One cookie expires earlier than its snapshot, which must also change
    # the cache partition when that individual credential stops being usable.
    assert connect(alice, [{**COOKIE, "expirationDate": now + 5},
                           {**COOKIE, "name": "preferences", "value": "layout"}]).status_code == 200
    routes[AI_SOURCE] = (302, {"Location": AI_PDF}, b"")
    routes[AI_PDF] = (200, {"Content-Type": "application/pdf"}, _text_pdf([AI_TEXT]))
    assert AI_TEXT in ai_fetch(alice)["result"]
    if revoke == "disconnect":
        assert alice.delete(f"/api/publisher-sessions/{HOST}").status_code == 200
    else:
        monkeypatch.setattr(sessions.time, "time", lambda: now + 10)
    routes[AI_PDF] = (403, {}, b"")
    seen.clear()
    action = ai_fetch(alice)
    assert action["error"] and AI_TEXT not in action["result"]
    assert seen and all(SECRET not in (cookie or "") for _, cookie in seen)


def test_ai_fetch_guest_and_anonymous_do_not_borrow_sessions(
        accounts, transport, ai_fetch, guest, anon):
    from gamma.ai_settings import load_provider_entries, save_provider_entries

    # Even if a guest somehow has stored credentials, the chat excludes them.
    sessions.save(guest_name(), HOST, [COOKIE])
    save_provider_entries(guest_name(), load_provider_entries("pub_alice"))
    try:
        routes, seen = transport
        routes[AI_SOURCE] = (302, {"Location": AI_PDF}, b"")
        routes[AI_PDF] = (403, {}, b"")
        assert ai_fetch(guest)["error"]
        assert seen and all(cookie is None for _, cookie in seen)
        seen.clear()
        assert anon.post("/api/ai/chat", json={"prompt": "Read it"}).status_code == 401
        assert seen == []
    finally:
        sessions.disconnect(guest_name(), HOST)
        save_provider_entries(guest_name(), [])


def test_connected_cookie_does_not_follow_external_redirect(accounts, transport):
    routes, seen = transport
    start, target = f"https://{HOST}/paper.pdf", "https://other.example/stolen"
    routes[start] = (302, {"Location": target}, b"")
    routes[target] = (200, {}, b"done")
    connect(accounts[0])
    token = sessions.current_user.set("pub_alice")
    try:
        with guarded_urlopen(start) as response:
            response.read()
    finally:
        sessions.current_user.reset(token)
    assert seen == [(start, "access=" + SECRET), (target, None)]


def test_reimport_replaces_cookie_snapshot_and_caps_lifetime(accounts):
    alice, _ = accounts
    connect(alice)
    result = connect(alice, [{**COOKIE, "value": "replacement", "expirationDate": time.time() + 10**8}])
    assert result.status_code == 200
    assert result.json()["expires_at"] <= time.time() + sessions.MAX_AGE
    assert cookie_header("pub_alice", f"https://{HOST}/") == "access=replacement"


def test_corrupt_ciphertext_does_not_break_pdf_fetch(accounts):
    connect(accounts[0])
    with connect_users_db() as conn:
        conn.execute("UPDATE publisher_sessions SET encrypted='broken' WHERE username='pub_alice'")
        conn.commit()
    assert cookie_header("pub_alice", f"https://{HOST}/") is None


def test_sibling_connections_keep_separate_credentials(accounts):
    alice, _ = accounts
    connect(alice)
    assert connect(alice, [{**COOKIE, "value": "sibling"}], host="link.aps.org").status_code == 200
    assert cookie_header("pub_alice", f"https://{HOST}/") == "access=" + SECRET
    assert cookie_header("pub_alice", "https://link.aps.org/") == "access=sibling"


def test_delete_account_removes_connections(accounts):
    alice, _ = accounts
    connect(alice)
    make_user("pub_admin", "publisher-password", is_admin=1)
    admin = login("pub_admin", "publisher-password")
    response = admin.delete("/api/admin/users/pub_alice")
    assert response.status_code == 200, response.text
    with connect_users_db() as conn:
        assert conn.execute("SELECT host FROM publisher_sessions WHERE username='pub_alice'").fetchall() == []


def test_import_rejects_plain_text_and_oversized_payload(accounts):
    alice, _ = accounts
    headers = {"x-forwarded-proto": "https"}
    assert alice.post("/api/publisher-sessions", content="{}", headers=headers).status_code == 415
    headers["content-type"] = "application/json"
    assert alice.post("/api/publisher-sessions", content="x" * (256 * 1024 + 1), headers=headers).status_code == 413
