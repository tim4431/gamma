"""list_feedback and read_feedback, the chat registry's two feedback tools
(gamma/ai_tools.py, docs/dev/ai_tools.md "list_feedback and
read_feedback"): the threads on a work folder's files in their Markdown
form, a section per work folder, a batch at a time; a directory, a file, a
work folder by name, a Send request's threads; a page chat's page; one item
with its JSON form and the pictures of its region; what lies outside a
work folder or the chat refused. The MCP side is test_mcp.py's."""

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


def passage(owner, page_id: str, quote: str = "the loss is defined as", page: int = 1, rect=RECT) -> str:
    """A highlighted passage on PDF page ``page`` of the file; its block id."""
    r = owner.post("/api/blocks", json={"parent_id": page_id, "content": "", "properties": {
        "quote": quote, "pdf_position": {"pageNumber": page, "width": 612, "height": 792,
                                         "boundingRect": dict(rect), "rects": [dict(rect)]}}})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def commented(owner, page_id: str, body: str, **more) -> dict:
    """A thread on a new passage of the file; the thread."""
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


def test_a_folder_chat_lists_every_work_folder_a_section_each(tim):
    paper = work_folder(tim, "fbt_tim", "Paper")
    thesis = work_folder(tim, "fbt_tim", "Thesis", files=("thesis.pdf",))
    first = commented(tim, paper.files["main.pdf"], "lambda is never introduced.")
    second = commented(tim, thesis.files["thesis.pdf"], "cite the survey here.", quote="as shown before")
    text, action = run_agent_tool(_ws(), _folder_chat(), "list_feedback", {})
    assert not action.get("error"), text
    assert "# Feedback on Paper" in text and "# Feedback on Thesis" in text
    assert "## main.pdf" in text and "## thesis.pdf" in text
    assert f"id: `{first['id']}`" in text and f"id: `{second['id']}`" in text
    assert "owner fbt_tim" in text and "> lambda is never introduced." in text
    # pictures are named by the tool that reads them; the chat's links are queries alone
    assert f'Picture: read_feedback(thread_id="{first["id"]}")' in text
    assert action["kind"] == "list" and "page_id" not in action
    # a work folder by name (any case) or id, a file, alone
    text, action = run_agent_tool(_ws(), _folder_chat(), "list_feedback", {"scope": "thesis"})
    assert second["id"] in text and first["id"] not in text and "# Feedback on Thesis" in text
    text, action = run_agent_tool(_ws(), _folder_chat(), "list_feedback", {"scope": paper.files["main.pdf"]})
    assert first["id"] in text and second["id"] not in text and action["page_id"] == paper.files["main.pdf"]
    # the system prompt names them
    from gamma.ai_tools import agent_system
    assert "list_feedback and read_feedback read the comment threads" in agent_system(_folder_chat())


