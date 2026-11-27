"""show_page (the app shows the user a page), create_page, export (a page
as Markdown, a PDF, BibTeX or another library's format) and backlinks (the
notes that link to a page or a block)."""

import re

from ..ai_context import is_text_document
from ..blocks_store import FOLDERS, page_ancestors, page_root_id, write_lock
from ..documents.model import is_pdf, page_document
from .base import (_NOTE_SNIPPET, _TITLE_MAX, _drawing_hand, _int_arg, _load_scoped_block, _load_scoped_page, _made,
                   _one_line, _resolve_folder, _save_folder, _scope_pages, _where)


# --- showing a page in the app ------------------------------------------------------

def _run_show_page(conn, ws: str, scope: dict, args: dict):
    """Ask the app to show the user a page (``needs: "app"``: only a live
    chat in the app has one): at a PDF page and passage, a note, or a text
    file's line. The app jumps at once when the page is open in the chat's
    view, and opens it when the reply ends otherwise (App.jsx
    ``handleAgentEvent``); nothing is stored."""
    loaded, error = _load_scoped_page(conn, scope, args)
    if error:
        return error, None
    page_id, title, props, _ = loaded
    view = page_document(props)
    show, where = {"page_id": page_id}, ""
    block_id = str(args.get("block_id") or "").strip()
    page_no = _int_arg(args, "pdf_page", 0, 0, 100_000)
    line = _int_arg(args, "line", 0, 0, 10_000_000)
    quote = " ".join(str(args.get("quote") or "").split())[:500]
    if block_id and block_id != page_id:
        if page_root_id(conn, block_id) != page_id:
            return "error: that block is not a note of this page: read_block lists its notes", None
        show["block_id"], where = block_id, f" at note [{block_id}]"
    elif line:
        if not is_text_document(view):
            return f'error: "{title}" is no text file: line names a text file\'s line', None
        show["line"], where = line, f" at line {line}"
    elif page_no or quote:
        if not is_pdf(view):
            return f'error: "{title}" has no PDF: pdf_page and quote name a place in one', None
        if not page_no:
            return "error: a passage needs its pdf_page (1-based)", None
        show.update(pdf_page=page_no, **({"quote": quote} if quote else {}))
        where = f" at PDF page {page_no}" + (f", “{quote[:60]}”" if quote else "")
    return (f"ok — the app shows the user “{title}”{where}: at once when the chat is beside that page, else "
            "when your reply ends. Don't describe the navigation; say what to look at there.",
            {"kind": "show", "page_id": page_id, "title": title, "show": show,
             "summary": f"Showed “{title[:60]}”{where[:60]}"})


# --- making a page ------------------------------------------------------------------

def _plan_create_page(conn, scope: dict, args: dict):
    """``(plan, None)``: the new page's ``title`` and where it goes — inside
    the page ``parent_id`` names (filed where that page is), else in a
    folder (``_save_folder``: the chat's folder by default, a folder below
    it, made when missing); ``(None, answer)``."""
    title = re.sub(r"\s+", " ", str(args.get("title") or "")).strip()[:_TITLE_MAX]
    if not title:
        return None, "error: title is the new page's title"
    parent_id = str(args.get("parent_id") or "").strip()
    if parent_id:
        if str(args.get("folder") or "").strip():
            return None, "error: a page inside a page is filed where that page is: give parent_id or folder"
        loaded, error = _load_scoped_page(conn, scope, {"page_id": parent_id})
        if error:
            return None, error
        return {"title": title, "parent": loaded[0], "target": None, "where": f"inside “{loaded[1]}”",
                "inside": loaded[1]}, None
    target, error = _save_folder(conn, scope, args.get("folder"))
    if error:
        return None, error
    return {"title": title, "parent": "", "target": target,
            "where": f'in "{target["path"]}"' if target["path"] else "at the library root"}, None


def _preview_create_page(conn, scope: dict, args: dict):
    plan, answer = _plan_create_page(conn, scope, args)
    if not plan:
        return None, answer
    return {"title": plan["title"], "diff": [["ins", plan["title"]]],
            **({"to": plan["target"]["path"]} if plan["target"] else {}),
            **({"inside": plan["inside"]} if plan["parent"] else {}),
            **({"new_folder": True} if plan["target"] and plan["target"].get("names") else {})}, None


