"""A folder of the workspace as a folder on disk (docs/dev/folder_sync.md).

The server's half of the folder sync: the two reads a round of
``gamma_sync.Round`` needs, Gamma to disk. The gamma-sync client
(gamma/gamma_sync.py) and a server's links to another server ask for them
over HTTP with a read-scope token (``/api/sync/folders*``); a server's links
to its own workspaces call them in-process (gamma/folder_links.py
``LocalSource``).

- The folder's **manifest** (``manifest``): its subfolders as directory
  paths and, per page, the paths its files take — the page's PDF and its
  notes share one stem, ``<dir>/<Title>`` — each with its identity: the
  PDF's ``doc_id`` (its content hash) and the notes' ``version``. The
  client compares it with what it wrote last time and fetches what moved.
- The pages' **notes** (``notes``) rendered as members of the folder: the
  Obsidian vault dialect (``obsidian_export``), so the directory opens as a
  vault — links by title into the folder, the PDF as ``[[Title.pdf]]``
  beside the note, labels as tags — plus the page's id in the front matter
  (``gamma_id``), which is how a file keeps saying which page it is.

The layout is the vault export's with one difference: a paper's PDF sits
next to its note, not under ``attachments/``, so the folder reads as a
folder of papers. Images and other uploads go to ``attachments/`` at the
top, by their content-hash name, linked relative to the note.
"""

import hashlib
import json

from .blocks_store import fetch_subtree, folder_paths, load_json, newest_change_seq, pages_in_folder
from .db import safe_doc_id
from .markdown_export import block_ref_resolver, build_tree, collect_and_rewrite
from .obsidian_export import (Filing, VaultContext, page_dir, referenced_blocks, render_vault_page, unique_name,
                              vault_name)
from .storage import find_upload_file

ID_KEY = "gamma_id"   # the front-matter property naming the page
ROOT = "root"         # the folder id that means the whole library
MAX_NOTES = 200       # pages one notes request renders


def _props(text) -> dict:
    try:
        props = load_json(text or "{}")
    except (TypeError, ValueError):
        return {}
    return props if isinstance(props, dict) else {}


def _page_rows(conn, folder_id: str) -> list[tuple]:
    """``(id, content, properties, updated_at, seq)`` of the library pages the
    folder reaches — filed in it or below, ``pages_in_folder``'s rule; every
    page for ``ROOT`` — in library order, ``seq`` the page's latest op."""
    if folder_id == ROOT:
        ids = [r[0] for r in conn.execute("SELECT id FROM unified_blocks WHERE parent_id = 'root' ORDER BY position")]
    else:
        ids = pages_in_folder(conn, folder_id)
    return conn.execute(
        "SELECT p.id, p.content, p.properties, p.updated_at, "
        "(SELECT COALESCE(MAX(o.seq), 0) FROM page_ops o WHERE o.page_id = p.id) "
        "FROM unified_blocks p WHERE p.id IN (SELECT value FROM json_each(?)) ORDER BY p.position",
        (json.dumps(ids),)).fetchall()


def _stored_pdf(ws: str, doc_id: str):
    """The stored file of a page's ``doc_id``, or None (no PDF, a proxied
    one that was never stored, a bad id)."""
    if not doc_id:
        return None
    try:
        return find_upload_file(f"{safe_doc_id(doc_id)}.pdf", ws)
    except ValueError:
        return None


