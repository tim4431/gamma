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
def _quick_approvals(monkeypatch):
    """A chat test whose tool call waits on an approval nobody answers gives
    up in seconds, not the ten minutes a person gets (gamma/ai_permissions.py)."""
    from gamma import ai_permissions
    monkeypatch.setattr(ai_permissions, "APPROVAL_TIMEOUT", 3.0)


def page_id_drift(conn):
    """The blocks whose stored ``page_id`` is not where their parent links
    lead, ``[(id, stored, walked)]`` — empty when every writer kept it. The
    walk, in Python apart from the migration's SQL one: '' on a reserved
    parentless row (and on a row no walk from one reaches: its parent gone,
    a cycle), every block below ``folders`` / ``labels`` is in that tree, the
    block right under another reserved row is its own page, any other block
    is in its parent's page."""
    rows = conn.execute("SELECT id, parent_id, page_id FROM unified_blocks").fetchall()
    parent = {r[0]: r[1] for r in rows}

    def walked(block_id):
        below, cur, seen = "", block_id, set()
        while cur in parent and cur not in seen:
            seen.add(cur)
            if parent[cur] is None:
                return cur if below and cur in ("folders", "labels") else below
            below, cur = cur, parent[cur]
        return ""

    return [(bid, stored, walked(bid)) for bid, _, stored in rows if stored != walked(bid)]


def page_changes_drift(conn):
    """The pages whose row of the change log (``page_changes``) does not say
    what they are, ``[(id, where, kind)]`` — empty when every writer touched
    what it wrote (blocks_store.touch_page): a page of the library has a
    ``live`` row stamped no earlier than the page (a writer that stamped the
    page without touching it leaves the page newer than its row), a page in
    Recently deleted a ``deleted`` one, the folder or label tree (the
    pseudo-pages) a ``live`` one no older than its newest block once it has
    blocks, and no ``live`` row names anything else."""
    rows = conn.execute(
        "SELECT b.id, b.parent_id, c.kind FROM unified_blocks b LEFT JOIN page_changes c ON c.page_id = b.id "
        "WHERE b.parent_id IN ('root', 'trash') AND (c.kind IS NULL OR c.kind IS NOT "
        "(CASE WHEN b.parent_id = 'trash' THEN 'deleted' WHEN c.at >= b.updated_at THEN 'live' END))").fetchall()
    trees = conn.execute(
        "SELECT t.page_id, NULL, c.kind FROM (SELECT page_id, MAX(updated_at) AS at FROM unified_blocks "
        "WHERE page_id IN ('folders', 'labels') GROUP BY page_id) t LEFT JOIN page_changes c ON c.page_id = t.page_id "
        "WHERE c.kind IS NOT 'live' OR c.at < t.at").fetchall()
    return rows + trees + conn.execute(
        "SELECT c.page_id, b.parent_id, c.kind FROM page_changes c LEFT JOIN unified_blocks b ON b.id = c.page_id "
        "WHERE c.kind = 'live' AND b.parent_id IS NOT 'root' "
        "AND NOT (c.page_id IN ('folders', 'labels') AND b.id IS NOT NULL AND b.parent_id IS NULL)").fetchall()


def notes_index_drift(conn):
    """'' when the notes index (``block_fts``) holds exactly what its view
    makes of the block rows — every writer's rows went through the
    triggers — else FTS5's complaint. FTS5's check is an INSERT: ``conn``
    is writable, with db.register_functions; nothing is kept."""
    import sqlite3

    try:
        conn.execute("INSERT INTO block_fts (block_fts, rank) VALUES ('integrity-check', 1)")
        return ""
    except sqlite3.OperationalError:
        raise  # a locked file: no verdict
    except sqlite3.DatabaseError as e:  # SQLITE_CORRUPT_VTAB: the index and the rows disagree
        return str(e)
    finally:
        conn.rollback()


_PAGES_DBS_CHECKED: dict = {}


