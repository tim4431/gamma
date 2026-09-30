from conftest import guest_name, login, make_user


def test_session_requires_login(anon):
    r = anon.get("/api/blocks/root/children")
    assert r.status_code == 401


def test_bad_login_rejected(client):
    r = client.post("/api/login", json={"username": "nobody", "password": "wrong"})
    assert r.status_code == 401


def test_guest_login_and_session(guest):
    r = guest.get("/api/session")
    assert r.status_code == 200
    data = r.json()
    assert data["user"] == guest_name() and data["user"].startswith("guest-") and len(data["user"]) == 14
    assert data["is_guest"] is True
    assert data["guest_expires_at"].endswith("Z")  # when the account and its workspace go
    # The build a problem report names the server by (gamma/version.py).
    assert set(data["build"]) == {"version", "commit", "label", "frozen"}
    assert data["build"]["label"]


def test_user_guard_matching_header_passes(guest):
    r = guest.get("/api/blocks/root/children", headers={"X-Gamma-User": guest_name()})
    assert r.status_code == 200


def test_user_guard_mismatch_rejected(guest):
    """A tab that believes it's another user must not read or write this
    session's data (the browser-wide cookie was switched under it)."""
    r = guest.get("/api/blocks/root/children", headers={"X-Gamma-User": "someone-else"})
    assert r.status_code == 409
    assert r.headers["X-Gamma-Session-User"] == guest_name()
    assert len(r.headers["X-Gamma-Request-ID"]) == 8

    r = guest.post("/api/blocks", json={"parent_id": "root", "content": "x"},
                   headers={"X-Gamma-User": "someone-else"})
    assert r.status_code == 409


def test_user_guard_signed_out_session():
    """Guard header without a valid session → 409 with an empty session user
    (the frontend turns that into the auth-expired flow, not the conflict UI)."""
    from fastapi.testclient import TestClient
    from gamma.app import app
    with TestClient(app) as c:
        r = c.get("/api/blocks/root/children", headers={"X-Gamma-User": "ghost"})
        assert r.status_code == 409
        assert r.headers["X-Gamma-Session-User"] == ""


def test_request_log_names_why_a_request_failed(client, caplog):
    """Uvicorn prints every access; Gamma's own [http] line supplements only
    the ones worth reading, and its `reason` says which kind each was. A 404
    is "not-found": nothing was rejected, the thing asked for is not here —
    routine for a client polling something the server may have let go, such
    as a chat's fetch handoff after a restart."""
    import logging

    make_user("log_reader", "log-password")
    signed_in = login("log_reader", "log-password")

    def reason_for(path):
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="gamma"):
            signed_in.get(path)
        lines = [r.message for r in caplog.records if r.message.startswith("[http]")]
        assert lines, f"no [http] line for {path}"
        return lines[-1].split("reason=")[1].strip()

    assert reason_for("/api/ai/handoffs/no-such-request") == "not-found"
    assert reason_for("/api/session") == "session-operation"


def test_ai_disabled_without_keys(guest):
    r = guest.get("/api/ai/models")
    assert r.status_code == 200
    data = r.json()
    assert data["enabled"] is False
    assert data["default_prompt"]
    assert data["metadata_prompt"]
    assert data["cite_prompt"]
