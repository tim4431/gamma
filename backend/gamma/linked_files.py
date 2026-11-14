"""Files read where they are on this computer (docs/research/vaults.md
"Reading a PDF where it is"; docs/research/open-with-plan.md for formats
other than PDF): a page of a workspace whose document stays at a path,
nothing copied (but the versions shown while a comment thread on the page
is open, gamma/feedback/kept.py) and nothing ever written into it or beside
it. Only the desktop app's own server has them
(``config.folder_links_enabled``).

Such a page's document is read in place (``doc.at`` ``path``,
gamma/documents/model.py) and has a stored name like any other
(``stored_name``: ``<hash>.pdf`` for a PDF, ``<hash><ext>`` for a file of any
other format); vaults.db ``linked_files`` says which path holds that name.
``storage.open_path`` answers from there, by one stat: a file whose size and
time are as recorded is the bytes that name hashes, one that moved reads as
nothing, and the read tells this module (``storage.linked_moved``). Hashing
and the replacement never run inside a read: a worker thread takes the name
off the queue (``follow``). The same bytes refresh the stat of a row that
still stands (``_refresh_row``); new bytes wait until the file has settled
(``gamma_sync.settled``: a build's half-written PDF is never a page's; a file
of another format has no ``%%EOF`` to wait for), then are
``replace_attachment`` through ``folder_links.LocalSink``, as a file vault's
push is, as the account the row names, keeping the document's format, so the
highlights are placed again and open viewers switch on the ops, and the row
moves to the new name. Until then the old name reads as nothing; the upload
route waits a moment for the worker (``settle_wait``), so that a file only
touched is no failure. ``sweep``, from the links' tick, looks at every row's
stat, which also picks up what the worker could not take, and forgets what
nobody kept.

A file a vault's round reads (its page has a ``vault_files`` row of a class
read in place, a PDF's or a work folder's file's) is that round's to
replace, and nothing here touches it: one replacer per file (``_in_vault``).
A work folder's file of any format has its row here too, named
``<hash><ext>`` (``_put_row`` with its ``ext``; ``hash_any`` hashes it with
no header check, ``restat`` refreshes a touched one, ``store_page_bytes``
stores its version under its own extension).

An opened file is a page inside the one page *Opened files*, found by its
property (``OPENED_FILES``) and made on demand, newest first: a sub-page is
the one way a page stays out of the library's list that server and client
share. It holds a file of any format Gamma can show, in the viewer of its
format (``_read``: a Markdown file is a text document there, never a note;
a binary, a program or a file past the keep limit is refused).
``open_file`` opens, ``add_to_library`` stores the bytes and takes the page
out (or to another workspace of this server, with everything on it, the
page here to Recently deleted), ``let_go`` sends a PDF's to Recently deleted
the same way once the desktop app has carried it to another server
(``carry_digest``: only a page unchanged since), ``locate`` finds a file that went,
``missing`` and ``reader`` say how a page stands. A path has one page,
deleted or not: ``open_file`` brings that page back and ``locate`` refuses
the path to any other, so that following the file never takes a row from a
page. A page taken to another workspace goes to Recently deleted without its
rows, so the file is no page here any more. A page's rows name the account that
opened it, whoever opens the file again or locates it later (``_opener``):
keeping it pushing and taking it elsewhere are that account's."""

import hashlib
import json
import os
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fractional_indexing import generate_key_between

from . import collab, comments, config, folder_links, gamma_sync, pdf_meta, storage, workspaces, ws_backup
from .blocks_store import (BLOCK_COLUMNS, FOLDERS, PARENT_PAGE, TRASH, TREES, block_to_dict, create_page, folder_paths,
                           free_position, load_props, page_for_file, page_root_id, touch_page,
                           with_sub_pages, write_lock)
from .db import connect_pages_db, connect_users_db, connect_vaults_db, format_stamp
from .documents import formats
from .documents.model import SQL_IN_PLACE, document, document_at, in_place, own_hash, page_document, stored_name
from .feedback import kept
from .logbuf import log
from .ops import after_commits, apply_batches, commit_ops, delete_page, purge_pages, restore_page, trash_page
from .work_folders import selection

FORGET_DAYS = 90                 # an opened PDF nobody marked or asked about goes after this long untouched
OPENED_FILES = "opened_files"    # the page property of the page *Opened files*
OPENED_TITLE = "Opened files"
HELD_THERE = "That workspace has this page, a page inside it, or a page of its PDF already: open it there."
CHANGED_HERE = ("This page changed while it was taken there: it stays here, all of it, and nothing of it went there. "
                "Try again.")
CHANGED_SINCE = ("This page changed here after it was exported: it stays here, all of it, and the copy on the other "
                 "server lacks what was written since. It is in both places now.")
IN_BOTH = ("Someone wrote on this page in that workspace as it arrived there, so it stays there, and it stays here "
           "too: it is in both workspaces now.")
SETTLE_WAIT_S = 3                # how long the upload route waits for the worker
LOOK_AGAIN_S = 1                 # how soon a file still being written is looked at again
MARKDOWN_EXTS = (".md", ".markdown")   # an opened file of these is text, and never joins the library (``_add``)
# What ``locate`` calls a file of each format Gamma reads in place
_A_FORMAT = {"pdf": "a PDF", "text": "a text", "image": "an image", "audio": "an audio", "video": "a video"}

_opening = threading.Lock()      # one open, locate, add or let-go at a time: a path gets one page, a page one place
_waiting: set = set()            # (ws, name) of the linked files a read found moved
_again: set = set()              # (ws, name) that come back to the queue shortly (``_look_again``)
_following = None                # the (ws, name) the worker has in hand
_guard = threading.Condition()   # the three above and ``_working``; told when the worker is done with a name
_working = False                 # the worker is running

# The pages inside *Opened files* nothing holds back: read and left, the file
# all there is to them. A page someone moved in that is no opened PDF stays.
_IDLE = ("SELECT p.id FROM unified_blocks p WHERE p.parent_page = ? AND p.parent_id = 'root' AND p.updated_at < ? "
         f"AND {SQL_IN_PLACE} "
         "AND NOT EXISTS (SELECT 1 FROM unified_blocks b WHERE b.page_id = p.id AND b.id != p.id) "
         "AND NOT EXISTS (SELECT 1 FROM unified_blocks s WHERE s.parent_page = p.id) "
         "AND NOT EXISTS (SELECT 1 FROM comments c WHERE c.page_id = p.id) "
         "AND NOT EXISTS (SELECT 1 FROM chats t WHERE t.bucket = p.id) "
         "AND NOT EXISTS (SELECT 1 FROM chat_history h WHERE h.bucket = p.id)")


def hash_any(path) -> tuple[str, int, int, str, bytes]:
    """``(hash, size, mtime_ns, file_id, head)`` of the file at ``path``, read
    through once and copied nowhere: the stem its bytes would be stored
    under, and its first ``formats.SNIFF_BYTES`` for what it is
    (``formats.format_of``). ValueError for a file that changed under the
    read; OSError for one that cannot be read."""
    before = os.stat(path)
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        chunk = f.read(storage.SPOOL_CHUNK)
        head = chunk[:formats.SNIFF_BYTES]
        while chunk:
            digest.update(chunk)
            chunk = f.read(storage.SPOOL_CHUNK)
    after = os.stat(path)
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("the file changed while it was read")
    return storage.digest_id(digest), after.st_size, after.st_mtime_ns, str(after.st_ino), head


def hash_file(path) -> tuple[str, int, int, str]:
    """``(doc_id, size, mtime_ns, file_id)`` of the PDF at ``path``
    (``hash_any``): ValueError for no ``%PDF`` header too."""
    doc_id, size, mtime_ns, file_id, head = hash_any(path)
    if not storage.is_pdf(head):
        raise ValueError("not a valid PDF (missing %PDF header)")
    return doc_id, size, mtime_ns, file_id


