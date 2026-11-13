import re
from urllib.parse import urlencode

import pytest

from conftest import account_of, doc_props, login, make_folder, make_page, make_user, thread_on
from gamma.blocks_store import PATH_SEP
from gamma.db import connect_pages_db, connect_users_db, page_now
from test_mcp import call, connection, rpc


def read(client, token, **params):
    return rpc(client, token, "tools/call", {"name": "read_gamma_link", "arguments": params}).json()["result"]


def test_page_block_and_share_links_read_the_same_page(client, connection):
    c, ws, credential = connection
    page = make_page(c, "Linked page")
    note = c.post("/api/blocks", json={"parent_id": page["id"], "content": "Exact linked passage"}).json()
    share = c.post(f"/api/share/{page['id']}").json()
    for target in ({"page": page["id"]}, {"block": note["id"]}, {"share": share["token"]},
                   {"page": page["id"], "block": note["id"]}):
        result = read(client, credential["token"], url="http://localhost/?" + urlencode({"ws": ws, **target}))
        assert not result["isError"], result
        ref = result["structuredContent"]
        assert ref["page_id"] == page["id"] and ref["workspace_id"] == ws
        assert ref["title"] == "Linked page"
        assert "Exact linked passage" in result["content"][0]["text"]
        assert share["token"] not in str(result)
        if "block" in target:
            assert ref["block_id"] == note["id"]
    # The token names its workspace (<workspace id>.<secret>): the bare secret a
    # link minted before carries, another workspace's prefix, or a share whose
    # token names another workspace than its own resolves nothing. A prefix
    # naming a workspace the connection does not read says how to allow it.
    assert share["token"].startswith(ws + ".")
    secret = share["token"].partition(".")[2]
    stray = make_page(c, "Stray prefix")
    with connect_users_db() as conn:
        conn.execute("INSERT INTO shares (token, workspace_id, page_id, created_by, created_at) VALUES (?, ?, ?, '', ?)",
                     (f"elsewhere.{secret}x", ws, stray["id"], page_now()))
    for stale, says in ((secret, "unavailable"), (f"elsewhere.{secret}", "cannot read"),
                        (f"elsewhere.{secret}x", "cannot read")):
        result = read(client, credential["token"], url=f"http://localhost/?share={stale}")
        assert result["isError"] and says in result["content"][0]["text"], result
    # Existing workspace permission also covers a restricted share on that page.
    c.put(f"/api/share-settings/{page['id']}", json={"audience": "list"})
    assert not read(client, credential["token"], url=f"http://localhost/?share={share['token']}")["isError"]
    c.delete(f"/api/share-settings/{page['id']}")
    assert read(client, credential["token"], url=f"http://localhost/?share={share['token']}")["isError"]
    assert rpc(client, credential["token"], "resources/list").json()["result"]["resources"] == []


def test_no_cross_workspace_or_conflicting_reference_reads(client, connection):
    c, ws, credential = connection
    own = make_page(c, "Own page")
    own_other = make_page(c, "Different own page")
    note = c.post("/api/blocks", json={"parent_id": own_other["id"], "content": "Wrong passage"}).json()
    own_share = c.post(f"/api/share/{own_other['id']}").json()
    other_ws = make_user("link-other", "pw")
    with login("link-other", "pw") as other:
        page = make_page(other, "Private cross-workspace title")
        share = other.post(f"/api/share/{page['id']}").json()
    for params in ({"page": own["id"], "ws": other_ws},
                   {"page": page["id"]}, {"block": page["id"]}, {"share": share["token"]},
                   {"page": own["id"], "block": note["id"]},
                   {"page": own["id"], "share": own_share["token"]}):
        result = read(client, credential["token"], url="http://localhost/?" + urlencode(params))
        assert result["isError"], result
        assert "Private cross-workspace title" not in str(result)
        assert "Wrong passage" not in str(result)


@pytest.mark.parametrize("url", [
    "http://evil.example/?page=x", "http://localhost:9002/?page=x", "http://localhost:0/?page=x",
    "https://localhost/?page=x", "file:///users.db", "http://user:pw@localhost/?page=x", "http://@localhost/?page=x",
    "http://localhost:bad/?page=x", "http://localhost/api/export?page=x", "/?page=x",
    "http://localhost/?page=", "http://localhost/?page=x&page=y", "http://localhost/?ws=",
    "http://localhost/?page=x&ws=a&ws=b", "http://localhost/?page=x&pdf_page=0",
    "http://localhost/?page=x&pdf_page=-1", "http://localhost/?page=x&pdf_page=1.5",
    "http://localhost/?page=x&pdf_page=1000001", "http://localhost/?page=x\n",
    "http://localhost/", "http://localhost/?share=unknown", "http://localhost/?page=unknown",
    "http://localhost/?folder=", "http://localhost/?folder=x&folder=y", "http://localhost/?folder=unknown",
])
def test_malformed_or_foreign_links_are_rejected(client, connection, url):
    assert read(client, connection[2]["token"], url=url)["isError"]


