"""The agent's tool registry: which tools each scope offers, how the
permission map prunes them, and what the system prompt tells the model."""

import re
from pathlib import Path

import pytest

from gamma import ai_permissions
from gamma.ai_tools import AGENT_PROMPT, TOOLS, agent_system, agent_tools

from ai_fixtures import ALL_PERMS, ALL_TOOLS, folder


def test_registry_scopes_and_permissions():
    assert [t["name"] for t in agent_tools("folder")] == [
        "list_pages", "list_folders", "read_page", "read_block", "read_chats", "list_feedback", "read_feedback",
        "view_pdf_page", "view_ink", "view_image", "cite", "search_library", "search_papers", "related_papers",
        "fetch_paper", "save_paper", "rename_page", "move_page", "list_deleted", "restore_page", "delete_page",
        "rename_folder", "create_label", "rename_label", "label_page", "delete_folder", "delete_label", "edit_block",
        "create_block", "move_block", "delete_block", "clip_region"]
    # Paper chats never list, rename, move or restore pages; the note-block
    # tools and save_paper exist there.
    assert [t["name"] for t in agent_tools("page")] == [
        "read_page", "read_block", "read_chats", "list_feedback", "read_feedback", "view_pdf_page", "view_ink",
        "view_image", "cite", "search_library", "search_papers", "related_papers", "fetch_paper", "save_paper",
        "edit_block", "create_block", "move_block", "delete_block", "clip_region"]
    assert agent_tools("") == []  # plain chat
    assert [t["name"] for t in agent_tools(
        "folder", {"rename": "off", "move": "off", "block_edit": "off", "save": "off", "restore": "off",
                   "delete": "off", "organize": "off", "organize_delete": "off"})] == [
        "list_pages", "list_folders", "read_page", "read_block", "read_chats", "list_feedback", "read_feedback",
        "view_pdf_page", "view_ink", "view_image", "cite", "search_library", "search_papers", "related_papers",
        "fetch_paper", "list_deleted"]
    names = [t["name"] for t in agent_tools("folder", {"search": "off"})]
    assert "search_library" not in names and "read_page" in names
    # "List pages" gates the folder tree and Recently deleted too; "Read
    # pages" the page chats, the feedback and the citation records; the
    # viewer handwriting.
    names = [t["name"] for t in agent_tools("folder", {"list": "off", "read": "off", "view": "off"})]
    assert not {"list_pages", "list_folders", "list_deleted", "read_page", "read_chats", "list_feedback",
                "read_feedback", "cite", "view_pdf_page", "view_ink", "view_image"} & set(names)
    # Restoring and deleting pages are changes of their own; listing what is deleted is reading.
    names = [t["name"] for t in agent_tools("folder", {"restore": "off"})]
    assert "restore_page" not in names and {"list_deleted", "save_paper", "delete_page"} <= set(names)
    names = [t["name"] for t in agent_tools("folder", {"delete": "off"})]
    assert "delete_page" not in names and {"restore_page", "delete_folder", "delete_block"} <= set(names)
    # One permission gates all three note-editing tools.
    names = [t["name"] for t in agent_tools("page", {"block_edit": "off"})]
    assert names == ["read_page", "read_block", "read_chats", "list_feedback", "read_feedback", "view_pdf_page",
                     "view_ink", "view_image", "cite", "search_library", "search_papers", "related_papers",
                     "fetch_paper", "save_paper"]
    # The two web tools have their own permissions.
    names = [t["name"] for t in agent_tools("page", {"web_search": "off", "web_read": "off"})]
    assert "search_papers" not in names and "fetch_paper" not in names and "read_page" in names
    assert agent_tools("folder", {k: "off" for k in ALL_PERMS}) == []
    assert agent_tools("folder", None) == ALL_TOOLS  # missing map = everything on


def test_organize_permissions_gate_their_tools_and_the_prompt():
    names = [t["name"] for t in agent_tools("folder", {"organize": "off"})]
    assert "rename_folder" not in names and "label_page" not in names and "delete_folder" in names
    names = [t["name"] for t in agent_tools("folder", {"organize_delete": "off"})]
    assert "delete_folder" not in names and "delete_label" not in names and "rename_folder" in names
    text = agent_system(folder(""))
    assert "Folders and labels: you can rename folders" in text and "their pages stay in the library" in text
    assert "delete_page: they go to Recently deleted" in text
    page_text = agent_system({"type": "page", "page_id": "p1"})
    assert "Folders and labels" not in page_text and "their pages stay" not in page_text
    assert "delete_page" not in page_text
    assert "edit labels" not in AGENT_PROMPT


