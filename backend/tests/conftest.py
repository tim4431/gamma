"""Test bootstrap: the whole suite runs against a throwaway data directory,
with AI providers unconfigured, using FastAPI's in-process TestClient (no
network, no running server needed)."""

import ipaddress
import os
import socket
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

# Must happen BEFORE importing gamma — config reads the environment at import.
os.environ["GAMMA_DATA_DIR"] = tempfile.mkdtemp(prefix="gamma-test-")
os.environ["GAMMA_SYNC_INTERVAL"] = "0"  # mirror rounds run only when a test asks
for var in ("GAMMA_STATIC_DIR", "GAMMA_AI_ANTHROPIC_API_KEY", "GAMMA_AI_OPENAI_API_KEY",
            "GAMMA_AI_API_KEY", "ANTHROPIC_AUTH_TOKEN", "GAMMA_AI_MODELS", "GAMMA_AI_MODEL",
            "GAMMA_ADMIN_USER", "GAMMA_ADMIN_PASSWORD"):
    os.environ.pop(var, None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _reset_ratelimit():
    """The whole suite shares one TestClient source IP, so the per-IP login
    throttle would trip mid-run. Clear counters before each test — production
    behavior is unchanged."""
    from gamma import ratelimit
    ratelimit._buckets.clear()
    yield


_LOOPBACK = {"localhost", "127.0.0.1", "::1", None, ""}
_socket_connect = socket.socket.connect
_getaddrinfo = socket.getaddrinfo


def _host_is_local(host):
    """Loopback names, and numeric addresses: the SSRF guard test hands
    getaddrinfo IP literals such as 169.254.169.254, which resolve without
    any network and are refused before a connect."""
    if host in _LOOPBACK:
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _blocked_connect(self, addr):
    host = addr[0] if isinstance(addr, tuple) else str(addr)
    if host in _LOOPBACK or (isinstance(host, str) and host.startswith("127.")):
        return _socket_connect(self, addr)
    raise AssertionError(f"test tried to open a network connection to {addr!r} — stub the fetch")


def _blocked_getaddrinfo(host, *args, **kwargs):
    if _host_is_local(host):
        return _getaddrinfo(host, *args, **kwargs)
    raise AssertionError(f"test tried to resolve {host!r} — stub the fetch")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """The suite is offline by construction: every metadata / PDF / AI fetch
    is stubbed, and a test that forgets to is a failure here, not a slow
    pass that depends on the network. TestClient talks to the app in-process,
    so only loopback ever needs a real socket."""
    monkeypatch.setattr(socket.socket, "connect", _blocked_connect)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked_getaddrinfo)
    yield


@pytest.fixture(scope="session")
def client():
    from gamma.app import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def anon():
    """A TestClient with no session at all — for "not signed in" checks.
    (`client` is shared by the whole run and carries whatever cookie the
    last login left, so it is never anonymous by the time most tests run.)"""
    from gamma.app import app
    return TestClient(app)


_GUEST: dict = {}


@pytest.fixture(scope="session")
def guest(client):
    """A TestClient logged in as a guest account of its own (cookie persists
    on the client). Every guest login mints a fresh account
    (gamma/guests.py); ``guest_name()`` is the one this fixture got."""
    r = client.post("/api/login-guest")
    assert r.status_code == 200, r.text
    _GUEST["name"] = r.json()["username"]
    return client


def guest_name():
    """The username of the session's ``guest`` fixture account (request the
    fixture first)."""
    assert _GUEST.get("name"), "request the guest fixture before guest_name()"
    return _GUEST["name"]


_USER_OWNERS: dict = {}


def _caller_file():
    """The test module whose code asked for the account (a fixture imported
    from another module counts for the module that defines it)."""
    import inspect
    return Path(inspect.stack()[2].filename).name


def make_user(username, password, is_admin=0):
    """Create (idempotently) a password account plus its personal workspace.
    Returns the workspace id.

    The whole run shares one data directory, so an account name belongs to
    the module that first creates it: a second module asking for the same
    name (with whatever password) would pass or fail depending on file
    order. Prefix names with the module's area (`bk_admin`, `ca_alice`)."""
    import bcrypt
    from gamma import workspaces
    from gamma.db import connect_users_db, page_now

    owner = _USER_OWNERS.setdefault(username.lower(), _caller_file())
    if owner != _caller_file():
        pytest.fail(f"account {username!r} is already used by {owner}; pick a module-unique name")

    with connect_users_db() as conn:
        if not conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            conn.execute(
                "INSERT INTO users (username, password_hash, is_guest, is_admin, created_at) VALUES (?, ?, 0, ?, ?)",
                (username, bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(), is_admin, page_now()),
            )
            conn.commit()
    return workspaces.ensure_personal(username)