def test_scopes_states_and_what_is_no_feedback(tim):
    work = work_folder(tim, "fbt_tim", "Scoped", files=("top.pdf", "fig.pdf"), directory="figures")
    top, fig = work.files["top.pdf"], work.files["fig.pdf"]
    on_fig = commented(tim, fig, "use the viridis palette.")
    whole = thread_on(tim, fig, fig, "the whole figure is too dark")
    library = make_page(tim, "A library paper")
    on_library = thread_on(tim, library["id"], library["id"], "not feedback")
    # a directory: its files only (fig.pdf is in it, top.pdf is not)
    text, _ = run_agent_tool(_ws(), _folder_chat(), "list_feedback", {"scope": work.dir})
    assert on_fig["id"] in text and whole["id"] in text and "## figures/fig.pdf" in text
    assert text.index("The whole file") < text.index("“the loss is defined as”")  # the file's threads first
    # resolved and all
    assert tim.post(f"/api/comments/{whole['id']}/resolve", json={"resolved": True}).status_code == 200
    text, _ = run_agent_tool(_ws(), _folder_chat(), "list_feedback", {"scope": fig})
    assert whole["id"] not in text and on_fig["id"] in text
    text, _ = run_agent_tool(_ws(), _folder_chat(), "list_feedback", {"scope": fig, "state": "resolved"})
    assert whole["id"] in text and on_fig["id"] not in text and "1 resolved comment" in text
    text, _ = run_agent_tool(_ws(), _folder_chat(), "list_feedback", {"scope": fig, "state": "all"})
    assert whole["id"] in text and on_fig["id"] in text
    text, action = run_agent_tool(_ws(), _folder_chat(), "list_feedback", {"scope": fig, "state": "later"})
    assert action["error"] and "open, resolved or all" in text
    # a file with none, and what is no work folder's
    text, _ = run_agent_tool(_ws(), _folder_chat(), "list_feedback", {"scope": top})
    assert "No open comments." in text
    for scope, says in ((library["id"], "Feedback is for a work folder's files."),
                        ("No such work folder", "No work folder is named"), ("x" * 24, "No work folder is named")):
        text, action = run_agent_tool(_ws(), _folder_chat(), "list_feedback", {"scope": scope})
        assert action["error"] and says in text, text
    assert on_library["id"] not in run_agent_tool(_ws(), _folder_chat(), "list_feedback", {})[0]
    # a chat in a library folder reaches no work folder; one in a directory reaches that directory
    lonely = make_folder(tim, "Lonely shelf")
    text, action = run_agent_tool(_ws(), _folder_chat(lonely), "list_feedback", {})
    assert not action.get("error") and "No work folder is in this chat's folder" in text
    text, action = run_agent_tool(_ws(), _folder_chat(lonely), "list_feedback", {"scope": work.w})
    assert action["error"] and "outside this chat's scope" in text
    text, _ = run_agent_tool(_ws(), _folder_chat(work.dir), "list_feedback", {})
    assert on_fig["id"] in text and "# Feedback on figures" in text


def test_a_page_chat_lists_its_page_s_threads(tim):
    work = work_folder(tim, "fbt_tim", "Paged", files=("a.pdf", "b.pdf"))
    on_a = commented(tim, work.files["a.pdf"], "on a")
    on_b = commented(tim, work.files["b.pdf"], "on b")
    chat = {"type": "page", "page_id": work.files["a.pdf"], "actor": account_of("fbt_tim")}
    text, action = run_agent_tool(_ws(), chat, "list_feedback", {})
    assert on_a["id"] in text and on_b["id"] not in text and action["page_id"] == work.files["a.pdf"]
    text, action = run_agent_tool(_ws(), chat, "list_feedback", {"scope": work.files["b.pdf"]})
    assert action["error"] and on_b["id"] not in text
    text, action = run_agent_tool(_ws(), chat, "list_feedback", {"scope": work.w})
    assert action["error"]
    library = make_page(tim, "A paper of the library")
    thread_on(tim, library["id"], library["id"], "a library comment")
    text, action = run_agent_tool(_ws(), {"type": "page", "page_id": library["id"]}, "list_feedback", {})
    assert text == "This page is no work folder's file: its comments are not feedback." and not action.get("error")
    # read_feedback reaches the page chat's own threads only
    assert not run_agent_tool(_ws(), chat, "read_feedback", {"thread_id": on_a["id"]})[1].get("error")
    text, action = run_agent_tool(_ws(), chat, "read_feedback", {"thread_id": on_b["id"]})
    assert action["error"] and "outside this chat's scope" in text


def test_list_feedback_goes_a_batch_at_a_time(tim):
    work = work_folder(tim, "fbt_tim", "Batched")
    file = work.files["main.pdf"]
    threads = [commented(tim, file, f"comment {n}", rect={"x1": 100, "y1": 100 + 40 * n, "x2": 300, "y2": 120 + 40 * n})
               for n in range(5)]
    ids = [t["id"] for t in threads]  # top to bottom: the document's order
    text, _ = run_agent_tool(_ws(), _folder_chat(), "list_feedback", {"scope": work.w, "limit": 2})
    assert all(i in text for i in ids[:2]) and not any(i in text for i in ids[2:])
    assert f'More: list_feedback(scope="{work.w}", limit=2, after="{ids[1]}")' in text and "(3 left)" in text
    text, _ = run_agent_tool(_ws(), _folder_chat(), "list_feedback", {"scope": work.w, "limit": 2, "after": ids[1]})
    assert all(i in text for i in ids[2:4]) and ids[1] not in text and f'after="{ids[3]}"' in text
    # the thread a batch ended at answered since: the listing goes on from where it stood
    assert tim.post(f"/api/comments/{ids[3]}/resolve", json={"resolved": True}).status_code == 200
    text, _ = run_agent_tool(_ws(), _folder_chat(), "list_feedback", {"scope": work.w, "limit": 2, "after": ids[3]})
    assert ids[4] in text and ids[3] not in text and "More:" not in text
    text, _ = run_agent_tool(_ws(), _folder_chat(), "list_feedback", {"scope": work.w, "after": ids[4]})
    assert "Nothing more after that thread." in text
    text, action = run_agent_tool(_ws(), _folder_chat(), "list_feedback", {"scope": work.w, "after": "nosuchthread"})
    assert action["error"] and "list again without after" in text


