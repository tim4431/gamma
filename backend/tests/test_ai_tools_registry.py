"""The agent's tool registry: which tools each scope offers, how the
permission map prunes them, and what the system prompt tells the model."""

from gamma.ai_tools import agent_system, agent_tools

from ai_fixtures import ALL_PERMS, ALL_TOOLS, folder


def test_registry_scopes_and_permissions():
    assert [t["name"] for t in agent_tools("folder")] == [
        "list_pages", "list_folders", "read_page", "read_block", "read_chats", "view_pdf_page", "view_ink",
        "cite", "search_library", "search_papers", "related_papers", "fetch_paper", "save_paper",
        "rename_page", "move_page", "list_deleted", "restore_page", "edit_block", "create_block", "move_block"]
    # Paper chats never list, rename, move or restore pages; the note-block
    # tools and save_paper exist there.
    assert [t["name"] for t in agent_tools("page")] == [
        "read_page", "read_block", "read_chats", "view_pdf_page", "view_ink", "cite", "search_library",
        "search_papers", "related_papers", "fetch_paper", "save_paper", "edit_block", "create_block",
        "move_block"]
    assert agent_tools("") == []  # plain chat
    assert [t["name"] for t in agent_tools(
        "folder", {"rename": False, "move": False, "block_edit": False, "save": False, "restore": False})] == [
        "list_pages", "list_folders", "read_page", "read_block", "read_chats", "view_pdf_page", "view_ink",
        "cite", "search_library", "search_papers", "related_papers", "fetch_paper", "list_deleted"]
    names = [t["name"] for t in agent_tools("folder", {"search": False})]
    assert "search_library" not in names and "read_page" in names
    # "List pages" gates the folder tree and Recently deleted too; "Read
    # pages" the page chats and the citation records; the viewer handwriting.
    names = [t["name"] for t in agent_tools("folder", {"list": False, "read": False, "view": False})]
    assert not {"list_pages", "list_folders", "list_deleted", "read_page", "read_chats", "cite",
                "view_pdf_page", "view_ink"} & set(names)
    # Restoring is a change of its own; listing what is deleted is reading.
    names = [t["name"] for t in agent_tools("folder", {"restore": "off"})]
    assert "restore_page" not in names and {"list_deleted", "save_paper"} <= set(names)
    # One permission gates all three note-editing tools.
    names = [t["name"] for t in agent_tools("page", {"block_edit": False})]
    assert names == ["read_page", "read_block", "read_chats", "view_pdf_page", "view_ink", "cite",
                     "search_library", "search_papers", "related_papers", "fetch_paper", "save_paper"]
    # The two web tools have their own permissions.
    names = [t["name"] for t in agent_tools("page", {"web_search": False, "web_read": False})]
    assert "search_papers" not in names and "fetch_paper" not in names and "read_page" in names
    assert agent_tools("folder", {k: False for k in ALL_PERMS}) == []
    assert agent_tools("folder", None) == ALL_TOOLS  # missing map = everything on


def test_agent_system_mentions_scope_and_armed_tools():
    # A folder chat's folder is named by its path, as the builder put it in the scope, and its id.
    text = agent_system({**folder("f1"), "folder_path": "readout / fast"})
    assert 'the folder "readout / fast" (id f1)' in text and "rename_page" in text
    assert "the root of their library" in agent_system(folder(""))
    page_text = agent_system({"type": "page", "page_id": "p1"},
                             {"search": False, "block_edit": False, "save": False})
    assert 'page_id "p1"' in page_text
    assert "read_page" in page_text and "search_library" not in page_text
    assert "suggest them" in page_text  # no write tools armed
    # With note editing armed, the editing guidance replaces the read-only line.
    edit_text = agent_system({"type": "page", "page_id": "p1"})
    assert "Note editing" in edit_text and "suggest them" not in edit_text
    # The base role prompt is user-replaceable; the mechanical lines stay.
    custom = agent_system(folder(""), None, "Be terse.")
    assert custom.startswith("Be terse.") and "Available tools" in custom


def test_block_tools_gated_by_permissions():
    armed = {t["name"] for t in agent_tools("page", {"block_read": False, "block_edit": False, "view": False,
                                                        "web_search": False, "web_read": False, "save": False})}
    assert armed == {"read_page", "read_chats", "cite", "search_library"}


def test_agent_system_explains_the_new_tools_only_when_armed():
    text = agent_system(folder(""))
    # Handwriting: look first, then write the transcription into the caption.
    assert "view_ink" in text and "[illegible]" in text and "caption" in text
    assert "cite returns the citation records" in text
    assert "save_paper adds a paper" in text and "never as a side effect" in text
    assert "restore_page brings one back" in text
    # Without note editing the transcription is not written anywhere.
    read_only = agent_system({"type": "page", "page_id": "p1"}, {"block_edit": False})
    assert "view_ink" in read_only and "[illegible]" not in read_only
    assert "restore_page" not in read_only  # folder chats only
    off = agent_system(folder(""), {"view": False, "read": False, "save": False, "restore": False})
    for phrase in ("Handwriting:", "cite returns", "save_paper adds", "Recently deleted for"):
        assert phrase not in off


def test_agent_system_tells_the_model_how_to_link_pages():
    # The chat renders /?page=<id> links as open-in-place, so the prompt
    # asks for that form wherever a reading tool hands the model page ids.
    text = agent_system(folder(""))
    assert "/?page=<page_id>" in text
    assert "Use only page ids the tools returned" in text
