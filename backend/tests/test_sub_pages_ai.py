"""Sub-pages in the agent's tools and the MCP adapter: list_pages names a
sub-page's parent (``parent=<id>``) and lists the pages right inside one
(``parent``), read_page's header places the page among its sub-pages, a
folder chat reaches a sub-page through the page it is inside, move_page
leaves a sub-page to its top, and list_folders counts library pages only;
a page chat reads and searches the pages inside its page and edits only
it, read_page's properties name a sub-page's path and its top's folders,
and the trash tools say which pages come back with a page or inside one."""

import pytest

from conftest import doc_props, login, make_folder, make_label, make_user
from gamma import ai_context
from gamma.ai_tools import run_agent_tool
from gamma.db import connect_pages_db

from ai_fixtures import folder, org  # noqa: F401  (org is a fixture)


def _page(c, title, **body):
    r = c.post("/api/pages", json={"title": title, **body})
    assert r.status_code == 200, r.text
    return r.json()["id"]


@pytest.fixture(scope="module")
def tree(org):
    """Inside the fixture's paper a (filed in "readout"): "Mid", and "Leaf"
    inside Mid; plus a library page "Lone" filed in "cooling"."""
    c, ids = org
    mid = _page(c, "Mid", parent_page=ids["a"])
    leaf = _page(c, "Leaf", parent_page=mid)
    lone = _page(c, "Lone", folders=[ids["cooling"]])
    return {"mid": mid, "leaf": leaf, "lone": lone}


def _line(text, page_id):
    return next(line for line in text.splitlines() if f"id={page_id} " in line)


def test_list_pages_names_the_parent_of_a_sub_page_only(org, tree):
    c, ids = org
    text, _ = run_agent_tool(ids["ws"], folder(""), "list_pages", {})
    assert f"parent={ids['a']}" in _line(text, tree["mid"]) and f"parent={tree['mid']}" in _line(text, tree["leaf"])
    # A sub-page's line names its parent in place of folders; a library page has no parent=.
    assert "folders=" not in _line(text, tree["mid"])
    assert "parent=" not in _line(text, ids["a"]) and 'folders=["readout"]' in _line(text, ids["a"])
    assert "parent=" not in _line(text, tree["lone"])
    # `parent` lists the pages right inside one, and the next batch's call keeps it.
    text, action = run_agent_tool(ids["ws"], folder(""), "list_pages", {"parent": ids["a"]})
    assert f"id={tree['mid']}" in text and tree["leaf"] not in text and action["summary"].endswith(
        f"inside page {ids['a']}")
    text, _ = run_agent_tool(ids["ws"], folder(""), "list_pages", {"parent": tree["lone"]})
    assert text == "No pages match those filters."


def test_a_folder_chat_reaches_sub_pages_through_their_top(org, tree):
    c, ids = org
    text, _ = run_agent_tool(ids["ws"], folder(ids["readout"]), "list_pages", {})
    assert tree["mid"] in text and tree["leaf"] in text and tree["lone"] not in text
    text, _ = run_agent_tool(ids["ws"], folder(""), "list_pages", {"folder": "readout"})
    assert tree["leaf"] in text
    text, _ = run_agent_tool(ids["ws"], folder(ids["readout"]), "read_page", {"page_id": tree["leaf"]})
    assert not text.startswith("error"), text
    # A sub-page's own folders are no filing: a sub-page of a page in "cooling" is not in "readout".
    stray = _page(c, "Stray", parent_page=_page(c, "Cooled", folders=[ids["cooling"]]))
    with connect_pages_db(ids["ws"]) as cx:  # past the writers, which drop a sub-page's folders
        cx.execute("UPDATE unified_blocks SET properties = json_set(properties, '$.folders', json_array(?)) "
                   "WHERE id = ?", (ids["readout"], stray))
        cx.commit()
    text, _ = run_agent_tool(ids["ws"], folder(ids["readout"]), "read_page", {"page_id": stray})
    assert text == "error: page is outside this chat's scope"
    text, _ = run_agent_tool(ids["ws"], folder(ids["cooling"]), "read_page", {"page_id": stray})
    assert not text.startswith("error"), text


