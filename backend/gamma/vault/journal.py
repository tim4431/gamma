"""What a round that died left in the journal, settled when the next one opens
(``_recover``, then ``_recover_moved`` once the scan is read).

**Crash safety** is a journal (gamma/vault/store.py): before any effect a
file's row holds ``pending``, what its base is about to be, under a token;
after the effect it is promoted. A push names its first batch's log row with
the token (``page_ops.batch_id``), and a round that finds the pending
promotes it when that row is there or, where the log no longer says, the page
holds what the plan wrote (a block it made, or each block it wrote as it
wrote it), and drops it otherwise, so that the file is read again; one whose
page is in Recently deleted on another server, which serves no log of it,
waits for the push that brings the page back (``pages._settled_push``); a new page
is there or not, or there without its blocks (a remote over HTTP makes the
page, then its blocks), and then the note's page all the same, its blocks
merged into it from no base; a file pull wrote holds the pending's hash, or
is still the file the write put there (the file id the pending names, or a
text nearer the pending's than the base's after a safe save: a person saved
over it since, which the scan then reads), or is that file moved (the scan
finds the file id elsewhere, and reads the move), or neither, and the bytes a
write replaced, kept beside the note until the history has them, go to the
history; a file on its way to the vault's trash is where the pending says or
where it was. Neither: the old base stays, and the worst is a block read
twice, never one lost. A pending that cannot be settled waits for a later
round, the vault going on. A move on disk holds its rows' pending until each
page is filed and named by it. The ids of new blocks are minted from the
token, and a plan is written as the journal named it, never built again under
the lock: a page Gamma wrote in between is planned again outside it.
"""

import os
import time

from ..documents.model import is_listed, page_document
from ..logbuf import log
from . import disk, kinds, store
from .remote import Refused


def _carries(row: dict, pending: dict, data: bytes) -> bool:
    """Whether the note a pull's write (``pending``) went into, saved by a
    person since, still holds that write: its text nearer the pending's than
    the base's (``disk.nearer``)."""
    if row["kind"] != kinds.NOTES.name or not pending.get("text"):
        return False
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return disk.nearer(text, pending["text"], row.get("text") or "")


def _recover(rnd) -> None:
    """What a round that died left in the journal, settled by what the page
    and the file say now (the module doc)."""
    for row in store.pending(rnd.id):
        path, pending = row["path"], row["pending"]
        op, token = pending.get("op"), pending["token"]
        fields = pending.get("fields") or {}
        try:
            if op == "push":
                page_id = pending.get("page_id")
                done = page_id and (_doc_landed(rnd, page_id, pending["hash"], fields.get("size"))
                                    if kinds.by_kind(row["kind"]).in_place else _plan_landed(rnd, page_id, pending))
                if done:
                    store.promote(rnd.id, path, token, **fields)
                elif done is not None or not page_id:     # None: settled when the push brings the page back
                    store.drop(rnd.id, path, token)
            elif op == "create":
                _settle_create(rnd, path, pending)
            elif op in ("pull", "move"):
                _recover_write(rnd, row, path, pending, fields)
            elif op == "moved":
                rnd.refiles.append((path, pending["from"], token))      # once the rows are read (``sync._open``)
            elif op == "trash":
                _recover_trash(rnd, row, path, pending)
            elif op == "restore":
                _recover_restore(rnd, path, pending)
            else:
                store.drop(rnd.id, path, token)
        except (disk.Stopped, disk.Offline, Refused):
            raise
        except Exception as e:  # noqa: BLE001: a pending that cannot be settled yet waits; the vault goes on
            log.warning(f"[vault] {path}: the journal could not be settled yet: {e!r}")


def _doc_landed(rnd, page_id: str, hash: str, size) -> bool:
    """Whether a push of a document read in place landed: the page's
    document is the bytes ``hash``; for a listed file (no hash: a work
    folder's file past the keep limit, pushed as a document of ``size``
    with no bytes) the page's document is listed at that size."""
    if rnd.remote.doc_of(page_id) != hash:
        return False
    if hash:
        return True
    page = rnd.remote.page(page_id)
    view = page_document(page.blocks[page_id]["props"]) if page and page_id in page.blocks else None
    return is_listed(view) and view["size"] == size


