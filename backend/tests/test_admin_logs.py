"""Server log buffer: admin-only access, poll cursor, and secret scrubbing."""

import pytest

from conftest import drop_user, login as _login, make_user as _make_user
from gamma.logbuf import last_seq, log, scrub, tail


@pytest.fixture(scope="module")
def logadmin(client):
    _make_user("logadmin", "logadminpw", is_admin=1)
    yield _login("logadmin", "logadminpw")
    drop_user("logadmin")


@pytest.fixture(scope="module")
def plainuser(client):
    _make_user("plainuser", "plainpw", is_admin=0)
    yield _login("plainuser", "plainpw")
    drop_user("plainuser")


def test_scrub_masks_secret_shapes():
    assert "hunter2" not in scrub("login failed: password=hunter2")
    assert "hunter2" not in scrub("config had api_key: hunter2")
    assert "abcdefgh12345678" not in scrub("header Authorization: Bearer abcdefgh12345678")
    assert scrub("rejected key sk-proj-abc123def456") == "rejected key sk-***"
    token43 = "x" * 43  # token_urlsafe(32) length — session/share tokens
    assert token43 not in scrub(f"lookup for {token43} failed")


def test_scrub_keeps_debuggable_ids():
    # Block ids (uuid-hex, 32) and upload names (sha256[:24]) stay readable.
    assert scrub("indexing 0123456789abcdef01234567 failed") == "indexing 0123456789abcdef01234567 failed"
    line = "[http] request=a1b2c3d4 GET /api/blocks status=401 session=alice"
    assert scrub(line) == line


def test_logs_require_admin(anon, plainuser):
    assert anon.get("/api/admin/logs").status_code == 401         # no session
    assert plainuser.get("/api/admin/logs").status_code == 403    # non-admin


def test_logs_tail_cursor_and_endpoint_scrub(logadmin):
    log.warning("[test] upstream refused token=supersecretvalue")
    r = logadmin.get("/api/admin/logs")
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert entries, "buffer should contain at least the test line"
    line = next(e for e in reversed(entries) if e["msg"].startswith("[test]"))
    assert "supersecretvalue" not in line["msg"]
    assert line["level"] == "WARNING"
    # Poll cursor: nothing new after the newest seq.
    last_seq = entries[-1]["seq"]
    assert logadmin.get(f"/api/admin/logs?after={last_seq}").json()["entries"] == []


def test_seeded_admin_password_never_enters_buffer():
    """seed.py prints the one-time admin password with raw print() on purpose —
    the startup seed ran in this very process, so its line must be absent."""
    assert not any("[startup]   password" in e["msg"] for e in tail(0))


def test_the_console_access_log_leaves_out_answered_task_polls(client):
    """uvicorn's access log keeps every request but the tray's answered
    GET /api/jobs, which comes every 1.5 s while a job runs."""
    import logging

    access = logging.getLogger("uvicorn.access")

    def shown(method, path, status):
        record = access.makeRecord("uvicorn.access", logging.INFO, __file__, 0, '%s - "%s %s HTTP/%s" %d',
                                   ("127.0.0.1:1", method, path, "1.1", status), None)
        return access.filter(record)

    assert not shown("GET", "/api/jobs", 200)
    assert shown("GET", "/api/jobs", 500)
    assert shown("GET", "/api/jobs/abc", 200)
    assert shown("POST", "/api/jobs/metadata", 200)
    assert shown("GET", "/api/quota", 200)


@pytest.mark.parametrize("http", ["httptools", "h11"])
def test_an_invalid_request_says_who_sent_what(client, http):
    """uvicorn's bare "Invalid HTTP request received." gains the sender and
    the shape of what arrived (logbuf._InvalidRequestDetail), from a real
    server with either parser. h11 waits for a line end before it parses,
    so the bare TLS handshake only trips httptools (the Docker image's)."""
    import socket
    import threading
    import time

    import uvicorn

    async def app(scope, receive, send):
        pass

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, http=http, lifespan="off", log_config=None))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]

    def warning_for(payload):
        after = tail()[-1]["seq"] if tail() else 0
        with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
            s.sendall(payload)
            assert s.recv(1024).startswith(b"HTTP/1.1 400")  # logged before the answer goes out
        return next(e["msg"] for e in tail(after) if e["msg"].startswith("Invalid HTTP request received"))

    try:
        line = warning_for(b"GET /x?share=hunter2 HTTP/1.1\r\nHost: a\r\nBad Header\r\n\r\n")
        assert line.startswith("Invalid HTTP request received from 127.0.0.1:")
        assert "starts 'GET /x?...'" in line and "hunter2" not in line
        assert line.endswith(")")  # the parser's own complaint
        line = warning_for(b"PROXY TCP4 203.0.113.9 10.0.0.1 51234 9001\r\nGET / HTTP/1.1\r\n\r\n")
        assert line.endswith(": a PROXY protocol header, which this server does not read")
        if http == "httptools":
            line = warning_for(b"\x16\x03\x01\x00\xa5\x01\x00\x00\xa1\x03\x03" + bytes(32))
            assert line.endswith(": a TLS handshake (https:// sent to this plain-HTTP port)")
    finally:
        server.should_exit = True
        thread.join(10)


def test_a_dropped_websocket_is_one_info_line(client):
    """The websockets library logs a socket that failed with a plain OSError
    (Windows' WinError 121) as "data transfer failed" with a traceback, on
    uvicorn.error with the protocol as `websocket`; logbuf._DroppedWebSocket
    makes it one info line, which raises no log-errors notice. Any other
    exception stays an error with its traceback."""
    import logging

    class Protocol:
        client = ("127.0.0.1", 50123)

    socket_log = logging.LoggerAdapter(logging.getLogger("uvicorn.error"), {"websocket": Protocol()})

    def logged(exc):
        after = tail()[-1]["seq"] if tail() else 0
        try:
            raise exc
        except Exception:
            socket_log.error("data transfer failed", exc_info=True)
        return next(e for e in tail(after) if "dropped:" in e["msg"] or "data transfer failed" in e["msg"])

    errors = last_seq("error")
    entry = logged(OSError(22, "The semaphore timeout period has expired", None, 121))
    assert entry["level"] == "INFO"
    assert entry["msg"].startswith("WebSocket from 127.0.0.1:50123 dropped: [")
    assert entry["msg"].endswith("The semaphore timeout period has expired")
    assert last_seq("error") == errors
    entry = logged(RuntimeError("a bug in the frame reader"))
    assert entry["level"] == "ERROR" and "Traceback" in entry["msg"]