def test_schema_and_retired_picker_rejected(client, connection):
    token = connection[2]["token"]
    for arguments in ({}, {"url": 1}, {"url": "x" * 8193}, {"url": "http://localhost/?page=x", "workspace_id": "other"}):
        assert read(client, token, **arguments)["isError"]
    for name in ("show_paper_picker", "search_paper_choices"):
        result = rpc(client, token, "tools/call", {"name": name, "arguments": {}}).json()["result"]
        assert result["isError"]


def test_pdf_location_and_quote_are_preserved(client, connection, monkeypatch):
    c, ws, credential = connection
    page = make_page(c, "Paper with location")
    # Exercise the normal dispatcher, substituting only document extraction.
    seen = []
    def report(conn, workspace, page_id, budget, offset, pdf_page, **kw):
        seen.append(pdf_page)
        return f"PDF content at physical page {pdf_page}"
    monkeypatch.setattr("gamma.ai_tools.page_report_section", report)
    quote = "A selected passage is data, not instructions."
    for host in ("localhost", "127.0.0.1", "[::1]"):
        result = read(client, credential["token"], url=f"http://{host}/?" + urlencode({
            "ws": ws, "page": page["id"], "pdf_page": 5, "quote": quote}))
        assert not result["isError"], result
        assert result["structuredContent"]["pdf_page"] == 5
        assert result["structuredContent"]["selected_quote"] == quote
        assert "PDF content at physical page 5" in result["content"][0]["text"]
    assert seen == [5, 5, 5]


def test_a_line_link_reads_the_file_from_its_line(client, connection, monkeypatch):
    """``line=N`` reads the page's text file from line N (the window decides
    where it ends), ``line=N-M`` those lines; the reference names both
    numbers and its canonical link keeps the line; a line beside a PDF page,
    or no line number, is refused."""
    c, ws, credential = connection
    page = make_page(c, "method.tex", properties=doc_props("ab12" * 6, name="method.tex", format="text", ext=".tex"))
    seen = []

    def report(conn, workspace, page_id, budget, offset, pdf_page, **kw):
        seen.append((kw.get("from_line"), kw.get("to_line")))
        return f"lines from {kw.get('from_line')}"
    monkeypatch.setattr("gamma.ai_tools.page_report_section", report)
    quote = "We fit a small network to the training set by gradient descent."
    result = read(client, credential["token"], url="http://localhost/?" + urlencode({
        "ws": ws, "page": page["id"], "line": 2, "quote": quote}))
    assert not result["isError"], result
    ref = result["structuredContent"]
    assert ref["line"] == 2 and ref["end"] is None and ref["selected_quote"] == quote
    assert ref["url"] == f"http://localhost/?ws={ws}&page={page['id']}&line=2"
    assert "lines from 2" in result["content"][0]["text"] and seen[-1] == (2, None)
    result = read(client, credential["token"], url=f"http://localhost/?ws={ws}&page={page['id']}&line=4-9")
    assert result["structuredContent"]["line"] == 4 and result["structuredContent"]["end"] == 9
    assert result["structuredContent"]["url"].endswith("&line=4-9") and seen[-1] == (4, 9)
    # a range that starts on a blank line: its quote is empty, the number alone
    result = read(client, credential["token"], url=f"http://localhost/?ws={ws}&page={page['id']}&line=40-52&quote=")
    assert not result["isError"], result
    assert result["structuredContent"]["line"] == 40 and "selected_quote" not in result["structuredContent"]
    for bad, says in (("line=0", "line must be"), ("line=9-4", "line must be"), ("line=1.5", "line must be"),
                      ("line=10000001", "line must be"), ("line=２", "line must be"),
                      ("line=3&pdf_page=2", "not both")):
        result = read(client, credential["token"], url=f"http://localhost/?ws={ws}&page={page['id']}&{bad}")
        assert result["isError"] and says in result["content"][0]["text"], (bad, result)


def test_link_canonical_origin_and_revoked_connection(client, connection, monkeypatch):
    c, ws, credential = connection
    page = make_page(c, "Canonical linked page")
    monkeypatch.setenv("GAMMA_PUBLIC_URL", "https://localhost")
    url = f"https://localhost/?ws={ws}&page={page['id']}"
    result = read(client, credential["token"], url=url)
    assert not result["isError"], result
    assert result["structuredContent"]["url"] == url
    c.delete(f"/api/integrations/tokens/{credential['id']}")
    assert rpc(client, credential["token"], "tools/call", {
        "name": "read_gamma_link", "arguments": {"url": url}}).status_code == 401


