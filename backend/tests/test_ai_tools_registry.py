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
        "list_pages", "list_folders", "read_page", "read_block", "read_chats", "view_pdf_page", "view_ink",
        "view_image", "cite", "search_library", "search_papers", "related_papers", "fetch_paper", "save_paper",
        "rename_page", "move_page", "list_deleted", "restore_page", "rename_folder", "create_label", "rename_label",
        "label_page", "delete_folder", "delete_label", "edit_block", "create_block", "move_block",
        "delete_block", "clip_region"]
    # Paper chats never list, rename, move or restore pages; the note-block
    # tools and save_paper exist there.
    assert [t["name"] for t in agent_tools("page")] == [
        "read_page", "read_block", "read_chats", "view_pdf_page", "view_ink", "view_image", "cite", "search_library",
        "search_papers", "related_papers", "fetch_paper", "save_paper", "edit_block", "create_block",
        "move_block", "delete_block", "clip_region"]
    assert agent_tools("") == []  # plain chat
    assert [t["name"] for t in agent_tools(
        "folder", {"rename": False, "move": False, "block_edit": False, "save": False, "restore": False,
                   "organize": False, "organize_delete": False})] == [
        "list_pages", "list_folders", "read_page", "read_block", "read_chats", "view_pdf_page", "view_ink", "view_image",
        "cite", "search_library", "search_papers", "related_papers", "fetch_paper", "list_deleted"]
    names = [t["name"] for t in agent_tools("folder", {"search": False})]
    assert "search_library" not in names and "read_page" in names
    # "List pages" gates the folder tree and Recently deleted too; "Read
    # pages" the page chats and the citation records; the viewer handwriting.
    names = [t["name"] for t in agent_tools("folder", {"list": False, "read": False, "view": False})]
    assert not {"list_pages", "list_folders", "list_deleted", "read_page", "read_chats", "cite",
                "view_pdf_page", "view_ink", "view_image"} & set(names)
    # Restoring is a change of its own; listing what is deleted is reading.
    names = [t["name"] for t in agent_tools("folder", {"restore": "off"})]
    assert "restore_page" not in names and {"list_deleted", "save_paper"} <= set(names)
    # One permission gates all three note-editing tools.
    names = [t["name"] for t in agent_tools("page", {"block_edit": False})]
    assert names == ["read_page", "read_block", "read_chats", "view_pdf_page", "view_ink", "view_image", "cite",
                     "search_library", "search_papers", "related_papers", "fetch_paper", "save_paper"]
    # The two web tools have their own permissions.
    names = [t["name"] for t in agent_tools("page", {"web_search": False, "web_read": False})]
    assert "search_papers" not in names and "fetch_paper" not in names and "read_page" in names
    assert agent_tools("folder", {k: False for k in ALL_PERMS}) == []
    assert agent_tools("folder", None) == ALL_TOOLS  # missing map = everything on


def test_organize_permissions_gate_their_tools_and_the_prompt():
    names = [t["name"] for t in agent_tools("folder", {"organize": "off"})]
    assert "rename_folder" not in names and "label_page" not in names and "delete_folder" in names
    names = [t["name"] for t in agent_tools("folder", {"organize_delete": "off"})]
    assert "delete_folder" not in names and "delete_label" not in names and "rename_folder" in names
    text = agent_system(folder(""))
    assert "Folders and labels: you can rename folders" in text and "never a page" in text
    page_text = agent_system({"type": "page", "page_id": "p1"})
    assert "Folders and labels" not in page_text and "never a page" not in page_text
    assert "edit labels" not in AGENT_PROMPT


def test_the_settings_catalog_names_every_permission():
    """The client's permission list (chat/chatSettings.js) has a row for
    every permission of the registry, and offers Ask and Off only for the
    server's ask-only ones."""
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
    assert ask_only == ai_permissions.ASK_ONLY


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
    # It agrees with delete_block, armed under the same permission, and leaves
    # the modes to edit_block's own description.
    assert "selection edit" in edit_text and "delete_block removes a whole block" in edit_text
    assert "cannot delete blocks" not in edit_text and "read_block first" not in edit_text
    assert "append" not in edit_text.split("Note editing")[1]
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
    # a selection is read from its own picture, not from view_ink's whole saved block
    assert "attached as a picture of just those strokes, saved or not: read it there, not with view_ink" in text
    assert "(a selection: from its picture)" in text
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


def test_the_prompt_takes_many_pages_a_batch_at_a_time():
    # Only where the chat can both list pages and change them.
    assert "list_pages(limit=100)" in agent_system(folder(""))
    assert "list_pages(limit=100)" not in agent_system(folder(""), {"rename": "off", "move": "off",
                                                                    "organize": "off"})
    assert "list_pages(limit=100)" not in agent_system({"type": "page", "page_id": "p1"})


def test_agent_system_tells_the_model_how_to_link_pages():
    # The chat renders /?page=<id> links as open-in-place, so the prompt
    # asks for that form wherever a reading tool hands the model page ids.
    text = agent_system(folder(""))
    assert "/?page=<page_id>" in text
    assert "Use only page ids the tools returned" in text
