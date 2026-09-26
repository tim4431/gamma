"""The shared AI allowance (docs/dev/guests.md): the admin's limits on the
server's shared entries, tokens per account per rolling 24 hours, counted
from the ai_usage rows on ``server:`` provider ids, reported by ai_runtime,
/api/ai/models and /api/ai/usage, and enforced in ai_client.open_ai — the
one door every token-spending call goes through — as a 429. An account's
own entries are never metered."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from ai_fixtures import FakeResp

LIMIT = 1000


@pytest.fixture(scope="module")
def admin(client):
    from conftest import login, make_user
    make_user("allow_admin", "pw", is_admin=1)
    return login("allow_admin", "pw")


@pytest.fixture(scope="module")
def member(client):
    from conftest import login, make_user
    make_user("allow_member", "pw")
    return login("allow_member", "pw")


@pytest.fixture
def shared(admin):
    """One shared Anthropic entry (the fake upstream below speaks its SSE);
    the server's shared config — entries, guest switch, allowance — is
    emptied after the test (the whole run shares one users.db)."""
    from gamma import ai_settings
    r = admin.post("/api/admin/ai-providers", json={
        "protocol": "anthropic", "name": "Lab key", "api_key": "sk-ant-shared-allow-4242",
        "models": "lab-model"})
    assert r.status_code == 200, r.text
    try:
        yield r.json()["providers"][0]["id"]
    finally:
        ai_settings.save_server_ai({"providers": [], "guests": False})


def forget_usage(user):
    """Every usage row of ``user``, metered ones included (ai_usage.clear
    keeps those)."""
    from gamma.db import connect_users_db
    with connect_users_db() as conn:
        conn.execute("DELETE FROM ai_usage WHERE username = ?", (user,))


@pytest.fixture(autouse=True)
def _fresh_usage():
    for user in ("allow_member", "allow_admin"):
        forget_usage(user)


def spend(user, provider_id, tokens, hours_ago=0):
    """One usage row as the recorder writes it (``hours_ago`` backdates it)."""
    from gamma import ai_usage
    from gamma.db import connect_users_db
    ai_usage.record(user, "chat", provider_id, "Lab key", "lab-model", {"input": tokens, "output": 0})
    if hours_ago:
        at = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        with connect_users_db() as conn:
            conn.execute("UPDATE ai_usage SET at = ? WHERE id = (SELECT MAX(id) FROM ai_usage "
                         "WHERE username = ?)", (at, user))


def turn(text, input_tokens=10):
    """One streamed Anthropic reply reporting its tokens."""
    return FakeResp([
        {"type": "message_start", "message": {"usage": {"input_tokens": input_tokens, "output_tokens": 1}}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}},
    ])


class Sent(list):
    """The requests that went upstream; ``.queue`` = responses to hand out."""
    queue: list


@pytest.fixture
def upstream(monkeypatch):
    """Fake the provider BELOW ai_client.open_ai (the allowance check runs
    for real): every call gets the next queued response, else a short
    streamed reply. Returns the list of requests that went out."""
    import urllib.request
    sent, queue = Sent(), []

    def fake_urlopen(req, timeout=None):
        sent.append(req)
        return queue.pop(0) if queue else turn("ok")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    sent.queue = queue
    return sent


def lines(r):
    return [json.loads(line) for line in r.text.splitlines() if line.strip()]


def set_allowance(admin, **allowance):
    r = admin.put("/api/admin/ai-providers", json={"allowance": allowance})
    assert r.status_code == 200, r.text
    return r.json()["allowance"]


# --- the admin's setting ---------------------------------------------------------

def test_admin_round_trip_and_validation(admin, member, shared):
    from gamma.ai_settings import ALLOWANCE_MAX
    assert admin.get("/api/admin/ai-providers").json()["allowance"] == {"accounts": 0, "guests": 0}
    # Partial: a key left out keeps its value; the guest switch rides along.
    assert set_allowance(admin, accounts=5000) == {"accounts": 5000, "guests": 0}
    r = admin.put("/api/admin/ai-providers", json={"guests": True, "allowance": {"guests": 300}})
    assert r.status_code == 200 and r.json()["guests"] is True
    assert r.json()["allowance"] == {"accounts": 5000, "guests": 300}
    assert admin.get("/api/admin/ai-providers").json()["allowance"] == {"accounts": 5000, "guests": 300}
    # Only the switch: the allowance stays.
    assert admin.put("/api/admin/ai-providers", json={"guests": False}).json()["allowance"] == \
        {"accounts": 5000, "guests": 300}
    for bad in ({"accounts": -1}, {"accounts": "5"}, {"guests": 1.5}, {"accounts": True},
                {"everyone": 5}, {"accounts": ALLOWANCE_MAX + 1}, [5]):
        assert admin.put("/api/admin/ai-providers", json={"allowance": bad}).status_code in (400, 422), bad
    assert set_allowance(admin, accounts=0, guests=0) == {"accounts": 0, "guests": 0}
    assert member.put("/api/admin/ai-providers", json={"allowance": {"accounts": 1}}).status_code == 403


# --- what is counted and reported ------------------------------------------------

def test_runtime_reports_the_allowance(admin, member, shared):
    from gamma.ai_settings import ai_runtime
    from gamma.ai_usage import shared_used
    # No limit: the usage is reported, nothing is metered.
    spend("allow_member", shared, 40)
    assert ai_runtime("allow_member")["allowance"] == {"limit": 0, "used": 40, "exhausted": False}
    assert "allowance" not in ai_runtime("allow_member")["providers"][shared]
    forget_usage("allow_member")

    set_allowance(admin, accounts=LIMIT)
    spend("allow_member", shared, 300)
    spend("allow_member", "own-entry", 5000)          # an own entry never counts
    spend("allow_member", shared, 5000, hours_ago=25)  # outside the window
    spend("allow_admin", shared, 700)                  # someone else's
    assert shared_used("allow_member") == 300
    rt = ai_runtime("allow_member")
    assert rt["allowance"] == {"limit": LIMIT, "used": 300, "exhausted": False}
    assert rt["providers"][shared]["allowance"] == {"user": "allow_member", "limit": LIMIT}

    spend("allow_member", shared, 700)
    rt = ai_runtime("allow_member")
    assert rt["allowance"] == {"limit": LIMIT, "used": LIMIT, "exhausted": True}
    # The shared models stay listed (the picker shows them, and why they refuse).
    assert [m["id"] for m in rt["models"]] == [f"{shared}:lab-model"]

    models = member.get("/api/ai/models").json()
    assert models["enabled"] is True and models["allowance"] == rt["allowance"]


def test_usage_carries_the_allowance(admin, member, shared):
    body = member.get("/api/ai/usage").json()
    assert body["allowance"] == {"limit": 0, "used": 0, "exhausted": False}
    assert "windows" in body and "models" in body
    set_allowance(admin, accounts=LIMIT)
    spend("allow_member", shared, 250)
    body = member.get("/api/ai/usage").json()
    assert body["allowance"] == {"limit": LIMIT, "used": 250, "exhausted": False}
    assert body["windows"]["today"]["input"] == 250
    assert member.get("/api/ai/models").json()["allowance"] == body["allowance"]
    # Reset forgets the rest but not what the allowance still counts.
    spend("allow_member", "own-entry", 40)
    spend("allow_member", shared, 60, hours_ago=30)
    assert member.delete("/api/ai/usage").json()["deleted"] == 2
    body = member.get("/api/ai/usage").json()
    assert body["allowance"]["used"] == 250 and body["windows"]["all"]["input"] == 250


# --- the choke point -------------------------------------------------------------

def test_chat_under_then_over_the_limit(admin, member, shared, upstream):
    set_allowance(admin, accounts=LIMIT)
    spend("allow_member", shared, 500)
    r = member.post("/api/ai/chat", json={"prompt": "hi", "stream": True})
    assert r.status_code == 200, r.text
    assert "".join(l.get("delta", "") for l in lines(r)) == "ok" and len(upstream) == 1
    # The reply's own tokens were recorded against the shared entry.
    assert member.get("/api/ai/usage").json()["allowance"]["used"] == 500 + 10 + 2

    spend("allow_member", shared, LIMIT)
    used = 500 + 12 + LIMIT
    for stream in (True, False):
        r = member.post("/api/ai/chat", json={"prompt": "hi", "stream": stream})
        assert r.status_code == 429, r.text
        detail = r.json()["detail"]
        assert detail.startswith("This server's shared AI allowance for your account is used up")
        assert f"({used} of {LIMIT} tokens in the last 24 hours)" in detail
        assert "Settings → AI" in detail
    # Every other token-spending route refuses the same way; nothing went upstream.
    for body in ({"texts": ["Hallo"], "lang": "en"}, {"texts": ["Bonjour"], "lang": "en", "stream": True}):
        r = member.post("/api/ai/translate", json=body)
        assert r.status_code == 429 and "allowance" in r.json()["detail"], r.text
    probe = member.post("/api/ai/health", json={"provider_id": shared, "mode": "test"}).json()
    assert probe["ok"] is False and "allowance" in probe["error"]
    assert len(upstream) == 1
    # Raising the limit lets the account through again.
    set_allowance(admin, accounts=10 * LIMIT)
    assert member.post("/api/ai/chat", json={"prompt": "hi", "stream": True}).status_code == 200


def test_agent_loop_stops_at_the_limit_mid_stream(admin, member, shared, upstream):
    """The count is read fresh on every call: a round that uses the rest of
    the allowance ends the tool loop with the allowance's own error line."""
    set_allowance(admin, accounts=LIMIT)
    spend("allow_member", shared, 500)
    upstream.queue.append(FakeResp([
        {"type": "message_start", "message": {"usage": {"input_tokens": 600, "output_tokens": 1}}},
        {"type": "content_block_start", "content_block": {"type": "tool_use", "id": "t1", "name": "list_pages"}},
        {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": "{}"}},
        {"type": "content_block_stop"},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 5}},
    ]))
    r = member.post("/api/ai/chat", json={"prompt": "list", "agent_scope": "folder", "folder": "",
                                          "stream": True})
    assert r.status_code == 200, r.text
    out = lines(r)
    assert any("action" in l for l in out)
    assert out[-1]["error"].startswith("This server's shared AI allowance for your account is used up")
    assert len(upstream) == 1  # the second round never went out


