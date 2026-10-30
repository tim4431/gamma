"""Shared fixtures and helpers of the Zotero link's engine test files
(test_zotero_sync*.py), against the fake Zotero (zotero_fake.py) serving
``zotero_fixtures.standard_library``: the owner's account, signed in with
Zotero connected; rounds that run only when a test asks, with the budget
lifted; PDF replacements whose relocation is recorded, not run; the page's
own notes sent without their quiet wait (opt-in); the check that one more
round writes nothing (zotero_idle); and the helpers that make a link, run
its round through the API and read what the link and the workspace hold.
test_workspaces.py and test_ws_backups.py take only ``link_row`` and
``drop_link_row`` (a link's row written straight into users.db).

A test file imports what it uses by name, the fixtures with ``# noqa: F401
(fixtures)``, and keeps its own ``USER`` and ``PASSWORD``: ``owner_id`` and
``me`` read them from the importing module. Imported, the autouse fixtures
``background`` and ``no_relocation`` apply to that file alone. Never load
this module through ``pytest_plugins``: that registers it for the whole
session, and its autouse patches would reach every test there."""

import json
import time

import pytest
from conftest import account_of, login, make_user
from zotero_fake import FakeZotero
from zotero_fixtures import pdf_bytes, standard_library
from zotero_idle import Idle

from gamma import (ops, storage, sync_clock, zotero_account, zotero_local, zotero_remote, zotero_sync,
                   zotero_sync_blocks, zotero_sync_items, zotero_sync_pages, zotero_sync_pdfs, zotero_sync_rows)
from gamma import zotero_annotations as za
from gamma.blocks_store import FOLDERS, tree_rows
from gamma.db import connect_pages_db, connect_users_db, page_now

LIB = "users/123"
GROUP = "groups/9"
WRITES = ("POST", "PATCH", "PUT", "DELETE")

_OWNERS: dict = {}  # USER: the engine file it belongs to


# --- fixtures ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def owner_id(request):
    """The account of the importing module's ``USER`` (password
    ``PASSWORD``), one engine file's alone."""
    user, module = request.module.USER, request.module.__name__
    assert _OWNERS.setdefault(user.lower(), module) == module, f"{user} is {_OWNERS[user.lower()]}'s account"
    make_user(user, request.module.PASSWORD)
    return account_of(user)


@pytest.fixture(autouse=True)
def background(monkeypatch):
    """Rounds run only when a test asks, with the budget lifted; the
    background rounds asked for are kept (the fixture's value)."""
    asked = []
    monkeypatch.setattr(zotero_sync, "sync_in_background", asked.append)
    monkeypatch.setattr(zotero_sync, "ROUND_UNITS", 100_000)
    monkeypatch.setattr(zotero_sync, "ROUND_BUDGET_S", 100_000)
    zotero_sync.forget_access()
    yield asked
    zotero_sync.forget_access()


@pytest.fixture(autouse=True)
def no_relocation(monkeypatch):
    """The relocation runs a PDF replacement starts are recorded, not run:
    the fixture's value lists ``(page_id, new_doc, outgoing)``."""
    from gamma.routers import pages
    started = []

    def start(owner, ws, page_id, title, new_doc, *, outgoing=None, quiet=True):
        started.append((page_id, new_doc, outgoing))
        return None, ""

    monkeypatch.setattr(pages, "_start_relocation", start)
    return started


@pytest.fixture
def bounded(monkeypatch):
    """A round's units bounded (``zotero_sync.ROUND_UNITS``) well above what
    the standard library needs, so a unit that queues itself again stops
    its round at the bound and asks for the next at once (``asked_soon``)
    rather than running on."""
    monkeypatch.setattr(zotero_sync, "ROUND_UNITS", 300)


@pytest.fixture
def quiet(monkeypatch):
    """The page's own notes go at once (no 60 s of quiet to wait for)."""
    monkeypatch.setattr(zotero_sync_blocks, "NOTES_QUIET_S", -3600)


@pytest.fixture
def fake(monkeypatch):
    return FakeZotero().install(monkeypatch)


@pytest.fixture
def made(fake):
    return standard_library(fake)


@pytest.fixture
def idle():
    """After the test, one more round of each of the owner's links writes
    nothing on either side (zotero_idle)."""
    return Idle()


@pytest.fixture
def me(owner_id, fake, idle, request):
    """The owner signed in, Zotero connected; one more round of each link of
    theirs writes nothing (``idle``), then every link is removed and Zotero
    disconnected."""
    zotero_account.connect(owner_id, fake.api_key)
    c = login(request.module.USER, request.module.PASSWORD)
    yield c
    links = [link["workspace_id"] for link in zotero_sync.list_links(owner_id)]
    try:
        idle.check(fake, links)
    finally:
        for ws in links:
            zotero_sync.remove_link(ws)
        zotero_account.disconnect(owner_id, revoke=False)