def _plan_landed(rnd, page_id: str, pending: dict) -> bool | None:
    """Whether the push the journal names by ``pending["token"]`` is on the
    page: its batch in the page's log, read from the seq the plan was made
    at (``pending["at"]``) or, in this server's log, among rows as old as
    the note's read; where the log cannot say (the batch pruned, too much
    after it to read over HTTP), the page itself: a block the plan made
    there (``round._named``: its id is the token's), or each block it wrote as it
    wrote it (``pending["wrote"]``, ``pages._wrote``: the text it set, gone for
    one it deleted), whatever Gamma wrote on other blocks since. One whose
    blocks were written on since reads as not landed, and the merge from
    the old base finds the change in them (``merge.file_wins`` makes
    a change once that the text holds). None for a page in Recently
    deleted on a server whose log of it is not served: dropped, a plan that
    only set text would be merged into once more from the old base, its
    words twice, so the push that brings the page back settles it
    (``pages._push_note``)."""
    token, read_at = pending["token"], (pending.get("fields") or {}).get("read_at", 0.0)
    landed = rnd.remote.landed(page_id, token, pending.get("at"), read_at)
    if landed is not None:
        return landed
    page = rnd.remote.page(page_id)
    if page is None:
        return False
    if page.trashed and not page.blocks:      # over HTTP a page in Recently deleted shows none
        return None
    made = next((n["id"] for n in pending.get("nodes") or () if str(n["id"]).startswith(token)), None)
    wrote = pending.get("wrote") or {}
    return made is not None and made in page.blocks or bool(wrote) and all(
        block_id not in page.blocks if text is None else page.blocks.get(block_id, {}).get("content") == text
        for block_id, text in wrote.items())


def _settle_create(rnd, path: str, pending: dict) -> None:
    """A create's pending settled by what the remote holds (``_made``):
    promoted, dropped, or, for a page made without its blocks, a row of
    that page with no base, so that the scan reads the note as changed and
    merges its blocks into it, and pull writes nothing for it meanwhile."""
    made, token, fields = _made(rnd, pending), pending["token"], pending.get("fields") or {}
    if made == "made":
        store.promote(rnd.id, path, token, **fields)
    elif made == "empty":
        store.put(rnd.id, path, page_id=pending["page_id"], hash="", text=None, nodes=None, size=0,
                        mtime_ns=0, file_id="", read_at=0.0, title=fields.get("title"), folder=fields.get("folder"),
                        seq=-1, ours=0, pending=None)
    else:
        store.drop(rnd.id, path, token)
        rnd.rows.pop(path, None)
        return
    rnd._promoted(path)


def _made(rnd, pending: dict) -> str:
    """Whether the page a create named was made: ``made`` with its
    blocks (live with a note's blocks, it holds the first of them),
    ``empty`` without (a remote over HTTP makes the page, then its
    blocks: the page stays the note's, and the scan reads the note as
    changed from no base and merges every block into it, never into
    another page made each round), "" for none."""
    page_id, nodes = pending.get("page_id"), pending.get("nodes") or []
    state = rnd.remote.state(page_id) if page_id else None
    if not state:
        return ""
    if state != "live" or not nodes or rnd.remote.page_of(nodes[0]["id"]) == page_id:
        return "made"
    return "empty"


