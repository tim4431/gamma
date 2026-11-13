"""This server's workspace as a synced vault's remote (docs/dev/synced_vaults.md
"A round"): ``LocalRemote`` reads and writes it in-process as the account
that made the link, asking before each write whether that account may still
edit there, every write under the workspace's write lock and logged as the
vault's client (``remote.CLIENT``). The interface is what a round needs of
Gamma (gamma/vault/remote.py); gamma/vault/http_remote.py's ``HttpRemote``
answers the same over HTTP.
"""

import json
import os
from pathlib import Path

from .. import collab, folder_links, gamma_sync, highlight_relocate, linked_files, storage, upload_gc, workspaces
from ..documents import formats
from ..blocks_store import (FOLDERS, LABELS, PARENT_PAGE, TRASH, fetch_subtree, filing, folder_inserts,
                            folder_subtree_ids, insert_page_tree, label_inserts, label_names, live_pages, load_props,
                            newest_change_seq, page_in_folder, page_parents, pages_in_folder, top_of, tree_rows,
                            upload_refs, write_lock)
from ..db import connect_pages_db, connect_vaults_db, page_now, parse_stamp
from ..documents.model import document, document_at, in_place, page_document, pdf_file_hash, stored_name
from ..feedback import kept
from ..folder_sync import ROOT
from ..ops import MAX_OPS, OpError, after_commits, apply_batches, delete_tree, notify_commit, restore_page, trash_page
from ..sync_tree import snapshot_from_rows
from ..work_folders import marks
from ..work_folders.library import work_folder_of
from . import disk
from .remote import (CLIENT, CONFLICT, Changed, Edit, Gone, Info, Layout, Page, Refused, doc_info, info_stamps, set_doc,
                     set_page)

NO_ACCESS = "the account that made this vault has no access to its workspace any more"