@pytest.fixture
def held(monkeypatch, idle):
    """The item, child, page and file units pass every entry over (each
    stays queued), so a test reads what the listing and the feed queue."""
    idle.skip("the units are stubbed out: what they would do stays queued")
    for module, name in ((zotero_sync_items, "unit_item"), (zotero_sync_items, "unit_child"),
                         (zotero_sync_pages, "unit_page"), (zotero_sync_pdfs, "unit_file")):
        monkeypatch.setattr(module, name, lambda r, entry: zotero_sync_rows.HELD)


# --- the link and its rounds ------------------------------------------------------------

def drop_pending(ws) -> None:
    """The clock's request for a round of ``ws`` dropped, as a round the
    clock starts takes it."""
    zotero_sync._clock.pending.pop(ws, None)


def asked_soon(ws) -> bool:
    """Whether the clock holds a round of ``ws`` asked for within a few
    seconds (``sync_clock.BUSY_RETRY_S``): at once, as a round asks when it
    stopped at its unit cap with work left, or soon, for what it set aside
    to decide again. A wait's own round (a backoff, a PDF whose highlights
    are being placed) comes later than that."""
    pending = zotero_sync._clock.pending.get(ws)
    return pending is not None and pending - time.monotonic() <= sync_clock.BUSY_RETRY_S + 1


def _link(c, library=LIB, **body) -> str:
    r = c.post("/api/zotero/links", json={"library": library, **body})
    assert r.status_code == 201, r.text
    return r.json()["workspace_id"]


def _sync(c, ws, *, ok=True) -> dict:
    """Sync now, waiting for the round; its status (with ``ok``, one that
    ended without an error)."""
    r = c.post(f"/api/zotero/links/{ws}/sync?wait=1")
    assert r.status_code == 200, r.text
    status = r.json()["status"]
    if ok:
        assert not status.get("last_error"), status
    return status


def _force(c, ws, direction) -> None:
    """A force (``pull`` or ``push``) asked for, then its round."""
    r = c.post(f"/api/zotero/links/{ws}/force", json={"direction": direction})
    assert r.status_code in (200, 202), r.text
    _sync(c, ws)


def _due(ws) -> None:
    """Every queue entry due now (a backoff or a quiet wait lifted)."""
    with connect_pages_db(ws) as conn:
        conn.execute("UPDATE zotero_queue SET next_at = ''")
        conn.commit()


def _conflicts(c, ws, kind=None) -> list:
    """The link's open conflicts (of ``kind``)."""
    found = c.get(f"/api/zotero/links/{ws}/conflicts").json()["conflicts"]
    return [x for x in found if kind is None or x["kind"] == kind]


def link_row(ws, owner, mode="two-way", library=LIB) -> None:
    """A link's row written straight into users.db: no round, no Zotero."""
    with connect_users_db() as conn:
        conn.execute("INSERT INTO zotero_links (workspace_id, owner, library, mode, created_at) VALUES (?, ?, ?, ?, ?)",
                     (ws, owner, library, mode, page_now()))
        conn.commit()


def drop_link_row(ws) -> None:
    with connect_users_db() as conn:
        conn.execute("DELETE FROM zotero_links WHERE workspace_id = ?", (ws,))
        conn.commit()


# --- what the link keeps, what Zotero was sent ------------------------------------------

def _rows(ws, kind=None) -> dict:
    """The link's rows (of ``kind``) by key."""
    with connect_pages_db(ws) as conn:
        return zotero_sync_rows.read_rows(conn, "kind = ?", (kind,)) if kind else zotero_sync_rows.read_rows(conn)


def _collection_rows(ws) -> dict:
    """The link's collection rows by key."""
    return _rows(ws, "collection")


def _queue_entries(ws) -> dict:
    """The queue's entries by id: kind, version, priority, attempts, next_at, reason."""
    with connect_pages_db(ws) as conn:
        return {row[0]: dict(zip(("kind", "version", "priority", "attempts", "next_at", "reason"), row[1:]))
                for row in conn.execute("SELECT id, kind, version, priority, attempts, next_at, reason "
                                        "FROM zotero_queue")}


def _due_now(ws) -> list:
    """The ids of the queue's entries due now: none held back by a wait or
    a backoff."""
    with connect_pages_db(ws) as conn:
        return [entry.id for entry in zotero_remote.take(conn, now=page_now())]


def _conflict_count(ws) -> int:
    """The workspace's conflict rows, open or resolved."""
    with connect_pages_db(ws) as conn:
        return conn.execute("SELECT COUNT(*) FROM sync_conflicts").fetchone()[0]


def _page_of(ws, key) -> str:
    return _rows(ws)[key]["page_id"]


def _block_of(ws, key) -> str:
    """The block of annotation ``key``."""
    return _rows(ws, "annotation")[key]["block_id"]


