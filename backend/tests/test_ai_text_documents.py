"""A work folder's text file in the chat and the tools, the backend half of
step 4's gate (docs/research/file-explorer.md, "In steps" row 4): the sample
paper read in place, its ``sections/method.tex`` in a chat's context as
numbered lines beside its open thread, read by lines and by path, found by
search at its line, and cited with a line link over MCP; and step 6's ranges
of ``main.tex`` and a selection of its lines as the model is told of them."""

import json
import shutil
import uuid
from pathlib import Path

import pytest

from conftest import account_of, login, make_folder, make_user, mark_work_folder, thread_on, workspace_of
from gamma import ai_context, folder_links, gamma_sync, highlights, mcp_export, pdf_meta, storage
from gamma.ai_tools import run_agent_tool
from gamma.db import connect_data_db, connect_pages_db
from gamma.documents import text as doc_text
from gamma.documents.model import page_document
from gamma.feedback import agent as feedback_agent, entries as feedback_entries
from gamma.search import indexer
from gamma.search.index import as_doc, store_docs
from gamma.vault import disk as vault_disk, kinds, store as vault_store
from gamma.vault.local_remote import LocalRemote
from test_mcp import call
from test_vault_sync import Vault, insert

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


def _range(lines, line, needle, hash, **flags) -> tuple[dict, str]:
    """The ``text_position`` and quote of ``needle`` on ``line`` of the file."""
    col = lines[line - 1].index(needle)
    span = (line, col, line, col + len(needle))
    quote, prefix, suffix = highlights.span_text(lines, span)
    return {**highlights.text_position(*span, hash=hash, prefix=prefix, suffix=suffix), **flags}, quote


def test_ranges_of_main_tex_and_a_selection_of_its_lines_reach_the_model(owner, paper, monkeypatch):
    """Step 6: the page's ranges are told with their lines and how the last
    change left them (the context, read_block), a thread on one by the lines
    it holds now and its state, and a selection in the code view far past
    the head the context shows arrives with the lines around it, its
    heading naming them."""
    main = paper.page_of("main.tex")
    hash = page_document(paper.blocks(main)[main]["props"])["hash"]
    lines = (PAPER / "main.tex").read_text("utf-8").split("\n")
    placed = {key: _range(lines, line, needle, hash, **flags) for key, line, needle, flags in (
        ("H", 16, "Weight decay is the oldest remedy", {}),
        ("R", 23, "Training and test loss per epoch", {"approx": True, "match": 0.9}),
        ("L", 47, "validation loss was lowest.", {"lost": True}),
        ("P", 31, "fifth epoch the test loss", {}))}
    placed["P"][0]["hash"] = "e" * 32     # measured on another version: the run has not placed it yet
    ids = {key: f"aitx{key}{uuid.uuid4().hex[:6]}" for key in placed}
    paper.edit(main, *(insert(main, "", {"text_position": pos, "quote": quote}, ids[key])
                       for key, (pos, quote) in placed.items()))
    thread = thread_on(owner, main, ids["H"], "is it really the oldest?")
    lost = thread_on(owner, main, ids["L"], "say which epoch")
    pending = thread_on(owner, main, ids["P"], "which loss?")
    # the range moved since the thread opened: it is named by the lines it holds now
    paper.edit(main, {"op": "set", "id": ids["H"], "props": {"text_position": _range(
        lines, 52, "Weight decay is a simple", hash)[0], "quote": "Weight decay is a simple"}})

    selected = "Weight decay is a simple and reliable way to keep a small model from"
    seen, (cover,) = _chat(owner, monkeypatch, main, include_notes=True, context_char_limit=2500,
                           selections=[{"text": selected, "page": 0, "line": 52, "end": 52}])
    content = seen["messages"][-1]["content"]
    assert '- Highlighted: "Weight decay is a simple" (line 52)' in content
    assert '- Highlighted: "Training and test loss per epoch" (line 23; placed roughly)' in content
    assert '- Highlighted: "validation loss was lowest." (line 47; not found in the current file)' in content
    assert '- Highlighted: "fifth epoch the test loss" (line 31; lines of an earlier version of the file)' in content
    assert f'- thread {thread["id"]} on line 52, “Weight decay is a simple”: ' in content
    # a lost range's lines no longer hold its text, a pending one's are another version's: each says so
    assert f'- thread {lost["id"]} on line 47 (not found in the current version), “validation loss was lowest.”: ' in content
    assert f'- thread {pending["id"]} on line 31 of an earlier version, “fifth epoch the test loss”: ' in content
    assert cover["partial"] is True and cover["lines_shown"] < 32     # the head stops well before line 52
    [where] = cover["selection"]["passages"]
    assert where["lines"] == [52, 52] and where["window"][0] == 32 and where["window"][1] >= 52
    assert f"[lines 32–{where['window'][1]} of main.tex]\n    32\t" in content
    assert "    52\tWeight decay is a simple and reliable" in content
    assert "from the page's document. Answer specifically about it:" in content
    assert f'Selected passage (line 52):\n"""\n{selected}\n"""' in content

    text, _ = run_agent_tool(paper.ws, folder(paper.folder), "read_block", {"block_id": main})
    assert f'[{ids["H"]}] (highlight: "Weight decay is a simple"; line 52)' in text
    assert f'[{ids["L"]}] (highlight: "validation loss was lowest."; line 47; not found in the current file)' in text


