"""list_annotations and read_annotation, the chat registry's two
annotations tools (gamma/ai_tools.py, docs/dev/ai_tools.md
"list_annotations and read_annotation"): the highlights and comment threads
of a page, a folder or the library in their Markdown form, a batch at a
time; a work folder, a directory, a file, a work folder by name, a library
folder by id or path, a library page, the library; a page chat's page;
one item, a thread's or a bare highlight's,
with its JSON form and the pictures of its region; what lies outside the
chat refused. The MCP side is test_mcp.py's."""

import io
import json
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter

from conftest import (account_of, doc_props, login, make_folder, make_page, make_user, mark_work_folder, thread_on,
                      work_props, workspace_of)
from gamma.ai_tools import run_agent_tool
from gamma.blocks_store import create_page
from gamma.db import connect_pages_db

RECT = {"x1": 100, "y1": 72, "x2": 300, "y2": 92}


def upload_pdf(owner, name: str) -> str:
    """A two-page PDF of its own (``name`` its title) stored in the client's
    open workspace; its hash."""
    writer = PdfWriter()
    for _ in range(2):
        writer.add_blank_page(width=612, height=792)
    writer.add_metadata({"/Title": name})
    buf = io.BytesIO()
    writer.write(buf)
    r = owner.post("/api/uploads", files={"file": (name, buf.getvalue(), "application/pdf")})
    assert r.status_code == 200, r.text
    return r.json()["doc_id"]


def work_folder(owner, user: str, name: str, files=("main.pdf",), directory: str = ""):
    """A work folder ``name`` of the client ``owner``'s open workspace with
    ``files`` (each a PDF of its own, in the directory ``directory`` when
    given), made as its vault makes them: ``{ws, w, dir, files: {name: page
    id}}``."""
    from gamma.auth import DISK_CLIENT

    ws = owner.headers.get("X-Gamma-Workspace") or workspace_of(user)
    w = make_folder(owner, name)
    below = make_folder(owner, f"{name}/{directory}") if directory else ""  # before the mark: then the disk's
    mark_work_folder(owner, w)
    made = {}
    with connect_pages_db(ws) as conn:
        for file_name in files:
            doc = doc_props(upload_pdf(owner, f"{name}-{file_name}"))["doc"]
            made[file_name] = create_page(conn, file_name, work_props(w, file_name, doc=doc, directory=below),
                                          actor=account_of(user), client=DISK_CLIENT)["id"]
    return SimpleNamespace(ws=ws, w=w, dir=below, files=made)


def passage(owner, page_id: str, quote: str = "the loss is defined as", page: int = 1, rect=RECT,
            note: str = "") -> str:
    """A highlighted passage on PDF page ``page`` of the page, ``note``
    written on it; its block id."""
    r = owner.post("/api/blocks", json={"parent_id": page_id, "content": note, "properties": {
        "quote": quote, "pdf_position": {"pageNumber": page, "width": 612, "height": 792,
                                         "boundingRect": dict(rect), "rects": [dict(rect)]}}})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def commented(owner, page_id: str, body: str, **more) -> dict:
    """A thread on a new passage of the page; the thread."""
    return thread_on(owner, page_id, passage(owner, page_id, **more), body)


@pytest.fixture(scope="module")
def tim():
    make_user("fbt_tim", "fbtpw12345")
    c = login("fbt_tim", "fbtpw12345")
    yield c
    c.close()


def _folder_chat(folder=""):
    return {"type": "folder", "folder": folder, "can_write": False, "actor": account_of("fbt_tim")}


def _ws():
    return workspace_of("fbt_tim")