def _state(ws) -> dict:
    """The link's state row (zotero_remote.get_state)."""
    with connect_pages_db(ws) as conn:
        return zotero_remote.get_state(conn)


def _writes(fake, since=0) -> list:
    """(method, path) of the writes Zotero received since call ``since``."""
    return [(m, path) for m, path, _ in fake.calls[since:] if m in WRITES]


def _sent_keys(fake, since=0) -> set:
    """The keys of the objects the writes since ``since`` sent (a create's
    has none yet: "")."""
    out = set()
    for _, path, body in fake.sent[since:]:
        for obj in body if isinstance(body, list) else [body]:
            out.add(obj.get("key") or (path.rsplit("/", 1)[-1] if "/items/" in path else ""))
    return out


def _collection(fake, key) -> dict:
    return fake.data(LIB, key)


def _by_name(fake) -> dict:
    """Zotero's collections by name."""
    return {c["name"]: c for c in fake.collections(LIB)}


# --- the workspace ----------------------------------------------------------------------

def _block(ws, block_id) -> dict | None:
    """A block's parent, content, properties and page (None once gone)."""
    with connect_pages_db(ws) as conn:
        row = conn.execute("SELECT parent_id, content, properties, page_id FROM unified_blocks WHERE id = ?",
                           (block_id,)).fetchone()
    return {"parent": row[0], "content": row[1], "props": json.loads(row[2]), "page": row[3]} if row else None


def _props(ws, block_id) -> dict:
    with connect_pages_db(ws) as conn:
        return json.loads(conn.execute("SELECT properties FROM unified_blocks WHERE id = ?", (block_id,)).fetchone()[0])


def _kids(ws, parent) -> list:
    """[(id, content)] under ``parent``, in order."""
    with connect_pages_db(ws) as conn:
        return [tuple(r) for r in conn.execute("SELECT id, content FROM unified_blocks WHERE parent_id = ? "
                                               "ORDER BY position, id", (parent,)).fetchall()]


def _pages(ws) -> dict:
    """{page id: title} of the live pages."""
    with connect_pages_db(ws) as conn:
        return dict(conn.execute("SELECT id, content FROM unified_blocks WHERE parent_id = 'root'").fetchall())


def _folders(ws) -> dict:
    """{folder id: (name, parent folder id or "")}."""
    with connect_pages_db(ws) as conn:
        return {fid: (name, "" if parent == FOLDERS else parent) for fid, parent, name, _ in tree_rows(conn, FOLDERS)}


def _paths(ws) -> dict:
    """{(names from the top): folder id}."""
    folders, out = _folders(ws), {}
    for fid in folders:
        names, at = [], fid
        while at:
            names.append(folders[at][0])
            at = folders[at][1]
        out[tuple(reversed(names))] = fid
    return out


def _edit(ws, owner, page_id, batch, client="t"):
    """A person's edit of a page (or of a tree: FOLDERS, LABELS)."""
    return ops.commit_ops(ws, page_id, batch, actor=owner, client=client)


def _folder_ops(ws, owner, batch):
    return ops.commit_ops(ws, FOLDERS, batch, actor=owner, client="t")


def _stored_pdf(ws, content: bytes) -> str:
    doc_id, _ = storage.store_pdf(ws, content)
    return doc_id


def _page(ws, page_id, title="A page", **props):
    """A page made here with the properties ``props``."""
    zotero_local.create_page(ws, page_id=page_id, title=title, props=props, actor="zs", client="t")
    return page_id


def _paper(ws, page_id, title="A paper", **props):
    """A paper page made here, its record a person's (``meta`` merged in)."""
    meta = {"title": title, "authors": ["Ada Lovelace"], "year": "2024", "venue": "", "volume": "", "pages": "",
            "doi": "", "arxiv_id": "", "publisher": "", "isbn": "", "source": "manual", **props.pop("meta", {})}
    zotero_local.create_page(ws, page_id=page_id, title=title, props={"meta": meta, **props}, actor="zi", client="t")
    return page_id


def _highlight(rects, *, color="#2ea8e5", quote="", content=""):
    """A highlight block as Gamma's viewer makes one on page 1 of the
    fixture paper, its rects in PDF user space: its content and properties."""
    with za.Pages(pdf_bytes()) as pages:
        made = za.from_item({"annotationType": "highlight", "annotationColor": color, "annotationText": quote,
                             "annotationComment": "", "key": "GAMMA234",
                             "annotationPosition": json.dumps({"pageIndex": 0, "rects": rects})}, pages.page(1))
    return {"content": content, "props": {k: v for k, v in made["props"].items()
                                          if k not in ("zotero_annotation", "imported_annot", "annot_stripped")}}


class Crash(BaseException):
    """The process dying in a round: nothing in the round catches it."""