def test_the_settings_catalog_names_every_permission():
    """The client's permission list (chat/chatSettings.js) has a row for
    every permission of the registry, and offers Allow for every one: the
    server holds none back (gamma/ai_permissions.py)."""
    source = Path(__file__).resolve().parents[2] / "frontend" / "src" / "chat" / "chatSettings.js"
    if not source.exists():
        pytest.skip("no frontend checkout beside the backend")
    text = source.read_text(encoding="utf-8")
    assert {key for key in re.findall(r'\["(\w+)", "(?:library|web|edit)"', text)} == (
        {t["perm"] for t in TOOLS} | {"publisher_cookies"})
    states_of = re.search(r"const STATES_OF = \{(.*?)\};", text, re.S)
    assert states_of, "STATES_OF is no longer in chatSettings.js"
    ask_only = {key for key, states in re.findall(r'(\w+): \[([^\]]*)\]', states_of.group(1))
                if '"allow"' not in states}
    assert not ask_only
    assert all(ai_permissions.permission_state({key: "allow"}, key, True) == "allow"
               for key in {t["perm"] for t in TOOLS})


def test_agent_system_mentions_scope_and_armed_tools():
    # A folder chat's folder is named by its path, as the builder put it in the scope, and its id.
    text = agent_system({**folder("f1"), "folder_path": "readout / fast"})
    assert 'the folder "readout / fast" (id f1)' in text and "rename_page" in text
    assert "the root of their library" in agent_system(folder(""))
    page_text = agent_system({"type": "page", "page_id": "p1"},
                             {"search": "off", "block_edit": "off", "save": "off"})
    assert 'page_id "p1"' in page_text
    assert "read_page" in page_text and "search_library" not in page_text
    assert "suggest them" in page_text  # no write tools armed
    # With note editing armed, the editing guidance replaces the read-only line.
    edit_text = agent_system({"type": "page", "page_id": "p1"})
    assert "Note editing" in edit_text and "suggest them" not in edit_text
    # It agrees with delete_block, armed under the same permission, and leaves
    # the modes to edit_block's own description.
    assert "selection edit" in edit_text and "delete_block removes a whole block" in edit_text
    assert "cannot delete blocks" not in edit_text and "read_block first" not in edit_text
    assert "append" not in edit_text.split("Note editing")[1]
    # The base role prompt is user-replaceable; the mechanical lines stay.
    custom = agent_system(folder(""), None, "Be terse.")
    assert custom.startswith("Be terse.") and "Available tools" in custom


def test_block_tools_gated_by_permissions():
    armed = {t["name"] for t in agent_tools("page", {"block_read": "off", "block_edit": "off", "view": "off",
                                                        "web_search": "off", "web_read": "off", "save": "off"})}
    assert armed == {"read_page", "read_chats", "list_feedback", "read_feedback", "cite", "search_library"}


def test_agent_system_explains_the_new_tools_only_when_armed():
    text = agent_system(folder(""))
    # Handwriting: look first, then write the transcription into the caption.
    assert "view_ink" in text and "[illegible]" in text and "caption" in text
    # a selection is read from its own picture, not from view_ink's whole saved block
    assert "attached as a picture of just those strokes, saved or not: read it there, not with view_ink" in text
    assert "(a selection: from its picture)" in text
    assert "cite returns the citation records" in text
    assert "list_feedback and read_feedback read the comment threads on a work folder's files" in text
    assert "save_paper adds a paper" in text and "never as a side effect" in text
    assert "restore_page brings them back" in text
    # Without note editing the transcription is not written anywhere.
    read_only = agent_system({"type": "page", "page_id": "p1"}, {"block_edit": "off"})
    assert "view_ink" in read_only and "[illegible]" not in read_only
    assert "restore_page" not in read_only  # folder chats only
    off = agent_system(folder(""), {"view": "off", "read": "off", "save": "off", "restore": "off"})
    for phrase in ("Handwriting:", "cite returns", "save_paper adds", "Recently deleted for", "list_feedback"):
        assert phrase not in off