def layout(conn, ws: str, folder_id: str) -> dict | None:
    """Where the folder's files go: None when ``folder_id`` is no folder
    (``ROOT`` is the whole library), else ``{"path": the folder's names from
    the top, "dirs": [{id, path}], "pages": [{id, title, stem, doc_id, pdf,
    pdf_size, notes, version, labels}]}``.

    Every folder below becomes a directory (``dirs``, empty ones too), its
    path the sanitized names (``page_dir``). A page filed in the folder or
    below appears once, under its first folder below it — the export's
    rule (``Filing.folder``) — as ``<dir>/<Title>``: a stem unique in its
    directory ignoring case (``unique_name``), shared by the page's PDF
    (``pdf``, only when the file is stored here) and its notes (``notes``).
    ``version`` changes whenever the notes file would: the page's latest op
    and stamp, and the names of its labels."""
    top = [] if folder_id == ROOT else folder_paths(conn).get(folder_id)
    if top is None:
        return None
    fil = Filing(conn, None if folder_id == ROOT else folder_id)
    dirs = [{"id": f, "path": page_dir(names).rstrip("/")} for f, names in fil.paths.items() if names]

    used, pages = set(), []
    for pid, content, props_text, updated_at, seq in _page_rows(conn, folder_id):
        props = _props(props_text)
        names = fil.folder(props)
        stem = unique_name(used, page_dir(names), vault_name(content or ""), "")
        tags = fil.tags(props)
        doc_id = str(props.get("doc_id") or "")
        pdf = _stored_pdf(ws, doc_id)
        version = f"{seq}:{updated_at or ''}"
        if tags:
            version += ":" + hashlib.blake2b("\n".join(tags).encode("utf-8"), digest_size=4).hexdigest()
        pages.append({"id": pid, "title": (content or "").strip() or "Untitled", "stem": stem,
                      "doc_id": doc_id if pdf else "", "pdf": f"{stem}.pdf" if pdf else None,
                      "pdf_size": pdf.stat().st_size if pdf else 0,
                      "notes": f"{stem}.md", "version": version, "labels": tags})
    return {"path": top, "dirs": dirs, "pages": pages}


_PUBLIC = ("id", "title", "stem", "doc_id", "pdf", "pdf_size", "notes", "version")


def manifest(conn, ws: str, folder_id: str) -> dict | None:
    """What the client compares with its last round: the layout's dirs and
    pages, the folder's path, and ``cursor``, the change log's newest seq
    (``GET /sync/changes?since=<cursor>`` lists nothing while the workspace
    is quiet, so a watching client can skip the round)."""
    lay = layout(conn, ws, folder_id)
    if lay is None:
        return None
    return {"folder": {"id": folder_id, "path": lay["path"]}, "cursor": str(newest_change_seq(conn)), "dirs": lay["dirs"],
            "pages": [{k: p[k] for k in _PUBLIC} for p in lay["pages"]]}


def notes(conn, ws: str, folder_id: str, page_ids) -> dict | None:
    """The notes files of ``page_ids`` (pages of the folder; others are left
    out) as ``{page id: {markdown, attachments, version}}``, None when
    ``folder_id`` is no folder. Each is a vault page rendered against the
    whole folder — so a mention of a sibling page is ``[[Title]]`` and a
    linked block carries its anchor — with the page's id first in the front
    matter, its PDF linked as the file beside it, and upload references
    rewritten to ``attachments/<name>`` relative to the note's directory;
    ``attachments`` lists those names for the client to fetch."""
    lay = layout(conn, ws, folder_id)
    if lay is None:
        return None
    by_id = {p["id"]: p for p in lay["pages"]}
    ctx = VaultContext(block_ref_resolver(conn))
    ctx.id_key = ID_KEY
    ctx.place_pages({p["id"]: p["notes"] for p in lay["pages"]})
    for p in lay["pages"]:
        if p["pdf"]:
            ctx.place_pdf(p["id"], p["pdf"], p["doc_id"])
    referenced_blocks((r[0] for r in conn.execute("SELECT content FROM unified_blocks WHERE content LIKE '%[[%'")), ctx)

    out = {}
    for pid in list(dict.fromkeys(page_ids))[:MAX_NOTES]:
        p = by_id.get(pid)
        page = build_tree(fetch_subtree(conn, pid), pid) if p else None
        if page is None:
            continue
        md, assets = collect_and_rewrite(render_vault_page(page, ctx, tags=p["labels"]),
                                         include_pdf=True, prefix="../" * p["stem"].count("/") + "attachments/")
        out[pid] = {"markdown": md, "attachments": sorted(assets), "version": p["version"]}
    return out