def test_a_work_folder_a_file_and_the_library(tim):
    paper = work_folder(tim, "fbt_tim", "Paper")
    thesis = work_folder(tim, "fbt_tim", "Thesis", files=("thesis.pdf",))
    first = commented(tim, paper.files["main.pdf"], "lambda is never introduced.")
    second = commented(tim, thesis.files["thesis.pdf"], "cite the survey here.", quote="as shown before")
    # the library chat reads the library
    text, action = run_agent_tool(_ws(), _folder_chat(), "list_annotations", {})
    assert not action.get("error"), text
    assert "# Annotations on the library" in text and "## main.pdf" in text and "## thesis.pdf" in text
    assert f"id: `{first['id']}`" in text and f"id: `{second['id']}`" in text
    assert "owner fbt_tim" in text and "> lambda is never introduced." in text
    # pictures are named by the tool that reads them; the chat's links are queries alone
    assert f'Picture: read_annotation(id="{first["id"]}")' in text
    assert action["summary"] == "Listed the annotations of the library" and "page_id" not in action
    assert run_agent_tool(_ws(), _folder_chat(), "list_annotations", {"scope": "home"})[0] == text
    # a work folder by name (any case) or id, a file, alone
    text, action = run_agent_tool(_ws(), _folder_chat(), "list_annotations", {"scope": "thesis"})
    assert second["id"] in text and first["id"] not in text and "# Annotations on Thesis" in text
    text, action = run_agent_tool(_ws(), _folder_chat(), "list_annotations", {"scope": paper.files["main.pdf"]})
    assert first["id"] in text and second["id"] not in text and action["page_id"] == paper.files["main.pdf"]
    # the system prompt names them
    from gamma.ai_tools import agent_system
    assert "list_annotations and read_annotation read the highlights and comment threads" in agent_system(
        _folder_chat())


def test_a_library_folder_and_page_with_a_bare_highlight(tim):
    shelf, deeper = make_folder(tim, "Shelf"), make_folder(tim, "Shelf/Deeper")
    with connect_pages_db(_ws()) as conn:
        book = create_page(conn, "A library book", {"folders": [deeper], **doc_props(upload_pdf(tim, "book"))},
                           actor=account_of("fbt_tim"), client="t")["id"]
    bare = passage(tim, book, quote="a passage marked alone", page=2, note="read this twice")
    on_book = commented(tim, book, "what does this mean?", quote="a commented passage")
    elsewhere = make_page(tim, "Another book")["id"]
    other = passage(tim, elsewhere, quote="not on the shelf")
    for scope in (shelf, "Shelf", "Shelf / Deeper", deeper):
        text, action = run_agent_tool(_ws(), _folder_chat(), "list_annotations", {"scope": scope})
        assert not action.get("error"), (scope, text)
        assert bare in text and on_book["id"] in text and other not in text and "## A library book" in text, scope
    assert "# Annotations on Shelf\n" in run_agent_tool(_ws(), _folder_chat(), "list_annotations", {"scope": shelf})[0]
    text, _ = run_agent_tool(_ws(), _folder_chat(), "list_annotations", {"scope": shelf, "state": "resolved"})
    assert bare in text and on_book["id"] not in text and "0 resolved comments" not in text
    assert "1 highlight" in text
    assert "- id: `" + bare + "` · highlight · passage · placed exactly\n- note: “read this twice”" in text
    # the chat of a folder reads its folder, and nothing above or beside it
    text, action = run_agent_tool(_ws(), _folder_chat(shelf), "list_annotations", {})
    assert bare in text and other not in text and action["summary"] == "Listed the annotations on “Shelf”"
    for scope in ("home", elsewhere):
        text, action = run_agent_tool(_ws(), _folder_chat(deeper), "list_annotations", {"scope": scope})
        assert action["error"] and "outside this chat's scope" in text, scope
    # a page chat: its page
    chat = {"type": "page", "page_id": book, "actor": account_of("fbt_tim")}
    text, action = run_agent_tool(_ws(), chat, "list_annotations", {})
    assert bare in text and on_book["id"] in text and action["page_id"] == book
    # the bare highlight, read alone
    text, chip = run_agent_tool(_ws(), chat, "read_annotation", {"id": bare})
    assert not chip.get("error"), text
    entry = json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert (entry["type"], entry["file"], entry["state"], entry["comments"]) == ("highlight", "A library book", None, [])
    assert entry["where"]["note"] == "read this twice" and chip["pdf_page"] == 2
    assert entry["picture_url"].startswith(f"/api/annotations/{bare}/picture?at=now&v=")