def _format(name: str, head: bytes) -> tuple[str, str]:
    """``(format, ext)`` an opened file named ``name`` whose first bytes are
    ``head`` is read as (``formats.format_of``): a PDF stored as ``.pdf``
    whatever its name says, a Markdown file as a text document (an opened
    file is no note: only a work folder edits a note where it is), any
    other under ``formats.vault_extension`` (``Makefile`` text as ``.bin``,
    as a work folder stores it)."""
    fmt, _language = formats.format_of(name, head)
    if fmt == "pdf":
        return "pdf", ".pdf"
    return ("text" if fmt == "markdown" else fmt), formats.vault_extension(name)


def _no_program(path: str) -> None:
    """LinkError for a file whose name says the system runs it
    (``formats.BLOCKED_EXTENSIONS``): Gamma opens no program."""
    ext = formats.extension_of(Path(path).name)
    if ext in formats.BLOCKED_EXTENSIONS:
        raise folder_links.LinkError(f"Gamma does not open programs ({ext}).")


def _read(path: str) -> tuple[tuple, str, str]:
    """A file someone just named, read to be opened or located: ``(seen,
    format, ext)``, ``seen`` ``hash_any``'s first four, ``format`` and
    ``ext`` ``_format``'s. LinkError for what stops it (theirs to hear:
    another program may hold it), a program (``_no_program``), a ``.pdf``
    that is no PDF, a file Gamma cannot show (``binary``), and one of
    another format than PDF past the keep limit (``config.vault_keep_bytes``,
    measured before it is read through; a PDF has none)."""
    _no_program(path)
    name = Path(path).name
    try:
        with open(path, "rb") as f:
            head = f.read(formats.SNIFF_BYTES)
        size = os.stat(path).st_size
    except OSError as e:
        raise folder_links.LinkError(f"Cannot read {path}: {e}") from e
    fmt, ext = _format(name, head)
    if fmt == "pdf" and not storage.is_pdf(head):
        raise folder_links.LinkError(f"Cannot read {path}: not a valid PDF (missing %PDF header)")
    if fmt == "binary":
        raise folder_links.LinkError(f"Gamma cannot show {name}: open it in another app.")
    keep = config.vault_keep_bytes()
    if fmt != "pdf" and size > keep:
        raise folder_links.LinkError(f"{name} is too large for Gamma to show here (over {keep >> 20} MB).")
    try:
        *seen, head = hash_any(path)
    except (OSError, ValueError) as e:
        raise folder_links.LinkError(f"Cannot read {path}: {e}") from e
    if _format(name, head) != (fmt, ext):
        raise folder_links.LinkError(f"Cannot read {path}: the file changed while it was read")
    return tuple(seen), fmt, ext


def _put_row(ws: str, path: str, seen: tuple, page_id: str, actor: str = "", *, name: str = "",
             ext: str = ".pdf") -> None:
    """``path`` as the file ``seen`` (``hash_file``, ``hash_any``'s first
    four) of the page, ``actor`` the account whose dialog named it, whom its
    changes are made as while it may edit (``_sink``), under the name of its
    document (its hash and ``ext``), or ``name`` (a vault's picture, with no
    page). The same name at the same path keeps what its row says of the
    highlights; a row with no page takes the page (a vault's picture made a
    page of)."""
    doc_id, size, mtime_ns, file_id = seen[:4]
    with connect_vaults_db() as conn:
        conn.execute("INSERT INTO linked_files (ws, name, path, size, mtime_ns, file_id, page_id, actor) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (ws, name, path) DO UPDATE SET size = excluded.size, "
                     "mtime_ns = excluded.mtime_ns, file_id = excluded.file_id, page_id = excluded.page_id, "
                     "actor = excluded.actor",
                     (ws, name or stored_name(doc_id, ext), path, size, mtime_ns, file_id, page_id, actor))
    storage.forget_linked_stat(path)


def restat(ws: str, name: str, path: str, seen: tuple) -> None:
    """The row ``name`` at ``path`` brought to the stat ``seen`` of the same
    bytes (a file touched, or saved again with the very bytes it held), when
    it still stands: what a vault's round does for a file read in place
    whose hash did not move, so that ``storage.open_path`` goes on serving
    it (a row whose stat differs from the file's reads as nothing)."""
    _hash, size, mtime_ns, file_id = seen[:4]
    with connect_vaults_db() as conn:
        conn.execute("UPDATE linked_files SET size = ?, mtime_ns = ?, file_id = ? WHERE ws = ? AND name = ? AND path = ?",
                     (size, mtime_ns, file_id, ws, name, path))
    storage.forget_linked_stat(path)


def _refresh_row(ws: str, name: str, path: str, page_id: str, seen: tuple, actor: str) -> None:
    """The row ``name`` at ``path`` of the page, brought to the stat ``seen``
    of the same bytes and naming ``actor``, when it still stands. Never a
    row written again: the file was hashed after its row was read, and a
    carry, an add or a locate may have dropped the row meanwhile, which
    written back would give the file to a page that has let it go."""
    _doc_id, size, mtime_ns, file_id = seen
    with connect_vaults_db() as conn:
        conn.execute("UPDATE linked_files SET size = ?, mtime_ns = ?, file_id = ?, actor = ? "
                     "WHERE ws = ? AND name = ? AND path = ? AND page_id = ?",
                     (size, mtime_ns, file_id, actor, ws, name, path, page_id))
    storage.forget_linked_stat(path)


def _drop_row(ws: str, name: str, path: str) -> None:
    with connect_vaults_db() as conn:
        conn.execute("DELETE FROM linked_files WHERE ws = ? AND name = ? AND path = ?", (ws, name, path))


def _rows_of(ws: str, page_id: str) -> list[tuple]:
    """``(name, path, size, mtime_ns)`` of the page's rows."""
    with connect_vaults_db() as conn:
        return conn.execute("SELECT name, path, size, mtime_ns FROM linked_files WHERE ws = ? AND page_id = ?",
                            (ws, page_id)).fetchall()


def _openers(ws: str, page_id: str) -> set:
    """The accounts the page's rows name: whoever's dialog named its file."""
    with connect_vaults_db() as conn:
        return {r[0] for r in conn.execute("SELECT actor FROM linked_files WHERE ws = ? AND page_id = ?", (ws, page_id))}


def _opener(ws: str, page_id: str, actor: str) -> str:
    """The account a row of the page names when ``actor`` writes one: the
    page's opener, whoever opens the file again or locates it (keeping it
    pushing and taking it elsewhere are the opener's, ``add_to_library``),
    ``actor`` for a page whose rows name none (a crash left it no row)."""
    return min(_openers(ws, page_id) - {""}, default=actor)


def _rows_at(ws: str, path: str) -> dict[str, str]:
    """``{name: page id}`` of the rows of a path."""
    with connect_vaults_db() as conn:
        return dict(conn.execute("SELECT name, page_id FROM linked_files WHERE ws = ? AND path = ? AND page_id != ''",
                                 (ws, path)).fetchall())


def _owned_file(props) -> str:
    """The stored name (``<hash><ext>``) of the document a page owns, from
    its properties: "" for none, and for a copy (it owns no bytes)."""
    view = page_document(load_props(props) if isinstance(props, str) else props)
    own = own_hash(view)
    return stored_name(own, view["ext"]) if own else ""


def _rows_standing(ws: str, path: str) -> dict[str, str]:
    """``_rows_at`` once the rows that stand for nothing are gone: the page
    does not exist any more, or carries another document now (a replacement
    left the row behind). A page in Recently deleted keeps its row, but one
    taken to another workspace, which went there without its rows
    (``_carry``)."""
    rows = _rows_at(ws, path)
    with connect_pages_db(ws) as conn:
        carried = {page_id: _owned_file(props) for page_id, props in conn.execute(
            "SELECT id, properties FROM unified_blocks WHERE id IN (SELECT value FROM json_each(?))",
            (json.dumps(sorted(set(rows.values()))),))}
    for name in [name for name, page_id in rows.items() if carried.get(page_id, "") != name]:
        _drop_row(ws, name, path)
        del rows[name]
    return rows