def _recover_write(rnd, row: dict, path: str, pending: dict, fields: dict) -> None:
    """A file pull wrote or renamed: its bytes say which base stands. Other
    bytes in the file the write put there, by the file id the journal
    named (``disk.replace_note``'s ``ready``), or a text nearer the
    pending's than the base's (a safe save gave the file another id), are
    a person's save since: the pending is the base all the same, at the
    stat the write left, so that the scan reads the save and pushes it as
    a change, never Gamma's lines a second time. A file gone from where it
    was written waits for the scan (``_recover_moved``). What the write
    replaced, kept beside the note, goes to the history first."""
    _kept_history(rnd, path, pending.get("kept") or "", row["hash"])
    at = pending.get("path") or path
    try:
        data = disk.read(rnd.root, at)
    except FileNotFoundError:
        if pending["op"] == "pull" and pending.get("file_id"):
            rnd.unsettled.append((path, pending))
        else:
            store.drop(rnd.id, path, pending["token"])
        return
    if pending["op"] == "move":       # the file is at its new name, or the rename never happened
        if disk.lstat(rnd.root, path) is not None:
            store.drop(rnd.id, path, pending["token"])
        else:
            store.promote(rnd.id, path, pending["token"])
        return
    st = os.stat(disk.path(rnd.root, at))
    if disk.sha(data) == pending["hash"]:
        store.promote(rnd.id, path, pending["token"], **fields, size=st.st_size, mtime_ns=st.st_mtime_ns,
                            file_id=str(st.st_ino), read_at=time.time())
    elif pending.get("file_id") and str(st.st_ino) == pending["file_id"] or _carries(row, pending, data):
        _landed(rnd, path, pending)
    else:
        store.drop(rnd.id, path, pending["token"])


def _landed(rnd, path: str, pending: dict) -> None:
    """A pull's write known to have landed though the file holds other
    bytes now: the pending is the base, at the stat the write left."""
    store.promote(rnd.id, path, pending["token"], **(pending.get("fields") or {}), size=pending["size"],
                        mtime_ns=pending["mtime_ns"], file_id=pending["file_id"], read_at=pending["mtime_ns"] / 1e9)


def _recover_moved(rnd) -> None:
    """Pull's writes whose file was not where they put it when the journal
    was settled: a person moved it before this round. The file the scan
    finds with the file id the write put there means the write landed, so
    its pending is the base where it was written and the scan reads the
    move; with none, it is dropped."""
    ids = {found.file_id for found in rnd.scan.files.values() if found.file_id}
    for path, pending in rnd.unsettled:
        if pending["file_id"] in ids:
            _landed(rnd, path, pending)
        else:
            store.drop(rnd.id, path, pending["token"])
    rnd.unsettled.clear()


def _kept_history(rnd, path: str, kept: str, base: str) -> None:
    """The bytes a write that died had replaced and kept beside the note
    (``disk.replace_note``'s ``hold``), into the history (always
    when they were not the base: a person's save), and the copy gone."""
    if not kept:
        return
    try:
        data = disk.read(rnd.root, kept)
    except FileNotFoundError:
        return
    store.keep(rnd.id, path, data, always=disk.sha(data) != base)
    disk.drop_temp(rnd.root, kept)


def _recover_trash(rnd, row: dict, path: str, pending: dict) -> None:
    """Files on their way to the trash finish the way, and a page whose
    files all went from the disk goes to Recently deleted."""
    if not row["was"]:
        store.drop(rnd.id, path, pending["token"])     # never buried: decided again
        return
    if os.path.lexists(rnd.root / row["was"]) and not os.path.lexists(rnd.root / path):
        disk.move(rnd.root, row["was"], path)
        rnd.remote.relink(str(rnd.root / row["was"]), str(rnd.root / path))
    if pending.get("disk") and row["page_id"]:
        rnd.remote.trash(row["page_id"])
    store.put(rnd.id, path, pending=None)


def _recover_restore(rnd, path: str, pending: dict) -> None:
    target = pending["path"]
    if os.path.lexists(rnd.root / path) and not os.path.lexists(rnd.root / target):
        disk.move(rnd.root, path, target)
    if os.path.lexists(rnd.root / target):
        rnd.remote.relink(str(rnd.root / path), str(rnd.root / target))
        store.unbury(rnd.id, path, target)
        store.put(rnd.id, target, pending=None)
    else:
        store.put(rnd.id, path, pending=None)
