"""PDFs in a Zotero link's round (see docs/dev/zotero_sync.md#pdfs). An
item's PDF is its primary attachment as Zotero's reader picks it, its file
kept the same on both sides by MD5. ``UnitPdf``, the PDF part of an item's
unit (gamma/zotero_sync_items.py mixes it in), decides what becomes of the
page's PDF and that attachment once the record is decided (``_file_plan``:
bound, downloaded, uploaded in place, sent as a new attachment beside a
file whose annotations Gamma cannot move, or left waiting with its reason,
Gamma's own while the page's highlights are being placed in it) and
queues the transfer. A new attachment keeps the PDF it went beside, so the
page can become that PDF's again (``_back_beside``: "Take Zotero's"). A
file entry's own unit (``unit_file``) decides it again on what both sides
hold then and moves the file: a download (spared when the workspace holds
the same file already), an upload in place or into an attachment Gamma
made; when Gamma's file goes over Zotero's, or beside a file Zotero
replaced too, Zotero's is kept here first, for a ``zotero_file`` conflict
(gamma/zotero_sync_resolve.py takes it back), which an upload whose answer
was lost keeps once Zotero holds Gamma's file (``_went``). The end of a
relocation asks for the PDFs that waited on it again (``relocation_ended``).
Zotero's full storage (413) holds every PDF for a while (``quota_step``).

Of the engine's parts it imports only gamma/zotero_sync_reads.py and
gamma/zotero_sync_rows.py.
"""

import json
from datetime import datetime, timedelta, timezone

from . import jobs, storage, sync_clock, zotero_local, zotero_merge
from . import zotero_remote as zr
from .blocks_store import attachment_file, load_props, page_attachment, write_lock
from .db import connect_pages_db, format_stamp, page_now
from .logbuf import log
from .ops import OpError
from .zotero_annotations import file_md5, hold_reason
from .zotero_sync_reads import HELD_ROWS, Side, entries_done, file_sig, keep_ahead, page_side, read_ahead
from .zotero_sync_rows import (ENGINE, Round, drop_rows, get_link, new_row, page_note, patch_status, read_rows,
                               record_conflict, without, without_quota_wait, write_rows)
from .zotero_web import ZoteroAuthError, ZoteroNotFound, object_key

NEWEST_FILES = 50          # "When opened": the PDFs of this many newest items come without being opened
FILENAME_TITLE_MAX = 120   # characters of a page's title a PDF sent without a file name of its own is named by
QUOTA_WAIT_S = 24 * 3600   # after Zotero's 413 no PDF goes until Sync now, a settings change or this long
STORAGE_RETRY_S = 300      # after this server's 507 a download waits this long before it is tried again
STORAGE_WAIT = "this server's storage is full"  # the reason of a file's queue entry while it waits so
UPLOAD_TITLE = "Full Text PDF"  # the title Zotero gives a PDF it finds for an item, and Gamma one it sends
PLACING_RECHECK_S = 30     # a PDF waiting for its page's highlights to be placed is looked at again this often
FILE_PLACING = "placing"   # that PDF's reason for waiting (``detail.file``, ``status.waiting.files``)
PLACING_WAIT = "its highlights are being placed"  # ...and its queue entry's, while it is held back
COPY_TRIES = 3             # downloads that went wrong of Zotero's file before Gamma's goes beside it without a copy
BESIDE_RESULT = "The PDF was replaced on both sides: Gamma's went to Zotero as a new attachment beside Zotero's"
# Why no copy of Zotero's file is kept here (a ``both_new`` conflict's result), by its reason for waiting
_UNKEPT = {"linked": "it is a linked file, which Zotero Storage does not hold",
           "unavailable": "Zotero Storage holds no copy of it", "no_access": "the key may not read files",
           "failed": "it could not be downloaded"}
# A PDF's reason for waiting (``detail.file`` of its row, ``status.waiting.files``) by what kept it
# from coming (``zotero_remote.FileUnavailable.reason``)...
_WAIT_OF = {zr.LINKED: "linked", zr.UNAVAILABLE: "unavailable", zr.COMPRESSED: "unavailable",
            zr.NO_ACCESS: "no_access", zr.QUOTA_HERE: "storage", zr.FAILED: "failed"}
_HELD_WAITS = ("storage", "failed", FILE_PLACING)  # the waits whose file entry stays queued, tried again later
# ...and how the page's ``zotero_pdf`` mark words it (the frontend's ZOTERO_PDF_REASONS)
_MARK_REASON = {"linked": "linked", "unavailable": "unavailable", "no_access": "files", "storage": "quota",
                "failed": "failed"}
# The annotation rows a PDF of Gamma's cannot go in place over: another member's, refused, not shown (a ``held``
# one is judged by its block: ``_unmovable``)
_UNMOVABLE = ("readonly", "rejected", "waiting", "skipped")
_is_pdf = zotero_merge.is_pdf_attachment  # a PDF attachment of a file link mode
_size = zotero_merge.stored_size          # the bytes of an attachment's stored file, as Zotero lists them


# --- the plan ---------------------------------------------------------------------------

def pdf_filename(side) -> str:
    """The name a page's PDF goes to Zotero under: the file's own, else the
    page's title as a PDF's name."""
    name = str(side.props.get("original_filename") or "").strip()
    if name:
        return name
    title = " ".join((side.title or "").split())[:FILENAME_TITLE_MAX] or "document"
    return title if title.lower().endswith(".pdf") else f"{title}.pdf"


def attachment_body(parent: str, filename: str) -> dict:
    """A new imported attachment under the item ``parent`` for a page's PDF,
    as it is created (its file goes after it, by the file queue)."""
    return {"itemType": "attachment", "parentItem": parent, "linkMode": "imported_file", "title": UPLOAD_TITLE,
            "contentType": zotero_merge.PDF_TYPE, "filename": filename}


def quota_active(r: Round) -> bool:
    """Whether Zotero's storage is full as the round knows it (``quota_step``,
    ``_quota_full``): no PDF goes before ``quota_until``."""
    until = r.quota_until
    return bool(until) and until > page_now()


def may_send_files(r: Round) -> bool:
    """PDFs may go to Zotero: a two-way round, uploads on, the key's file
    access (whether Zotero's storage has room is asked when one goes)."""
    return r.pushes and bool(r.link["upload"]) and bool(r.access.get("files"))


def _relocating(r: Round, side: Side) -> bool:
    """Whether the page's highlights are still being placed in its PDF (the
    relocate job of a replacement): its file waits to go until they are,
    neither in place nor as a new attachment (``FILE_PLACING``)."""
    from .routers.pages import RELOCATE  # local: keep gamma.* free of the routers package
    if not side.doc_id:
        return False
    job = jobs.latest(r.link["owner"], RELOCATE, f"{side.page_id}:{side.doc_id}")
    return bool(job) and job.get("state") in jobs.ACTIVE


def _unmovable(annotations, rows: dict, view) -> bool:
    """Whether any of an attachment's live annotations in Zotero is one Gamma
    may not write now, and so could not move with a new file: one with no row
    Gamma writes, a row in ``_UNMOVABLE``, or a block of the page as read
    (``view``, ``zotero_local.read_page``) that is held (a relocation not
    done, a dashed or lost highlight: ``hold_reason``). A ``held`` row is
    judged by its block as the page reads now (held while the two files
    differ, as every annotation of a PDF replaced here is until its file
    goes, or for a hold its block no longer has): it moves with Gamma's file
    when it synced before, is the user's own and its block is an annotation
    block nothing holds."""
    held, placed = set(), set()
    for ann in (view.annotations if view else ()):
        (held if hold_reason(ann.block) else placed).add(ann.block["id"])

    def stuck(row) -> bool:
        if row is None or row["block_id"] in held:
            return True
        if row["state"] == "held":
            return not (row["zbase"] and row["block_id"] in placed and not row["detail"].get("author"))
        return row["state"] in _UNMOVABLE

    return any(stuck(rows.get(obj["key"])) for obj in annotations)