def test_scopes_states_and_refusals(tim):
    work = work_folder(tim, "fbt_tim", "Scoped", files=("top.pdf", "fig.pdf"), directory="figures")
    top, fig = work.files["top.pdf"], work.files["fig.pdf"]
    on_fig = commented(tim, fig, "use the viridis palette.")
    whole = thread_on(tim, fig, fig, "the whole figure is too dark")
    # a directory: its files only (fig.pdf is in it, top.pdf is not)
    text, _ = run_agent_tool(_ws(), _folder_chat(), "list_annotations", {"scope": work.dir})
    assert on_fig["id"] in text and whole["id"] in text and "## figures/fig.pdf" in text
    assert text.index("The whole file") < text.index("“the loss is defined as”")  # the file's threads first
    # resolved and all
    assert tim.post(f"/api/comments/{whole['id']}/resolve", json={"resolved": True}).status_code == 200
    text, _ = run_agent_tool(_ws(), _folder_chat(), "list_annotations", {"scope": fig})
    assert whole["id"] not in text and on_fig["id"] in text
    text, _ = run_agent_tool(_ws(), _folder_chat(), "list_annotations", {"scope": fig, "state": "resolved"})
    assert whole["id"] in text and on_fig["id"] not in text and "1 resolved comment" in text
    text, _ = run_agent_tool(_ws(), _folder_chat(), "list_annotations", {"scope": fig, "state": "all"})
    assert whole["id"] in text and on_fig["id"] in text
    text, action = run_agent_tool(_ws(), _folder_chat(), "list_annotations", {"scope": fig, "state": "later"})
    assert action["error"] and "open, resolved or all" in text
    # a file with none, and what names nothing
    text, _ = run_agent_tool(_ws(), _folder_chat(), "list_annotations", {"scope": top})
    assert "No open comments and no highlights." in text
    for scope in ("No such folder", "x" * 24):
        text, action = run_agent_tool(_ws(), _folder_chat(), "list_annotations", {"scope": scope})
        assert action["error"] and "no folder" in text.lower(), text
    # a chat in a library folder reaches no work folder beside it; one in a directory reaches that directory
    lonely = make_folder(tim, "Lonely shelf")
    text, action = run_agent_tool(_ws(), _folder_chat(lonely), "list_annotations", {})
    assert not action.get("error") and "No open comments and no highlights." in text
    text, action = run_agent_tool(_ws(), _folder_chat(lonely), "list_annotations", {"scope": work.w})
    assert action["error"] and "outside this chat's scope" in text
    text, _ = run_agent_tool(_ws(), _folder_chat(work.dir), "list_annotations", {})
    assert on_fig["id"] in text and "# Annotations on figures" in text


def test_a_page_chat_lists_its_page(tim):
    work = work_folder(tim, "fbt_tim", "Paged", files=("a.pdf", "b.pdf"))
    on_a = commented(tim, work.files["a.pdf"], "on a")
    on_b = commented(tim, work.files["b.pdf"], "on b")
    chat = {"type": "page", "page_id": work.files["a.pdf"], "actor": account_of("fbt_tim")}
    text, action = run_agent_tool(_ws(), chat, "list_annotations", {})
    assert on_a["id"] in text and on_b["id"] not in text and action["page_id"] == work.files["a.pdf"]
    for scope in (work.files["b.pdf"], work.w, "home"):
        text, action = run_agent_tool(_ws(), chat, "list_annotations", {"scope": scope})
        assert action["error"] and on_b["id"] not in text, scope
    library = make_page(tim, "A paper of the library")
    on_library = thread_on(tim, library["id"], library["id"], "a library comment")
    text, action = run_agent_tool(_ws(), {"type": "page", "page_id": library["id"]}, "list_annotations", {})
    assert on_library["id"] in text and "## A paper of the library" in text and action["page_id"] == library["id"]
    # read_annotation reaches the page chat's own annotations only
    assert not run_agent_tool(_ws(), chat, "read_annotation", {"id": on_a["id"]})[1].get("error")
    text, action = run_agent_tool(_ws(), chat, "read_annotation", {"id": on_b["id"]})
    assert action["error"] and "outside this chat's scope" in text


