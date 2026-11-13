"""A work folder's text file in the chat and the tools, the backend half of
step 4's gate (docs/research/file-explorer.md, "In steps" row 4): the sample
paper read in place, its ``sections/method.tex`` in a chat's context as
numbered lines beside its open thread, read by lines and by path, found by
search at its line, and cited with a line link over MCP."""

import json
import shutil
import uuid
from pathlib import Path

import pytest

from conftest import account_of, login, make_folder, make_user, mark_work_folder, thread_on, workspace_of
from gamma import ai_context, folder_links, gamma_sync, pdf_meta, storage
from gamma.ai_tools import run_agent_tool
from gamma.db import connect_data_db
from gamma.documents import text as doc_text
from gamma.documents.model import page_document
from gamma.search import indexer
from gamma.search.index import as_doc, store_docs
from gamma.vault import disk as vault_disk, kinds, store as vault_store
from gamma.vault.local_remote import LocalRemote
from test_mcp import call
from test_vault_sync import Vault

from ai_fixtures import FakeResp, folder

OWNER = "aitext_owner"
PAPER = Path(__file__).parent / "samples" / "synctex" / "paper" / "v1"
GRADIENT = "We fit a small network to the training set by gradient descent."


@pytest.fixture(scope="module")
def owner(client):
    make_user(OWNER, "pw")
    c = login(OWNER, "pw")
    r = c.post("/api/ai/providers", json={"protocol": "anthropic", "api_key": "sk-test-key-123",
                                          "models": "claude-solo"})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setenv("GAMMA_FOLDER_LINKS", "1")
    monkeypatch.setattr(kinds, "SETTLE_NOTE_S", 0)
    monkeypatch.setattr(gamma_sync, "SETTLE_S", 0)
    monkeypatch.setattr(vault_store, "MISSING_S", 0)
    monkeypatch.setattr(pdf_meta, "schedule", lambda ws, doc_id: None)
    monkeypatch.setattr(storage, "linked_moved", [])
    monkeypatch.setattr(storage, "LINKED_STAT_S", 0)
    monkeypatch.setattr(storage, "_stats", {})
    monkeypatch.setattr(indexer, "index_missing_async", lambda ws, docs: True)
    monkeypatch.setattr("gamma.ai_catalog.context_window", lambda *args: (0, ""))
    monkeypatch.setattr("gamma.ai_catalog.image_input", lambda *args: (None, ""))


class PaperVault(Vault):
    """``test_vault_sync.Vault`` of this module's account: a work folder
    tracking every file, the sample paper copied into it."""

    def __init__(self, owner, root):
        self.owner, self.root = owner, root
        self.ws, self.user = workspace_of(OWNER), account_of(OWNER)
        self.name = f"paper {uuid.uuid4().hex[:6]}"
        self.folder = make_folder(owner, self.name)
        shutil.copytree(PAPER, root)
        (root / ".gammaignore").write_text("# every file\n", encoding="utf-8")
        for path in root.rglob("*"):
            if path.is_file():
                self.write(path.relative_to(root).as_posix(), path.read_bytes(), age=10)
        marker = vault_disk.make_marker(root)
        self.link = {"id": uuid.uuid4().hex, "workspace_id": self.ws, "folder_id": self.folder, "path": str(root),
                     "pull": 1, "push": folder_links.PUSH_ALL, "created_by": self.user,
                     "state": {"vault": marker["id"]}, "kind": "folder", "remote_url": "", "library": 0}
        self.remote = LocalRemote(self.ws, self.folder, self.user)
        mark_work_folder(owner, self.folder)


@pytest.fixture
def paper(owner, tmp_path):
    v = PaperVault(owner, tmp_path / "paper")
    v.sync()
    yield v
    vault_store.forget_link(v.link["id"])


def _chat(owner, monkeypatch, page_id, **more):
    import gamma.routers.ai as ai_mod

    seen = {}

    def fake_open(messages, system, entry, rt, pdf_b64s=None, **kw):
        seen["messages"], seen["system"] = messages, system
        return FakeResp([{"type": "content_block_delta", "delta": {"type": "text_delta", "text": "ok"}}])

    monkeypatch.setattr(ai_mod, "_open_ai", fake_open)
    r = owner.post("/api/ai/chat", json={"prompt": "Where is the method stated?", "page_id": page_id,
                                         "stream": True, **more})
    assert r.status_code == 200, r.text
    cover = [json.loads(line) for line in r.text.splitlines() if line.strip()][0]["context"]
    return seen, cover