def _moved(path: str, size: int, mtime_ns: int) -> bool:
    """Whether the file is there with another size or time than recorded; one
    that is gone has not moved, it is missing."""
    st = storage.linked_stat(path)
    return st is not None and (st.st_size, st.st_mtime_ns) != (size, mtime_ns)


def _at_rest(rows: list[tuple], name: str) -> str:
    """The path of the rows ``rows`` (``_rows_of``) under ``name`` that holds
    the file as it was hashed, "" when it is gone or has changed since."""
    return next((path for row_name, path, size, mtime_ns in rows if row_name == name and storage.linked_stat(path)
                 and not _moved(path, size, mtime_ns)), "")


def _in_vault(page_id: str) -> bool:
    """Whether a vault's round reads the page's document where it is (a row
    of a class read in place, gamma/vault/kinds.py ``in_place``: a PDF's or
    a work folder's file's): the round alone replaces it, and no reader's
    path may."""
    with connect_vaults_db() as conn:
        return conn.execute("SELECT 1 FROM vault_files WHERE page_id = ? AND kind IN ('pdf', 'file') LIMIT 1",
                            (page_id,)).fetchone() is not None


def drop_pageless(ws: str, root: Path) -> None:
    """The rows of no page below ``root`` (a vault's pictures read where they
    are, gamma/vault/local_remote.py ``link_file``) whose name a page's row
    serves now: a work folder's picture became a page, and that page's row
    serves the name (one statement, nothing when there are none)."""
    below = str(root).rstrip("\\/") + os.sep
    with connect_vaults_db() as conn:
        conn.execute("DELETE FROM linked_files WHERE ws = ? AND page_id = '' AND substr(path, 1, ?) = ? AND EXISTS "
                     "(SELECT 1 FROM linked_files f WHERE f.ws = linked_files.ws AND f.name = linked_files.name "
                     "AND f.page_id != '')", (ws, len(below), below))


def _sink(ws: str, actor: str) -> folder_links.LocalSink:
    """Where a file's new bytes are written when no request asked: as
    ``actor``, the account the row names, as the account that made a link
    does for a file vault. One that may no longer edit here leaves the
    change to the workspace's first owner: the file is what it is. The row
    goes on naming its opener (``follow``): the owner only wrote a change."""
    if workspaces.at_least(workspaces.role_of(ws, actor), "editor"):
        return folder_links.LocalSink(ws, actor)
    with connect_users_db() as conn:
        row = conn.execute("SELECT user_id FROM workspace_members WHERE workspace_id = ? "
                           "ORDER BY role = 'owner' DESC, added_at LIMIT 1", (ws,)).fetchone()
    return folder_links.LocalSink(ws, row[0] if row else "")


def _take_bytes(sink: folder_links.LocalSink, page_id: str, filename: str, path: str, seen: tuple,
                opener: str = "", *, format: str = "pdf", ext: str = ".pdf") -> None:
    """The page takes the bytes ``seen`` at ``path`` as its document, of
    ``format`` stored under ``ext`` (its PDF, or an opened file of another
    format, which stays of its format), as ``sink``'s account: its row
    under the new name first, naming ``opener`` (``sink``'s account when
    none), so the relocation run reads the new file, the bytes stored while
    the page keeps them (``kept.keeps_bytes``: an open comment thread holds
    the version it was opened on, so the outgoing one, stored at the open or
    at the replacement before, is the run's reference), then the
    replacement a file vault's push makes. The bytes may be a library
    page's too: the disk is what it is. SyncError when the page does not
    take them or they cannot be stored, and the row is gone again. A
    replacement stands without its relocation run (past the account's
    background tasks): nobody is there to hear why, so the row keeps it for
    the page (``reader``), and Find highlights in this PDF places them
    later. A PDF's metadata lookup is scheduled."""
    ws, name = sink.ws, stored_name(seen[0], ext)
    _put_row(ws, path, seen, page_id, opener or sink.user_id, ext=ext)
    try:
        if kept.keeps_bytes(ws, page_id) and sink.store(Path(path), ext) != seen[0]:
            raise gamma_sync.SyncError("the file changed while it was read")
        answer = sink.replace(page_id, seen[0], filename, check_held=False, format=format, ext=ext, size=seen[1])
    except gamma_sync.SyncError:
        _drop_row(ws, name, path)
        raise
    reason = answer.get("relocation_error")
    if reason:
        log.warning(f"[linked] {path}: the highlights were not placed: {reason}")
        with connect_vaults_db() as conn:
            conn.execute("UPDATE linked_files SET relocation_error = ? WHERE ws = ? AND name = ? AND path = ?",
                         (reason, ws, name, path))
    if format == "pdf":
        pdf_meta.schedule(ws, seen[0])


def _catch_up(sink: folder_links.LocalSink, name: str, path: str, page_id: str, page: dict, seen: tuple,
              opener: str = "") -> None:
    """The row ``name`` at ``path`` brought up to the file as it is now,
    ``seen``: the same bytes refresh its stat (``_refresh_row``), new ones
    are the page's (``page``, ``LocalSink.current``: of the format and
    extension of its document) and the row moves to their name, naming
    ``opener`` (``_take_bytes``)."""
    if stored_name(seen[0], page["ext"]) == name:
        _refresh_row(sink.ws, name, path, page_id, seen, opener or sink.user_id)
        return
    _take_bytes(sink, page_id, page["filename"] or Path(path).name, path, seen, opener, format=page["format"],
                ext=page["ext"])
    _drop_row(sink.ws, name, path)


def _look(sink: folder_links.LocalSink, name: str, path: str, recorded: int, page_id: str, page: dict,
          opener: str) -> None:
    """The file at ``path``, whose stat moved, its row naming ``opener``. One
    of the size it had (``recorded``) may be the same bytes, only touched,
    which are whole however lately it was written: hashed at once, its stat
    refreshed. Other bytes wait until they have settled, the next look soon
    for a file still being written and the sweep's for one that stopped
    short. A PDF is hashed with its header checked (``hash_file``) and
    settles once it ends as a PDF ends; a file of another format
    (``page``'s document's) has no header or end to wait for, and past the
    keep limit is left as it is (ValueError: Gamma shows no such file)."""
    pdf = page["format"] == "pdf"
    if not pdf and os.stat(path).st_size > config.vault_keep_bytes():
        raise ValueError("the file is too large for Gamma to show here now")

    def read(p):
        return hash_file(p) if pdf else hash_any(p)[:4]
    seen = read(path) if os.stat(path).st_size == recorded else None
    if seen and stored_name(seen[0], page["ext"]) == name:
        _refresh_row(sink.ws, name, path, page_id, seen, opener)
        return
    why = gamma_sync.settled(Path(path)) if pdf else gamma_sync.settled(Path(path), tail=None)
    if why == gamma_sync.WRITING:
        _look_again(sink.ws, name)
    if not why:
        _catch_up(sink, name, path, page_id, page, seen or read(path), opener)


def follow(ws: str, name: str) -> None:
    """What a moved stat comes to, for each path of ``name`` that moved. A
    vault's file is its round's, a page in the trash waits, a row a
    replacement left behind goes, and what cannot be read now, or written
    (nobody may edit the workspace any more, which ``LocalSink.current``
    refuses), stays as it is for the sweep's next look. The row keeps naming
    its opener, whoever the change is written as (``_sink``). A file of any
    format is followed so, under its document's extension."""
    with connect_vaults_db() as conn:
        rows = conn.execute("SELECT path, size, mtime_ns, page_id, actor FROM linked_files "
                            "WHERE ws = ? AND name = ? AND page_id != ''", (ws, name)).fetchall()
    for path, size, mtime_ns, page_id, actor in rows:
        if not _moved(path, size, mtime_ns) or _in_vault(page_id):
            continue
        sink = _sink(ws, actor)
        try:
            page = sink.current(page_id)  # refused when nobody left in the workspace may edit it
            if page is None:
                continue
            if stored_name(page["doc_id"], page["ext"]) != name:
                _drop_row(ws, name, path)
                continue
            _look(sink, name, path, size, page_id, page, actor)
        except (OSError, ValueError, gamma_sync.SyncError) as e:
            log.warning(f"[linked] {path}: {e}")