def test_read_page_names_the_parent_and_the_sub_pages(org, tree, monkeypatch):
    c, ids = org
    text, _ = run_agent_tool(ids["ws"], folder(""), "read_page", {"page_id": tree["leaf"], "pdf_chars": 0})
    assert f'Inside page: "cavity paper" › "Mid" (the parent\'s page ID: {tree["mid"]})' in text
    assert "Pages inside" not in text
    text, _ = run_agent_tool(ids["ws"], folder(""), "read_page", {"page_id": tree["mid"], "pdf_chars": 0})
    assert f'Inside page: "cavity paper" (the parent\'s page ID: {ids["a"]})' in text
    assert f'Pages inside (1): "Leaf" (page ID: {tree["leaf"]})' in text
    text, _ = run_agent_tool(ids["ws"], folder(""), "read_page", {"page_id": tree["lone"]})
    assert "Inside page" not in text and "Pages inside" not in text
    # A long list names the first ones and the call that lists them all.
    second = _page(c, "Second", parent_page=tree["mid"])
    monkeypatch.setattr(ai_context, "SUB_PAGES_SHOWN", 1)
    text, _ = run_agent_tool(ids["ws"], folder(""), "read_page", {"page_id": tree["mid"], "pdf_chars": 0})
    assert (f'Pages inside (2): "Leaf" (page ID: {tree["leaf"]}); … 1 more — '
            f'list_pages(parent="{tree["mid"]}") lists them all') in text
    assert second not in text


def test_move_page_leaves_a_sub_page_to_its_top(org, tree):
    c, ids = org
    text, _ = run_agent_tool(ids["ws"], folder(""), "move_page", {"page_ids": [tree["mid"]], "folder": "cooling"})
    assert text == (f"error: that page is inside page {ids['a']} and is filed where that page is — "
                    "move the outermost page instead")
    r = c.get(f"/api/blocks/{tree['mid']}")
    assert "folders" not in r.json()["properties"]


def test_list_folders_counts_library_pages_only(org, tree):
    c, ids = org
    text, _ = run_agent_tool(ids["ws"], folder(""), "list_folders", {})
    assert f'- [{ids["readout"]}] "readout" (1 here, 2 with subfolders)' in text.splitlines()
    # Only the fixture's loose note is in no folder; the sub-pages are not counted there.
    assert text.splitlines()[-2] == "1 page is in no folder."


def test_mcp_lists_and_reads_sub_pages(client):
    make_user("mcp-sub-pages", "pw")
    c = login("mcp-sub-pages", "pw")
    token = c.post("/api/integrations/tokens", json={"name": "Codex"}).json()["token"]
    top = _page(c, "McpTop", folders=[make_folder(c, "mcp-sub")])
    inner = _page(c, "McpInner", parent_page=top)

    def call(name, arguments):
        r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": name, "arguments": arguments}},
                        headers={"Host": "localhost", "Authorization": f"Bearer {token}",
                                 "Accept": "application/json, text/event-stream",
                                 "MCP-Protocol-Version": "2025-11-25"})
        assert r.status_code == 200, r.text
        result = r.json()["result"]
        assert not result["isError"], result
        return result["content"][0]["text"]

    assert f"parent={top}" in call("list_pages", {"parent": top})
    assert f'Pages inside (1): "McpInner" (page ID: {inner})' in call("read_page", {"page_id": top})
    assert 'Inside page: "McpTop"' in call("read_gamma_link", {"url": f"http://localhost/?page={inner}"})
    assert inner in call("list_pages", {"folder": "mcp-sub"})