def _run_create_page(conn, ws: str, scope: dict, args: dict):
    """A new page of notes, as POST /api/pages makes one: the parent page
    checked live and not too deep, the share host's page cap, under the
    write lock it is made in (``blocks_store.create_page``, held to the
    work-folder rule as a person's). Its revert moves it to Recently
    deleted while it is empty (ai_revert ``_plan_made``)."""
    from .. import publish
    from ..blocks_store import MAX_PAGE_DEPTH, PARENT_PAGE, create_page
    from ..ops import OpError

    writer, answer = _drawing_hand(scope, ws)
    if writer is None:
        return answer, None
    plan, answer = _plan_create_page(conn, scope, args)
    if not plan:
        return answer, None
    folder = _made(ws, conn, scope, plan["target"]) if plan["target"] else ""
    props = ({PARENT_PAGE: plan["parent"]} if plan["parent"] else {FOLDERS: [folder]} if folder else {})
    write_lock(conn)
    if plan["parent"]:
        if page_root_id(conn, plan["parent"]) != plan["parent"]:
            conn.rollback()
            return "error: the page it was to go inside was deleted", None
        if len(page_ancestors(conn, plan["parent"])) + 1 >= MAX_PAGE_DEPTH:  # ops._parent_patch's rule
            conn.rollback()
            return f"error: pages nest at most {MAX_PAGE_DEPTH} deep: that page holds no more inside it", None
    refusal = publish.cap_refusal(ws)
    if refusal:
        conn.rollback()
        return f"error: {refusal['detail']}", None
    try:
        page = create_page(conn, plan["title"], {**props, "agent": writer.stamp}, actor=writer.user_id, client="ai")
    except OpError as e:
        conn.rollback()
        return f"error: {e.detail}", None
    page_id = page["id"]
    path = plan["target"]["path"] if plan["target"] else ""
    return (f"ok — made the page [{plan['title']}](/?page={page_id}) (page_id {page_id}) {plan['where']}. "
            f"Write its notes with create_block(parent_id=\"{page_id}\").",
            {"kind": "create", "page_id": page_id, "block_id": page_id, "title": plan["title"], "to": path,
             **({"inside": plan["inside"]} if plan["parent"] else {}),
             "summary": f"Made the page “{plan['title'][:60]}”", "revert": {"made": page_id}})


# --- exporting ----------------------------------------------------------------------

# The formats export offers, by the name the model uses: the export mode
# each is (routers/export.py _BUILDERS) and how a reply names it.
EXPORT_FORMATS = {
    "markdown": ("readable", "Markdown"), "pdf": ("annotated-pdf", "the annotated PDF"),
    "notes-pdf": ("notes-pdf", "a PDF of the notes"), "bibtex": ("bibtex", "BibTeX"),
    "biblatex": ("biblatex", "BibLaTeX"), "ris": ("ris", "RIS"), "csljson": ("csljson", "CSL JSON"),
    "obsidian": ("obsidian", "an Obsidian vault"), "zotero": ("zotero-rdf", "Zotero RDF"),
    "notion": ("notion", "a Notion import"), "logseq": ("logseq-graph", "a Logseq graph"),
}


def _run_export(conn, ws: str, scope: dict, args: dict):
    """Start the export job the Export dialog starts (routers/export.py
    ``start_export``, as the person) for a page or a folder in reach; the
    file is in Background tasks, which the live chat's app adopts and
    downloads (``needs: "app"``)."""
    from fastapi import HTTPException

    from ..routers.export import ExportJob, start_export

    fmt = str(args.get("format") or "markdown").strip().lower()
    if fmt not in EXPORT_FORMATS:
        return f"error: format is one of {', '.join(EXPORT_FORMATS)}", None
    if args.get("page_id") and str(args.get("folder") or "").strip():
        return "error: export a page (page_id) or a folder, not both", None
    if args.get("page_id") or scope.get("type") == "page":
        loaded, error = _load_scoped_page(conn, scope, {"page_id": args.get("page_id") or scope.get("page_id")})
        if error:
            return error, None
        page_id, title, folder = loaded[0], loaded[1], ""
    else:
        target, error = _resolve_folder(conn, scope, args.get("folder"))
        if error:
            return error, None
        if not target["id"]:
            return "error: name a page (page_id) or a folder to export: the library root is no folder", None
        page_id, title, folder = "", target["path"], target["id"]
    try:
        job = start_export(ws, owner=scope.get("actor", ""), user=(scope.get("agent") or {}).get("for", ""),
                           payload=ExportJob(page_id=page_id, folder=folder, mode=EXPORT_FORMATS[fmt][0],
                                             sub_pages=bool(args.get("sub_pages")) and not folder))
    except HTTPException as e:
        return f"error: {e.detail}", None
    words = EXPORT_FORMATS[fmt][1]
    return (f"ok — exporting “{title}” as {words}: the file downloads in the user's browser when it is ready, and "
            "stays in Background tasks. Say so; the file's text is not yours to read.",
            {"kind": "export", "page_id": page_id, "title": title, "format": fmt, "job": job,
             "summary": f"Exported “{title[:60]}” as {words}"})


# --- backlinks ----------------------------------------------------------------------