def _file_plan(r: Round, side: Side, att: dict | None, row: dict | None, *, any_pdf: bool, first: bool,
               unmovable, declined: str = "", adopt: str = "") -> tuple[str, str]:
    """What becomes of a live page's PDF and its item's primary attachment
    ``att`` (a whole object; None: the item has none), ``row`` the attachment
    row bound to the page (None: none); see docs/dev/zotero_sync.md#pdfs:

    - ``none``: nothing to do; ``bind``: both hold the same file (MD5);
    - ``fetch``: Zotero's file becomes the page's (a page without a PDF, a
      file changed in Zotero, a primary switched, a first link taking it);
    - ``wait`` (with the reason): Zotero's file cannot come, or Gamma's
      waits while the page's highlights are being placed in it
      (``FILE_PLACING``: decided again once the relocation ends);
    - ``upload``: Gamma's file goes over Zotero's in place; ``new``: as a
      new attachment (never over a file whose annotations Gamma cannot
      move); ``both`` / ``both_new``: both changed, Gamma's goes so, and a
      ``zotero_file`` conflict keeps Zotero's (``both_new``: Zotero's stays
      in its own attachment, a copy of it kept here);
    - ``park``: the two files differ and neither moves (annotations wait);
    - ``kept_out``: the page let its PDF go; Zotero's comes only when asked;
    - ``unbind``: the row names no primary any more.

    ``unmovable()`` says whether ``att`` holds annotations Gamma may not
    write (asked only for an upload in place, and never while the page's
    highlights are still being placed: whether they end dashed or lost is
    known only then). ``declined``: the MD5 of
    Gamma's file whose attachment Zotero let go (removed, moved to another
    item): that file stays here and is not sent back, a changed one is.
    ``adopt``: the side a ``first`` pair's row keeps (``pair_side``), else
    the round's."""
    if att is None:
        if row is not None:
            return "unbind", ""
        sends = may_send_files(r) and not any_pdf and bool(side.doc_id)
        return ("new", "") if sends and side.md5 != declined else ("none", "")
    stored = (att.get("data") or {}).get("linkMode") in zotero_merge.STORED_LINK_MODES
    data = att.get("data") or {}
    zm = str(data.get("md5") or "").lower()
    ok, why = zotero_merge.fetchable(data, bool(r.access.get("files")))
    bound = row is not None and row["key"] == att["key"]
    if not side.has_pdf:
        if bound and row["detail"].get("gmd5") and not first:
            return "kept_out", ""
        return ("fetch", "") if ok else ("wait", why)
    gm = side.md5
    if not gm:  # a PDF shown from an address alone: nothing to compare, nothing to send
        return ("none", "") if bound else ("park", "")

    def in_place() -> bool:
        return may_send_files(r) and stored and not _relocating(r, side) and not unmovable()

    if bound and not first:
        synced_z, synced_g = row["detail"].get("md5") or "", row["detail"].get("gmd5") or ""
        z_changed, g_changed = zm != synced_z, gm != synced_g
        if gm == zm:
            return ("bind", "") if (z_changed or g_changed or row["state"] != "live") else ("none", "")
        if not z_changed and not g_changed:
            return "none", ""
        if not g_changed:
            return ("fetch", "") if ok else ("wait", why)
        if not stored or (row["state"] == "waiting" and row["detail"].get("file") in ("linked", "unavailable")):
            return "park", ""  # a file Zotero Storage does not serve gets no second PDF beside it
        if may_send_files(r) and _relocating(r, side):
            return "wait", FILE_PLACING  # in place or beside it: decided once its highlights are placed
        if not z_changed:
            return ("upload", "") if in_place() else ("new", "") if may_send_files(r) else ("park", "")
        return ("both", "") if in_place() else ("both_new", "") if may_send_files(r) else ("park", "")
    if gm == zm:
        return "bind", ""
    if first:  # a first link's pair (or a force): adopt's side, but Zotero's file while its annotations cannot move
        adopt = adopt or r.adopt
        if adopt == "mine" and stored and may_send_files(r) and _relocating(r, side):
            return "wait", FILE_PLACING  # whether its annotations can move is known once its highlights are placed
        annotated = unmovable()
        if ok and (annotated or adopt != "mine"):
            return "fetch", ""
        if not stored or (row is not None and row["state"] == "waiting"
                          and row["detail"].get("file") in ("linked", "unavailable")):
            return "park", ""  # a file Zotero Storage does not serve: nothing goes over it
        if adopt == "mine" and not annotated and in_place():
            return "upload", ""  # (Zotero's file kept here first: ``_upload``'s ``keep``)
        return "park", ""
    if row is not None:  # the primary switched (the old one trashed, deleted or moved): Zotero's replacement
        return ("fetch", "") if ok else ("wait", why)
    return "park", ""  # Gamma's own PDF beside a Zotero one it never held: both stay


def pair_side(*rows) -> str:
    """The side a ``first`` pair among ``rows`` (the item's, then the
    attachment's; None skipped) keeps (``detail.adopt``: written when a
    first link or a force ends with the pair still to decide), "" for
    none: the round's ``adopt`` then."""
    return next((str(row["detail"]["adopt"]) for row in rows
                 if row is not None and row["detail"].get("first") and row["detail"].get("adopt")), "")


# --- an item's PDF, decided with its unit -----------------------------------------------

