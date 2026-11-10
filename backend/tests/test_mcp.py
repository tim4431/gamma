"""Exercise the real SDK transport, scoped credentials, and shared dispatch."""

import time

import pytest

from conftest import account_of, login, make_folder, make_page, make_user
from gamma.ai_tools import agent_tools, mcp_tools, run_agent_tool
from gamma.db import connect_users_db
from gamma.integrations import resolve_token


@pytest.fixture
def connection(client):
    ws = make_user("mcp-reader", "pw")
    c = login("mcp-reader", "pw")
    created = c.post("/api/integrations/tokens", json={"name": "Codex"})
    assert created.status_code == 201, created.text
    item = created.json()
    yield c, ws, item
    c.delete(f"/api/integrations/tokens/{item['id']}")
    c.close()


def rpc(client, token, method, params=None, *, host="localhost", notification=False):
    body = {"jsonrpc": "2.0", "method": method}
    if not notification:
        body["id"] = 1
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers={
        "Host": host, "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"})


def test_initialize_and_read_tools(client, connection):
    c, ws, item = connection
    page = make_page(c, "MCP integration paper")
    note = c.post("/api/blocks", json={"parent_id": page["id"], "content": "UniqueMcpEvidence observation"}).json()
    init = rpc(client, item["token"], "initialize", {
        "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}})
    assert init.status_code == 200, init.text
    assert init.json()["result"]["serverInfo"]["name"] == "Gamma"
    assert init.json()["result"]["serverInfo"]["icons"][0]["src"].startswith("data:image/png;base64,")
    assert rpc(client, item["token"], "notifications/initialized", notification=True).status_code == 202
    tools = rpc(client, item["token"], "tools/list").json()["result"]["tools"]
    # The library's reading tools come from the registry by rule (ai_tools.mcp_tools:
    # a new one is offered without touching the adapter); the literal makes growth visible.
    assert {t["name"] for t in tools} == mcp_tools() | {"list_workspaces", "read_gamma_link", "export_page"}
    assert {t["name"] for t in tools} == {"list_pages", "list_folders", "read_page", "read_block", "read_chats",
                                          "view_pdf_page", "view_ink", "view_image", "cite", "search_library",
                                          "list_workspaces", "read_gamma_link", "export_page"}
    # every tool but the workspace list and a link (which names its own) can be pointed at a workspace
    assert all("workspace" in t["inputSchema"]["properties"] for t in tools
               if t["name"] not in ("list_workspaces", "read_gamma_link"))
    assert all(t["annotations"]["readOnlyHint"] for t in tools)
    assert all(t["icons"] == init.json()["result"]["serverInfo"]["icons"] for t in tools)
    for name, arguments, expected in [
        ("list_pages", {"title_contains": "MCP integration paper"}, page["id"]),
        ("read_page", {"page_id": page["id"]}, "UniqueMcpEvidence"),
        ("read_block", {"block_id": note["id"]}, "UniqueMcpEvidence"),
        ("search_library", {"query": "UniqueMcpEvidence"}, page["id"]),
    ]:
        response = rpc(client, item["token"], "tools/call", {"name": name, "arguments": arguments})
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        assert not result["isError"], result
        text = result["content"][0]["text"]
        assert expected in text
        assert f"http://localhost/?ws={ws}&page=" in text


def call(client, token, name, arguments):
    response = rpc(client, token, "tools/call", {"name": name, "arguments": arguments})
    assert response.status_code == 200, response.text
    return response.json()["result"]


def _pdf_page(c, title):
    """A page carrying a stored two-page PDF, with one highlight (its note
    under it) and an image block; the highlight's id is ``<page id>-hl``."""
    import io
    from pypdf import PdfWriter

    w = PdfWriter()
    for _ in range(2):
        w.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    w.write(buf)
    up = c.post("/api/uploads", files={"file": ("p.pdf", buf.getvalue(), "application/pdf")})
    assert up.status_code == 200, up.text
    page = make_page(c, title, properties={"doc_id": up.json()["doc_id"], "source_url": up.json()["source_url"]})
    rect = {"x1": 100, "y1": 72, "x2": 300, "y2": 92}
    r = c.put(f"/api/blocks/{page['id']}/children", json={"blocks": [
        {"id": f"{page['id']}-hl", "content": "McpHighlightNote", "properties": {
            "quote": "McpQuotedPassage",
            "pdf_position": {"pageNumber": 1, "width": 612, "height": 792, "boundingRect": dict(rect), "rects": [rect]}},
         "children": []},
        {"id": f"{page['id']}-img", "content": "![figure](/api/uploads/abc123.png)", "properties": {}, "children": []},
    ]})
    assert r.status_code == 200, r.text
    return page


def test_folders_chats_and_pdf_pictures(client, connection):
    c, ws, item = connection
    paper = _pdf_page(c, "Folder tree paper")
    tree = make_folder(c, "mcp/tree")
    empty = make_folder(c, "mcp/empty")
    assert c.put(f"/api/blocks/{paper['id']}", json={"properties": {"folders": [tree]}}).status_code == 200
    result = call(client, item["token"], "list_folders", {})
    text = result["content"][0]["text"]
    assert not result["isError"] and f'[{tree}] "mcp / tree" (1 page)' in text
    assert f'[{empty}] "mcp / empty" (no pages)' in text  # the tree, ids and empty folders included
    assert c.put(f"/api/chats/{paper['id']}", json={"messages": [
        {"role": "user", "text": "UniqueMcpChatQuestion"}, {"role": "ai", "text": "An answer"}]}).status_code == 200
    result = call(client, item["token"], "read_chats", {"page_id": paper["id"]})
    text = result["content"][0]["text"]
    assert not result["isError"] and "[1] User: UniqueMcpChatQuestion" in text
    assert f"Page URL: http://localhost/?ws={ws}&page={paper['id']}" in text
    # The PDF page arrives as an image the client shows the model.
    result = call(client, item["token"], "view_pdf_page", {"page_id": paper["id"], "pdf_page": 2})
    assert not result["isError"] and "PDF page 2 of 2" in result["content"][0]["text"]
    (image,) = [part for part in result["content"] if part["type"] == "image"]
    assert image["mimeType"] in ("image/jpeg", "image/png") and len(image["data"]) > 100


def test_results_carry_links_and_structured_content(client, connection, monkeypatch):
    """Every located hit links to itself with an absolute URL — the page, the
    PDF page, the note block — so an external assistant copies links out of
    results instead of assembling them; the chat's results stay link-free."""
    from gamma.db import connect_data_db
    from gamma.pdf_index import store_doc
    from gamma.pdf_text import extract_text
    from gamma.textnorm import normalize_text

    c, ws, item = connection
    token = item["token"]
    paper = make_page(c, "Linked paper", properties={"doc_id": "l" * 24})
    note = c.post("/api/blocks", json={"parent_id": paper["id"], "content": "UniqueLinkedNote"}).json()
    # The search index holds the hit; a read extracts the text from the PDF itself.
    pages = ["(page 1) first-page-body " * 5, "(page 2) UniqueLinkedPassage on the second page " * 3]
    with connect_data_db(ws) as db:
        store_doc(db, "l" * 24, [(2, normalize_text(pages[1]))])
    monkeypatch.setattr("gamma.pdf_text.iter_page_texts",
                        lambda src, max_pages=None, start_page=1: iter(pages[start_page - 1:]))
    monkeypatch.setattr("gamma.ai_context.extract_text", extract_text)
    monkeypatch.setattr("gamma.ai_context.pdf_path", lambda u, d: "fake.pdf")
    prefix = f"http://localhost/?ws={ws}"
    page_url = f"{prefix}&page={paper['id']}"
    # Search hits: the PDF page and the note block, each with its own URL.
    text = call(client, token, "search_library", {"query": "UniqueLinkedPassage"})["content"][0]["text"]
    assert f'p.2 (page_id {paper["id"]}; {page_url}&pdf_page=2):' in text
    text = call(client, token, "search_library", {"query": "UniqueLinkedNote"})["content"][0]["text"]
    assert f'(page_id {paper["id"]}; {prefix}&block={note["id"]}):' in text
    # A read names its links once: the page, and the citation form of its PDF pages.
    result = call(client, token, "read_page", {"page_id": paper["id"], "pdf_page": 2})
    text = result["content"][0]["text"]
    assert f"[Links: page {page_url}; PDF page 2: {page_url}&pdf_page=2, the other pages likewise" in text
    assert "Page URL:" not in text  # not repeated under a result that already links the page
    assert result["structuredContent"]["page_id"] == paper["id"]
    assert result["structuredContent"]["url"] == page_url and result["structuredContent"]["pdf_pages"][0] == 2
    # A note outline links its page; a block, itself.
    text = call(client, token, "read_block", {"block_id": note["id"]})["content"][0]["text"]
    assert text.split("\n\n", 1)[1].startswith(
        f'Block [{note["id"]}] in page "Linked paper" (page_id {paper["id"]}; {prefix}&block={note["id"]}):')
    # The chat's results carry no URLs: its model writes relative links itself.
    text, _ = run_agent_tool(ws, {"type": "folder", "folder": "", "can_write": False},
                             "search_library", {"query": "UniqueLinkedPassage"})
    assert "http://" not in text and f"(page_id {paper['id']}):" in text


def test_cite_and_view_ink(client, connection):
    """The two reading tools the derived allowlist adds: a page's citation
    record, and a handwriting picture through the same image path as a PDF page."""
    from gamma.ink import encode_points

    c, ws, item = connection
    paper = _pdf_page(c, "Cited paper")
    assert c.put(f"/api/blocks/{paper['id']}", json={"properties": {
        "meta": {"title": "Cited paper", "authors": ["A. Author"], "year": "2021"}}}).status_code == 200
    result = call(client, item["token"], "cite", {"page_ids": [paper["id"]]})
    text = result["content"][0]["text"]
    assert not result["isError"], result
    assert f'## "Cited paper" (page_id {paper["id"]}; http://localhost/?ws={ws}&page={paper["id"]})' in text
    assert "```bibtex" in text and "A. Author" in text
    # Handwriting on PDF page 1, stored the way the pen uploads it.
    ink = {"format": "gamma-ink", "version": 2,
           "space": {"kind": "pdf-page", "width": 612, "height": 792, "page": 1},
           "strokes": [{"id": "s1", "color": "#ff0000", "size": 6, "ch": "xy",
                        "pts": encode_points([{"x": 40, "y": 60}, {"x": 260, "y": 340}], "xy")}]}
    drawn = c.post("/api/upload-ink", json=ink)
    assert drawn.status_code == 200, drawn.text
    group = c.post("/api/blocks", json={"parent_id": paper["id"], "content": "circled the result", "properties": {
        "ink_url": drawn.json()["url"], "pdf_position": drawn.json()["pdf_position"], "ink_strokes": 1}}).json()
    result = call(client, item["token"], "view_ink", {"block_id": group["id"]})
    assert not result["isError"], result
    assert f'Handwriting block [{group["id"]}] on PDF page 1 of "Cited paper"' in result["content"][0]["text"]
    (image,) = [part for part in result["content"] if part["type"] == "image"]
    assert len(image["data"]) > 100
    assert result["structuredContent"]["block_id"] == group["id"] and result["structuredContent"]["pdf_page"] == 1


def test_export_page_formats(client, connection):
    import base64
    import io
    from pypdf import PdfReader

    c, ws, item = connection
    paper = _pdf_page(c, "Exported paper")
    token = item["token"]
    # Markdown is the text itself; upload links point at this server and workspace.
    result = call(client, token, "export_page", {"page_id": paper["id"], "format": "markdown"})
    text = result["content"][0]["text"]
    assert not result["isError"] and len(result["content"]) == 1
    assert "# Exported paper" in text and "> McpQuotedPassage" in text and "McpHighlightNote" in text
    assert f"(http://localhost/api/uploads/abc123.png?ws={ws})" in text
    no_notes = call(client, token, "export_page", {"page_id": paper["id"], "format": "markdown", "notes": False})
    assert "McpQuotedPassage" in no_notes["content"][0]["text"]
    assert "McpHighlightNote" not in no_notes["content"][0]["text"]
    # The annotated PDF is an embedded file, named like the Export dialog's download.
    result = call(client, token, "export_page", {"page_id": paper["id"], "format": "pdf"})
    assert not result["isError"], result
    text_part, file_part = result["content"]
    assert "-notes.pdf" in text_part["text"]
    resource = file_part["resource"]
    assert file_part["type"] == "resource" and resource["mimeType"] == "application/pdf"
    assert resource["uri"].startswith(f"http://localhost/api/pages/{paper['id']}/export-pdf?ws={ws}")
    annots = PdfReader(io.BytesIO(base64.b64decode(resource["blob"]))).pages[0]["/Annots"]
    assert any(a.get_object()["/Subtype"] == "/Highlight" for a in annots)
    # The notes typeset as a PDF work for any page, with or without a paper.
    note_page = make_page(c, "Notes only page")
    c.post("/api/blocks", json={"parent_id": note_page["id"], "content": "a typeset note"})
    result = call(client, token, "export_page", {"page_id": note_page["id"], "format": "notes_pdf"})
    assert not result["isError"], result
    assert base64.b64decode(result["content"][1]["resource"]["blob"]).startswith(b"%PDF")
    # Refusals are tool errors the model can act on.
    result = call(client, token, "export_page", {"page_id": note_page["id"], "format": "pdf"})
    assert result["isError"] and "page has no PDF" in result["content"][0]["text"]
    result = call(client, token, "export_page", {"page_id": f"{paper['id']}-hl", "format": "markdown"})  # a block
    assert result["isError"] and "No such page" in result["content"][0]["text"]
    assert call(client, token, "export_page", {"page_id": paper["id"], "format": "docx"})["isError"]


@pytest.mark.parametrize("name,args", [
    ("rename_page", {"page_id": "x", "title": "changed"}),
    ("fetch_paper", {"source": "https://example.com"}),
    ("search_pdfs", {"query": "x"}),  # alias must not expand the public surface
    ("read_page", {}),
    ("read_block", {"block_id": 123}),
    ("list_pages", {"workspace_id": "other"}),
])
def test_reject_disallowed_or_malformed_calls(client, connection, name, args):
    result = rpc(client, connection[2]["token"], "tools/call", {"name": name, "arguments": args}).json()["result"]
    assert result["isError"], result


def test_no_cross_workspace_reads(client, connection):
    other_ws = make_user("mcp-other", "pw")
    other = login("mcp-other", "pw")
    page = make_page(other, "Private other library")
    for name, args in [("read_page", {"page_id": page["id"]}), ("read_block", {"block_id": page["id"]})]:
        result = rpc(client, connection[2]["token"], "tools/call", {"name": name, "arguments": args}).json()["result"]
        assert result["isError"]
        assert "Private other library" not in str(result)
    # Headers/query cannot retarget a token either.
    r = client.post(f"/mcp?ws={other_ws}", headers={"Host": "localhost", "X-Gamma-Workspace": other_ws,
        "Authorization": "Bearer " + connection[2]["token"], "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "list_pages", "arguments": {}}})
    assert "Private other library" not in r.text
    other.close()


def test_read_links_use_canonical_origin(client, connection, monkeypatch):
    c, ws, item = connection
    page = make_page(c, "Canonical origin paper")
    monkeypatch.setenv("GAMMA_PUBLIC_URL", "https://localhost")
    # The incoming request is HTTP, as it can be behind a TLS proxy.
    for name, args in (("read_page", {"page_id": page["id"]}), ("list_pages", {})):
        result = rpc(client, item["token"], "tools/call", {"name": name, "arguments": args}).json()["result"]
        assert not result["isError"]
        assert f"https://localhost/?ws={ws}&page=" in result["content"][0]["text"]
        assert "http://localhost/" not in result["content"][0]["text"]


def test_tokens_are_hashed_private_and_revocable(client, connection):
    c, ws, item = connection
    assert resolve_token(item["token"]) == (account_of("mcp-reader"), ws)
    listed = c.get("/api/integrations/tokens")
    assert listed.headers["cache-control"] == "no-store"
    assert item["token"] not in listed.text and "token_hash" not in listed.text
    with connect_users_db() as db:
        stored = db.execute("SELECT token_hash FROM integration_tokens WHERE id = ?", (item["id"],)).fetchone()[0]
    assert item["token"] != stored and len(stored) == 64
    c.delete(f"/api/integrations/tokens/{item['id']}")
    assert rpc(client, item["token"], "tools/list").status_code == 401


def test_connection_listing_is_the_accounts(client, connection):
    from gamma import workspaces
    from gamma.integrations import create_token

    c, ws, item = connection
    second = workspaces.create("Second library", account_of("mcp-reader"))
    other = create_token(account_of("mcp-reader"), second["id"], "Codex second", 90)
    foreign_ws = make_user("another-reader", "pw")
    foreign = create_token(account_of("another-reader"), foreign_ws, "Private connection", 90)
    try:
        result = c.get("/api/integrations/tokens")
        data = result.json()
        assert data["workspace_id"] == ws
        # every connection of the account, whichever workspace is open, each naming what it reads
        listed = {t["id"]: t for t in data["tokens"]}
        assert set(listed) == {item["id"], other["id"]}
        assert listed[item["id"]]["workspaces"] == [ws] and not listed[item["id"]]["all_workspaces"]
        assert listed[other["id"]]["workspaces"] == [second["id"]]
        assert {w["id"] for w in data["workspaces"]} >= {ws, second["id"]}
        assert all(private not in result.text for private in (item["token"], other["token"], foreign["token"], foreign["id"]))
        assert {t["id"] for t in c.get(f"/api/integrations/tokens?ws={second['id']}").json()["tokens"]} == set(listed)
    finally:
        c.delete(f"/api/integrations/tokens/{other['id']}")
        workspaces.delete(second["id"])


def test_expiration_and_membership_loss(client, connection):
    _, ws, item = connection
    with connect_users_db() as db:
        db.execute("UPDATE integration_tokens SET expires_at = ? WHERE id = ?", (int(time.time()) - 1, item["id"]))
    assert rpc(client, item["token"], "tools/list").status_code == 401
    with connect_users_db() as db:
        db.execute("UPDATE integration_tokens SET expires_at = ? WHERE id = ?", (int(time.time()) + 100, item["id"]))
        db.execute("DELETE FROM workspace_members WHERE workspace_id = ? AND user_id = ?",
                   (ws, account_of("mcp-reader")))
    try:
        assert rpc(client, item["token"], "tools/list").status_code == 401
    finally:
        with connect_users_db() as db:
            db.execute("INSERT INTO workspace_members VALUES (?, ?, 'owner', '', '')",
                       (ws, account_of("mcp-reader")))


def test_no_cookie_fallback_and_transport_guards(client, connection):
    c, _, item = connection
    assert c.post("/mcp", json={}).status_code == 401
    assert rpc(client, "bad", "tools/list").status_code == 401
    assert rpc(client, item["token"], "tools/list", host="attacker.example").status_code == 421
    assert client.post("/mcp", headers={"Origin": "https://attacker.example", "Authorization": "Bearer " + item["token"]}).status_code == 403
    r = client.post("/mcp", headers={"Host": "localhost", "Authorization": "Bearer " + item["token"],
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}, content=" " * 65537)
    assert r.status_code == 413


def test_token_management_requires_owner_session(anon, connection):
    c, ws, item = connection
    assert anon.get("/api/integrations/tokens").status_code == 401
    assert anon.post("/api/integrations/tokens", json={}).status_code == 401
    # a token is an identity on the HTTP API (auth.py) but never a session that manages tokens
    assert anon.get("/api/integrations/tokens", headers={"Authorization": "Bearer " + item["token"]}).status_code == 403
    anon.post("/api/login-guest")
    assert anon.post("/api/integrations/tokens", json={}).status_code == 403
    assert c.post("/api/integrations/tokens", json={}, headers={"Origin": "https://attacker.example"}).status_code == 403
    assert c.post("/api/integrations/tokens", json={"expires_in_days": 0}).status_code == 422
    make_user("mcp-token-other", "pw")
    other = login("mcp-token-other", "pw")
    assert other.get(f"/api/integrations/tokens?ws={ws}").status_code == 403
    other.delete(f"/api/integrations/tokens/{item['id']}")
    assert resolve_token(item["token"]) is not None
    other.close()


def test_shared_dispatch_enforces_permissions(connection):
    _, ws, _ = connection
    result, action = run_agent_tool(ws, {"type": "folder"}, "list_pages", {}, allowed_tools=set())
    assert action["error"] and "not enabled" in result
    assert all(s["name"] not in {"rename_page", "move_page", "edit_block", "create_block", "move_block"}
               for s in agent_tools("folder", can_write=False))


def _page_with_note(c, ws, title, note):
    page = c.post(f"/api/blocks?ws={ws}", json={"parent_id": "root", "content": title})
    assert page.status_code == 200, page.text
    assert c.post(f"/api/blocks?ws={ws}", json={"parent_id": page.json()["id"], "content": note}).status_code == 200
    return page.json()


def test_one_connection_reads_several_workspaces(client, connection):
    from gamma import workspaces

    c, ws, _ = connection
    lab = workspaces.create("Lab notebook", account_of("mcp-reader"))["id"]
    hidden = workspaces.create("Hidden drafts", account_of("mcp-reader"))["id"]
    try:
        home_page = _page_with_note(c, ws, "Home spectra paper", "McpSpectraEvidence at home")
        lab_page = _page_with_note(c, lab, "Lab spectra paper", "McpSpectraEvidence in the lab")
        hidden_page = _page_with_note(c, hidden, "Hidden spectra paper", "McpSpectraEvidence kept hidden")
        made = c.post("/api/integrations/tokens", json={"name": "Two", "workspaces": [ws, lab]})
        assert made.status_code == 201, made.text
        assert made.json()["workspaces"] == [ws, lab]
        token = made.json()["token"]
        listed = call(client, token, "list_workspaces", {})
        assert [w["id"] for w in listed["structuredContent"]["workspaces"]] == [ws, lab]
        assert "Lab notebook" in listed["content"][0]["text"] and hidden not in listed["content"][0]["text"]
        # an id finds its own workspace
        read = call(client, token, "read_page", {"page_id": lab_page["id"]})
        assert not read["isError"], read
        assert "McpSpectraEvidence in the lab" in read["content"][0]["text"]
        assert f"?ws={lab}&page={lab_page['id']}" in read["content"][0]["text"]
        assert read["structuredContent"]["workspace_id"] == lab
        # a search covers every connected workspace, one section each, and no other
        found = call(client, token, "search_library", {"query": "McpSpectraEvidence"})["content"][0]["text"]
        assert home_page["id"] in found and lab_page["id"] in found and hidden_page["id"] not in found
        assert "Workspace Lab notebook" in found
        # a listing reads the default workspace, or the one named, by id or name
        assert lab_page["id"] not in call(client, token, "list_pages", {})["content"][0]["text"]
        for name in (lab, "lab NOTEBOOK"):
            text = call(client, token, "list_pages", {"workspace": name})["content"][0]["text"]
            assert lab_page["id"] in text and home_page["id"] not in text
        assert call(client, token, "list_pages", {"workspace": hidden})["isError"]
        refused = call(client, token, "read_page", {"page_id": hidden_page["id"]})
        assert refused["isError"] and "McpSpectraEvidence" not in str(refused)
        # a link reads in its own workspace, among the connection's only
        link = call(client, token, "read_gamma_link", {"url": f"http://localhost/?ws={lab}&page={lab_page['id']}"})
        assert not link["isError"] and link["structuredContent"]["workspace_id"] == lab
        refused = call(client, token, "read_gamma_link",
                       {"url": f"http://localhost/?ws={hidden}&page={hidden_page['id']}"})
        assert refused["isError"] and "McpSpectraEvidence" not in str(refused)
        # the HTTP API keeps the token to its own workspace
        bearer = {"Authorization": f"Bearer {token}"}
        assert client.get(f"/api/blocks/{lab_page['id']}?ws={lab}", headers=bearer).status_code == 403
    finally:
        workspaces.delete(lab)
        workspaces.delete(hidden)


def test_access_changes_and_follows_the_workspaces(client, connection):
    from gamma import workspaces

    c, ws, item = connection
    lab = workspaces.create("Changing lab", account_of("mcp-reader"))["id"]
    later = None
    try:
        lab_page = _page_with_note(c, lab, "Changing lab paper", "McpChangingEvidence")
        assert call(client, item["token"], "read_page", {"page_id": lab_page["id"]})["isError"]
        changed = c.patch(f"/api/integrations/tokens/{item['id']}", json={"workspaces": [ws, lab]})
        assert changed.status_code == 200, changed.text
        assert not call(client, item["token"], "read_page", {"page_id": lab_page["id"]})["isError"]
        # every workspace, including one made afterwards
        assert c.patch(f"/api/integrations/tokens/{item['id']}", json={"all_workspaces": True}).json()["all_workspaces"]
        later = workspaces.create("Later lab", account_of("mcp-reader"))["id"]
        names = [w["id"] for w in call(client, item["token"], "list_workspaces", {})["structuredContent"]["workspaces"]]
        assert names[0] == ws and {lab, later} <= set(names)
        # only workspaces the account can open (none named: the current one), never a write token's second
        foreign_ws = make_user("mcp-foreign", "pw")
        assert c.patch(f"/api/integrations/tokens/{item['id']}", json={"workspaces": [foreign_ws]}).status_code == 403
        assert c.patch(f"/api/integrations/tokens/{item['id']}", json={"workspaces": []}).json()["workspaces"] == [ws]
        write = c.post("/api/integrations/tokens", json={"name": "Mirror", "scope": "write"}).json()
        assert write["workspaces"] == [ws]
        assert c.patch(f"/api/integrations/tokens/{write['id']}", json={"workspaces": [ws, lab]}).status_code == 400
        c.delete(f"/api/integrations/tokens/{write['id']}")
        # a deleted workspace leaves the grant; the connection keeps the rest
        assert c.patch(f"/api/integrations/tokens/{item['id']}", json={"workspaces": [lab, ws]}).status_code == 200
        workspaces.delete(lab)
        lab = None
        row = next(t for t in c.get("/api/integrations/tokens").json()["tokens"] if t["id"] == item["id"])
        assert row["workspaces"] == [ws] and row["workspace_id"] == ws
        assert rpc(client, item["token"], "tools/list").status_code == 200
    finally:
        for gone in (lab, later):
            if gone:
                workspaces.delete(gone)


def test_setup_code_is_claimed_once_for_a_token(client, anon, connection):
    from gamma import workspaces

    c, ws, _ = connection
    lab = workspaces.create("Setup lab", account_of("mcp-reader"))["id"]
    try:
        assert anon.post("/api/integrations/setup-codes", json={"name": "DeepSeek Harness"}).status_code == 401
        made = c.post("/api/integrations/setup-codes", json={"name": "DeepSeek Harness", "workspaces": [ws, lab]})
        assert made.status_code == 201, made.text
        code = made.json()["code"]
        assert made.json()["expires_in"] == 600
        # no connection until the setup command claims it
        assert all(t["name"] != "DeepSeek Harness" for t in c.get("/api/integrations/tokens").json()["tokens"])
        claimed = anon.post("/api/integrations/claim", json={"code": code})
        assert claimed.status_code == 200, claimed.text
        assert claimed.headers["cache-control"] == "no-store"
        token = claimed.json()["token"]
        names = [w["id"] for w in call(client, token, "list_workspaces", {})["structuredContent"]["workspaces"]]
        assert names == [ws, lab]
        row = next(t for t in c.get("/api/integrations/tokens").json()["tokens"] if t["name"] == "DeepSeek Harness")
        assert row["scope"] == "read" and row["workspaces"] == [ws, lab]
        assert anon.post("/api/integrations/claim", json={"code": code}).status_code == 400
        assert anon.post("/api/integrations/claim", json={"code": "x" * 43}).status_code == 400
        c.delete(f"/api/integrations/tokens/{row['id']}")
        # signing out ends the codes that session asked for
        make_user("mcp-setup-out", "pw")
        leaving = login("mcp-setup-out", "pw")
        late = leaving.post("/api/integrations/setup-codes", json={"name": "DeepSeek Harness"}).json()["code"]
        leaving.post("/api/logout")
        assert anon.post("/api/integrations/claim", json={"code": late}).status_code == 400
        leaving.close()
    finally:
        workspaces.delete(lab)