@pytest.fixture(autouse=True)
def _page_ids_kept():
    """After every test, the suite's workspaces whose pages.db it wrote hold
    a ``page_id`` on every row that matches the parent walk
    (``page_id_drift``), a change-log row on every page that says what it
    is (``page_changes_drift``) and a notes index that matches the rows
    (``notes_index_drift``): whatever writer the test drove kept all
    three. A file in an older shape (a test building one) or that will not
    open is passed by."""
    import sqlite3
    from contextlib import closing

    from gamma.db import register_functions

    yield
    from gamma import config
    for db in config.WORKSPACES_DIR.glob("*/pages.db"):
        try:
            stamp = tuple(p.stat().st_mtime_ns for p in (db, db.with_name("pages.db-wal")) if p.exists())
            if _PAGES_DBS_CHECKED.get(db) == stamp:
                continue
            _PAGES_DBS_CHECKED[db] = stamp
            with closing(sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True, timeout=10)) as conn:
                if "page_id" not in {r[1] for r in conn.execute("PRAGMA table_info(unified_blocks)")}:
                    continue
                drift = page_id_drift(conn)
                has_log = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'page_changes'").fetchone()
                untouched = page_changes_drift(conn) if has_log else []
                has_index = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'block_fts'").fetchone()
            stale = ""
            if has_index:
                with closing(sqlite3.connect(str(db), timeout=10)) as conn:
                    register_functions(conn)
                    stale = notes_index_drift(conn)
        except (OSError, sqlite3.Error):
            continue
        assert not drift, f"workspace {db.parent.name}: page_id off the parent walk (id, stored, walked): {drift[:5]}"
        assert not untouched, f"workspace {db.parent.name}: pages the change log misreads (id, parent, kind): {untouched[:5]}"
        assert not stale, f"workspace {db.parent.name}: the notes index does not match the blocks: {stale}"


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
    from gamma.db import account_id, connect_users_db
    from gamma.seed import insert_account

    owner = _USER_OWNERS.setdefault(username.lower(), _caller_file())
    if owner != _caller_file():
        pytest.fail(f"account {username!r} is already used by {owner}; pick a module-unique name")

    with connect_users_db() as conn:
        user_id = account_id(conn, username)
        if not user_id:
            user_id = insert_account(conn, username, bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
                                     is_admin=bool(is_admin))
            conn.commit()
    return workspaces.ensure_personal(user_id)


def drop_user(username):
    """Remove an account ``make_user`` made, with its sessions: a module that
    makes an admin must not leave one behind (test_admin_users assumes it
    knows every admin in the shared users.db)."""
    from gamma.db import connect_users_db

    with connect_users_db() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id = (SELECT id FROM users WHERE username = ?)",
                     (username,))
        conn.execute("DELETE FROM users WHERE username = ?", (username,))
        conn.commit()


def account_of(username):
    """The id of the account named ``username`` ("" when none is) — what the
    storage helpers and the account columns take."""
    from gamma.db import account_id, connect_users_db
    with connect_users_db() as conn:
        return account_id(conn, username)


def workspace_of(username):
    """The account's personal workspace id."""
    from gamma import workspaces
    return workspaces.default_workspace(account_of(username))


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


def _tree_block(client, tree, names, params=None):
    """The block at ``names`` (from the top) of the folder or label tree,
    inserted through the tree's ops where missing; its id."""
    import secrets
    node = client.get(f"/api/blocks/{tree}/subtree", params=params).json()["block"]
    parent = tree
    for name in names:
        node = next((c for c in node["children"] if c["content"] == name), None)
        if node is None:
            node = {"id": secrets.token_urlsafe(9), "content": name, "children": []}
            r = client.post(f"/api/pages/{tree}/ops", params=params, json={"ops": [
                {"op": "insert", "id": node["id"], "parent": parent, "content": name}]})
            assert r.status_code == 200, r.text
        parent = node["id"]
    return parent


def make_folder(client, path, params=None):
    """The id of the folder at ``path`` ("a/b": names from the top) in the
    client's workspace, made through the ``folders`` tree's ops where
    missing — what a test files pages under (``properties.folders``)."""
    return _tree_block(client, "folders", [n.strip() for n in path.split("/") if n.strip()], params)


def make_label(client, name, params=None):
    """The id of the label ``name``, made through the ``labels`` tree's ops
    when missing."""
    return _tree_block(client, "labels", [name], params)


def folder_names(client, params=None):
    """{folder id: its names from the top} in the client's workspace, read
    through the API (``GET /blocks/folders/subtree``)."""
    out, todo = {}, [([], n) for n in client.get("/api/blocks/folders/subtree", params=params).json()["block"]["children"]]
    while todo:
        above, node = todo.pop()
        out[node["id"]] = [*above, node["content"]]
        todo += [(out[node["id"]], c) for c in node["children"]]
    return out


def label_names(client, params=None):
    """{label id: name} in the client's workspace."""
    tree = client.get("/api/blocks/labels/subtree", params=params).json()["block"]
    return {n["id"]: n["content"] for n in tree["children"]}


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