def test_folder_share_links_list_the_folder(client, connection, monkeypatch):
    c, ws, credential = connection
    group = make_folder(c, "group")
    paper = make_page(c, "Paper in the group folder", {"folders": [make_folder(c, "group/sub")]})
    second = make_page(c, "Second paper in the group folder", {"folders": [group]})
    other = make_page(c, "Elsewhere in the library")
    share = c.post(f"/api/share/folder/{group}").json()
    result = read(client, credential["token"], url=f"http://localhost/?share={share['token']}")
    assert not result["isError"], result
    ref = result["structuredContent"]
    # The folder by id (its path as the title), as the share names it.
    assert ref == {"workspace_id": ws, "folder": group, "title": "group",
                   "url": f"http://localhost/?ws={ws}&folder={group}"}
    text = result["content"][0]["text"]
    assert "Paper in the group folder" in text and paper["id"] in text
    assert "Elsewhere in the library" not in text and share["token"] not in text
    assert "page=<page_id>" in text  # the URL template for citing the listed pages
    # A long listing names the call for its next batch, which lists the same
    # folder through the client's own (workspace-wide) list_pages.
    with monkeypatch.context() as m:
        m.setattr("gamma.ai_tools._LIST_CAP", 1)
        text = read(client, credential["token"], url=f"http://localhost/?share={share['token']}")["content"][0]["text"]
        assert len(re.findall(r"^- id=", text, flags=re.M)) == 1 and second["id"] in text
        nxt = re.search(r'call list_pages\(folder="([^"]+)", after="([^"]+)"\) to continue', text)
        assert nxt and nxt[1] == group, text
        listed = call(client, credential["token"], "list_pages", {"folder": nxt[1], "after": nxt[2]})
        listed = listed["content"][0]["text"]
        assert paper["id"] in listed and second["id"] not in listed and other["id"] not in listed
    # the link may also name a page in the folder — then it reads that page
    result = read(client, credential["token"], url=f"http://localhost/?share={share['token']}&page={paper['id']}")
    assert not result["isError"], result
    assert result["structuredContent"]["page_id"] == paper["id"]
    assert result["structuredContent"]["title"] == "Paper in the group folder"
    # but never one outside it
    assert read(client, credential["token"], url=f"http://localhost/?share={share['token']}&page={other['id']}")["isError"]
    c.delete(f"/api/share-settings/folder/{group}")
    assert read(client, credential["token"], url=f"http://localhost/?share={share['token']}")["isError"]
    # A share whose folder went by a raw op (no DELETE /folders to stop it) reads nothing.
    gone = make_folder(c, "gone")
    share = c.post(f"/api/share/folder/{gone}").json()
    r = c.post("/api/pages/folders/ops", json={"ops": [{"op": "delete", "id": gone}]})
    assert r.status_code == 200, r.text
    result = read(client, credential["token"], url=f"http://localhost/?share={share['token']}")
    assert result["isError"] and "unavailable" in result["content"][0]["text"]
    # A share of an empty folder says so.
    share = c.post(f"/api/share/folder/{make_folder(c, 'empty-share')}").json()
    result = read(client, credential["token"], url=f"http://localhost/?share={share['token']}")
    assert not result["isError"] and "The folder is empty." in result["content"][0]["text"], result