def test_own_entries_are_never_metered(admin, member, shared, upstream):
    set_allowance(admin, accounts=LIMIT)
    spend("allow_member", shared, 2 * LIMIT)
    own = member.post("/api/ai/providers", json={
        "protocol": "anthropic", "api_key": "sk-ant-own-allow-0001", "models": "own-model"}).json()
    own_id = next(p["id"] for p in own["providers"] if not p.get("shared"))
    try:
        models = member.get("/api/ai/models").json()
        assert models["default"] == f"{own_id}:own-model"
        assert models["allowance"]["exhausted"] is True
        r = member.post("/api/ai/chat", json={"prompt": "hi", "stream": True, "model": f"{own_id}:own-model"})
        assert r.status_code == 200 and len(upstream) == 1, r.text
        r = member.post("/api/ai/chat", json={"prompt": "hi", "stream": True, "model": f"{shared}:lab-model"})
        assert r.status_code == 429
        # The own reply's tokens don't count toward the shared allowance.
        assert member.get("/api/ai/usage").json()["allowance"]["used"] == 2 * LIMIT
    finally:
        member.delete(f"/api/ai/providers/{own_id}")


def test_guests_have_their_own_limit(admin, member, shared, upstream):
    from gamma.app import app
    guest = TestClient(app)
    assert guest.post("/api/login-guest").status_code == 200
    session = guest.get("/api/session").json()
    name = session["user"]
    assert session["is_guest"] is True
    forget_usage(name)
    try:
        # Accounts unlimited, guests metered; the switch decides access at all.
        r = admin.put("/api/admin/ai-providers", json={"guests": True, "allowance": {"guests": 300}})
        assert r.status_code == 200
        assert member.get("/api/ai/models").json()["allowance"]["limit"] == 0  # unlimited, still reported
        assert guest.get("/api/ai/models").json()["allowance"] == {"limit": 300, "used": 0, "exhausted": False}
        spend(name, shared, 100)
        r = guest.post("/api/ai/chat", json={"prompt": "hi", "stream": True})
        assert r.status_code == 200, r.text
        spend(name, shared, 200)
        r = guest.post("/api/ai/chat", json={"prompt": "hi", "stream": True})
        assert r.status_code == 429 and "of 300 tokens" in r.json()["detail"]
        assert guest.get("/api/ai/usage").json()["allowance"]["exhausted"] is True
        # Switched off: no shared entry applies, so nothing is reported either.
        admin.put("/api/admin/ai-providers", json={"guests": False})
        assert guest.get("/api/ai/models").json()["allowance"] is None
        assert guest.get("/api/ai/usage").json()["allowance"] is None
    finally:
        forget_usage(name)
