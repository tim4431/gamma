"""Pages copied from one workspace of this server into another
(docs/dev/workspaces.md "Copies between workspaces"): the pages a person
picked in the library, each with the pages inside it, into a workspace they
may edit (Copy to workspace ▸), or every page of the library into a new
personal workspace of theirs (Settings → Workspaces → Duplicate…).

One way for both, the way a page already travels between two workspaces of
this server (gamma/linked_files.py ``_carry``): the pages exported as a
Gamma export is (routers/export.py ``pages_gamma_zip``) and merged in as an
import is (``ws_backup.restore_zip`` merge). They come whole, with their
notes, highlights, handwriting, comments and chats, under the same ids,
filed in the target's folders and labels of the same names (made there
when it has none); a page the target holds already, by id or by the PDF it
owns, stays as it is there. What ties a page to the workspace it leaves
stays behind: a Zotero link's rows and the marks it wrote
(``zotero_local.LINK_MARKS``), a clone's sync state, a work folder (its
files come as library pages), a file read where it lies (its bytes come
instead), *Opened files* and Recently deleted.

A PDF the workspace's Zotero link has not brought yet is not here to copy:
``zotero_pdfs`` counts the pages that come without one, which the app says
before it copies (``preview``) and after.
"""

import json
import tempfile
from pathlib import Path

from . import jobs, storage, workspaces, ws_backup
from .blocks_store import live_pages, page_parents, top_of, with_sub_pages
from .db import connect_pages_db
from .server_settings import check_upload_allowed

# What the result keeps of the merge's report: its counts, never its lists
# of ids (a job's result is read whole by the tray).
COUNTS = ("pages_added", "pages_skipped", "from_trash", "chats_added", "comments_added", "uploads_added")


class CopyError(ValueError):
    """Nothing that can be copied: the job's message, a 400 for the routes."""


def pages_of(conn, pages: list[str] | None) -> list[str]:
    """The pages a copy takes, in library order, each followed by the pages
    inside it (``with_sub_pages``): those of ``pages`` that are live pages
    of the workspace of ``conn``, or with None every live page, a work
    folder's files included. Never *Opened files* nor the files opened from
    this computer inside it, which are no library pages: Add to library…
    files one (gamma/linked_files.py)."""
    from .linked_files import opened_files_page  # local: linked_files imports the routers

    parents = page_parents(conn)
    if pages is None:
        ids = live_pages(conn, parents)
    else:
        live = {r[0] for r in conn.execute("SELECT id FROM unified_blocks WHERE parent_id = 'root' "
                                           "AND id IN (SELECT value FROM json_each(?))", (json.dumps(pages),))}
        ids = with_sub_pages(conn, [p for p in dict.fromkeys(pages) if p in live], parents)
    opened = opened_files_page(conn)
    return [p for p in ids if top_of(parents, p) != opened] if opened else ids


def zotero_pdfs(conn, ids: list[str]) -> int:
    """How many of the pages ``ids`` have no PDF here while their Zotero
    item holds one (the page's ``zotero_pdf``, docs/dev/zotero_sync.md): a
    copy takes them without it."""
    return conn.execute("SELECT COUNT(*) FROM unified_blocks WHERE id IN (SELECT value FROM json_each(?)) "
                        "AND json_extract(properties, '$.zotero_pdf') IS NOT NULL", (json.dumps(ids),)).fetchone()[0]


def preview(ws: str, pages: list[str] | None) -> dict:
    """What a copy of ``pages`` (``pages_of``) would take: ``{pages,
    zotero_pdfs}``."""
    with connect_pages_db(ws) as conn:
        ids = pages_of(conn, pages)
        return {"pages": len(ids), "zotero_pdfs": zotero_pdfs(conn, ids)}


def copy(ws: str, pages: list[str] | None, *, to: str = "", name: str = "", by: str,
         progress=jobs.no_progress) -> dict:
    """Copy the pages (``pages_of``) of ``ws`` into the workspace ``to``, or
    with ``name`` into a new personal workspace of ``by`` of that name, made
    once the export is written and removed again when the copy fails or is
    stopped (it holds nothing ``ws`` does not). The files the target lacks
    count against its storage quota, as an upload's would: 507, before
    anything is merged. ``progress`` (the job's report) hears the export's
    pages and bytes, then the merge's phases. Returns the merge's
    ``COUNTS`` with ``workspace``, the target's id, and ``zotero_pdfs``.
    Raises CopyError for nothing to copy or the workspace itself as the
    target, and what ``restore_zip`` raises."""
    from .routers.export import pages_gamma_zip  # local: keep gamma.* free of the routers package

    if bool(to) == bool(name):
        raise CopyError("name the workspace to copy into, or a name for a new one")
    if to == ws:
        raise CopyError("the pages are in that workspace already")
    with connect_pages_db(ws) as conn:
        ids = pages_of(conn, pages)
        waiting = zotero_pdfs(conn, ids)
    if not ids:
        raise CopyError("none of these pages is in the library any more")
    made = ""
    with tempfile.TemporaryDirectory(prefix="gamma-copy-") as td:
        zpath = Path(td) / "pages.zip"
        files = pages_gamma_zip(ws, ids, zpath, progress)
        try:
            if name:
                made = to = workspaces.create(name, by, kind="personal", by=by)["id"]
            check_upload_allowed(to, sum(storage.size(ws, f) or 0 for f in files if not storage.exists(to, f)))
            merged = ws_backup.restore_zip(to, zpath, "merge", by=by, progress=progress)
        except BaseException:
            if made:
                try:
                    workspaces.delete(made, by=by, keep=False)
                except Exception:  # noqa: BLE001 — the copy's own error is the one to report
                    pass
            raise
    return {key: merged[key] for key in COUNTS if key in merged} | {"workspace": to, "zotero_pdfs": waiting}