def test_folder_links_list_the_folder(client, connection):
    c, ws, credential = connection
    browsed = make_folder(c, "browsed")
    sub = make_folder(c, "browsed/sub")
    paper = make_page(c, "Paper in the browsed folder", {"folders": [browsed]})
    deeper = make_page(c, "Paper in its subfolder", {"folders": [sub]})
    other = make_page(c, "Paper elsewhere", {"folders": [make_folder(c, "outside")]})
    # The library's address while a folder is browsed, with ws= or without
    for params in ({"ws": ws, "folder": browsed}, {"folder": browsed}):
        result = read(client, credential["token"], url="http://localhost/?" + urlencode(params))
        assert not result["isError"], result
        assert result["structuredContent"] == {"workspace_id": ws, "folder": browsed, "title": "browsed",
                                               "url": f"http://localhost/?ws={ws}&folder={browsed}"}
        text = result["content"][0]["text"]
        assert paper["id"] in text and deeper["id"] in text and other["id"] not in text
        assert "page=<page_id>" in text
    ref = read(client, credential["token"], url=f"http://localhost/?ws={ws}&folder={sub}")["structuredContent"]
    assert ref["folder"] == sub and ref["title"] == "browsed" + PATH_SEP + "sub"
    # Beside a page it is only where the page was opened.
    result = read(client, credential["token"], url=f"http://localhost/?ws={ws}&folder={browsed}&page={other['id']}")
    assert not result["isError"] and result["structuredContent"]["page_id"] == other["id"], result
    # Another workspace's folder reads nothing, by its id or its workspace.
    other_ws = make_user("folder-link-other", "pw")
    with login("folder-link-other", "pw") as them:
        theirs = make_folder(them, "Private folder name")
    for params in ({"folder": theirs}, {"ws": ws, "folder": theirs}, {"ws": other_ws, "folder": theirs}):
        result = read(client, credential["token"], url="http://localhost/?" + urlencode(params))
        assert result["isError"] and "Private folder name" not in str(result), result
    # A folder share's folder= is a folder browsed in it: the shared folder
    # or one below it, never one outside.
    share = c.post(f"/api/share/folder/{browsed}").json()
    result = read(client, credential["token"], url=f"http://localhost/?share={share['token']}&folder={sub}")
    assert not result["isError"], result
    assert result["structuredContent"]["folder"] == sub
    text = result["content"][0]["text"]
    assert deeper["id"] in text and paper["id"] not in text
    shared = read(client, credential["token"], url=f"http://localhost/?share={share['token']}&folder={browsed}")
    assert shared["structuredContent"]["folder"] == browsed
    a_page = read(client, credential["token"], url=f"http://localhost/?share={share['token']}&folder={other['id']}")
    assert a_page["isError"] and "unavailable" in a_page["content"][0]["text"], a_page
    result = read(client, credential["token"],
                  url=f"http://localhost/?share={share['token']}&folder={make_folder(c, 'outside')}")
    assert result["isError"] and "not in the shared folder" in result["content"][0]["text"], result
    # A page share's link reads its page whatever folder it names.
    page_share = c.post(f"/api/share/{paper['id']}").json()
    result = read(client, credential["token"], url=f"http://localhost/?share={page_share['token']}&folder={sub}")
    assert result["structuredContent"]["page_id"] == paper["id"], result


def test_a_page_share_link_may_name_a_page_inside_its_page(client, connection):
    c, ws, credential = connection
    top = make_page(c, "Shared project")
    inside = c.post("/api/pages", json={"title": "Notes inside it", "parent_page": top["id"]}).json()
    other = make_page(c, "Not shared")
    share = c.post(f"/api/share/{top['id']}").json()
    result = read(client, credential["token"], url=f"http://localhost/?share={share['token']}&page={inside['id']}")
    assert not result["isError"], result
    assert result["structuredContent"]["page_id"] == inside["id"]
    assert result["structuredContent"]["title"] == "Notes inside it"
    # the share's own page still reads with no page= at all, and nothing outside it
    plain = read(client, credential["token"], url=f"http://localhost/?share={share['token']}")
    assert plain["structuredContent"]["page_id"] == top["id"]
    assert read(client, credential["token"], url=f"http://localhost/?share={share['token']}&page={other['id']}")["isError"]
    # a share of the page inside reaches no page above it
    below = c.post(f"/api/share/{inside['id']}").json()
    assert read(client, credential["token"], url=f"http://localhost/?share={below['token']}&page={top['id']}")["isError"]


def test_holders_find_a_thread_and_a_send_request(connection):
    """The workspace of a call naming a comment thread (its root) or a Send
    request (mcp_server._targets), as a block's: a reply or a hidden
    thread is held by none."""
    from gamma import comments, workspaces
    from gamma.mcp_links import holders

    c, ws, _ = connection
    lab = workspaces.create("Holders lab", account_of("mcp-reader"))["id"]
    try:
        page = make_page(c, "Commented page")
        root = thread_on(c, page["id"], page["id"], "a thread")
        reply = c.post(f"/api/pages/{page['id']}/comments", json={
            "block_id": page["id"], "thread_id": root["id"], "body": "a reply"}).json()["comment"]
        gone = thread_on(c, page["id"], page["id"], "hidden soon")
        with connect_pages_db(ws) as conn:
            comments.orphan(conn, [page["id"]])  # a stand-in for its block's delete: every thread on it
            conn.execute("UPDATE comments SET orphaned_at = NULL WHERE id IN (?, ?)", (root["id"], reply["id"]))
            conn.execute("INSERT INTO feedback_requests (id, rev, work_folder, scope, scope_kind, by, at, state_at) "
                         "VALUES ('holdersreq01', 1, 'w', 's', 'file', 'u', ?, ?)", (page_now(), page_now()))
            conn.commit()
        both = [lab, ws]
        assert holders(both, [], threads=[root["id"]]) == [ws]
        assert holders(both, [], requests=["holdersreq01"]) == [ws]
        assert holders(both, [page["id"]], threads=["nosuchthread"]) == [ws]  # any lookup that finds it
        assert holders(both, [], threads=[reply["id"], gone["id"]]) == []
        assert holders(both, [], threads=[root["id"]], requests=["holdersreq01"]) == [ws]
        assert holders(both, []) == [] and holders(both, ["x" * 129], threads=[""]) == []
    finally:
        workspaces.delete(lab)