def test_list_annotations_goes_a_batch_at_a_time(tim):
    work = work_folder(tim, "fbt_tim", "Batched")
    file = work.files["main.pdf"]
    threads = [commented(tim, file, f"comment {n}", rect={"x1": 100, "y1": 100 + 40 * n, "x2": 300, "y2": 120 + 40 * n})
               for n in range(5)]
    ids = [t["id"] for t in threads]  # top to bottom: the document's order
    text, _ = run_agent_tool(_ws(), _folder_chat(), "list_annotations", {"scope": work.w, "limit": 2})
    assert all(i in text for i in ids[:2]) and not any(i in text for i in ids[2:])
    assert f'More: list_annotations(scope="{work.w}", limit=2, after="{ids[1]}")' in text and "(3 left)" in text
    text, _ = run_agent_tool(_ws(), _folder_chat(), "list_annotations", {"scope": work.w, "limit": 2, "after": ids[1]})
    assert all(i in text for i in ids[2:4]) and ids[1] not in text and f'after="{ids[3]}"' in text
    # the thread a batch ended at answered since: the listing goes on from where it stood
    assert tim.post(f"/api/comments/{ids[3]}/resolve", json={"resolved": True}).status_code == 200
    text, _ = run_agent_tool(_ws(), _folder_chat(), "list_annotations", {"scope": work.w, "limit": 2, "after": ids[3]})
    assert ids[4] in text and ids[3] not in text and "More:" not in text
    text, _ = run_agent_tool(_ws(), _folder_chat(), "list_annotations", {"scope": work.w, "after": ids[4]})
    assert "Nothing more after that one." in text
    text, action = run_agent_tool(_ws(), _folder_chat(), "list_annotations", {"scope": work.w, "after": "nosuchthread"})
    assert action["error"] and "list again without after" in text


def test_read_annotation_gives_one_item_with_its_pictures(tim):
    work = work_folder(tim, "fbt_tim", "Pictured")
    file = work.files["main.pdf"]
    thread = commented(tim, file, "is this the right loss?")
    reply = tim.post(f"/api/pages/{file}/comments", json={"block_id": thread["block_id"], "thread_id": thread["id"],
                                                         "body": "yes"}).json()["comment"]
    text, chip = run_agent_tool(_ws(), _folder_chat(), "read_annotation", {"id": thread["id"]})
    assert not chip.get("error"), text
    assert "## main.pdf" in text and "### 1. Page 1 · “the loss is defined as”" in text
    assert "> is this the right loss?" in text and "> yes" in text
    entry = json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert (entry["id"], entry["type"], entry["file"], entry["kind"], entry["state"]) == (
        thread["id"], "thread", "main.pdf", "passage", "open")
    assert entry["where"]["page"] == 1 and entry["where"]["placed"] == "exact"
    assert entry["link"] == f"?ws={_ws()}&page={file}&block={thread['block_id']}&thread={thread['id']}"
    assert entry["picture_url"].startswith(f"/api/annotations/{thread['id']}/picture?at=now&v=")
    assert entry["before_url"] is None
    assert (chip["page_id"], chip["block_id"], chip["pdf_page"]) == (file, thread["block_id"], 1)
    (image,) = chip["images"]
    assert image[0] == "image/jpeg" and len(image[1]) > 100
    # a model that reads text only gets the item without its picture
    _, chip = run_agent_tool(_ws(), {**_folder_chat(), "pictures": False}, "read_annotation", {"id": thread["id"]})
    assert not chip.get("error") and "images" not in chip
    # a resolved one is read too, and by read_feedback's name and argument, which saved chats replay
    assert tim.post(f"/api/comments/{thread['id']}/resolve", json={"resolved": True}).status_code == 200
    text, _ = run_agent_tool(_ws(), _folder_chat(), "read_annotation", {"id": thread["id"]})
    assert '"state": "resolved"' in text
    assert run_agent_tool(_ws(), _folder_chat(), "read_feedback", {"thread_id": thread["id"]})[0] == text
    # a reply's id, a highlight that has a thread and nothing are no items
    for entry_id in (reply["id"], thread["block_id"], "", "nosuchthread"):
        text, chip = run_agent_tool(_ws(), _folder_chat(), "read_annotation", {"id": entry_id})
        assert chip["error"] and "no annotation has that id" in text