def _look_again(ws: str, name: str) -> None:
    """The name back on the queue in ``LOOK_AGAIN_S``: a build's PDF is on
    screen as soon as it has settled, not at the next sweep."""
    with _guard:
        if (ws, name) in _again:
            return
        _again.add((ws, name))
    timer = threading.Timer(LOOK_AGAIN_S, _look_due, (ws, name))
    timer.daemon = True
    timer.start()


def _look_due(ws: str, name: str) -> None:
    with _guard:
        _again.discard((ws, name))
        _note_moved(ws, name)


def _note_moved(ws: str, name: str) -> None:
    """``storage.linked_moved``'s entry, inside a read: the name on the queue
    and the worker awake."""
    global _working
    with _guard:
        _waiting.add((ws, name))
        if _working:
            return
        _working = True
    threading.Thread(target=_work, daemon=True, name="linked-files").start()


def _work() -> None:
    global _working, _following
    while True:
        with _guard:
            _following = None
            _guard.notify_all()
            if not _waiting:
                _working = False
                return
            ws, name = _following = _waiting.pop()
        try:
            follow(ws, name)
        except Exception:  # noqa: BLE001: one file never stops the others
            log.exception(f"[linked] following {name} in workspace {ws} failed")


def settle_wait(ws: str, name: str) -> None:
    """Back from a read that found nothing, up to ``SETTLE_WAIT_S``, while the
    worker has ``name`` in hand, so that a second read answers from what it
    found. At once for any name it does not have; the read itself never waits
    (``storage.open_path``)."""
    key, deadline = (ws, name), time.monotonic() + SETTLE_WAIT_S
    with _guard:
        while key in _waiting or key in _again or key == _following:
            left = deadline - time.monotonic()
            if left <= 0:
                return
            _guard.wait(left)


storage.linked_moved.append(_note_moved)


def _block(conn, page_id: str) -> dict | None:
    row = conn.execute(f"SELECT {BLOCK_COLUMNS} FROM unified_blocks WHERE id = ? AND parent_id = 'root'",
                       (page_id,)).fetchone()
    return block_to_dict(row) if row else None


def _opened_files(conn) -> str | None:
    """The page *Opened files*, by its property, oldest first; None before
    the first file is opened."""
    row = conn.execute("SELECT id FROM unified_blocks WHERE parent_id = 'root' "
                       f"AND json_extract(properties, '$.{OPENED_FILES}') IS NOT NULL ORDER BY created_at, id LIMIT 1"
                       ).fetchone()
    return row[0] if row else None


def _top_key(conn, opened: str) -> str:
    """A sibling key before every page inside *Opened files*."""
    first = conn.execute("SELECT MIN(position) FROM unified_blocks WHERE parent_page = ? AND parent_id = 'root'",
                         (opened,)).fetchone()[0]
    return generate_key_between(None, first)