_BACKLINKS_MAX = 50


def _run_backlinks(conn, ws: str, scope: dict, args: dict):
    """The notes that link to a page or a block (``[[id]]``), among the
    pages the chat reaches (``blocks_store.backlinks``: filtered before the
    limit), newest first."""
    from ..blocks_store import backlinks

    loaded, error = _load_scoped_block(conn, scope, args.get("block_id"))
    if error:
        return error, None
    block, page_id, title = loaded
    pages = _scope_pages(conn, scope)
    rows = backlinks(conn, block["id"], pages=list(pages), limit=_BACKLINKS_MAX + 1)
    named = "page" if block["id"] == page_id else f"note [{block['id']}] of"
    if not rows:
        return f"No note this chat reaches links to the {named} “{title}”.", {
            "kind": "search", "page_id": page_id, "summary": f"No notes link to “{title[:60]}”"}
    lines = [f"- note [{bid}] in “{pages[pid]['title']}” ({_where(scope, pid, block_id=bid)}): "
             f"{_one_line(content)[:_NOTE_SNIPPET]}" for bid, content, pid in rows[:_BACKLINKS_MAX]]
    more = "\n[… more notes link to it than are shown — the newest are listed]" if len(rows) > _BACKLINKS_MAX else ""
    n = min(len(rows), _BACKLINKS_MAX)
    said = "1 note links" if n == 1 else f"{n} notes link"
    return (f"{said} to the {named} “{title}”, newest first:\n" + "\n".join(lines) + more,
            {"kind": "search", "page_id": page_id, "summary": f"Found {n} note{'s' if n != 1 else ''} linking to "
                                                              f"“{title[:60]}”"})


TOOLS = [
    {
        "perm": "show", "kind": "show", "scopes": ("folder", "page"), "mutating": False, "needs": "app",
        "run": _run_show_page,
        "spec": {
            "name": "show_page",
            "description": (
                "Show the user a page in the app: at a PDF page and passage (`pdf_page`, with a `quote` of "
                "it), at a note (`block_id`), or at a text file's `line`. When the chat is beside that page it "
                "jumps there at once; another page opens when your reply ends. Use it when the user asks you to "
                "show, open or take them to something; once a reply."),
            "parameters": {
                "type": "object",
                "properties": {
                    "page_id": {"type": "string"},
                    "pdf_page": {"type": "integer", "minimum": 1},
                    "quote": {"type": "string", "description": "the passage, verbatim, to light up"},
                    "block_id": {"type": "string", "description": "a note of the page"},
                    "line": {"type": "integer", "minimum": 1},
                },
                "required": ["page_id"],
            },
        },
    },
    {
        "perm": "create", "kind": "create", "scopes": ("folder",), "mutating": True,
        "run": _run_create_page, "preview": _preview_create_page,
        "spec": {
            "name": "create_page",
            "description": (
                "Make a new page of notes for the user: in the current folder, a folder below it (`folder`, "
                "read as move_page's; made when it does not exist), or inside a page (`parent_id`). Returns its "
                "page_id; write its notes with create_block(parent_id=<page_id>). Only when the user asks for a "
                "page; the user can revert it from the reply."),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "folder": {"type": "string"},
                    "parent_id": {"type": "string", "description": "a page to put it inside"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "perm": "read", "kind": "export", "scopes": ("folder", "page"), "mutating": False, "needs": "app",
        "run": _run_export,
        "spec": {
            "name": "export",
            "description": (
                "Export a page (`page_id`; `sub_pages` with the pages inside it) or a folder (`folder`) to a "
                "file the user downloads, as the Export dialog does: markdown (the default), pdf (the PDF with "
                "the highlights and notes), notes-pdf (the notes typeset), bibtex, biblatex, ris, csljson (CSL "
                "JSON, for Pandoc), obsidian, zotero, notion or logseq. The file goes to the user; only when they "
                "ask for one."),
            "parameters": {
                "type": "object",
                "properties": {
                    "page_id": {"type": "string"},
                    "folder": {"type": "string"},
                    "format": {"type": "string", "enum": list(EXPORT_FORMATS)},
                    "sub_pages": {"type": "boolean"},
                },
                "required": [],
            },
        },
    },
    {
        "perm": "read", "kind": "search", "scopes": ("folder", "page"), "mutating": False, "run": _run_backlinks,
        "spec": {
            "name": "backlinks",
            "description": (
                "The notes that link to a page or a note (`block_id`: a page id or a note's), among the pages "
                "you can read, newest first, each with its block id and page: what else the user wrote about "
                "it."),
            "parameters": {
                "type": "object",
                "properties": {"block_id": {"type": "string"}},
                "required": ["block_id"],
            },
        },
    },
]