class LocalRemote:
    """This server's workspace ``ws`` in-process: the vault's folder
    ``folder_id`` (``folder_sync.ROOT``: the whole library), written as
    ``user_id``, the account that made the link. ``leave_out``: the folders of
    the vaults whose pages are theirs (``remote_for``), ``siblings`` those
    of them beside this one, whose pages filed here too a round reports
    (``Layout.shared``)."""

    def __init__(self, ws: str, folder_id: str, user_id: str, leave_out=(), siblings=()):
        self.ws, self.folder, self.user_id = ws, folder_id, user_id
        self.leave_out = [f for f in leave_out if f and f != folder_id]
        self.siblings = [f for f in siblings if f in self.leave_out]
        self.touched = set()

    # --- reading -------------------------------------------------------------------------

    def may_write(self) -> None:
        """``folder_links.may_edit``, asked before each write."""
        if not folder_links.may_edit(self.ws, self.user_id):
            raise Refused("the account that made this vault may no longer edit its workspace")

    def layout(self, page_id: str = "") -> Layout:
        """The cursor, then the pages below the folder (their rows and their
        seqs one query each) and the folders below it; with ``page_id`` that
        page alone, when it is one of them (a page's round reads no other).
        A file of another work folder filed here too is that one's, and so
        are another work folder's directories, a W moved into this folder
        included. The
        folder's mark is the layout's ``work``. ``Refused`` when the account
        has no role there any more."""
        if not workspaces.role_of(self.ws, self.user_id):
            raise Refused(NO_ACCESS)
        with connect_pages_db(self.ws) as conn:
            cursor = newest_change_seq(conn)
            parents = page_parents(conn)
            top = None if self.folder == ROOT else self.folder
            mark = None
            if top:
                found = conn.execute("SELECT properties FROM unified_blocks WHERE id = ? AND page_id = ?",
                                     (top, FOLDERS)).fetchone()
                if not found:
                    raise disk.Stopped("the vault's folder is no longer in Gamma")   # never read as every page gone
                mark = marks.mark_of(load_props(found[0]))
            shared = []
            if page_id:
                live = conn.execute("SELECT 1 FROM unified_blocks WHERE id = ? AND parent_id = 'root'",
                                    (top_of(parents, page_id),)).fetchone()
                ids = [page_id] if (live and (top is None or page_in_folder(conn, page_id, top))
                                    and not any(page_in_folder(conn, page_id, other) for other in self.leave_out)) else []
            else:
                held = {other: pages_in_folder(conn, other, parents) for other in self.leave_out}
                away = set().union(*held.values())
                beside = {page for other in self.siblings for page in held[other]}
                ids, shared = [], []
                for pid in pages_in_folder(conn, top, parents) if top else live_pages(conn, parents):
                    if pid not in away:
                        ids.append(pid)
                    elif pid in beside:
                        shared.append(pid)
                titles = dict(conn.execute("SELECT id, content FROM unified_blocks WHERE id IN (SELECT value FROM "
                                           "json_each(?))", (json.dumps(shared),)).fetchall())
                shared = [(pid, titles.get(pid) or "Untitled") for pid in shared]
            rows = conn.execute("SELECT id, content, properties, doc_id FROM unified_blocks WHERE id IN "
                                "(SELECT value FROM json_each(?))", (json.dumps(ids),)).fetchall()
            seqs = dict(conn.execute("SELECT page_id, seq FROM page_changes WHERE page_id IN "
                                     "(SELECT value FROM json_each(?))", (json.dumps([*ids, LABELS]),)).fetchall())
            names = label_names(conn)
            props = {r[0]: load_props(r[2]) for r in rows}
            tops = {page_id: top_of(parents, page_id) for page_id in ids}
            missing = sorted(set(tops.values()) - set(props))
            for page_id, raw in conn.execute("SELECT id, properties FROM unified_blocks WHERE id IN "
                                             "(SELECT value FROM json_each(?))", (json.dumps(missing),)):
                props[page_id] = load_props(raw)
            inside = folder_subtree_ids(conn, top) if top else None
            tree = tree_rows(conn, FOLDERS)
            works = marks.Tree.of(tree)
            folders = {block_id: (parent, name or "") for block_id, parent, name, _props in tree
                       if (inside is None or block_id in inside) and block_id != top
                       and works.work_of(block_id) in ("", self.folder)}
        by_id = {r[0]: r for r in rows}
        pages = {}
        for page_id in ids:
            row = by_id.get(page_id)
            own = props.get(page_id)
            if row is None or work_folder_of(own) not in ("", self.folder):
                continue
            view = page_document(own)
            pages[page_id] = Info(page_id, row[1] or "Untitled", seqs.get(page_id, 0),
                                  filing(props.get(tops[page_id], {}), FOLDERS), row[3] or "", in_place(view),
                                  [names[i] for i in filing(own, LABELS) if i in names],
                                  [str(a) for a in own.get("aliases") or [] if isinstance(a, str)],
                                  parents.get(page_id, ""), **info_stamps(own), **doc_info(view))
        return Layout(pages, folders, cursor, seqs.get(LABELS, 0), shared, mark)

    def mark(self) -> dict | None:
        """The mark the vault's folder carries (``Layout.work``), None for
        none or no folder."""
        if self.folder == ROOT:
            return None
        with connect_pages_db(self.ws) as conn:
            found = conn.execute("SELECT properties FROM unified_blocks WHERE id = ? AND page_id = ?",
                                 (self.folder, FOLDERS)).fetchone()
        return marks.mark_of(load_props(found[0])) if found else None

    def work_tree(self) -> marks.Tree:
        """The folder tree as the work-folder rules read it."""
        with connect_pages_db(self.ws) as conn:
            return marks.Tree.read(conn)

    def work_pages(self) -> dict:
        """``{page id: properties}`` of the live files of the vault's work
        folder (``idx_ub_work_folder``)."""
        with connect_pages_db(self.ws) as conn:
            return {r[0]: load_props(r[1]) for r in conn.execute(
                "SELECT id, properties FROM unified_blocks WHERE work_folder = ? AND +parent_id = 'root'", (self.folder,))}

    def page(self, page_id: str) -> Page | None:
        """The page's seq, then its blocks; None for a page gone for good."""
        with connect_pages_db(self.ws) as conn:
            return _page_in(conn, page_id)

    def state(self, page_id: str) -> str | None:
        """``live``, ``trashed``, or None for a page gone for good."""
        with connect_pages_db(self.ws) as conn:
            row = conn.execute("SELECT parent_id FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
        if row is None or row[0] not in ("root", TRASH):
            return None
        return "trashed" if row[0] == TRASH else "live"

    def filing(self, page_id: str) -> tuple[list, str]:
        """The page's own ``folders`` and its ``parent_page`` ("" for none)."""
        with connect_pages_db(self.ws) as conn:
            row = conn.execute("SELECT properties FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
        props = load_props(row[0]) if row else {}
        return filing(props, FOLDERS), str(props.get(PARENT_PAGE) or "")

    def doc_of(self, page_id: str) -> str:
        with connect_pages_db(self.ws) as conn:
            row = conn.execute("SELECT doc_id FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
        return (row[0] if row else "") or ""

    def landed(self, page_id: str, token: str, at: int | None = None, since: float = 0.0) -> bool | None:
        """Whether a batch of the vault's named ``token`` is in the page's log,
        a page in Recently deleted's too; None where the log cannot say: no
        row of it is older than ``since``, a time before the plan was made
        (pruning drops the oldest rows, ``ops._prune``, the batch perhaps among
        them), and the round asks the page itself (``Round._plan_landed``).
        ``at``, the seq the plan was made at, is for a log read over HTTP
        (``HttpRemote.landed``): here a page's seq is the workspace's change
        log's (``page_changes``), not its op log's."""
        with connect_pages_db(self.ws) as conn:
            if conn.execute("SELECT 1 FROM page_ops WHERE page_id = ? AND client = ? AND batch_id = ?",
                            (page_id, CLIENT, token)).fetchone():
                return True
            oldest = conn.execute("SELECT at FROM page_ops WHERE page_id = ? ORDER BY seq LIMIT 1", (page_id,)).fetchone()
        if oldest is None:
            return False         # pruning keeps the newest row: a log with none never held the batch
        written = parse_stamp(oldest[0])
        return None if written is None or written.timestamp() >= since else False

    def held(self, page_id: str, ids) -> dict:
        """The ``ids`` another page holds, Recently deleted's too (an insert of
        one is refused): ``{id: whether that page is live}``."""
        with connect_pages_db(self.ws) as conn:
            return _held_in(conn, page_id, ids)

    def page_of(self, block_id: str) -> str | None:
        with connect_pages_db(self.ws) as conn:
            row = conn.execute("SELECT p.id FROM unified_blocks b JOIN unified_blocks p ON p.id = b.page_id "
                               "WHERE b.id = ? AND p.parent_id = 'root'", (block_id,)).fetchone()
        return row[0] if row else None

    def title_of(self, page_id: str) -> str:
        with connect_pages_db(self.ws) as conn:
            row = conn.execute("SELECT content FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
        return (row[0] if row else "") or ""

    def address(self) -> str:
        """The workspace's absolute address, ``https://host/?ws=<id>``, the
        base of a link to a page outside the vault; "" when this server knows
        none (``cloud_auth.server_url``), and such a link stays as Gamma
        wrote it."""
        from ..cloud_auth import server_url  # local: it imports the account server's modules

        base = server_url()
        return f"{base.rstrip('/')}/?ws={self.ws}" if base else ""

    def carets(self, page_id: str) -> set:
        """The blocks someone has a caret or an editor on, in the page's room."""
        return collab.editing(self.ws, page_id)

    def read_upload(self, name: str) -> bytes | None:
        path = storage.find_upload_file(name, self.ws)
        return path.read_bytes() if path else None

    # --- writing --------------------------------------------------------------------------

    def apply(self, page_id: str, build=None, *, token: str = "", built=None) -> tuple[int, int, Edit]:
        """``build(page, held)`` under the write lock (``held(ids)``: those
        another page holds, ``LocalRemote.held``) and its ``Edit`` in one
        transaction: the ops in ``MAX_OPS`` chunks logged as ``CLIENT``,
        ``token`` naming the first batch's row, the conflicts last, with the
        page's seq read in it. ``built``, ``(seq, edit)``, is an edit built
        before the lock from the page at that seq, which the journal named:
        written as it is while the page's seq is still that, else ``Changed``
        and nothing written, for the caller to plan again outside the lock, so
        that what runs under it never grows with the page. ``Gone`` for a page
        that is not there. → (the seq before, the seq after, the edit)."""
        self.may_write()
        with connect_pages_db(self.ws) as conn:
            write_lock(conn)
            try:
                head = _head_in(conn, page_id)
                if head is None or head[0]:
                    raise Gone(page_id)
                if built is not None:
                    if built[0] != head[1]:
                        raise Changed(page_id)
                    edit = built[1]
                else:
                    edit = build(_page_in(conn, page_id), lambda ids: _held_in(conn, page_id, ids))
                chunks = [(page_id, edit.ops[i:i + MAX_OPS]) for i in range(0, len(edit.ops), MAX_OPS)]
                results = apply_batches(conn, chunks, actor=self.user_id, client=CLIENT, batch_id=token) if chunks else []
                _conflict_rows(conn, page_id, edit.conflicts, edit.resolved)
                seq = conn.execute("SELECT seq FROM page_changes WHERE page_id = ?", (page_id,)).fetchone()
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            after_commits(self.ws, conn, results)
        if results or edit.conflicts:
            self.touched.add(page_id)
        return head[1], (seq[0] if seq else 0), edit

    def create(self, page_id: str, title: str, props: dict, tree: list, labels=()) -> int:
        """A new page ``page_id`` with its block tree (``{id, content,
        properties, children}``), filed, labelled (the labels made first where
        missing: a page that is then not made leaves an unused label), last in
        the library, its blocks in one transaction. → its seq. OpError 409
        when a block holds the id."""
        props = {**props, LABELS: self.label_ids(labels)} if labels else dict(props)
        self.may_write()
        with connect_pages_db(self.ws) as conn:
            write_lock(conn)
            try:
                if conn.execute("SELECT 1 FROM unified_blocks WHERE id = ?", (page_id,)).fetchone():
                    raise OpError(409, "a block with that id exists")
                insert_page_tree(conn, page_id, title, props, tree, self.user_id, client=CLIENT)
                upload_gc.claim(conn, _tree_refs(title, props, tree))
                seq = conn.execute("SELECT seq FROM page_changes WHERE page_id = ?", (page_id,)).fetchone()
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        notify_commit(self.ws, CLIENT, page_id)
        self.touched.add(page_id)
        return seq[0] if seq else 0

    def set_page(self, page_id: str, *, title=None, props=None) -> tuple[int, int]:
        return set_page(self, page_id, title=title, props=props)

    def set_doc(self, page_id: str, doc: dict) -> tuple[int, int]:
        return set_doc(self, page_id, doc)

    def stamp(self, ids, plan) -> dict:
        """The live pages ``ids``' root properties patched quietly (no
        ``updated_at``: gamma/work_folders/takeover.py), ``plan(page id,
        properties)`` giving each patch (None: none) under the write lock, a
        transaction a ``MAX_OPS`` pages, logged as ``CLIENT``. → ``{page id:
        (seq before, seq after)}`` of the pages written."""
        self.may_write()
        ids, out = list(ids), {}
        for i in range(0, len(ids), MAX_OPS):
            chunk = json.dumps(ids[i:i + MAX_OPS])
            with connect_pages_db(self.ws) as conn:
                write_lock(conn)
                try:
                    batches = []
                    for page_id, raw in conn.execute("SELECT id, properties FROM unified_blocks WHERE id IN (SELECT "
                                                     "value FROM json_each(?)) AND parent_id = 'root'", (chunk,)).fetchall():
                        patch = plan(page_id, load_props(raw))
                        if patch:
                            batches.append((page_id, [{"op": "set", "id": page_id, "props": patch, "quiet": True}]))
                    seqs = "SELECT page_id, seq FROM page_changes WHERE page_id IN (SELECT value FROM json_each(?))"
                    written = json.dumps([page_id for page_id, _ops in batches])
                    before = dict(conn.execute(seqs, (written,)).fetchall())
                    results = apply_batches(conn, batches, actor=self.user_id, client=CLIENT) if batches else []
                    after = dict(conn.execute(seqs, (written,)).fetchall())
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise
                after_commits(self.ws, conn, results)
            for page_id, _ops in batches:
                out[page_id] = (before.get(page_id, 0), after.get(page_id, 0))
                self.touched.add(page_id)
        return out

    def label_ids(self, names) -> list:
        """The labels of ``names``' ids, made where missing."""
        ids = self._in_tree(LABELS, lambda conn: label_inserts(conn, names))
        return list(dict.fromkeys(ids[n] for n in names if n in ids))

    def _in_tree(self, tree: str, build):
        """``build(conn)`` → ``(ops, out)`` under the workspace's write lock,
        its ops on ``tree`` (the folders' or the labels') applied as
        ``CLIENT`` in the same transaction, the commit's listeners told. →
        ``out``."""
        self.may_write()
        with connect_pages_db(self.ws) as conn:
            write_lock(conn)
            try:
                ops, out = build(conn)
                results = apply_batches(conn, [(tree, ops)], actor=self.user_id, client=CLIENT) if ops else []
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            after_commits(self.ws, conn, results)
        return out

    def trash(self, page_id: str) -> None:
        """The page to Recently deleted, when it is live."""
        self.may_write()
        with connect_pages_db(self.ws) as conn:
            write_lock(conn)
            row = conn.execute("SELECT parent_id FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
            if not row or row[0] != "root":
                conn.rollback()
                return
            trash_page(self.ws, conn, page_id, actor=self.user_id, client=CLIENT)
        self.touched.add(page_id)

    def restore(self, page_id: str) -> None:
        """The page back from Recently deleted, when it is there."""
        self.may_write()
        with connect_pages_db(self.ws) as conn:
            write_lock(conn)
            row = conn.execute("SELECT parent_id FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
            if not row or row[0] != TRASH:
                conn.rollback()
                return
            restore_page(self.ws, conn, page_id, actor=self.user_id, client=CLIENT)
        self.touched.add(page_id)

    def folders(self, paths) -> dict:
        """The folders at ``paths`` (names from the vault's folder down), made
        where missing (``folder_inserts`` reuses one of the name). →
        ``{tuple(names): folder id}``."""
        under, work = ("", False) if self.folder == ROOT else (self.folder, self.mark() is not None)
        return self._in_tree(FOLDERS, lambda conn: folder_inserts(conn, [list(p) for p in paths], under, into_work=work))

    def folder_op(self, op: dict) -> None:
        """One op on the folder tree: a ``set`` of a name or a ``move``, a
        parent of "" being the vault's own folder."""
        if op["op"] == "move" and not op.get("parent"):
            op = {**op, "parent": FOLDERS if self.folder == ROOT else self.folder}
        self._in_tree(FOLDERS, lambda _conn: ([op], None))

    def drop_folder(self, folder_id: str) -> bool:
        """The folder deleted when no page is filed in it or below; whether it
        is gone."""
        self.may_write()
        with connect_pages_db(self.ws) as conn:
            write_lock(conn)
            if not conn.execute("SELECT 1 FROM unified_blocks WHERE id = ? AND page_id = ?", (folder_id, FOLDERS)).fetchone():
                conn.rollback()
                return True
            if pages_in_folder(conn, folder_id):
                conn.rollback()
                return False
            delete_tree(self.ws, conn, FOLDERS, folder_id, actor=self.user_id, client=CLIENT)
        return True

    def link_doc(self, path: str, seen: tuple, page_id: str, ext: str) -> None:
        """``path`` recorded as where the page's document, the bytes ``seen``
        (``linked_files.hash_file``, ``hash_any``) under ``ext``, is read."""
        linked_files._put_row(self.ws, path, seen, page_id, self.user_id, ext=ext)

    def unlink_doc(self, path: str, name: str) -> None:
        linked_files._drop_row(self.ws, name, path)

    def restat_doc(self, path: str, seen: tuple, name: str) -> None:
        """The row of the document ``name`` at ``path`` brought to the stat
        ``seen`` of the same bytes (``linked_files.restat``: a file touched
        goes on being served)."""
        linked_files.restat(self.ws, name, path, seen)

    def drop_pageless(self, root: Path) -> None:
        """``linked_files.drop_pageless``: the rows of no page below ``root``
        whose name a page's row serves now."""
        linked_files.drop_pageless(self.ws, root)

    def relink(self, old: str, new: str) -> None:
        """The linked files at ``old``, or below it, now read at ``new``: the
        vault's, and any other page's, of any workspace, that reads one of
        them in place (a PDF opened with *Open with*)."""
        below = old.rstrip("\\/") + os.sep
        with connect_vaults_db() as conn:
            conn.execute("UPDATE OR REPLACE linked_files SET path = ? WHERE path = ?", (new, old))
            conn.execute("UPDATE OR REPLACE linked_files SET path = ? || substr(path, ?) WHERE substr(path, 1, ?) = ?",
                         (new.rstrip("\\/") + os.sep, len(below) + 1, len(below), below))
        storage.forget_linked_stat(old)
        storage.forget_linked_stat(new)

    def spare(self, path: str, name: str, page_id: str) -> None:
        """The document ``name`` (``<hash><ext>``) at ``path``, which the
        vault is about to delete with its page ``page_id``, stored first for
        every other page that reads it in place: one of this workspace that
        holds those bytes under that extension (its row may be the vault's
        now: one name, one path), one of another workspace with a row there.
        OSError when it cannot be (``storage.content_refusal``: a ``.webm``
        that opens as no audio or video among the reasons): nothing is
        deleted."""
        stem, ext = os.path.splitext(name)
        with connect_vaults_db() as conn:
            wanted = set(conn.execute("SELECT ws, name FROM linked_files WHERE path = ? AND page_id != ? AND page_id != '' "
                                      "AND ws != ?", (path, page_id, self.ws)).fetchall())
        with connect_pages_db(self.ws) as conn:
            if conn.execute("SELECT 1 FROM unified_blocks WHERE doc_id = ? AND id != ? "
                            "AND json_extract(properties, '$.doc.ext') = ? LIMIT 1", (stem, page_id, ext)).fetchone():
                wanted.add((self.ws, name))
        for ws, held in sorted(wanted):
            if storage.exists(ws, held):
                continue
            with open(path, "rb") as f:
                try:
                    stored = _store_stream(ws, f, os.path.splitext(held)[1])
                except ValueError as e:
                    raise OSError(str(e)) from e
            if stored != held:
                raise OSError("the file is not the bytes its pages read")

    def replace_doc(self, page_id: str, path: str, seen: tuple, filename: str, *, format: str = "pdf",
                    ext: str = ".pdf") -> dict:
        """The page takes the bytes ``seen`` at ``path`` as its document (of
        ``format``, stored under ``ext``: a PDF's, or a work folder's file of
        any format), read where they are: their row first, so the relocation
        run reads the new file, then ``replace_attachment`` (a document
        other than a PDF names its format, extension and size to it: the
        relocation run and the stamps are two PDFs' alone), the page reading
        them in place from then on (``at`` ``path``, a page that stored its
        PDF too), the disk winning and no other page's bytes a refusal (a
        vault's file is what the disk holds); then the row of the bytes
        before goes, as ``linked_files._catch_up``'s does: the path no longer
        holds them. A work folder's file gets the size on its document. A
        page that keeps its bytes (``kept.keeps_bytes``: a work folder's file
        in the library, as a library document's bytes are, or a page with an
        open comment thread, whose outgoing version, stored at the thread's
        open or at the replacement before, the relocation run reads) has
        each version stored as it comes, under its extension, reading it in
        place all the same; OSError when it cannot be."""
        from ..routers.pages import replace_attachment  # local: keep gamma.* free of the routers package

        self.may_write()
        with connect_pages_db(self.ws) as conn:
            row = conn.execute("SELECT properties FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
        own = load_props(row[0]) if row else {}
        before = page_document(own)
        extra = None
        if kept.keeps_bytes(self.ws, page_id, own):
            try:
                stored = folder_links.LocalSink(self.ws, self.user_id).store(Path(path), ext)
            except gamma_sync.SyncError as e:
                raise OSError(str(e)) from e
            if stored != seen[0]:
                raise OSError("the file changed while it was read")
        if work_folder_of(own):
            extra = {"doc": document(seen[0], name=filename, at="path", format=format, ext=ext, size=seen[1])}
        shape = {} if format == "pdf" else {"format": format, "ext": ext, "size": seen[1]}
        self.link_doc(path, seen, page_id, ext)
        try:
            done = replace_attachment(self.ws, page_id, seen[0], "", filename, at="path", actor=self.user_id,
                                      client=CLIENT, owner=self.user_id, check_held=False, props=extra,
                                      keep_old_days=gamma_sync.KEEP_OLD_DAYS, **shape)
        except OpError:
            self.unlink_doc(path, stored_name(seen[0], ext))
            raise
        if before and before["file"] and before["file"] != stored_name(seen[0], ext):
            self.unlink_doc(path, before["file"])
        self.touched.add(page_id)
        return done

    def store_doc(self, path: Path, page_id: str) -> str:
        """The file stored as an upload, under the page's document's
        extension, and the page no longer reading it in place (its document
        at ``store``, the same hash and name): what a page that leaves the
        vault keeps."""
        with connect_pages_db(self.ws) as conn:
            row = conn.execute("SELECT properties FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
        view = page_document(load_props(row[0])) if row else None
        try:
            doc_id = folder_links.LocalSink(self.ws, self.user_id).store(path, view["ext"] if view else ".pdf")
        except gamma_sync.SyncError as e:
            raise OSError(str(e)) from e

        def build(page, _held):
            view = page_document(page.blocks[page_id]["props"])
            if not in_place(view):
                return Edit([])
            return Edit([{"op": "set", "id": page_id, "props": {"doc": document_at(view, "store")}}])
        self.apply(page_id, build)
        return doc_id

    def place_highlights(self, page_id: str) -> bool:
        """The relocation run started for the page when it has highlights
        known by their page and quote alone (a callout with no anchor Gamma
        knows: a vault copied from another computer): it places each where
        its quote is in the page's PDF, dashed, for the person to check
        (``highlight_relocate.place_all``). False when it could not start
        now (the account's background tasks are full)."""
        from ..routers.pages import _start_relocation  # local: keep gamma.* free of the routers package

        with connect_pages_db(self.ws) as conn:
            items = highlight_relocate.page_highlights(conn, page_id, unplaced=True)
            row = conn.execute("SELECT content, properties FROM unified_blocks WHERE id = ? AND parent_id = 'root'",
                               (page_id,)).fetchone()
        view = page_document(load_props(row[1])) if row else None
        doc_id = pdf_file_hash(view)
        if not doc_id or all(props["pdf_position"].get("rects") for _bid, props in items):
            return True
        job, _reason = _start_relocation(self.user_id, self.ws, page_id, row[0], doc_id)
        return job is not None

    def pictures(self, root: Path) -> dict:
        """``{vault path: upload name}`` of the pictures below ``root`` read
        where they are (``link_file``'s rows)."""
        below = str(root).rstrip("\\/") + os.sep
        with connect_vaults_db() as conn:
            rows = conn.execute("SELECT path, name FROM linked_files WHERE ws = ? AND page_id = '' AND substr(path, 1, ?) = ?",
                                (self.ws, len(below), below)).fetchall()
        return {path[len(below):].replace(os.sep, "/"): name for path, name in rows}

    def link_file(self, path: Path, name: str = "") -> str:
        """A file of the vault a note links, read where it is: its upload name
        (``name``, a picture pull wrote, else its bytes' under the extension
        it is stored by, ``formats.vault_extension``: the name its page's row
        takes in a work folder, so one row serves both), with the row that
        serves it (no page: the vault's)."""
        digest, st = storage.content_digest(path.read_bytes()), path.stat()
        name = name or stored_name(digest, formats.vault_extension(path.name))
        linked_files._put_row(self.ws, str(path), (digest, st.st_size, st.st_mtime_ns, str(st.st_ino)), "", self.user_id,
                              name=name)
        return name

    def tell(self) -> None:
        """Each page a round wrote hears so, for its vault chip to look again."""
        for page_id in sorted(self.touched):
            collab.publish(self.ws, page_id, {"t": "vault"})
        self.touched.clear()


def _store_stream(ws: str, stream, ext: str) -> str:
    """The bytes of ``stream`` stored under ``ext``: a ``.pdf`` through
    ``storage.store_pdf_stream`` (which schedules its manifest), any other
    through ``store_file_stream``. → the stored name. ValueError as those
    raise it."""
    if ext == ".pdf":
        doc_id, _existed, _size = storage.store_pdf_stream(ws, stream)
        return stored_name(doc_id, ext)
    name, _existed = storage.store_file_stream(ws, stream, ext)
    return name


def _head_in(conn, page_id: str) -> tuple[bool, int] | None:
    """``(in Recently deleted, seq)`` of a page; None for one gone for good."""
    row = conn.execute("SELECT parent_id FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
    if row is None or row[0] not in ("root", TRASH):
        return None
    seq = conn.execute("SELECT seq FROM page_changes WHERE page_id = ?", (page_id,)).fetchone()
    return row[0] == TRASH, seq[0] if seq else 0


def _page_in(conn, page_id: str) -> Page | None:
    head = _head_in(conn, page_id)
    if head is None:
        return None
    return Page(page_id, head[1], snapshot_from_rows(fetch_subtree(conn, page_id)), head[0])


def _held_in(conn, page_id: str, ids) -> dict:
    wanted = sorted({i for i in ids if i})
    if not wanted:
        return {}
    return {r[0]: bool(r[1]) for r in conn.execute(
        "SELECT b.id, p.parent_id = 'root' FROM unified_blocks b LEFT JOIN unified_blocks p ON p.id = b.page_id "
        "WHERE b.id IN (SELECT value FROM json_each(?)) AND b.page_id != ?", (json.dumps(wanted), page_id))}


def _conflict_rows(conn, page_id: str, conflicts, resolved: bool) -> None:
    """The conflicts the file won, in the caller's transaction: the row
    ``sync_engine._conflict`` writes, not twice while the same is open, or
    written resolved (a force's record)."""
    for c in conflicts:
        mine, theirs = c.get("mine") or "", c.get("theirs") or ""
        if not resolved and conn.execute(
                "SELECT 1 FROM sync_conflicts WHERE resolved = 0 AND page_id = ? AND block_id = ? AND kind = ? "
                "AND mine = ? AND theirs = ?", (page_id, c["block_id"], CONFLICT, mine, theirs)).fetchone():
            continue
        conn.execute("INSERT INTO sync_conflicts (page_id, block_id, kind, mine, theirs, result, base, at, resolved) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (page_id, c["block_id"], CONFLICT, mine, theirs, c.get("result") or "", c.get("base") or "",
                      page_now(), int(resolved)))


def _tree_refs(title: str, props: dict, tree) -> set:
    names, pending = upload_refs(title, props), list(tree or ())
    while pending:
        node = pending.pop()
        names |= upload_refs(node.get("content") or "", node.get("properties") or {})
        pending += node.get("children") or []
    return names