class UnitPdf:
    """The PDF part of an item's unit (gamma/zotero_sync_items.py's
    ``_Unit`` mixes it in and holds the state it reads and writes): the
    page's PDF against the item's primary attachment, decided once the
    record is (``_file``), with the page's ``zotero_pdf`` mark and a new
    attachment for Gamma's file."""

    def _attachments(self) -> list[dict]:
        return [c for c in self.children if (c.get("data") or {}).get("itemType") == "attachment"
                and (c.get("data") or {}).get("parentItem") == self.key]

    def _primary(self) -> dict | None:
        """The item's PDF: the attachment bound to the page while it still
        qualifies, else Zotero's own choice."""
        if self.standalone:
            return self.obj if _is_pdf(self.data) and not self.trashed else None
        bound = self.bound()
        if bound is not None and bound["key"] in self.kids:
            data = self.kids[bound["key"]].get("data") or {}
            if _is_pdf(data) and not data.get("deleted") and data.get("parentItem") == self.key:
                return self.kids[bound["key"]]
        return zotero_merge.primary(self._attachments(), str(self.data.get("url") or ""))

    def unmovable(self, att_key: str) -> bool:
        """Whether the attachment holds annotations Gamma may not write now
        (``_unmovable``), by the unit's rows and blocks."""
        annotations = []
        for child in self.children:
            data = child.get("data") or {}
            if data.get("itemType") == "annotation" and data.get("parentItem") == att_key and not data.get("deleted"):
                annotations.append(child)
        return _unmovable(annotations, self.rows, self.blocks.pv if self.blocks is not None else None)

    def _file(self) -> None:
        """The page's PDF against the item's primary attachment: the rows,
        the page's ``zotero_pdf`` mark, a transfer queued, a new
        attachment made."""
        if self.note:
            return
        side = self.side
        row = self.bound()
        if row is not None and not row["parent"] and row["detail"].get("parent_to") and (
                self.standalone or row["key"] not in self.kids):
            # a standalone PDF whose move under the page's new item is still to be sent: the page's unit sends
            # it (``zotero_sync_pages._send_move``); until then it is the page's PDF as it is, never one to
            # unbind or replace
            self.queue.append(zr.page_entry(self.page_id))
            if not self.standalone:
                return
        if row is not None and row["state"] == "waiting" and row["detail"].get("file") == "quota" and (
                quota_active(self.r) or row["detail"].get("trashed")):
            return  # Zotero's storage is full: the PDF waits as it is, its attachment back once the wait lifts
        att = self._primary()
        resend = None
        if row is not None and not self.standalone and row["detail"].get("made") and row["key"] not in self.kids \
                and int(row["version"]) <= 0:
            made, row = row, None  # Gamma's attachment whose create is not confirmed (or waits on Zotero's storage)
            if att is not None and side.md5 and _md5(att) != side.md5:
                # beside a PDF of Zotero's that is not Gamma's file: decided afresh against it, or its create goes
                # again (below) when the page held no row of it
                row = self._beside_again(made, att)
                resend = made if row is None else None
        elif row is not None and not self.standalone:
            back = self._back_beside(row)
            if back is not None:
                att, row = back
        if resend is not None and may_send_files(self.r) and _relocating(self.r, side):
            # its highlights are being placed: no attachment made now; decided again once they are
            self._put({**resend, "state": "waiting", "detail": {**resend["detail"], "file": FILE_PLACING}})
            self.queue.append(_placing_entry(resend["key"], max(0, int(resend["version"]))))
            self._mark(None)
            return
        if not self.standalone:  # attachments other pages held until now (Merge Items moved them here)
            for key, other in list(self.rows.items()):
                if other["kind"] == "attachment" and key in self.kids and other["page_id"] != self.page_id:
                    self.moved[key] = other
                    self._drop(key)
        # a first link's pair (or a force's) as the unit found it: the merge drops the flag from the item's row
        # once nothing of the record is to be sent, before the PDF is decided
        first = (self.first_link or bool((self.row or {}).get("detail", {}).get("first"))
                 or bool(row and row["detail"].get("first")))
        any_pdf = any(_is_pdf(c.get("data") or {}) for c in self._attachments()) or self.standalone
        declined = "" if self.standalone else str((self.row or {}).get("detail", {}).get("declined_md5") or "")
        if resend is not None:  # (decided afresh it would read as a PDF of Gamma's the item never held)
            action, why = "new", ""
        else:
            action, why = _file_plan(self.r, side, att, row, any_pdf=any_pdf, first=first, declined=declined,
                                     unmovable=lambda: self.unmovable(att["key"]) if att else False,
                                     adopt=pair_side(self.rows.get(self.key), row))
        if self.standalone and action in ("new", "both_new"):
            action = "park"  # a standalone PDF takes no attachment of its own
        if declined and (att is not None or action == "new"):
            self._decline("")  # Zotero holds a PDF of the item again, or Gamma's file changed: judged afresh
        mark = None
        if action == "unbind":
            if not self.standalone:
                self._drop(row["key"])
                if side.md5 and (row["detail"].get("gmd5") or "") == side.md5:
                    self._decline(side.md5)  # Zotero let go of the PDF Gamma holds unchanged: it is not sent back
        elif action == "new" and resend is not None:
            self._new_attachment(resend, theirs=resend["detail"].get("theirs"), beside=resend["detail"].get("beside"))
        elif action == "new":
            self._new_attachment(row, beside=self._beside(att, row))
        elif action == "both_new":  # Zotero's new file stays in its attachment, kept here too
            self._new_attachment(row, theirs=_theirs(att), beside=self._beside(att, row))
        elif att is not None:
            mark = self._file_rows(action, why, att, row, first=first)
        self._mark(mark)

    def _decline(self, md5: str) -> None:
        """The item's row's ``declined_md5`` (``_file_plan``): set to ``md5``,
        or dropped for ""."""
        item = self.out.get(self.key)
        if item is not None and not self.standalone:
            detail = without(item["detail"], "declined_md5")
            item["detail"] = {**detail, "declined_md5": md5} if md5 else detail

    def _file_rows(self, action: str, why: str, att: dict, row: dict | None, *, first: bool = False) -> dict | None:
        """The attachment row and queue entry for a plan on ``att``; the page's
        ``zotero_pdf`` mark (None: none). A ``first`` pair's transfer gives the
        row the files as last synced that the plan stands on (only Zotero's
        changed for a fetch, only Gamma's for an upload), so the file's unit,
        which no longer sees the flag, decides the same."""
        side, data = self.side, att.get("data") or {}
        zm = str(data.get("md5") or "").lower()
        bound = row is not None and row["key"] == att["key"]
        base = dict(row) if bound else (dict(self.out.get(att["key"]) or {}) if self.standalone else {})
        if not base:
            base = new_row(att["key"], "attachment", page_id=self.page_id, block_id=self.page_id, parent=self.key,
                           zotero_type="attachment", detail={"md5": "", "gmd5": side.md5 if side.has_pdf else "",
                                                             "fsig": side.fsig})
            came = self.moved.get(att["key"])
            if came is not None and (came["detail"].get("gmd5") or "") == zm and came["detail"].get("doc_id"):
                base["detail"]["copy"] = came["detail"]["doc_id"]  # the file another page holds: no download
        if not self.standalone:
            base["parent"] = self.key
            base["detail"] = without(base["detail"], "parent_to")  # (a move whose answer was lost: it landed)
        base.update(page_id=self.page_id, block_id=self.page_id)
        detail = {**base["detail"], "size": _size(att), "filename": str(data.get("filename") or ""),
                  "link_mode": str(data.get("linkMode") or "")}
        if row is not None and not bound and not self.standalone:
            self._drop(row["key"])  # the page's PDF was another attachment's
        if action == "bind":
            detail.update(md5=zm, gmd5=side.md5, doc_id=side.doc_id, fsig=side.fsig)
            base.update(state="live", version=int(att.get("version") or 0),
                        detail=without(detail, "file", "first", "keep", "theirs"))
            self._put(base)
            return None
        if action == "park":
            if not bound:
                detail.update(md5=zm, gmd5=side.md5, doc_id=side.doc_id, fsig=side.fsig)
            if bound and row["state"] == "waiting" and row["detail"].get("file") in ("linked", "unavailable"):
                # Zotero Storage still serves none of it: the row keeps waiting, so no later round reads the
                # page's own PDF as one to send over it
                base.update(version=int(att.get("version") or 0), detail=without(detail, "first"))
            else:
                base.update(state="live", version=int(att.get("version") or 0),
                            detail=without(detail, "file", "first", "keep"))
            self._put(base)
            self.notes.append("kept")
            return None
        if action == "wait":
            reason = _WAIT_OF.get(why, why)
            if reason == FILE_PLACING:  # a pair waits as a pair: the file's unit decides it as one once it is placed
                detail = {**detail, **({"first": True} if first else {})}
            else:
                detail = without(detail, "first")
            base.update(state="waiting", version=int(att.get("version") or 0), detail={**detail, "file": reason})
            self._put(base)
            if reason == FILE_PLACING:  # decided again at the relocation's end (``relocation_ended``), or a while later
                self.queue.append(_placing_entry(att["key"], int(att.get("version") or 0)))
            return None if side.has_pdf else self._pdf_mark(att, "unavailable", reason)
        if action == "none":
            if bound and int(base["version"]) != int(att.get("version") or 0):
                base.update(version=int(att.get("version") or 0), detail=detail)
                self._put(base)
            return None
        if action == "kept_out":
            return self._pdf_mark(att, "remote", "")
        # a transfer: the row keeps the files as last synced until it is done
        if first and action == "fetch":
            detail.update(md5="", gmd5=side.md5)
        elif first and action == "upload":
            detail.update(md5=zm, gmd5="", keep=True)  # Zotero's file is kept here before Gamma's goes over it
        if action == "fetch" and bound and row["state"] == "waiting" and row["detail"].get("file") == "storage":
            # this server's full storage still holds its entry back: the row keeps saying so
            base.update(state="waiting", version=int(att.get("version") or 0), detail=without(detail, "sent"))
        else:
            base.update(state="live", version=int(att.get("version") or 0), detail=without(detail, "file", "sent"))
        self._put(base)
        queued = action != "fetch" or side.has_pdf or self._wants_download()
        if queued:
            version = int(att.get("version") or 0)
            self.queue.append({"id": zr.FILE_PREFIX + att["key"], "kind": "file", "version": version,
                               "priority": max(self.version, version)})
            keep_ahead(self.r, att)  # as just read: its file's unit reads it no more
        if side.has_pdf:
            return None
        return self._pdf_mark(att, "queued" if queued else "remote", "")

    def _wants_download(self) -> bool:
        """Whether a PDF Gamma lacks comes now: always with ``download``
        "all"; with "open", for the ``NEWEST_FILES`` newest items (or when
        its page is opened: ``zotero_sync.fetch_page`` queues it then)."""
        if self.r.link["download"] != "open":
            return True
        with connect_pages_db(self.r.ws) as conn:  # (counted up to the bound: an old item's stops there)
            newer = conn.execute("SELECT COUNT(*) FROM (SELECT 1 FROM zotero_objects WHERE parent = '' "
                                 "AND version > ? AND kind IN ('item', 'attachment') AND key != ? LIMIT ?)",
                                 (self.version, self.key, NEWEST_FILES)).fetchone()[0]
        return newer < NEWEST_FILES

    def _pdf_mark(self, att: dict, state: str, reason: str) -> dict:
        data = att.get("data") or {}
        return {"key": att["key"], "state": state, "reason": _MARK_REASON.get(reason, reason), "size": _size(att),
                "filename": str(data.get("filename") or data.get("title") or "")}

    def _mark(self, mark: dict | None) -> None:
        """The page's ``zotero_pdf`` as the plan leaves it."""
        if self.change == "create":
            if mark:
                self.new_page["props"]["zotero_pdf"] = mark
            return
        current = self.side.props.get("zotero_pdf") if self.side else None
        if mark is None and current is None:
            return
        if mark is not None and isinstance(current, dict) and current.get("state") == "downloading" \
                and mark["state"] == "queued" and current.get("key") == mark["key"]:
            return  # its download runs
        if mark != current:
            self.ops.append(zotero_local.zotero_pdf_op(self.page_id, mark))

    def _new_attachment(self, row: dict | None, *, theirs: dict | None = None, beside: dict | None = None) -> None:
        """A new imported attachment for the page's PDF (an item with no PDF
        attachment at all, or a file whose annotations Gamma cannot move
        under the old one): its row written pending first, the create sent
        (again, for a create not confirmed: under the same key while Gamma's
        file is the same, which Zotero answers as ours when it landed), the
        upload queued once it lands. ``theirs``: the attachment whose file
        Zotero replaced too (``both_new``; ``_theirs``), named in the new row
        (``detail.theirs``) so that the upload first keeps a copy of that
        file here for a ``zotero_file`` conflict (``_upload``). ``beside``:
        Zotero's PDF it goes beside (``_beside``), kept in the new row
        (``detail.beside``) for as long as it is the page's PDF."""
        side = self.side
        for key, other in self.rows.items():
            if (other["kind"] == "attachment" and other["page_id"] == self.page_id and other["state"] in HELD_ROWS
                    and key not in self.kids):
                self._drop(key)  # an earlier one of Gamma's not confirmed, or the page's PDF until now
        if row is not None:
            self._drop(row["key"])
        key = object_key(self.r.ws, self.r.library, "attachment", self.key, self.page_id, side.md5)
        filename = pdf_filename(side)
        body = attachment_body(self.key, filename)
        detail = {"sent": body, "made": True, "md5": "", "gmd5": "", "filename": filename}
        if theirs:
            detail["theirs"] = dict(theirs)
        if beside:
            detail["beside"] = dict(beside)
        if quota_active(self.r):  # Zotero's storage is full: no fileless attachment; it waits
            self._put(new_row(key, "attachment", page_id=self.page_id, block_id=self.page_id, parent=self.key,
                              zotero_type="attachment", version=0, state="waiting", gsig=side.md5,
                              detail={**detail, "file": "quota"}))
            return
        self._put(new_row(key, "attachment", page_id=self.page_id, block_id=self.page_id, parent=self.key,
                          zotero_type="attachment", version=-1, state="pending", gsig=side.md5, detail=detail))
        self.writes.create("attachments", key, body)
        self.sent[key] = {"what": "attachment", "body": body}
        self.notes.append("created there")

    def _beside(self, att: dict | None, row: dict | None) -> dict | None:
        """Zotero's PDF ``att`` that a new attachment of Gamma's goes beside,
        as the new row keeps it (``detail.beside``): its key, its file's MD5
        now, its row as the page last synced it (``row``, None when the page
        held no row of it) and the rows of its annotations the page holds,
        which drop once the page's PDF is the new attachment. From them the
        PDF is decided afresh against it while the new attachment is not
        confirmed (``_beside_again``), and the page becomes its PDF again
        without bringing its annotations in twice (``_back_beside``)."""
        if att is None or self.standalone:
            return None
        kept = None
        if row is not None and row["key"] == att["key"]:
            kept = {**row, "detail": without(row["detail"], "beside", "theirs")}
        rows = {key: other for key, other in self.rows.items()
                if other["kind"] == "annotation" and other["page_id"] == self.page_id and other["parent"] == att["key"]
                and other["state"] in ("live", "readonly", "held") and key not in self.dropped}
        return {"key": att["key"], "md5": _md5(att), "row": kept, "rows": rows}

    def _beside_again(self, made: dict, att: dict) -> dict | None:
        """The row of Zotero's PDF ``att`` as the page last synced it, when
        Gamma's attachment ``made``, which Zotero does not hold (its create
        not confirmed, or waiting on Zotero's storage), was decided beside
        it: ``made`` is let go and the PDF decided afresh against ``att``,
        on what both sides hold now (in place once its annotations can move,
        beside it otherwise, under the same key while Gamma's file is the
        same; waiting while the highlights are placed). None: no row of
        ``att`` was kept, and ``made``'s create goes again. (Until ``made``
        is confirmed the annotation rows of ``att`` stay as they are.)"""
        beside = made["detail"].get("beside") or {}
        kept = beside.get("row")
        if beside.get("key") != att["key"] or not isinstance(kept, dict):
            return None
        self._drop(made["key"])
        row = dict(kept)
        self.rows[row["key"]] = row
        return row

    def _back_beside(self, row: dict):
        """``(Zotero's PDF, its row)`` when the page's file is now the file of
        the PDF that Gamma's attachment ``row``, the page's PDF, was made
        beside (``detail.beside``; "Take Zotero's" of a ``both_new``
        conflict brings it, or a person put it back): that PDF is the
        page's again, as the plan then decides it (Zotero's newer file, when
        Zotero replaced it since), its annotation rows as the page last
        synced them back (so none of them comes in twice), and Gamma's
        attachment stays in Zotero, no page's PDF. None otherwise."""
        side, beside = self.side, row["detail"].get("beside") or {}
        att = self.kids.get(str(beside.get("key") or ""))
        data = (att or {}).get("data") or {}
        if (att is None or row["key"] not in self.kids or not side.md5
                or side.md5 == (row["detail"].get("gmd5") or "") or side.md5 not in (beside.get("md5"), _md5(att))
                or not _is_pdf(data) or data.get("deleted") or data.get("parentItem") != self.key):
            return None
        self._drop(row["key"])
        back = beside.get("row") if isinstance(beside.get("row"), dict) else new_row(
            att["key"], "attachment", page_id=self.page_id, block_id=self.page_id, parent=self.key,
            zotero_type="attachment")
        back = {**back, "state": "live", "detail": {**without(back["detail"], "file", "first", "keep"),
                                                    "md5": side.md5, "gmd5": side.md5, "doc_id": side.doc_id,
                                                    "fsig": side.fsig}}
        self.rows[att["key"]] = back
        self._put(back)
        for key, kept in (beside.get("rows") or {}).items():
            if key in self.kids and key not in self.rows:
                self.rows[key] = kept
                self._put(dict(kept))
        log.info(f"[zotero] {self.r.ws}: page {self.page_id} holds the file of {att['key']} again: its PDF")
        return att, back