def test_the_prompt_takes_many_pages_a_batch_at_a_time():
    # Only where the chat can both list pages and change them.
    assert "list_pages(limit=100)" in agent_system(folder(""))
    assert "list_pages(limit=100)" not in agent_system(folder(""), {"rename": "off", "move": "off",
                                                                    "organize": "off"})
    assert "list_pages(limit=100)" not in agent_system({"type": "page", "page_id": "p1"})


def test_the_page_tools_take_lists_and_the_prompt_says_so():
    specs = {t["spec"]["name"]: t["spec"]["parameters"] for t in TOOLS}
    assert specs["rename_page"]["required"] == ["renames"]
    assert specs["rename_page"]["properties"]["renames"]["items"]["required"] == ["page_id", "title"]
    assert specs["move_page"]["required"] == ["page_ids", "folder"]
    assert specs["label_page"]["required"] == ["page_ids"] and specs["restore_page"]["required"] == ["page_ids"]
    assert specs["delete_page"]["required"] == ["page_ids"]
    text = agent_system(folder(""))
    assert ("Several pages at once: rename_page, move_page, label_page, restore_page and delete_page each take"
            in text)
    text = agent_system(folder(""), {"rename": "off"})
    assert "Several pages at once: move_page, label_page, restore_page and delete_page each take" in text
    assert "rename_page" not in text.split("Several pages at once")[1].split("\n")[0]
    text = agent_system(folder(""), {"rename": "off", "move": "off", "organize": "off", "delete": "off"})
    assert "Several pages at once: restore_page takes a list" in text
    off = agent_system(folder(""), {"rename": "off", "move": "off", "organize": "off", "restore": "off",
                                    "delete": "off"})
    assert "Several pages at once" not in off
    assert "Several pages at once" not in agent_system({"type": "page", "page_id": "p1"})


def test_agent_system_tells_the_model_how_to_link_pages():
    # The chat renders /?page=<id> links as open-in-place, so the prompt
    # asks for that form wherever a reading tool hands the model page ids.
    text = agent_system(folder(""))
    assert "/?page=<page_id>" in text
    assert "Use only page ids the tools returned" in text


def test_the_prompts_name_text_files_beside_pdfs():
    """The prompts call a page's document a PDF or a text file: the agent
    follows a file hit with read_page(from_line=N), reports a file's line,
    and cites one with a line link whose quote is the line's own text; the
    read_page spec takes a path and lines, and needs no page_id."""
    from gamma.ai_tools import CITATION_PROMPT

    text = agent_system(folder(""))
    assert "what a page, its PDF or its file says" in text
    assert "Follow a PDF hit with read_page(pdf_page=N), a file hit with read_page(from_line=N)" in text
    assert "from a file (with its line)" in text
    assert "a PDF or a text file" in AGENT_PROMPT and "a text file by its line" in AGENT_PROMPT
    assert "[p. N](/?page=PAGE_ID&pdf_page=N&quote=URL_ENCODED_QUOTE)" in CITATION_PROMPT
    assert "[<file name>:N](/?page=PAGE_ID&line=N&quote=URL_ENCODED_LINE)" in CITATION_PROMPT
    assert "a range is [<file name>:N–M](…&line=N-M)" in CITATION_PROMPT
    assert "Always add &quote=<the percent-encoded verbatim text of line N" in CITATION_PROMPT
    read_page = next(t["spec"] for t in TOOLS if t["spec"]["name"] == "read_page")
    assert read_page["parameters"]["required"] == []
    assert {"path", "from_line", "to_line"} <= set(read_page["parameters"]["properties"])
    formatted = next(t for t in agent_tools("folder", read_chars=9000) if t["name"] == "read_page")
    assert "up to 9000" in formatted["description"] and "{" not in formatted["description"]
    search = next(t["spec"] for t in TOOLS if t["spec"]["name"] == "search_library")
    assert "read_page(page_id, from_line=N)" in search["description"]
    listing = next(t["spec"] for t in TOOLS if t["spec"]["name"] == "list_pages")
    assert "`path=`" in listing["description"] and "read_page(path=…)" in listing["description"]