def test_a_folder_chat_lists_and_restores_a_sub_page_deleted_on_its_own(org):
    c, ids = org
    top = _page(c, "Kept top", folders=[ids["readout"]])
    sub = _page(c, "Deleted inside", parent_page=top)
    assert c.delete(f"/api/blocks/{sub}").status_code == 200
    # listed where it goes back to: its parent's top's folders
    entry = next(p for p in c.get("/api/trash").json()["pages"] if p["id"] == sub)
    assert entry["folders"] == [ids["readout"]] and entry["sub_pages"] == 0
    text, _ = run_agent_tool(ids["ws"], folder(ids["readout"]), "list_deleted", {})
    assert f"id={sub} " in text and '"readout"' in _line(text, sub)
    text, _ = run_agent_tool(ids["ws"], folder(ids["cooling"]), "list_deleted", {})
    assert sub not in text
    text, _ = run_agent_tool(ids["ws"], folder(ids["readout"]), "restore_page", {"page_ids": [sub]})
    assert text.startswith("ok"), text
    assert c.get(f"/api/blocks/{sub}").json()["properties"]["parent_page"] == top


def test_a_folder_chat_may_rename_a_label_on_a_sub_page_that_went_with_its_parent(org):
    c, ids = org
    label = make_label(c, "spa-went")
    top = _page(c, "Went top", folders=[ids["readout"]])
    _page(c, "Went inside", parent_page=top, properties={"labels": [label]})
    assert c.delete(f"/api/blocks/{top}").status_code == 200  # the sub-page goes with it
    text, _ = run_agent_tool(ids["ws"], folder(ids["cooling"]), "rename_label", {"label": "spa-went", "name": "x"})
    assert "outside this chat's folder" in text
    text, _ = run_agent_tool(ids["ws"], folder(ids["readout"]), "rename_label",
                             {"label": "spa-went", "name": "spa-went 2"})
    assert text.startswith("ok"), text


def test_a_label_on_a_live_sub_page_is_reached_where_its_top_is_filed(org):
    """_label_out_of_reach counts a live sub-page by the folders its top is
    filed in (it is filed nowhere of its own)."""
    c, ids = org
    label = make_label(c, "spa-live")
    top = _page(c, "Live top", folders=[ids["readout"]])
    _page(c, "Live inside", parent_page=top, properties={"labels": [label]})
    text, _ = run_agent_tool(ids["ws"], folder(ids["cooling"]), "rename_label", {"label": "spa-live", "name": "x"})
    assert "outside this chat's folder" in text, text
    text, _ = run_agent_tool(ids["ws"], folder(ids["readout"]), "rename_label",
                             {"label": "spa-live", "name": "spa-live 2"})
    assert text.startswith("ok"), text


def test_save_paper_leaves_a_sub_page_filed_where_its_top_is(org):
    c, ids = org
    top = _page(c, "Review", folders=[ids["readout"]])
    r = c.post("/api/blocks", json={"parent_id": "root", "content": "Inner paper", "properties": {
        **doc_props("e" * 24), "meta": {"arxiv_id": "2601.07777"}}})
    assert r.status_code == 200, r.text
    paper = r.json()["id"]
    r = c.post(f"/api/pages/{paper}/ops", json={"client": "t", "ops": [
        {"op": "set", "id": paper, "props": {"parent_page": top}}]})
    assert r.status_code == 200, r.text
    text, _ = run_agent_tool(ids["ws"], folder(ids["readout"]), "save_paper", {"source": "arXiv:2601.07777"})
    assert f"already in the library inside page {top}" in text and "nothing changed" in text, text
    text, _ = run_agent_tool(ids["ws"], folder(""), "save_paper", {"source": "arXiv:2601.07777", "folder": "cooling"})
    assert text.startswith("error") and f"inside page {top}" in text and "move_page" in text, text
    assert "folders" not in c.get(f"/api/blocks/{paper}").json()["properties"]