def _md5(att: dict) -> str:
    return str((att.get("data") or {}).get("md5") or "").lower()


def _theirs(att: dict) -> dict:
    """The attachment ``att`` whose file Zotero replaced too, as the row of
    Gamma's new attachment beside it names it (``detail.theirs``)."""
    data = att.get("data") or {}
    return {"key": att["key"], "md5": _md5(att), "filename": str(data.get("filename") or ""),
            "link_mode": str(data.get("linkMode") or ""), "size": _size(att)}


# --- the file's own unit: a transfer -----------------------------------------------------

def _later(seconds: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def _placing_entry(key: str, version: int) -> dict:
    """The file entry of a PDF that waits for its page's highlights to be
    placed (``FILE_PLACING``): held back ``PLACING_RECHECK_S``, so it is
    looked at again even when no relocation's end asks for it
    (``relocation_ended``)."""
    return {"id": zr.FILE_PREFIX + key, "kind": "file", "version": int(version),
            "next_at": _later(PLACING_RECHECK_S), "reason": PLACING_WAIT}


def relocation_ended(ws: str, page_id: str) -> None:
    """The relocate job of the page ``page_id`` ended, however it ended
    (gamma/routers/pages.py, as its run returns): the page's PDFs that
    waited for its highlights to be placed (``FILE_PLACING``, or a file
    entry still held back for it after a receive-only round parked the
    PDF) are due again, and so is the page's item while annotation rows of
    it are held or wait (a highlight the run left where it was reads as it
    last synced, so no change of the page's lifts the wait it was put on;
    its item's unit does). The link's round is asked for after the clock's
    quiet time, by when the job reads as ended. Nothing for a workspace
    with no link."""
    if get_link(ws) is None:
        return
    with connect_pages_db(ws) as conn:
        entries = [{"id": zr.FILE_PREFIX + key, "kind": "file", "next_at": "", "reason": ""} for (key,) in conn.execute(
            "SELECT key FROM zotero_objects WHERE kind = 'attachment' AND page_id = ? AND ((state = 'waiting' "
            "AND json_extract(detail, '$.file') = ?) OR key IN (SELECT substr(id, ?) FROM zotero_queue "
            "WHERE kind = 'file' AND reason = ?))",
            (page_id, FILE_PLACING, len(zr.FILE_PREFIX) + 1, PLACING_WAIT)).fetchall()]
        if conn.execute("SELECT 1 FROM zotero_objects WHERE kind = 'annotation' AND page_id = ? "
                        "AND state IN ('held', 'waiting') LIMIT 1", (page_id,)).fetchone():
            entries += [{"id": key, "kind": "top", "version": int(version)} for key, version in conn.execute(
                "SELECT key, version FROM zotero_objects WHERE page_id = ? AND state = 'live' AND parent = '' "
                "AND kind IN ('item', 'attachment')", (page_id,)).fetchall()]
        if not entries:
            return
        write_lock(conn)
        zr.enqueue(conn, entries)
        conn.commit()
    clock = sync_clock.get(ENGINE)
    if clock is not None:
        clock.request(ws)


def _stored_copy(conn, ws: str, md5: str) -> str:
    """A stored PDF of this workspace whose MD5 is ``md5`` (the file of
    another page the link keeps: a duplicated item, an attachment moved by
    Merge Items), so a download can be spared; "" for none."""
    if not md5:
        return ""
    for (doc_id,) in conn.execute(
            "SELECT json_extract(detail, '$.doc_id') FROM zotero_objects WHERE kind = 'attachment' "
            "AND json_extract(detail, '$.gmd5') = ?", (md5,)).fetchall():
        path = storage.open_path(ws, f"{doc_id}.pdf") if isinstance(doc_id, str) and doc_id else None
        if path and file_md5(path) == md5:
            return doc_id
    return ""


def unit_file(r: Round, entry: zr.Entry):
    """A PDF to transfer (``file:<attachment key>``): decided again on what
    Zotero and the page hold now, then downloaded (attached through
    ``replace_attachment``, whose relocation places the highlights),
    uploaded (in place, or the upload of an attachment Gamma made), or
    both. A file that cannot come waits (``waiting.files``), never an
    error."""
    key = entry.id[len(zr.FILE_PREFIX):]
    with connect_pages_db(r.ws) as conn:
        row = read_rows(conn, "key = ?", (key,)).get(key)
        if row is not None:
            owner_row = (read_rows(conn, "key = ?", (row["parent"],)).get(row["parent"]) if row["parent"]
                         else None)
            side = page_side(conn, r.ws, row["page_id"])
    if row is None or not row["page_id"] or not side.live:
        entries_done(r, [entry.id])
        return None
    att = read_ahead(r, key, "file", entry.version)
    data = (att or {}).get("data") or {}
    if att is None or data.get("deleted") or not _is_pdf(data):
        entries_done(r, [entry.id])
        if row["parent"]:  # its item decides its PDF again
            with connect_pages_db(r.ws) as conn:
                write_lock(conn)
                zr.enqueue(conn, [{"id": row["parent"], "kind": "top"}])
                conn.commit()
        return None
    first = bool(row["detail"].get("first")) or bool(owner_row and owner_row["detail"].get("first"))

    def unmovable():
        try:
            found = r.client.children(r.library, key)
        except ZoteroNotFound:
            return True
        with connect_pages_db(r.ws) as conn:
            rows = read_rows(conn, "parent = ? AND kind = 'annotation'", (key,))
            view = zotero_local.read_page(conn, row["page_id"], notes=False)
        return _unmovable([c for c in found if (c.get("data") or {}).get("itemType") == "annotation"
                           and not (c.get("data") or {}).get("deleted")], rows, view)

    adopt = pair_side(owner_row, row)
    action, why = _file_plan(r, side, att, row, any_pdf=True, first=first, unmovable=unmovable, adopt=adopt)
    if action == "kept_out" and entry.priority >= zr.OPENED_PRIORITY:  # Download now: asked for after all
        action, why = _file_plan(r, side, att, None, any_pdf=True, first=first, unmovable=unmovable, adopt=adopt)
    if action == "fetch":
        return _download(r, entry, row, att, side)
    if action in ("upload", "both"):
        return _upload(r, entry, row, att, side, both=action == "both",
                       keep=action == "upload" and (first or bool(row["detail"].get("keep"))))
    if action in ("new", "both_new"):
        with connect_pages_db(r.ws) as conn:  # its item makes the new attachment
            write_lock(conn)
            zr.enqueue(conn, [{"id": row["parent"] or key, "kind": "top"}])
            zr.done(conn, [entry.id])
            conn.commit()
        return None
    if action == "wait":
        return _file_waits(r, entry, row, att, side, _WAIT_OF.get(why, why))
    if action in ("bind", "park"):
        zm = str(data.get("md5") or "").lower()
        state, detail = "live", without(row["detail"], "file", "first", "keep")
        if action == "bind":  # one file on both sides: synced as it is now
            detail = without(detail, "theirs")
            detail.update(md5=zm, gmd5=side.md5, doc_id=side.doc_id, fsig=side.fsig)
        elif row["state"] == "waiting" and row["detail"].get("file") in ("linked", "unavailable"):
            state, detail = "waiting", without(row["detail"], "first", "keep")  # as ``_file_rows`` keeps it
        # (a park moves neither file now, as in a receive-only round: the files as last synced stay, a pair's
        # transfer's as its plan left them, so a round that may send decides again, as ``_file_rows`` leaves it)
        new = {**row, "state": state, "version": int(att.get("version") or 0), "detail": detail}
        with connect_pages_db(r.ws) as conn:
            write_lock(conn)
            write_rows(conn, [new])
            zr.done(conn, [entry.id])
            conn.commit()
        return None
    entries_done(r, [entry.id])
    return None


def _file_progress(r: Round, name: str, direction: str, total: int):
    """The status's ``progress.file`` while a transfer runs, as ``tick``."""
    def tick(done: int) -> None:
        patch_status(r.ws, lambda s: {**s, "progress": {**(s.get("progress") or {}), "file": {
           "name": name, "done": int(done), "total": int(total), "dir": direction},
           "bytes": {"done": r.fill_bytes + r.bytes_done + int(done),
                     "total": r.fill_bytes + max(r.bytes_total, r.bytes_done + int(done))}}})
    return tick


def _file_waits(r: Round, entry: zr.Entry, row: dict, att: dict, side: Side, reason: str):
    """A PDF that cannot come (or go) now: this server's full storage
    holds every download of the round and keeps the entry for a while; a
    transfer that went wrong is tried again later (given up after a few
    tries); Gamma's file whose highlights are being placed is looked at
    again at the relocation's end, or a while later (``FILE_PLACING``); a
    file Zotero cannot hand out waits for its next change. Its row says
    why; a page without a PDF shows it."""
    data = att.get("data") or {}
    new = {**row, "state": "waiting", "detail": {**row["detail"], "file": reason}}
    keep = reason in _HELD_WAITS
    if not keep:
        new["version"] = int(att.get("version") or 0)
    ops = [] if side.has_pdf else [zotero_local.zotero_pdf_op(side.page_id, {
        "key": att["key"], "state": "unavailable", "reason": _MARK_REASON.get(reason, reason), "size": _size(att),
        "filename": str(data.get("filename") or data.get("title") or "")})]
    if reason == "storage":
        r.no_downloads = True

    def rows(conn):
        write_rows(conn, [new])
        if reason == "storage":
            zr.enqueue(conn, [{"id": entry.id, "kind": "file", "next_at": _later(STORAGE_RETRY_S),
                               "reason": STORAGE_WAIT}])
        elif reason == "failed":
            attempts = zr.backoff(conn, entry.id, "the download failed", now=page_now())
            if attempts >= zr.MAX_ATTEMPTS:
                new["version"] = int(att.get("version") or 0)
                write_rows(conn, [new])
                zr.drop(conn, [entry.id])
        elif reason == FILE_PLACING:
            zr.enqueue(conn, [_placing_entry(row["key"], entry.version)])
        else:
            zr.done(conn, [entry.id])

    if side.props.get("zotero_pdf") == (ops[0]["props"]["zotero_pdf"] if ops else None):
        ops = []
    try:
        zotero_local.apply(r.ws, side.page_id, ops, rows=rows)
    except (zotero_local.Moved, OpError):
        with connect_pages_db(r.ws) as conn:
            write_lock(conn)
            rows(conn)
            conn.commit()
    if keep:
        r.excluded.add(entry.id)
    return None


def _download(r: Round, entry: zr.Entry, row: dict, att: dict, side: Side):
    """Zotero's file into the workspace and onto the page: a copy the
    workspace holds already when there is one, else a download spooled
    into the store; then ``zotero_local.set_pdf`` (no one-PDF-per-page
    check: linked items may share a file), its row in the same
    transaction, under a compare-and-set on the page's file."""
    data = att.get("data") or {}
    zm = str(data.get("md5") or "").lower()
    with connect_pages_db(r.ws) as conn:
        doc_id = _stored_copy(conn, r.ws, zm)
    hint = row["detail"].get("copy")
    if not doc_id and isinstance(hint, str) and hint:
        path = storage.open_path(r.ws, f"{hint}.pdf")
        doc_id = hint if path and file_md5(path) == zm else ""
    if not doc_id:
        if r.no_downloads:  # this server's storage filled up this round: no download is tried
            return _file_waits(r, entry, row, att, side, "storage")
        if entry.priority >= zr.OPENED_PRIORITY and not side.has_pdf:
            mark = side.props.get("zotero_pdf")
            if isinstance(mark, dict) and mark.get("state") != "downloading":
                try:
                    zotero_local.apply(r.ws, side.page_id, [zotero_local.zotero_pdf_op(
                        side.page_id, {**mark, "state": "downloading"})])
                except (zotero_local.Moved, OpError):
                    pass
        size = _size(att)
        try:
            doc_id = zr.download_pdf(r.ws, r.client, r.library, att["key"], md5=zm, link_mode=data.get("linkMode", ""),
                                     size=size, tick=_file_progress(r, str(data.get("filename") or ""), "down", size))
        except zr.FileUnavailable as e:
            return _file_waits(r, entry, row, att, side, _WAIT_OF.get(e.reason, e.reason))
        r.bytes_done += size
    expected = side.doc_id

    def check(conn):
        found = conn.execute("SELECT parent_id, properties FROM unified_blocks WHERE id = ?",
                             (side.page_id,)).fetchone()
        if not found or found[0] != "root":
            return False
        return attachment_file(page_attachment(load_props(found[1]))) == expected

    new = {**row, "state": "live", "version": int(att.get("version") or 0),
           "detail": {**without(row["detail"], "file", "first", "copy"), "md5": zm, "gmd5": zm, "doc_id": doc_id,
                      "fsig": file_sig(r.ws, doc_id),
                      "size": _size(att), "filename": str(data.get("filename") or "")}}

    def rows(conn):
        write_rows(conn, [new])
        others = [k for k, x in read_rows(conn, "kind = 'attachment' AND page_id = ? AND key != ?",
                                           (side.page_id, row["key"])).items() if x["state"] in HELD_ROWS]
        drop_rows(conn, others)
        zr.done(conn, [entry.id])
        _after_file(conn, new)  # in the attach's own transaction: a crash after it loses no annotation

    try:
        zotero_local.set_pdf(r.ws, side.page_id, doc_id, owner=r.link["owner"],
                             original_filename=str(data.get("filename") or ""), check=check, rows=rows)
    except zotero_local.Changed:
        r.decide_again(entry.id)  # the page's file changed meanwhile: decided again next round
        return None
    except OpError as e:
        if e.status != 404:
            raise
        entries_done(r, [entry.id])
        return None
    r.report["files_pulled"] = r.report.get("files_pulled", 0) + 1
    page_note(r, side.page_id, "replaced here" if side.has_pdf else "pulled")
    return None


def _after_file(conn, row: dict) -> None:
    """Once a file is the same on both sides (Zotero's downloaded, Gamma's
    uploaded): the item's unit is looked at again, so the annotations that
    waited for it come (and Gamma's go). In the transaction that records
    the file."""
    top = row["parent"] or row["key"]
    version = conn.execute("SELECT version FROM zotero_objects WHERE key = ?", (top,)).fetchone()
    zr.enqueue(conn, [{"id": top, "kind": "top", "version": int(version[0]) if version else 0}])


def _upload(r: Round, entry: zr.Entry, row: dict, att: dict, side: Side, *, both: bool, keep: bool = False):
    """Gamma's file up as the attachment's: in place over Zotero's
    (``If-Match`` its MD5 as fetched now) or into an attachment with no
    file yet; both changed, or a first link's or a force's "Keep Gamma's"
    going over Zotero's file (``keep``), Zotero's file is downloaded first
    and kept for a ``zotero_file`` conflict's "theirs", and while it
    cannot be kept here Gamma's waits as a download would (nothing goes
    over a file no copy of is kept); an attachment Gamma made beside a
    file Zotero replaced too (``detail.theirs``, ``both_new``, while that
    attachment has no file yet) keeps a copy of that file the same way,
    waiting while this server's storage is full and trying a download that
    went wrong ``COPY_TRIES`` times, but otherwise goes without one
    (nothing goes over Zotero's file: it stays in its own attachment), the
    conflict then written resolved, for the record, as no "theirs" can
    bring a copy not kept. An open conflict is written before the upload
    goes (``_hold_copy``), so a process killed once it went leaves the copy
    held; an upload Zotero refused takes it back, and one whose answer did
    not come asks Zotero whether Gamma's file is there now (``_went``):
    taken back when it is not, the upload done when it is, left held while
    that cannot be told (the next attempt reuses it). Zotero's full storage
    (413) stops every PDF for a while, and a fileless attachment Gamma just
    made goes to Zotero's trash; the register's version is recorded
    (re-read when Zotero had the bytes already)."""
    data = att.get("data") or {}
    zm = str(data.get("md5") or "").lower()
    path = storage.open_path(r.ws, f"{side.doc_id}.pdf")
    if not path or not side.md5:
        entries_done(r, [entry.id])
        return None
    if quota_active(r):
        return _quota_full(r, entry, row, att, "")
    kept = unkept = ""
    keep = (both or keep) and bool(zm)
    theirs = row["detail"].get("theirs") if not keep and not zm else None  # (once it holds a file, stale)
    if not (isinstance(theirs, dict) and theirs.get("key") and theirs.get("md5")):
        theirs = None
    if keep or theirs is not None:
        source = ({"key": att["key"], "md5": zm, "link_mode": data.get("linkMode", ""), "size": _size(att)} if keep
                  else theirs)
        with connect_pages_db(r.ws) as conn:
            kept = _stored_copy(conn, r.ws, source["md5"])
        if not kept and r.no_downloads:
            return _file_waits(r, entry, row, att, side, "storage")
        if not kept:
            try:
                kept = zr.download_pdf(r.ws, r.client, r.library, source["key"], md5=source["md5"],
                                       link_mode=source.get("link_mode") or "", size=int(source.get("size") or 0))
            except zr.FileUnavailable as e:
                reason = _WAIT_OF.get(e.reason, e.reason)
                if keep or reason == "storage" or (reason == "failed" and entry.attempts + 1 < COPY_TRIES):
                    return _file_waits(r, entry, row, att, side, reason)
                unkept = reason
                log.info(f"[zotero] {r.ws}: no copy of Zotero's {source['key']} kept here ({reason})")
    filename = str(side.props.get("original_filename") or data.get("filename") or "") or f"{side.doc_id}.pdf"
    size = path.stat().st_size
    held = None
    if keep:
        held = _hold_copy(r, side, att["key"], zm, kept, filename=str(data.get("filename") or ""), source=att["key"],
                          result=("The PDF was replaced on both sides: Gamma's went to Zotero" if both else
                                  "Gamma's PDF went over Zotero's (Keep Gamma's); Zotero's is kept here"))
    elif theirs is not None and kept:
        held = _hold_copy(r, side, att["key"], theirs["md5"], kept, filename=str(theirs.get("filename") or ""),
                          source=theirs["key"], result=BESIDE_RESULT)
    try:
        try:
            version = zr.upload_pdf(r.client, r.library, att["key"], path, filename=filename, md5=side.md5,
                                    replace_md5=zm, tick=_file_progress(r, filename, "up", size))
        except (zr.Quota, zr.Conflict, ZoteroNotFound, ZoteroAuthError):
            if held is not None:  # refused: nothing went, no conflict, its copy an ordinary file again
                _let_go(r.ws, held)
            raise
        except Exception:  # no answer: whether it went is asked when a conflict stands on it
            went = _went(r, att["key"], side.md5) if held is not None or theirs is not None else None
            if went == 0 and held is not None:
                _let_go(r.ws, held)
            if not went:
                raise
            version = went
    except zr.Quota as e:
        return _quota_full(r, entry, row, att, str(e))
    except zr.Conflict:
        with connect_pages_db(r.ws) as conn:  # Zotero's file is not the one assumed: its item decides again
            write_lock(conn)
            if row["detail"].get("first") or row["detail"].get("keep"):  # (a first pair's or a force's: parked)
                write_rows(conn, [{**row, "detail": {**without(row["detail"], "first", "keep"), "md5": zm,
                                                     "gmd5": side.md5}}])
            zr.enqueue(conn, [{"id": row["parent"] or att["key"], "kind": "top"}])
            zr.done(conn, [entry.id])
            conn.commit()
        r.decide_again(row["parent"] or att["key"])
        return None
    except ZoteroNotFound:
        entries_done(r, [entry.id])
        return None
    except ZoteroAuthError as e:  # the key may not store files here after all
        entries_done(r, [entry.id])
        record_conflict(r.ws, side.page_id, side.page_id, "zotero_rejected", result=f"Zotero refused the PDF: {e}")
        return None
    r.bytes_done += size
    if version is None:  # Zotero had these bytes already: its version read back
        found = zr.fetch_items(r.client, r.library, [att["key"]]).objects.get(att["key"])
        version = int((found or {}).get("version") or att.get("version") or 0)
    new = {**row, "state": "live", "version": int(version),
           "detail": {**without(row["detail"], "file", "first", "keep", "trashed", "sent", "body", "theirs"),
                      "md5": side.md5, "gmd5": side.md5, "doc_id": side.doc_id, "fsig": side.fsig, "size": size,
                      "filename": filename}}
    with connect_pages_db(r.ws) as conn:
        write_lock(conn)
        write_rows(conn, [new])
        zr.done(conn, [entry.id])
        _after_file(conn, new)
        conn.commit()
    if theirs is not None and not kept:  # beside a file of Zotero's no copy of could be kept: for the record
        _hold_copy(r, side, att["key"], theirs["md5"], "", filename=str(theirs.get("filename") or ""),
                   source=theirs["key"], resolved=True,
                   result=f"{BESIDE_RESULT}, which stays there (not kept here: "
                          f"{_UNKEPT.get(unkept, _UNKEPT['failed'])})")
    r.report["files_pushed"] = r.report.get("files_pushed", 0) + 1
    page_note(r, side.page_id, "replaced there" if zm else "pushed")
    return None


def _went(r: Round, key: str, md5: str) -> int | None:
    """After an upload into the attachment ``key`` whose answer did not
    come: its version when Zotero holds Gamma's file (``md5``) now, 0 when
    it holds another, None when that cannot be read."""
    try:
        found = zr.fetch_items(r.client, r.library, [key]).objects.get(key)
    except Exception as e:  # (the round's trouble, as the upload's was)
        log.info(f"[zotero] {r.ws}: whether {key}'s upload went cannot be told now: {e}")
        return None
    data = (found or {}).get("data") or {}
    if found is None or str(data.get("md5") or "").lower() != md5:
        return 0
    return int(found.get("version") or 0) or None


def _hold_copy(r: Round, side: Side, key: str, zm: str, kept: str, *, filename: str, source: str,
               result: str, resolved: bool = False) -> int:
    """The ``zotero_file`` row that keeps Zotero's file (``zm``, named
    ``filename`` in the attachment ``source``; its copy here ``kept``) for
    "theirs" once Gamma's goes to the attachment ``key`` (over Zotero's
    file there, or beside it in a new attachment), written before the
    upload goes, as a create's pending row is: its ``doc_id`` holds the
    copy from then on (gamma/upload_gc.py counts an open row's), so a
    process killed after the upload never leaves Zotero's file only in a
    copy the clean-up purges. Its ``key`` and ``synced`` (Gamma's MD5) let
    "theirs" tell when the page's PDF moved on since. One a killed upload
    left open is written over, never a second. ``resolved``: a row for the
    record (no copy kept), written once the upload went. The row's id."""
    mine = f"{side.props.get('original_filename') or side.doc_id} · {side.md5}"
    theirs = f"{filename or source} · {zm}"
    base = json.dumps({"doc_id": kept, "md5": zm, "filename": filename, "key": key, "synced": side.md5})
    if resolved:
        return record_conflict(r.ws, side.page_id, side.page_id, "zotero_file", mine=mine, theirs=theirs,
                               result=result, base=base, resolved=True)
    with connect_pages_db(r.ws) as conn:
        found = conn.execute("SELECT id FROM sync_conflicts WHERE resolved = 0 AND kind = 'zotero_file' AND "
                             "page_id = ? AND block_id = ? AND mine = ? AND theirs = ?",
                             (side.page_id, side.page_id, mine, theirs)).fetchone()
        if found:
            conn.execute("UPDATE sync_conflicts SET result = ?, base = ? WHERE id = ?", (result, base, found[0]))
            conn.commit()
            return found[0]
    return record_conflict(r.ws, side.page_id, side.page_id, "zotero_file", mine=mine, theirs=theirs, result=result,
                           base=base)


def _let_go(ws: str, conflict_id: int) -> None:
    """``_hold_copy``'s row taken back: the upload did not go."""
    with connect_pages_db(ws) as conn:
        conn.execute("DELETE FROM sync_conflicts WHERE id = ? AND resolved = 0", (conflict_id,))
        conn.commit()


# --- Zotero's full storage (413) --------------------------------------------------------

def _quota_full(r: Round, entry: zr.Entry, row: dict, att: dict, message: str):
    """Zotero's storage is full (413): no PDF goes until Sync now, a settings
    change or ``QUOTA_WAIT_S``; an attachment Gamma made that has no file yet
    goes to Zotero's trash rather than stay fileless; its row waits
    (``waiting.files.quota``)."""
    if not quota_active(r):
        until = format_stamp((datetime.now(timezone.utc) + timedelta(seconds=QUOTA_WAIT_S)).replace(tzinfo=None))
        r.quota_until = until
        patch_status(r.ws, lambda s: {**s, "waiting": {**(s.get("waiting") or {}), "quota_until": until}})
    until = r.quota_until
    data = att.get("data") or {}
    detail = {**row["detail"], "file": "quota"}
    version = int(att.get("version") or 0)
    if row["detail"].get("made") and not data.get("md5"):
        outcome = zr.trash(r.client, r.library, "attachments", [(att["key"], version)]).get(att["key"])
        if outcome is not None and outcome.status in zr.LANDED:
            detail["trashed"] = True
            version = int(outcome.version or version)
    new = {**row, "state": "waiting", "version": version, "detail": detail}
    with connect_pages_db(r.ws) as conn:
        write_lock(conn)
        write_rows(conn, [new])
        zr.done(conn, [entry.id])
        conn.commit()
    log.info(f"[zotero] {r.ws}: Zotero's storage is full ({message}); PDFs wait until {until}")
    return None


def quota_step(r: Round) -> None:
    """Zotero's full storage, as the round finds it: a wait lifted (24 h
    passed, Sync now, a settings change) brings back the PDFs it held,
    their trashed attachments restored when they go: by a round that may
    send PDFs (``may_send_files``; a receive-only one leaves them in
    Zotero's trash, still waiting, for the first that may)."""
    waiting = r.link["status"].get("waiting") or {}
    until = waiting.get("quota_until") or ""
    if until and until <= page_now():
        patch_status(r.ws, without_quota_wait)
        until = ""
    r.quota_until = until
    if until:
        return
    with connect_pages_db(r.ws) as conn:
        held = [(row["key"], row["page_id"], row["detail"], int(row["version"])) for row in read_rows(
           conn, "kind = 'attachment' AND state = 'waiting' AND json_extract(detail, '$.file') = 'quota'").values()]
        if not held:
            return
        write_lock(conn)  # each page decides again; an upload into an attachment Zotero holds is asked for again
        zr.enqueue(conn, [entry for key, page_id, detail, version in held for entry in (
            [zr.page_entry(page_id)] + ([zr.file_entry(key)] if version > 0 and not detail.get("trashed") else []))])
        conn.commit()
    if may_send_files(r):
        _restore_trashed_attachments(r, [(k, d) for k, _, d, _ in held if d.get("trashed")])


def _restore_trashed_attachments(r: Round, held) -> None:
    """The fileless attachments a 413 sent to Zotero's trash, brought back
    once Zotero's storage may take their files again; their uploads queued."""
    if not held:
        return
    with connect_pages_db(r.ws) as conn:
        rows = read_rows(conn, "key IN (SELECT value FROM json_each(?))", (json.dumps([k for k, _ in held]),))
    outcomes = zr.restore(r.client, r.library, "attachments", [(k, int(rows[k]["version"])) for k, _ in held
                                                                 if k in rows])
    with connect_pages_db(r.ws) as conn:
        write_lock(conn)
        for key, outcome in outcomes.items():
            if outcome.status in zr.LANDED and key in rows:
                row = rows[key]
                row.update(version=int(outcome.version or row["version"]), state="live",
                           detail=without(row["detail"], "trashed", "file"))
                write_rows(conn, [row])
                zr.enqueue(conn, [zr.file_entry(key)])
        conn.commit()
