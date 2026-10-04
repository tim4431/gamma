"""Shared fixtures and helpers of the AI test files (test_ai_*.py): a
per-module account with a small library, the note-tree page for the block
tools, a faked streaming provider response, and the wire-test constants."""

import json

import pytest

from conftest import account_of, login, make_folder, make_user, workspace_of
from gamma.ai_tools import agent_tools

ALL_TOOLS = agent_tools("folder")  # the full registry, for the wire tests
ALL_PERMS = ("list", "read", "block_read", "view", "search", "web_search", "web_read",
             "save", "rename", "move", "restore", "block_edit")
# Every tool runs without asking: for chats whose changes are not about approvals.
ALLOW_ALL = {k: "allow" for k in ALL_PERMS}

CONF = {"base_url": "https://example.test", "api_key": "k", "account_id": ""}
TURNS = [
    {"role": "user", "content": "tidy up"},
    {"role": "assistant", "content": "listing", "tool_calls": [
        {"id": "c1", "name": "list_pages", "arguments": {}}]},
    {"role": "tool", "call_id": "c1", "content": "Pages…"},
]


def folder(folder_id=""):
    """A folder chat's scope: the folder ``folder_id`` ("" = the library root)."""
    return {"type": "folder", "folder": folder_id}


@pytest.fixture(scope="module")
def org(client, request):
    """A non-guest account with a small library — two papers in folders, one
    loose note — private to the test module (its name is in the account's),
    so the AI test files never see each other's pages or provider entries.
    Returns (client, ids): the page ids, the folder ids ("readout", its
    subfolder "nondestructive", "cooling"), plus the account's name under
    "user", its id under "user_id" (what storage takes) and its workspace id
    under "ws"."""
    user = "organizer-" + request.module.__name__.removeprefix("test_ai_").replace("_", "-")
    make_user(user, "pw")
    c = login(user, "pw")

    def page(content, props):
        r = c.post("/api/blocks", json={"parent_id": "root", "content": content, "properties": props})
        assert r.status_code == 200, r.text
        return r.json()["id"]

    folders = {"readout": make_folder(c, "readout"),
               "nondestructive": make_folder(c, "readout/nondestructive"),
               "cooling": make_folder(c, "cooling")}
    ids = {
        "a": page("cavity paper", {"folders": [folders["readout"]], "doc_id": "d" * 24,
                                   "meta": {"authors": ["Ada One", "Bo Two"], "year": "2019", "venue": "Nature"}}),
        "b": page("qec paper", {"folders": [folders["nondestructive"], folders["cooling"]]}),
        "note": page("loose note", {}),
    }
    return c, {**ids, **folders, "user": user, "user_id": account_of(user), "ws": workspace_of(user)}


@pytest.fixture(scope="module")
def indexed_pdf(org):
    """Page a's PDF with a searchable hit on physical page 3, stored the way
    the indexer stores a paper (pdf_index.store_doc)."""
    from gamma.db import connect_data_db
    from gamma.pdf_index import store_doc
    from gamma.textnorm import normalize_text

    _, ids = org
    doc = "d" * 24
    with connect_data_db(ids["ws"]) as db:
        store_doc(db, doc, [(3, normalize_text("quantum error correction with cat qubits"))])
    return doc


@pytest.fixture(scope="module")
def notes(org):
    """A page with a small note tree (plus a highlight) for the block tools,
    filed in the folder "sandbox" (its id under "sandbox")."""
    c, ids = org
    sandbox = make_folder(c, "sandbox")
    last_pos: dict = {}  # per-parent, so siblings get real fractional order

    def block(parent, content, props=None):
        r = c.post("/api/blocks", json={"parent_id": parent, "content": content,
                                        "properties": props or {},
                                        "before": last_pos.get(parent)})
        assert r.status_code == 200, r.text
        last_pos[parent] = r.json()["position"]
        return r.json()["id"]
    page = block("root", "notes playground")
    r = c.put(f"/api/blocks/{page}", json={"properties": {"folders": [sandbox]}})
    assert r.status_code == 200
    top = block(page, "top-level idea")
    child = block(top, "supporting detail")
    other = block(page, "second thread")
    hl = block(page, "why this matters",
               {"pdf_position": {"pageNumber": 1}, "quote": "measured T1 of 300 µs"})
    return c, {**ids, "page": page, "top": top, "child": child,
               "other": other, "hl": hl, "sandbox": sandbox}


@pytest.fixture(scope="module")
def ai_provider(org):
    """An Anthropic provider entry on the module's account, so /api/ai/chat
    runs (the wire is faked per test by patching routers.ai._open_ai)."""
    c, _ = org
    r = c.post("/api/ai/providers", json={"protocol": "anthropic", "api_key": "sk-test-key-123",
                                          "models": "claude-solo"})
    assert r.status_code == 200, r.text


def props(c, block_id):
    r = c.get(f"/api/blocks/{block_id}")
    assert r.status_code == 200
    return r.json()


def children(c, parent):
    r = c.get(f"/api/blocks/{parent}/children")
    assert r.status_code == 200
    return [b["id"] for b in r.json()["children"]]


def sse(*events):
    return iter([f"data: {json.dumps(e)}\n".encode() for e in events] + [b"data: [DONE]\n"])


class FakeResp:
    """What routers.ai._open_ai returns: an iterable of SSE lines."""

    def __init__(self, events):
        self._lines = [f"data: {json.dumps(e)}\n".encode() for e in events] + [b"data: [DONE]\n"]

    def __iter__(self):
        return iter(self._lines)

    def close(self):
        pass


def payload(history):
    from types import SimpleNamespace
    return SimpleNamespace(history=history, prompt="now do it", selection="")