def test_a_page_chat_reads_and_searches_its_sub_pages_and_edits_only_its_page(org, tree, monkeypatch):
    c, ids = org
    note = c.post("/api/blocks", json={"parent_id": tree["leaf"], "content": "SubPageNeedle observation"}).json()
    page_chat = {"type": "page", "page_id": ids["a"]}
    text, _ = run_agent_tool(ids["ws"], page_chat, "read_page", {"page_id": tree["leaf"], "pdf_chars": 0})
    assert not text.startswith("error") and "SubPageNeedle" in text, text
    text, _ = run_agent_tool(ids["ws"], page_chat, "search_library", {"query": "SubPageNeedle"})
    assert tree["leaf"] in text, text
    text, _ = run_agent_tool(ids["ws"], page_chat, "read_block", {"block_id": note["id"]})
    assert "SubPageNeedle" in text, text
    # A page outside it stays out of reach, and a page inside it is read, never edited.
    text, _ = run_agent_tool(ids["ws"], page_chat, "read_page", {"page_id": tree["lone"]})
    assert text == "error: page is outside this chat's scope"
    text, _ = run_agent_tool(ids["ws"], {"type": "page", "page_id": tree["leaf"]}, "read_page", {"page_id": ids["a"]})
    assert text == "error: page is outside this chat's scope"  # the page it is inside is not its own
    text, _ = run_agent_tool(ids["ws"], page_chat, "edit_block",
                             {"block_id": note["id"], "mode": "replace", "content": "changed"})
    assert text.startswith("error") and "outside this chat's scope" in text, text
    assert c.get(f"/api/blocks/{note['id']}").json()["content"] == "SubPageNeedle observation"
    # The header's long list points a page chat at the search, which reaches them, not at list_pages.
    monkeypatch.setattr(ai_context, "SUB_PAGES_SHOWN", 0)
    text, _ = run_agent_tool(ids["ws"], page_chat, "read_page", {"page_id": tree["mid"], "pdf_chars": 0})
    assert "which this chat cannot list — search_library searches them all" in text and "list_pages" not in text


def test_read_page_properties_name_a_sub_page_s_path_and_its_top_s_folders(org, tree):
    c, ids = org
    with connect_pages_db(ids["ws"]) as cx:  # a stray folder of its own, past the writers, counts for nothing
        cx.execute("UPDATE unified_blocks SET properties = json_set(properties, '$.folders', json_array(?)) "
                   "WHERE id = ?", (ids["cooling"], tree["leaf"]))
        cx.commit()
    text, _ = run_agent_tool(ids["ws"], folder(""), "read_page", {"page_id": tree["leaf"], "pdf_chars": 0})
    line = next(line for line in text.splitlines() if line.startswith("Properties: "))
    assert line == 'Properties: path: "cavity paper" › "Mid" › "Leaf"; folders (its top page\'s): "readout"', line
    text, _ = run_agent_tool(ids["ws"], folder(""), "read_page", {"page_id": ids["a"], "pdf_chars": 0})
    assert 'Properties: folders: "readout"; authors' in text


def test_the_trash_tools_say_which_pages_come_back_with_a_parent(org):
    c, ids = org
    top = _page(c, "Trash top", folders=[ids["readout"]])
    _page(c, "Trash inner", parent_page=top)
    kept = _page(c, "Trash kept", folders=[ids["readout"]])
    alone = _page(c, "Trash alone", parent_page=kept)
    assert c.delete(f"/api/blocks/{top}").status_code == 200
    assert c.delete(f"/api/blocks/{alone}").status_code == 200
    text, _ = run_agent_tool(ids["ws"], folder(ids["readout"]), "list_deleted", {})
    assert "| the page inside it comes back with it |" in _line(text, top)
    assert f"| goes back inside page {kept} |" in _line(text, alone)
    text, _ = run_agent_tool(ids["ws"], folder(ids["readout"]), "restore_page", {"page_ids": [top]})
    assert text.startswith("ok") and text.endswith(", with the 1 page inside it"), text
    text, _ = run_agent_tool(ids["ws"], folder(ids["readout"]), "restore_page", {"page_ids": [alone]})
    assert f'to inside page {kept}, filed where that page is ("readout")' in text, text