def workspace_of(username):
    """The account's personal workspace id."""
    from gamma import workspaces
    return workspaces.default_workspace(username)


def login(username, password):
    """A fresh TestClient logged in as the account (cookie persists on it)."""
    from gamma.app import app
    c = TestClient(app)
    r = c.post("/api/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """A data directory of the test's own (tmp_path): every module that
    caches a data-directory path is pointed at it, so the suite's shared one
    is never touched. The app is built on the suite's directory first."""
    import gamma.app  # noqa: F401
    import gamma.auth as auth_mod
    import gamma.db as db_mod
    import gamma.seed as seed_mod
    import gamma.workspaces as ws_mod
    from gamma import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "USERS_DB", tmp_path / "users.db")
    monkeypatch.setattr(config, "WORKSPACES_DIR", tmp_path / "workspaces")
    monkeypatch.setattr(config, "LEGACY_USERS_DIR", tmp_path / "users")
    monkeypatch.setattr(config, "BACKUPS_DIR", tmp_path / "backups")
    monkeypatch.setattr(db_mod, "USERS_DB", tmp_path / "users.db")
    monkeypatch.setattr(db_mod, "WORKSPACES_DIR", tmp_path / "workspaces")
    monkeypatch.setattr(auth_mod, "USERS_DB", tmp_path / "users.db")
    monkeypatch.setattr(seed_mod, "WORKSPACES_DIR", tmp_path / "workspaces")
    monkeypatch.setattr(ws_mod, "WORKSPACES_DIR", tmp_path / "workspaces")
    return tmp_path


def make_page(guest, title="Test page", properties=None):
    r = guest.post("/api/blocks", json={"parent_id": "root", "content": title})
    assert r.status_code == 200, r.text
    block = r.json()
    if properties:
        r = guest.put(f"/api/blocks/{block['id']}", json={"properties": properties})
        assert r.status_code == 200, r.text
    return block


def require_math_renderer():
    """Guard for assertions that count typeset-math vector paths: they need
    ziamath, a hard requirement (requirements.txt) that is easy to miss when
    the tests run under some other interpreter than backend/venv's. Fail with
    the cause instead of a puzzling path count."""
    try:
        import ziamath  # noqa: F401
    except ImportError:
        pytest.fail("ziamath is not importable — run the tests with backend/venv's python "
                    "(pip install -r requirements.txt)")


# --- races and sockets ----------------------------------------------------------

def at_once(calls):
    """Run the callables in threads started together on a barrier; their
    results (or the exceptions they raised, noted with where and when) in
    order."""
    barrier = threading.Barrier(len(calls))
    results = [None] * len(calls)

    def run(i, fn):
        barrier.wait()
        started = time.monotonic()
        try:
            results[i] = fn()
        except Exception as e:  # noqa: BLE001 — the test reads it; the note says where and when
            where = traceback.extract_tb(e.__traceback__)[-4:]
            e.add_note(f"after {time.monotonic() - started:.2f} s at " +
                       " <- ".join(f"{Path(f.filename).name}:{f.lineno} {f.line}" for f in reversed(where)))
            results[i] = e

    threads = [threading.Thread(target=run, args=(i, fn)) for i, fn in enumerate(calls)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    return results


def together(n, fn):
    """``fn`` in ``n`` threads started together (``at_once``); its results,
    failing the test when one raised."""
    results = at_once([fn] * n)
    errors = [r for r in results if isinstance(r, Exception)]
    assert not errors, errors
    return results


def slowed(monkeypatch, module, name, seconds=0.05, *, before=False):
    """Make ``module.name`` take a moment, after its work (or ``before``
    it): the window between a writer's check and its write, wide enough for
    the others to walk into."""
    real = getattr(module, name)

    def slow(*args, **kwargs):
        if before:
            time.sleep(seconds)
        out = real(*args, **kwargs)
        if not before:
            time.sleep(seconds)
        return out
    monkeypatch.setattr(module, name, slow)


def recv(sock, kind, skip=None):
    """The next message of ``kind`` on a page socket (within 20). With
    ``skip``, only messages of those kinds may come before it; without,
    anything else is passed over."""
    for _ in range(20):
        msg = sock.receive_json()
        if msg["t"] == kind:
            return msg
        assert skip is None or msg["t"] in skip, msg
    raise AssertionError(f"no {kind} message")