def _to_top(conn, page_id: str, actor: str) -> dict:
    """The page, moved to the top of *Opened files* when it is still inside
    it. Pages have no op that moves them, so this is the row and the change
    log."""
    write_lock(conn)
    opened = _opened_files(conn)
    row = conn.execute("SELECT parent_page FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
    if opened and row[0] == opened:
        conn.execute("UPDATE unified_blocks SET position = ? WHERE id = ?",
                     (free_position(conn, "root", _top_key(conn, opened), page_id), page_id))
        touch_page(conn, page_id, actor)
    conn.commit()
    return _block(conn, page_id)


def _vault_row_page(link: dict, rel: str) -> str:
    """The page the vault's row at ``rel`` names, of any kind ("" for none):
    a paired paper's PDF and notes rows name one page, so either opens it.
    The name is compared as this computer compares names."""
    fold = "lower(path) = lower(?)" if os.path.normcase("A") == "a" else "path = ?"
    with connect_vaults_db() as conn:
        row = conn.execute(f"SELECT page_id FROM vault_files WHERE link_id = ? AND page_id != '' AND was = '' AND {fold} "
                           "ORDER BY path = ? DESC LIMIT 1", (link["id"], rel, rel)).fetchone()
    return row[0] if row else ""


def vault_page(link: dict, rel: str) -> tuple[str, bool]:
    """``(page, trashed)`` of the vault's file ``rel`` (``/`` between names):
    the page its row names (``_vault_row_page``); for a vault of this
    server "" and ``trashed`` when that page is in Recently deleted, "" when
    it is gone for good. A vault of another server's page is its id there,
    as the row has it, unchecked (its workspace is that server's)."""
    page_id = _vault_row_page(link, rel)
    if not page_id or link["remote_url"]:
        return page_id, False
    with connect_pages_db(link["workspace_id"]) as conn:
        row = conn.execute("SELECT parent_id FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
    if row and row[0] == "root":
        return page_id, False
    return "", bool(row) and row[0] == TRASH


def _reads_there(link: dict, rel: str) -> bool:
    """Whether a vault of another server reads its file ``rel``, so pushes
    it there: its row has a page, or it is a work folder whose
    ``.gammaignore`` tracks the file and whose scan may."""
    if _vault_row_page(link, rel):
        return True
    root = folder_links.dest_of(link)
    return (folder_links.work_vault(link) and selection.tracked(root, rel)
            and not selection.never_reason(root, rel)[0])


def _directory_vault_page(path: str, actor: str) -> dict | None:
    """The page of a file inside a directory vault, ``{workspace, page}``,
    when the vault's round reads the file and ``actor`` has a role in the
    vault's workspace. A vault that makes a page of each new file (it pushes
    everything) has a round now for a file it has not read yet that it
    tracks, and a LinkError when that leaves it no page; inside any other
    vault a file Gamma did not read is no page of it, and opens as any file
    does. A LinkError, before any round, for: any file of a work folder
    whose files are not chosen yet; a file whose page is in Recently
    deleted (never restored from here: the round put it there); a file the
    scan never tracks (``selection.never_reason``); a work folder's file it
    does not track (it opens from the work folder once ticked in its
    tracking dialog); a file with no page yet of a paused vault (no round
    runs it from here). A vault of another server that ``actor`` made and
    that reads the file (``_reads_there``: its host's round pushes it
    there) is a LinkError too, a backstop for the desktop app, which asks
    ``folder_links.where_is`` first: one replacer per file. Any other file
    there opens as a lone file, as a file a local kept folder does not read
    does."""
    for link in folder_links.all_links():
        if not folder_links.engine(link) or not folder_links.inside(path, link["path"]):
            continue
        rel = os.path.relpath(path, link["path"]).replace(os.sep, "/")
        if link["remote_url"]:
            if link["created_by"] == actor and _reads_there(link, rel):
                raise folder_links.LinkError(f"{path} is kept on {folder_links.server_name(link)}: open it there.")
            return None
        if not workspaces.role_of(link["workspace_id"], actor):
            return None
        if folder_links.choosing(link):
            raise folder_links.LinkError("Choose what this work folder tracks first.")
        root, name = folder_links.dest_of(link), folder_links.vault_name(link)
        page_id, trashed = vault_page(link, rel)
        if trashed:
            raise folder_links.LinkError(f"{path} is a file of “{name}”, whose page is in Recently deleted: "
                                         "restore it there.")
        if not page_id and link["push"] == folder_links.PUSH_ALL:
            code, words = selection.never_reason(root, rel)
            if code:
                raise folder_links.LinkError(f"Gamma never tracks {path}: {words}.")
            if not selection.tracked(root, rel):
                if folder_links.work_folder_link(link):
                    raise folder_links.LinkError(f"{path} is not tracked by its work folder: tick it in Choose files…")
                return None
            if link["status"].get(folder_links.PAUSED):
                raise folder_links.LinkError(f"{path} is in “{name}”, which is paused: resume it in the sync panel, "
                                             "then open the file again.")
            folder_links.run_link(link["id"], wait=True)
            page_id, _trashed = vault_page(link, rel)
            if not page_id:
                raise folder_links.LinkError(f"{path} is in the synced vault {link['path']}, which has not made it "
                                             "a page yet: try again in a moment.")
        if not page_id:
            return None
        with connect_pages_db(link["workspace_id"]) as conn:
            page = _block(conn, page_id)
        return {"workspace": link["workspace_id"], "page": page} if page else None
    return None


def _file_vault_page(path: str, actor: str) -> dict | None:
    """The page of the file vault at ``path``, ``{workspace, page}``, when the
    page is live and ``actor`` has a role in its workspace. A vault whose
    page is on another server has no page here."""
    with connect_users_db() as conn:
        row = conn.execute("SELECT workspace_id, page_id FROM folder_links "
                           "WHERE kind = 'file' AND remote_url = '' AND path = ?", (path,)).fetchone()
    if not row or not workspaces.role_of(row[0], actor):
        return None
    with connect_pages_db(row[0]) as conn:
        page = _block(conn, row[1])
    return {"workspace": row[0], "page": page} if page else None


def _reopen(sink: folder_links.LocalSink, path: str, rows: dict[str, str], seen: tuple, ext: str = ".pdf") -> dict:
    """The page that holds ``path`` (``rows``, ``_rows_standing``) opened
    again: back from Recently deleted when it is there, with all it held and
    its place in *Opened files*, brought up to the file (``_catch_up``, its
    rows naming its opener still), at the top of *Opened files*. ``ext`` is
    the file's as it was read now. LinkError for a page that does not take
    it."""
    name = stored_name(seen[0], ext)
    page_id = rows.get(name) or next(iter(rows.values()))
    with connect_pages_db(sink.ws) as conn:
        if page_root_id(conn, page_id) != page_id:
            restore_page(sink.ws, conn, page_id, actor=sink.user_id)
    try:
        if not _in_vault(page_id):
            _catch_up(sink, name if name in rows else next(iter(rows)), path, page_id, sink.current(page_id), seen,
                      _opener(sink.ws, page_id, sink.user_id))
    except gamma_sync.SyncError as e:
        raise folder_links.LinkError(str(e)) from e
    with connect_pages_db(sink.ws) as conn:
        return _to_top(conn, page_id, sink.user_id)


def open_file(ws: str, path: str, actor: str) -> dict:
    """The file at ``path``, of any format Gamma can show, opened in place
    → ``{workspace, page (a block dict), created}``. ``path`` is checked as
    a file link's is, of any extension (``folder_links.clean_file_path``),
    and a program is refused (``_no_program``). In this order: a file
    inside a vault opens that vault's page (``_directory_vault_page``, with
    its refusals), a file that is a vault of its own opens that page (either
    may be in another workspace), then the file is read (``_read``: its
    format, a Markdown file as text; a binary or one past the keep limit
    refused), bytes a page of ``ws`` stores open that page
    (``page_for_file``), and the page that holds the path opens, whatever
    became of it (``_reopen``): a page deleted comes back, and bytes that
    changed are its new document. Otherwise a new page inside *Opened
    files*, with the file's stem as its title and no ``auto_title`` (a
    lookup fills fields and never renames), its document of the file's
    format (a PDF's with no size, as ever; any other with its extension and
    size), and a PDF's metadata lookup scheduled. A clone's pages are its
    origin's, so a new one is refused there. LinkError for what cannot be
    opened."""
    from .sync_engine import get_mirror  # local: it imports the routers

    path = folder_links.clean_file_path(path, pdf_only=False)
    _no_program(path)
    vault = _directory_vault_page(path, actor) or _file_vault_page(path, actor)
    if vault:
        return {**vault, "created": False}
    seen, fmt, ext = _read(path)
    with _opening:
        rows = _rows_standing(ws, path)
        with connect_pages_db(ws) as conn:
            stored = page_for_file(conn, seen[0], ext, BLOCK_COLUMNS)
        if stored:
            return {"workspace": ws, "page": block_to_dict(stored), "created": False}
        if rows:
            page = _reopen(folder_links.LocalSink(ws, actor), path, rows, seen, ext)
            return {"workspace": ws, "page": page, "created": False}
        if get_mirror(ws) is not None:
            raise folder_links.LinkError("A clone keeps its origin's pages: open this file in another workspace.")
        shape = {} if fmt == "pdf" else {"format": fmt, "ext": ext, "size": seen[1]}
        with connect_pages_db(ws) as conn:
            opened = _opened_files(conn) or create_page(conn, OPENED_TITLE, {OPENED_FILES: True}, actor=actor)["id"]
            page = create_page(conn, Path(path).stem,
                               {"doc": document(seen[0], name=Path(path).name, at="path", **shape), PARENT_PAGE: opened},
                               actor=actor, position=_top_key(conn, opened))
        _put_row(ws, path, seen, page["id"], actor, ext=ext)
    if fmt == "pdf":
        pdf_meta.schedule(ws, seen[0])
    return {"workspace": ws, "page": page, "created": True}


def _linked_page(ws: str, page_id: str) -> dict:
    """The page ``page_id`` when it reads a file here and no vault's round
    does; LinkError otherwise."""
    with connect_pages_db(ws) as conn:
        page = _block(conn, page_id)
    if not page or not in_place(page_document(page["properties"])):
        raise folder_links.LinkError("That page does not read a file on this computer.")
    if _in_vault(page_id):
        raise folder_links.LinkError("That page's file belongs to its synced vault.")
    return page


def store_linked(ws: str, name: str, path: str) -> None:
    """The file at ``path``, which should be the bytes of the stored file
    ``name`` (``<hash><ext>``), stored: a ``.pdf`` through
    ``store_pdf_stream`` (which schedules its manifest), any other extension
    through ``store_file_stream`` (a ``.webm`` must open as audio or video,
    ``storage.content_refusal``). LinkError for one that cannot be read or
    stored, or is something else by now."""
    ext = os.path.splitext(name)[1]
    try:
        with open(path, "rb") as f:
            if ext == ".pdf":
                stored, _existed, _size = storage.store_pdf_stream(ws, f)
                stored = stored_name(stored, ext)
            else:
                stored, _existed = storage.store_file_stream(ws, f, ext)
    except (OSError, ValueError) as e:
        raise folder_links.LinkError(f"Cannot read {path}: {e}") from e
    if stored != name:
        raise folder_links.LinkError("The file changed while it was read: try again in a moment.")


def store_page_bytes(ws: str, page_id: str, name: str, doc_id: str) -> bool:
    """The page's document, the stored file ``name`` (``<hash><ext>``, of
    any format) of the bytes ``doc_id``, stored from a path this server
    reads it at (one of the page's rows that is there). → False when no path
    here holds it (a vault of another computer, a server that keeps no
    links: vaults.db is never made for the asking); LinkError when the file
    there is other bytes by now."""
    if not config.folder_links_enabled():
        return False
    path = next((p for n, p, _size, _mtime in _rows_of(ws, page_id) if n == name and storage.linked_stat(p)), "")
    if not path:
        return False
    store_linked(ws, name, path)
    return True


def add_to_library(ws: str, page_id: str, folder_id: str, keep_pushing: bool, actor: str, workspace: str = "") -> dict:
    """An opened file becomes a page of the library, here in its workspace:
    its bytes are stored (``store_linked``, under its document's extension;
    the same hash, so the page's document keeps its hash and name), the page
    leaves *Opened files* for ``folder_id`` or the library's top, and it no
    longer reads the file (its document at ``store``). With ``keep_pushing``
    (a PDF's alone: a vault of one file is a PDF's) the file goes on as a
    vault of one file (``create_file_link``), made before the page moves, so
    a refusal leaves the page as it was. The bytes may be a library page's
    already: two pages of one file, as a Zotero link's may be, and when the
    store has them already no file need be there (``keep_pushing`` wants
    one). With ``workspace``, another workspace of this server, the page
    goes there with everything on it (``_carry``) and ``folder_id`` is one
    of that workspace's. Both, and ``keep_pushing``, are the opener's alone
    (``_openers``), as a vault's disk is its maker's: the file is on that
    account's computer, and another editor of the workspace stores its bytes
    here at most. One add runs at a time (``_opening``), so a second press
    finds the page gone, never half carried. → the page. LinkError for a
    page that reads no file, a Markdown file read as text (every ``.md`` of
    the library is a note: dropped on the library it is one), a non-PDF
    with ``keep_pushing``, a folder that does not exist, or a file that is
    gone or changed."""
    with _opening:
        return _add(ws, page_id, folder_id, keep_pushing, actor, workspace or ws)


def _add(ws: str, page_id: str, folder_id: str, keep_pushing: bool, actor: str, target: str) -> dict:
    """``add_to_library``, under ``_opening``, into ``target``."""
    page = _linked_page(ws, page_id)
    shown = page_document(page["properties"])
    if shown["format"] == "text" and shown["ext"] in MARKDOWN_EXTS:
        raise folder_links.LinkError("A Markdown file joins the library as a note: drop it on the library, or open "
                                     "its folder with Gamma to edit it where it is.")
    if keep_pushing and shown["format"] != "pdf":
        raise folder_links.LinkError("Only a PDF keeps pushing from its file.")
    if (keep_pushing or target != ws) and actor not in _openers(ws, page_id):
        raise folder_links.LinkError("Only the account that opened this file may keep it pushing or take it to "
                                     "another workspace: the file is on that account's computer.")
    if target != ws and not workspaces.at_least(workspaces.role_of(target, actor), "editor"):
        raise folder_links.LinkError("You cannot add pages to that workspace.")
    with connect_pages_db(target) as conn:
        if folder_id and folder_id not in folder_paths(conn):
            raise folder_links.LinkError("That folder does not exist.")
    view = page_document(page["properties"])
    name = view["file"]
    rows = _rows_of(ws, page_id)
    path = _at_rest(rows, name)
    if not path and (keep_pushing or not storage.exists(ws, name)):
        raise folder_links.LinkError("The file is gone or has just changed: locate it, or try again in a moment.")
    if target != ws:
        return _carry(ws, page, target, folder_id, keep_pushing, path, actor)
    if not storage.exists(ws, name):
        store_linked(ws, name, path)
    link = folder_links.create_file_link(ws, page_id, path, actor) if keep_pushing else None
    commit_ops(ws, page_id, [{"op": "set", "id": page_id, "props": {
        "doc": document_at(view, "store"), PARENT_PAGE: None, FOLDERS: [folder_id] if folder_id else None}}], actor=actor)
    for row_name, row_path, _size, _mtime_ns in rows:
        _drop_row(ws, row_name, row_path)
    if link:
        folder_links.run_in_background(link["id"])
    with connect_pages_db(ws) as conn:
        return _block(conn, page_id)


def _carried(conn, pages: list[str]) -> str:
    """What a carry takes of ``pages`` (a page and the pages inside it,
    ``with_sub_pages``), as a digest: their list, their blocks, the comments
    shown on them and their chats, what a Gamma export of them reads
    (``page_gamma_zip``). A carry reads it before the export (``_carry``
    here, the desktop app's carry to another server through
    ``carry_digest``) and ``_let_go`` again under the write lock that moves
    them to Recently deleted here, which moves nothing that changed in
    between; and ``_carry`` of the pages its merge added to the other
    workspace, just after the merge and again under the write lock that
    takes them back out there (``_take_back``)."""
    each = json.dumps(pages)
    held = [pages, comments.rows_of_pages(conn, pages)]
    for table, key in (("unified_blocks", "page_id"), ("chats", "bucket"), ("chat_history", "bucket")):
        held += conn.execute(f"SELECT * FROM {table} WHERE {key} IN (SELECT value FROM json_each(?)) ORDER BY rowid",
                             (each,)).fetchall()
    return hashlib.sha256(repr(held).encode()).hexdigest()


def _made_rows(conn, made: list) -> dict:
    """``{id: row}`` of the folder and label blocks ``made`` as they stand."""
    return {r[0]: r for r in conn.execute(
        "SELECT id, page_id, parent_id, position, content, properties, updated_at FROM unified_blocks "
        "WHERE id IN (SELECT value FROM json_each(?))", (json.dumps(made),))}


def _unmade(conn, made: list, added: list, as_merged: dict) -> dict[str, list[str]]:
    """The folders and labels a carry's merge made (``made``; ``as_merged``,
    their ``_made_rows`` just after it) that go back out with its pages
    ``added``, as ``{tree: the top ones}``: all but those something else came
    to use meanwhile, renamed or moved, given a folder or label of their
    own, or holding a page other than ``added``, which stay with the ones
    above them."""
    rows, each = _made_rows(conn, made), json.dumps(made)
    used = {bid for bid, row in rows.items() if row != as_merged.get(bid)}
    used |= {r[0] for r in conn.execute("SELECT parent_id FROM unified_blocks WHERE parent_id IN "
                                        "(SELECT value FROM json_each(?)) AND id NOT IN (SELECT value FROM json_each(?))",
                                        (each, each))}
    for tree in TREES:
        used |= {r[0] for r in conn.execute(
            f"SELECT f.value FROM unified_blocks p, json_each(p.properties, '$.{tree}') f "
            "WHERE p.parent_id IN ('root', ?) AND p.id NOT IN (SELECT value FROM json_each(?)) "
            "AND f.value IN (SELECT value FROM json_each(?))", (TRASH, json.dumps(added), each))}
    kept = set()
    for bid in used:
        while bid in rows and bid not in kept:
            kept.add(bid)
            bid = rows[bid][2]
    gone = set(rows) - kept
    tops: dict[str, list[str]] = {}
    for bid in sorted(gone):
        if rows[bid][2] not in gone:
            tops.setdefault(rows[bid][1], []).append(bid)
    return tops


def _take_back(target: str, merged: dict, as_merged: tuple, actor: str) -> bool:
    """The pages a carry's merge added to ``target`` (its ``added``) deleted
    there for good, those alone (a page inside one of them that came another
    way stays, ``purge_pages``), and the folders and labels it made for
    them (``trees_added``) that nothing else came to use (``_unmade``), when
    the pages are as the merge left them (``as_merged``: their ``_carried``
    and those blocks' ``_made_rows`` just after it), checked under the
    write lock that deletes them: the copy goes, nothing of it stays there,
    and all it held is still in the workspace the carry left as it was.
    Pages someone wrote on there meanwhile stay there whole, filed as they
    are, and False says so (``IN_BOTH``). Trashing them instead would keep
    a copy in that workspace's Recently deleted that every member there sees
    and that refuses the next carry of the page (``HELD_THERE``); let by,
    that carry's merge would delete it for good (``ws_backup._merge``
    replaces a page only in Recently deleted)."""
    added, made = merged["added"], merged["trees_added"]
    if not added and not made:
        return True
    pages_as, made_as = as_merged
    with connect_pages_db(target) as conn:
        write_lock(conn)
        if _carried(conn, added) != pages_as:
            conn.rollback()
            return False
        results = apply_batches(conn, [(tree, [{"op": "delete", "id": bid} for bid in tops])
                                       for tree, tops in _unmade(conn, made, added, made_as).items()], actor=actor)
        purge_pages(target, conn, added, actor=actor)  # commits the trees' batches with it
        after_commits(target, conn, results)
    return True


def _titled(conn, pages: list[str]) -> dict[str, tuple[str, dict]]:
    """``{id: (title, properties)}`` of ``pages``."""
    return {pid: (title, load_props(props)) for pid, title, props in conn.execute(
        "SELECT id, content, properties FROM unified_blocks WHERE id IN (SELECT value FROM json_each(?))",
        (json.dumps(pages),))}


def _inside_at_hand(ws: str, page_id: str, pages: dict) -> dict[str, str]:
    """``{stored name: the file its bytes are read from}`` of the pages
    inside the opened one (``pages``, ``{id: (title, properties)}``, the
    opened page among them) that read a file here too: the path their rows
    hold it at as it was hashed, else the stored copy. LinkError for one
    whose file is neither, gone or changed since: the export would read it
    as nothing, and the page would go there without its document and with
    no path to find one by, while here it went to Recently deleted without
    its rows."""
    at_hand = {}
    for pid, (title, props) in pages.items():
        view = page_document(props)
        if pid == page_id or not own_hash(view) or not in_place(view):
            continue
        name = view["file"]
        at = _at_rest(_rows_of(ws, pid), name)
        if not at and not storage.exists(ws, name):
            raise folder_links.LinkError(f"The file of the page inside it, {title}, is gone or has just changed: "
                                         "locate it there, or try again in a moment.")
        at_hand[name] = at or str(storage.find_upload_file(name, ws))
    return at_hand


def _drop_rows(ws: str, pages: list[str]) -> None:
    """The ``linked_files`` rows of ``pages``."""
    with connect_vaults_db() as conn:
        conn.execute("DELETE FROM linked_files WHERE ws = ? AND page_id IN (SELECT value FROM json_each(?))",
                     (ws, json.dumps(pages)))


def _let_go(ws: str, page_id: str, digest: str, actor: str) -> bool:
    """The opened page and the pages inside it, carried elsewhere, to
    Recently deleted here (``trash_page``), all they held kept 30 days for a
    restore, when they are still what ``digest`` (``_carried``, read before
    the carry's export) says, checked under the write lock that moves them;
    once they are there their rows go (vaults.db is never written inside the
    write lock): the file opened again is a new page, not the one in
    Recently deleted back (``_rows_standing``). False, and nothing touched,
    for pages written on since: the copy elsewhere lacks what was written.
    The caller holds ``_opening``."""
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        pages = with_sub_pages(conn, [page_id])  # the digest holds the list: a page made or moved inside changes it
        if _carried(conn, pages) != digest:
            conn.rollback()
            return False
        trash_page(ws, conn, page_id, actor=actor)  # with its ``page_descendants``, read under this lock: ``pages``
    _drop_rows(ws, pages)
    return True


def _library_holds(conn, view) -> bool:
    """Whether a library page of the workspace of ``conn`` owns the bytes
    the document ``view`` owns, of its extension (``page_for_file``)."""
    own = own_hash(view)
    return bool(own and page_for_file(conn, own, view["ext"]))


def _carry(ws: str, page: dict, target: str, folder_id: str, keep_pushing: bool, path: str, actor: str) -> dict:
    """``add_to_library`` into ``target``, another workspace of this server:
    the bytes stored there, and those of each page inside it that reads a
    file here too (``_inside_at_hand``), then the page and the pages inside
    it exported as a Gamma export is and merged in as an import is
    (docs/dev/import_export.md "Gamma-to-Gamma export"), with their notes,
    highlights, comments and chats, out of *Opened files* and filed in
    ``folder_id``; with ``keep_pushing`` the file goes on as a vault of one
    file there. Then the pages carried go to Recently deleted here without
    their rows (``_let_go``). ``path`` is the file as its
    rows read it ("" when only the store has the bytes). LinkError for a
    clone, a workspace that has the page or a page of these bytes already,
    or a page inside it whose file is gone or changed, checked before the
    export. What goes wrong after the merge takes back out the pages it
    added and the folders and labels it made for them, by the merge's own
    lists (``added``, ``trees_added``, and nothing else there:
    ``_take_back``), and leaves everything here as
    it is: a merge that still skips a page (another import brought it
    meanwhile), a refused vault, and pages here written on since the export
    (``_carried``, compared under the write lock that moves them), whose new
    notes, pages, comments or chats the other workspace does not have."""
    from .routers.export import page_gamma_zip  # local: keep gamma.* free of the routers package
    from .sync_engine import get_mirror  # local: it imports the routers

    page_id, name = page["id"], page_document(page["properties"])["file"]
    if get_mirror(target) is not None:
        raise folder_links.LinkError("A clone keeps its origin's pages: add this file to another workspace.")
    with connect_pages_db(ws) as conn:
        pages = with_sub_pages(conn, [page_id])
        before = _carried(conn, pages)
        each = _titled(conn, pages)
    with connect_pages_db(target) as conn:
        held = [pid for pid, (_title, props) in each.items()
                if conn.execute("SELECT 1 FROM unified_blocks WHERE id = ?", (pid,)).fetchone()
                or _library_holds(conn, page_document(props))]
    if held:
        raise folder_links.LinkError(HELD_THERE)
    files = {**_inside_at_hand(ws, page_id, each), name: path or str(storage.find_upload_file(name, ws))}
    for stored, at in files.items():  # stored there before the export, which reads a file that moved as nothing
        if not storage.exists(target, stored):
            store_linked(target, stored, at)
    with tempfile.TemporaryDirectory(prefix="gamma-carry-") as td:
        zpath = Path(td) / "page.zip"
        page_gamma_zip(ws, page_id, zpath, {PARENT_PAGE: None, FOLDERS: [folder_id] if folder_id else None})
        try:
            merged = ws_backup.restore_zip(target, zpath, "merge", by=actor)
        except ws_backup.BackupError as e:
            raise folder_links.LinkError(str(e)) from e
    with connect_pages_db(target) as conn:
        as_merged = (_carried(conn, merged["added"]), _made_rows(conn, merged["trees_added"]))
    if merged["pages_skipped"] or not merged["pages_added"]:
        # a page came to be there meanwhile, and stays: what this merge added goes back out, nothing here is touched
        raise folder_links.LinkError(HELD_THERE if _take_back(target, merged, as_merged, actor) else IN_BOTH)
    link = None
    if keep_pushing:
        try:
            link = folder_links.create_file_link(target, page_id, path, actor)
        except folder_links.LinkError as e:
            if not _take_back(target, merged, as_merged, actor):
                raise folder_links.LinkError(IN_BOTH) from e
            raise
    if not _let_go(ws, page_id, before, actor):
        if link:
            folder_links.delete_link(link["id"])
        raise folder_links.LinkError(CHANGED_HERE if _take_back(target, merged, as_merged, actor) else IN_BOTH)
    if link:
        folder_links.run_in_background(link["id"])
    with connect_pages_db(target) as conn:
        return _block(conn, page_id)


def _opened_by(ws: str, page_id: str, actor: str) -> None:
    """LinkError unless the page reads a PDF here (``_linked_page``: the
    desktop app's carry to another server takes a PDF alone) and ``actor``
    opened it (``_openers``): taking the page to another server is that
    account's alone, as taking it to another workspace here is
    (``add_to_library``)."""
    page = _linked_page(ws, page_id)
    if page_document(page["properties"])["format"] != "pdf":
        raise folder_links.LinkError("Only a PDF is carried to another server.")
    if actor not in _openers(ws, page_id):
        raise folder_links.LinkError("Only the account that opened this file may take it to another server: the "
                                     "file is on that account's computer.")


def carry_digest(ws: str, page_id: str, actor: str) -> tuple[str, list[str]]:
    """What carrying the opened page to another server takes of it, the
    page and the pages inside it: ``(_carried's digest, their ids)``, the
    page first. The desktop app reads it before its export (``carryPage``),
    looks for each page on the other server (one there, live or in Recently
    deleted, would not go, or the merge would put the copy in its place and
    delete it for good: ``ws_backup._merge``), and hands the digest back to
    ``let_go`` once the page is there. LinkError as ``_opened_by``, and for a
    page inside it whose file is gone or changed (``_inside_at_hand``): the
    export would carry that page without its PDF, as for a carry to another
    workspace here."""
    _opened_by(ws, page_id, actor)
    with connect_pages_db(ws) as conn:
        pages = with_sub_pages(conn, [page_id])
        digest, each = _carried(conn, pages), _titled(conn, pages)
    _inside_at_hand(ws, page_id, each)
    return digest, pages


def let_go(ws: str, page_id: str, digest: str, actor: str) -> bool:
    """The opened page, which the desktop app carried to another server
    (its export merged in there), let go here as a carry to another
    workspace lets it go (``_let_go``): to Recently deleted with the pages
    inside it, and their rows dropped, when they are still what ``digest``
    (``carry_digest``, read before the export) says. False, and nothing
    touched, for pages written on since: the copy there lacks what was
    written, so the page stays here whole, in both places now. One add or
    let-go runs at a time (``_opening``), so a second press finds the page
    gone. LinkError as ``_opened_by``."""
    with _opening:
        _opened_by(ws, page_id, actor)
        return _let_go(ws, page_id, digest, actor)


def locate(ws: str, page_id: str, path: str, actor: str) -> dict:
    """An opened file found again at ``path`` (checked and read as
    ``open_file`` checks and reads one), of the format of the page's
    document: the same bytes are the page's as they stand, others replace
    its document, as ``actor``, and its old rows go, the new one naming the
    page's opener still (``_opener``). → the page. LinkError for a page that
    reads no file, a file that cannot be opened or is of another format, or
    one that is another page's, whatever it reads now and even from Recently
    deleted."""
    page = _linked_page(ws, page_id)
    shown = page_document(page["properties"])
    path = folder_links.clean_file_path(path, pdf_only=False)
    seen, fmt, ext = _read(path)
    if fmt != shown["format"]:
        raise folder_links.LinkError(f"Pick {_A_FORMAT.get(shown['format'], 'a ' + shown['format'])} file: this page "
                                     "reads one.")
    same = seen[0] == own_hash(shown)
    if same:
        ext = shown["ext"]   # its name is the page's document's
    name = stored_name(seen[0], ext)
    with _opening:
        if any(holder != page_id for holder in _rows_standing(ws, path).values()):
            raise folder_links.LinkError("Another opened page reads that file.")
        rows, opener = _rows_of(ws, page_id), _opener(ws, page_id, actor)
        if same:
            _put_row(ws, path, seen, page_id, opener, ext=ext)
        else:
            try:
                _take_bytes(folder_links.LocalSink(ws, actor), page_id, Path(path).name, path, seen, opener,
                            format=fmt, ext=ext)
            except gamma_sync.SyncError as e:
                raise folder_links.LinkError(str(e)) from e
        for old, old_path, _size, _mtime_ns in rows:
            if (old, old_path) != (name, path):
                _drop_row(ws, old, old_path)
    return _linked_page(ws, page_id)


def missing(ws: str, page_id: str) -> bool:
    """Whether the page has lost its file: no copy of its bytes (its
    document's stored name, of any format) is stored here and none of its
    paths holds a file. Another page's file of the same bytes does not
    count: it goes its own way."""
    with connect_pages_db(ws) as conn:
        row = conn.execute("SELECT properties FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
    view = page_document(load_props(row[0])) if row else None
    if view and view["file"] and storage.exists(ws, view["file"]):
        return False
    return not any(storage.linked_stat(path) for _name, path, _size, _mtime_ns in _rows_of(ws, page_id))


def reader(ws: str, page_id: str, actor: str = "") -> dict | None:
    """``{path, missing, mine, format, ext}`` of a page that reads a file
    here (``path`` "" when a crash left it no row; ``mine`` whether
    ``actor`` opened the file, the account whose alone it is to keep pushing
    or take elsewhere, ``add_to_library``; ``format`` and ``ext`` its
    document's, by which the client hides what a page of another format
    than PDF, or a Markdown file read as text, cannot do), and
    ``relocation_error`` when its last new bytes placed no highlights
    (``_take_bytes``); None for any other page."""
    with connect_pages_db(ws) as conn:
        row = conn.execute("SELECT properties FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
    view = page_document(load_props(row[0])) if row else None
    if not in_place(view):
        return None
    paths = [path for _name, path, _size, _mtime_ns in _rows_of(ws, page_id)]
    state = {"path": next((p for p in paths if storage.linked_stat(p)), paths[0] if paths else ""),
             "missing": missing(ws, page_id), "mine": bool(actor) and actor in _openers(ws, page_id),
             "format": view["format"], "ext": view["ext"]}
    with connect_vaults_db() as conn:
        reason = conn.execute("SELECT relocation_error FROM linked_files WHERE ws = ? AND page_id = ? "
                              "AND relocation_error != '' LIMIT 1", (ws, page_id)).fetchone()
    return {**state, "relocation_error": reason[0]} if reason else state


def _open_now(ws: str, page_id: str) -> bool:
    """Whether someone has the page open: reading touches nothing, so its date
    does not say."""
    room = collab.room_for(ws, page_id)
    return bool(room and room.peers)


def _forget(ws: str, page_ids: list[str]) -> None:
    """The pages inside *Opened files* with no mark, no comment and no chat,
    untouched for ``FORGET_DAYS`` and open nowhere, go for good: nothing of
    theirs is lost, the file is where it was. Each is looked at again under
    the write lock, so a mark made since the list was read keeps its page.
    Then a row whose page no longer exists goes. A page in Recently deleted
    keeps its row, for a restore."""
    cutoff = format_stamp(datetime.now(timezone.utc) - timedelta(days=FORGET_DAYS))
    with connect_pages_db(ws) as conn:
        opened = _opened_files(conn)
        idle = conn.execute(_IDLE, (opened, cutoff)).fetchall() if opened else []
        for row in idle:
            if _open_now(ws, row[0]):
                continue
            write_lock(conn)
            if conn.execute(f"{_IDLE} AND p.id = ?", (opened, cutoff, row[0])).fetchone():
                delete_page(ws, conn, row[0], actor="")
            else:
                conn.rollback()  # nothing to write: let go of the lock
        left = {r[0] for r in conn.execute("SELECT id FROM unified_blocks WHERE id IN (SELECT value FROM json_each(?))",
                                           (json.dumps(page_ids),))}
    with connect_vaults_db() as conn:
        conn.executemany("DELETE FROM linked_files WHERE ws = ? AND page_id = ?",
                         [(ws, page_id) for page_id in page_ids if page_id not in left])


def sweep() -> None:
    """A round of the links' tick (``folder_links.tick``, so only where links
    are kept): per workspace with rows what ``_forget`` lets go and the
    bytes of a page with an open comment thread that are not stored yet
    (``kept.repair``), the rows of a workspace that is gone, then every
    row's stat, a moved one queued as a read would queue it. A read may
    never come for a file an open viewer holds, and a file that was still
    being written has nobody else to look at it again. A row without a page
    is a vault's to keep. A workspace that cannot be opened stops neither
    the others nor the stat pass."""
    with connect_vaults_db() as conn:
        rows = conn.execute("SELECT ws, name, path, size, mtime_ns, page_id FROM linked_files WHERE page_id != ''"
                            ).fetchall()
    for ws in sorted({row[0] for row in rows}):
        try:
            if workspaces.get(ws):
                pages = sorted({row[5] for row in rows if row[0] == ws})
                _forget(ws, pages)
                kept.repair(ws, pages)
            else:
                with connect_vaults_db() as conn:
                    conn.execute("DELETE FROM linked_files WHERE ws = ?", (ws,))
        except Exception:  # noqa: BLE001: one workspace never stops the others
            log.exception(f"[linked] the sweep of workspace {ws} failed")
    for ws, name, path, size, mtime_ns, _page_id in rows:
        if _moved(path, size, mtime_ns):
            _note_moved(ws, name)