def test_a_chat_on_a_work_folders_tex_file_holds_its_lines_and_threads(owner, paper, monkeypatch):
    """The chat's context holds sections/method.tex numbered as the file
    is, its path in the work folder and its open thread; the file's head is
    read once for two turns on one version (text_head's cache)."""
    method = paper.page_of("sections/method.tex")
    thread = thread_on(owner, method, method, "say which optimizer")
    reads = []
    real = doc_text.read_lines
    monkeypatch.setattr(doc_text, "read_lines", lambda *a, **kw: reads.append(a) or real(*a, **kw))
    ai_context.text_head_clear()
    seen, (cover,) = _chat(owner, monkeypatch, method)
    context = seen["messages"][-1]["content"]
    assert f'path: "sections/method.tex"; work folder: "{paper.name}"' in context
    assert 'document: text "method.tex"' in context
    assert 'Document: text file "sections/method.tex" (LaTeX; 13 lines, ' in context
    assert "read where it is)" in context
    assert "[The whole file, 13 lines. The number and tab before each line are its line number" in context
    assert f"     2\t{GRADIENT}" in context and "    13\tweights, and" in context and "    14\t" not in context
    assert f'- thread {thread["id"]} on the whole file: ' in context and '"say which optimizer"' in context
    assert cover["format"] == "text" and cover["lines"] == 13 and cover["partial"] is False
    _chat(owner, monkeypatch, method, history=[{"role": "user", "text": "first"}, {"role": "ai", "text": "ok"}])
    assert len(reads) == 1  # the second turn reads the kept head


def test_the_tools_read_search_and_link_a_line_of_the_file(owner, paper, client):
    """read_page by lines and by path, list_pages' paths, search_library's
    file hit at its line, and the line link over MCP: read_page(from_line=2,
    to_line=2) gives the line with its &line=2 link."""
    method, main = paper.page_of("sections/method.tex"), paper.page_of("main.tex")
    ws = paper.ws
    # By path, in the work folder's chat and from the library root.
    text, chip = run_agent_tool(ws, folder(paper.folder), "read_page", {"path": "sections/method.tex"})
    assert chip["page_id"] == method and chip["path"] == "sections/method.tex" and GRADIENT in text
    for path in (f"{paper.name}/sections/method.tex", f"{paper.name}/Sections/Method", "sections\\method"):
        scope = folder(paper.folder) if path.startswith("sections") else folder("")
        text, chip = run_agent_tool(ws, scope, "read_page", {"path": path})
        assert chip and chip["page_id"] == method, (path, text)
    text, chip = run_agent_tool(ws, folder(""), "read_page", {"path": "nowhere/sections/method.tex"})
    assert text.startswith('error: no work folder is named "nowhere"') and chip["error"]
    text, _ = run_agent_tool(ws, folder(paper.folder), "read_page", {"path": "sections/missing.tex"})
    assert text.startswith("error: no file is at") and "list_pages" in text
    listed, _ = run_agent_tool(ws, folder(paper.folder), "list_pages", {})
    assert f'id={method} | title="method.tex" | path="sections/method.tex" | attachments=[text:latex]' in listed
    assert f'id={main} | title="main.tex" | path="main.tex"' in listed

    # The index holds the file once a search has run the job; here it is run in place.
    view = page_document(paper.blocks(method)[method]["props"])
    with connect_data_db(ws) as db:
        store_docs(db, [indexer.index_doc(ws, as_doc(view))])
    created = owner.post("/api/integrations/tokens", json={"name": "Codex"})
    assert created.status_code == 201, created.text
    token = created.json()["token"]
    try:
        prefix = f"http://localhost/?ws={ws}"
        text = call(client, token, "search_library", {"query": "gradient"})["content"][0]["text"]
        assert f'- file "sections/method.tex" l.2 (lines 1–13; page_id {method}; {prefix}&page={method}&line=2): ' \
               f"{GRADIENT}" in text, text
        result = call(client, token, "read_page", {"page_id": method, "from_line": 2, "to_line": 2})
        text = result["content"][0]["text"]
        assert f"     2\t{GRADIENT}" in text and "     3\t" not in text
        assert f"line 2: {prefix}&page={method}&line=2 (a range" in text
        assert result["structuredContent"]["lines"] == [2, 2]
        assert result["structuredContent"]["path"] == "sections/method.tex"
        result = call(client, token, "read_gamma_link", {"url": f"{prefix}&page={method}&line=2-3"})
        assert result["structuredContent"]["line"] == 2 and result["structuredContent"]["end"] == 3
        assert f"     2\t{GRADIENT}" in result["content"][0]["text"]
        result = call(client, token, "read_page", {"path": f"{paper.name}/sections/method.tex", "from_line": 2,
                                                   "to_line": 2})
        assert not result["isError"] and GRADIENT in result["content"][0]["text"]
    finally:
        owner.delete(f"/api/integrations/tokens/{created.json()['id']}")