def test_a_range_on_a_secrets_file_is_named_by_its_lines_alone(owner, paper, monkeypatch):
    """A secrets file's text is kept from the model, and so is the text of
    a range or a selection in it: the chat's context, read_block, read_page,
    a thread's place and the feedback entries name its lines, never its
    quote."""
    secret = "OPENAI_API_KEY=sk-live-abcdef0123456789"
    paper.write(".env", f"# keys\n{secret}\nOTHER=1\n".encode(), age=10)
    paper.sync()
    env = paper.page_of(".env")
    hash = page_document(paper.blocks(env)[env]["props"])["hash"]
    pos, quote = _range(["# keys", secret, "OTHER=1"], 2, secret, hash)
    bid = f"aitxS{uuid.uuid4().hex[:6]}"
    paper.edit(env, insert(env, "rotate it soon", {"text_position": pos, "quote": quote}, bid))
    thread = thread_on(owner, env, bid, "rotate this key")
    seen, (cover,) = _chat(owner, monkeypatch, env, include_notes=True,
                           selections=[{"text": secret, "page": 0, "line": 2, "end": 2}])
    content = seen["messages"][-1]["content"] + str(seen.get("system"))
    assert "sk-live" not in content
    assert "- Highlighted (line 2; its text is kept from the model: a secrets file)\n  User note: rotate it soon" in content
    assert f'- thread {thread["id"]} on line 2: ' in content
    assert 'Selected passage (line 2):\n"""\n(its text is kept from the model: a secrets file)\n"""' in content
    assert cover["selection"]["passages"] == [{"lines": [2, 2], "secret": True}]
    for tool, args in (("read_block", {"block_id": env}), ("read_page", {"page_id": env})):
        text, _ = run_agent_tool(paper.ws, folder(paper.folder), tool, args)
        assert "sk-live" not in text, tool
    text, _ = run_agent_tool(paper.ws, folder(paper.folder), "read_block", {"block_id": env})
    assert f"[{bid}] (highlight on line 2; its text is kept from the model: a secrets file)" in text
    with connect_pages_db(paper.ws) as conn:
        [entry] = [e for e in feedback_entries.entries(paper.ws, conn, feedback_entries.resolve_scope(conn, env))
                   if e["id"] == thread["id"]]
        named = feedback_agent._named(conn, {"page_id": env, "id": thread["id"], "block_id": bid})
    assert entry["where"] == {"line": 2, "placed": "exact"} and named == ".env, line 2"
    # MCP's export hands the model a page as Markdown or its notes as a PDF: no quote of the range
    text, _ = mcp_export.export_page(paper.ws, "http://gamma.test", account_of(OWNER),
                                     {"page_id": env, "format": "markdown"})
    assert "sk-live" not in text and "the highlights of 1 secrets file are left out" in text
    assert "rotate it soon" in text


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