def test_list_feedback_reads_a_send_request(tim):
    from gamma.feedback import entries, requests

    work = work_folder(tim, "fbt_tim", "Requested", files=("main.pdf", "other.pdf"))
    sent = commented(tim, work.files["main.pdf"], "fix this one")
    left = commented(tim, work.files["other.pdf"], "not sent")
    with connect_pages_db(_ws()) as conn:
        record = requests.create(_ws(), conn, entries.resolve_scope(conn, work.files["main.pdf"]),
                                 by=account_of("fbt_tim"), by_name="fbt_tim", note="fix these")["request"]
    text, action = run_agent_tool(_ws(), _folder_chat(), "list_feedback", {"request": record["id"]})
    assert not action.get("error"), text
    assert text.startswith(f'Send request {record["id"]}: sent, sent by fbt_tim at ')
    assert 'Its instruction: "fix these"' in text
    assert sent["id"] in text and left["id"] not in text and "- sent: waiting since" in text
    assert action["summary"] == "Listed the threads of a Send request"
    text, action = run_agent_tool(_ws(), _folder_chat(), "list_feedback", {"request": "nosuchrequest"})
    assert action["error"] and "no Send request" in text


def test_read_feedback_gives_one_item_with_its_pictures(tim):
    work = work_folder(tim, "fbt_tim", "Pictured")
    file = work.files["main.pdf"]
    thread = commented(tim, file, "is this the right loss?")
    reply = tim.post(f"/api/pages/{file}/comments", json={"block_id": thread["block_id"], "thread_id": thread["id"],
                                                         "body": "yes"}).json()["comment"]
    text, chip = run_agent_tool(_ws(), _folder_chat(), "read_feedback", {"thread_id": thread["id"]})
    assert not chip.get("error"), text
    assert "## main.pdf" in text and "### 1. Page 1 · “the loss is defined as”" in text
    assert "> is this the right loss?" in text and "> yes" in text
    entry = json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert (entry["id"], entry["file"], entry["kind"], entry["state"]) == (thread["id"], "main.pdf", "passage", "open")
    assert entry["where"]["page"] == 1 and entry["where"]["placed"] == "exact"
    assert entry["link"] == f"?ws={_ws()}&page={file}&block={thread['block_id']}&thread={thread['id']}"
    assert entry["picture_url"].startswith(f"/api/feedback/threads/{thread['id']}/picture?at=now&v=")
    assert entry["before_url"] is None
    assert (chip["page_id"], chip["block_id"], chip["pdf_page"]) == (file, thread["block_id"], 1)
    (image,) = chip["images"]
    assert image[0] == "image/jpeg" and len(image[1]) > 100
    # a model that reads text only gets the item without its picture
    _, chip = run_agent_tool(_ws(), {**_folder_chat(), "pictures": False}, "read_feedback", {"thread_id": thread["id"]})
    assert not chip.get("error") and "images" not in chip
    # a resolved one is read too; a reply's id, a library page's thread and nothing are no items
    assert tim.post(f"/api/comments/{thread['id']}/resolve", json={"resolved": True}).status_code == 200
    text, _ = run_agent_tool(_ws(), _folder_chat(), "read_feedback", {"thread_id": thread["id"]})
    assert '"state": "resolved"' in text
    library = make_page(tim, "Library page with a comment")
    on_library = thread_on(tim, library["id"], library["id"], "no feedback")
    for thread_id in (reply["id"], on_library["id"], "", "nosuchthread"):
        text, chip = run_agent_tool(_ws(), _folder_chat(), "read_feedback", {"thread_id": thread_id})
        assert chip["error"] and "no feedback item has that id" in text
