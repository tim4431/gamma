"""Exercise the real SDK transport, scoped credentials, and shared dispatch."""

import time

import pytest

from conftest import login, make_page, make_user
from gamma.ai_tools import agent_tools, run_agent_tool
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
    assert {t["name"] for t in tools} == {"list_pages", "list_folders", "read_page", "read_block", "read_chats",
                                          "view_pdf_page", "search_library", "read_gamma_link", "export_page"}
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
    from PyPDF2 import PdfWriter

    w = PdfWriter()
    for _ in range(2):
        w.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    w.write(buf)
    up = c.post("/api/uploads", files={"file": ("p.pdf", buf.getvalue(), "application/pdf")})
    assert up.status_code == 200, up.text
    page = make_page(c, title, properties={"doc_id": up.json()["doc_id"], "source_url": up.json()["source_url"]})
    rect = {"x1": 100, "y1": 72, "x2": 300, "y2": 92, "width": 612, "height": 792, "pageNumber": 1}
    r = c.put(f"/api/blocks/{page['id']}/children", json={"blocks": [
        {"id": f"{page['id']}-hl", "content": "McpHighlightNote", "properties": {
            "highlight_id": f"{page['id']}-hl", "quote": "McpQuotedPassage", "pdf_page": 1,
            "pdf_position": {"pageNumber": 1, "boundingRect": dict(rect), "rects": [rect]}}, "children": []},
        {"id": f"{page['id']}-img", "content": "![figure](/api/uploads/abc123.png)", "properties": {}, "children": []},
    ]})
    assert r.status_code == 200, r.text
    return page


def test_folders_chats_and_pdf_pictures(client, connection):
    c, ws, item = connection
    paper = _pdf_page(c, "Folder tree paper")
    assert c.put(f"/api/blocks/{paper['id']}", json={"properties": {"folder": "mcp/tree"}}).status_code == 200
    result = call(client, item["token"], "list_folders", {})
    assert not result["isError"] and '"mcp/tree" (1 page)' in result["content"][0]["text"]
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


def test_export_page_formats(client, connection):
    import base64
    import io
    from PyPDF2 import PdfReader

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
    assert resolve_token(item["token"]) == ("mcp-reader", ws)
    listed = c.get("/api/integrations/tokens")
    assert listed.headers["cache-control"] == "no-store"
    assert item["token"] not in listed.text and "token_hash" not in listed.text
    with connect_users_db() as db:
        stored = db.execute("SELECT token_hash FROM integration_tokens WHERE id = ?", (item["id"],)).fetchone()[0]
    assert item["token"] != stored and len(stored) == 64
    c.delete(f"/api/integrations/tokens/{item['id']}")
    assert rpc(client, item["token"], "tools/list").status_code == 401


def test_connection_listing_stays_scoped_to_current_workspace(client, connection):
    from gamma import workspaces
    from gamma.integrations import create_token

    c, ws, item = connection
    second = workspaces.create("Second library", "mcp-reader")
    other = create_token("mcp-reader", second["id"], "Codex second", 90)
    foreign_ws = make_user("another-reader", "pw")
    foreign = create_token("another-reader", foreign_ws, "Private connection", 90)
    result = c.get("/api/integrations/tokens")
    data = result.json()
    assert data["workspace_id"] == ws
    assert [t["id"] for t in data["tokens"]] == [item["id"]]
    assert all(private not in result.text for private in (item["token"], other["token"], other["id"], foreign["token"], foreign["id"]))
    second_listing = c.get(f"/api/integrations/tokens?ws={second['id']}").json()
    assert [t["id"] for t in second_listing["tokens"]] == [other["id"]]


def test_expiration_and_membership_loss(client, connection):
    _, ws, item = connection
    with connect_users_db() as db:
        db.execute("UPDATE integration_tokens SET expires_at = ? WHERE id = ?", (int(time.time()) - 1, item["id"]))
    assert rpc(client, item["token"], "tools/list").status_code == 401
    with connect_users_db() as db:
        db.execute("UPDATE integration_tokens SET expires_at = ? WHERE id = ?", (int(time.time()) + 100, item["id"]))
        db.execute("DELETE FROM workspace_members WHERE workspace_id = ? AND username = ?", (ws, "mcp-reader"))
    try:
        assert rpc(client, item["token"], "tools/list").status_code == 401
    finally:
        with connect_users_db() as db:
            db.execute("INSERT INTO workspace_members VALUES (?, ?, 'owner', '', '')", (ws, "mcp-reader"))


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
