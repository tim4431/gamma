"""Items Zotero removed for good, in a Zotero link's round (see
docs/dev/zotero_sync.md#items-and-pages): ``items_gone`` applies the keys a
listing's ``/deleted`` names, ``absent`` decides on a key a unit asked
Zotero for and did not get, and ``send_back`` sends a note page or a
standalone PDF's page that ``items_gone`` kept back to Zotero under its
fresh key (an item's page goes back through gamma/zotero_sync_pages.py's
``_resend``). The removals of an item's notes and annotations are
gamma/zotero_sync_blocks.py's (``children_gone``), those of collections
gamma/zotero_sync_folders.py's.

Of the engine's parts it imports only gamma/zotero_sync_blocks.py,
gamma/zotero_sync_pdfs.py, gamma/zotero_sync_reads.py and
gamma/zotero_sync_rows.py.
"""

import json

from . import zotero_local, zotero_merge
from . import zotero_remote as zr
from .blocks_store import write_lock
from .db import connect_pages_db, page_now
from .logbuf import log
from .ops import OpError
from .zotero_sync_blocks import NOTE_MAX, NOTE_TOO_LONG, children_gone, note_page_html
from .zotero_sync_pdfs import may_send_files, pdf_filename, quota_active
from .zotero_sync_reads import (Side, collection_map, entries_done, gamma_item, gamma_note, gamma_standalone, page_side,
                                zotero_note, zotero_standalone)
from .zotero_sync_rows import (HELD, STOPS_ROUND, Round, drop_rows, new_row, page_note, read_rows, record_conflict,
                               signature, without, write_rows)
from .zotero_web import ZoteroError, item_uri, random_key

# What a page changed here becomes once Zotero removed its object for good (``_goes_back``), in the words of
# its ``zotero_deleted_edited`` row, by the kind of that object
_GOES_BACK = {"item": "it goes back as a new item", "note": "it goes back as a new note",
              "pdf": "it goes back as a new PDF", "": "it stays here, unlinked"}
_WHAT = {"item": "item", "note": "note", "attachment": "PDF"}


def items_gone(r: Round, keys) -> None:
    """Items Zotero removed for good (``/deleted``): a page unchanged since it
    last synced goes to Recently deleted, its rows with it; one changed here
    since stays, its rows of the removed object gone, and goes back under a
    fresh key (Zotero takes no removed key again) as ``_goes_back`` says: an
    item's page as a new item, a note page as a new standalone note, a
    standalone PDF's page as a new standalone PDF while the link sends PDFs
    without a record (else it is a page like any other of Gamma's from then
    on); its ``zotero_deleted_edited`` row says which. An attachment that
    was a page's PDF leaves the page its file, Gamma's alone (its item, if
    any, decides again; a file Gamma holds unchanged is not sent back:
    ``declined_md5``)."""
    keys = list(dict.fromkeys(keys))
    if not keys:
        return
    with connect_pages_db(r.ws) as conn:
        rows = read_rows(conn, "key IN (SELECT value FROM json_each(?))", (json.dumps(keys),))
        cmap = collection_map(conn)
        sides = {page_id: page_side(conn, r.ws, page_id) for page_id in {row["page_id"] for row in rows.values()
                                                                    if row["page_id"]}}
    inverse = {fid: key for key, fid in cmap.items()}
    doomed, kept, requeue, simple, children = [], [], [], [], {}
    for key, row in rows.items():
        side = sides.get(row["page_id"])
        is_target = row["kind"] == "item" or (row["kind"] in ("attachment", "note") and not row["parent"])
        if not is_target:
            if row["kind"] in ("note", "annotation") and row["page_id"]:
                children.setdefault(row["page_id"], []).append(row)
            else:
                simple.append(key)
            if row["kind"] in ("attachment", "note", "annotation") and row["parent"] and row["parent"] not in keys:
                requeue.append(row["parent"])  # an annotation's parent is its PDF: its item is queued below
            continue
        if side is None or not side.live:
            simple.append(key)
            continue
        if row["kind"] == "note":
            with connect_pages_db(r.ws) as conn:
                G = gamma_note(page_side(conn, r.ws, row["page_id"], note=True), inverse)
        else:
            G = (gamma_item(side, inverse, row["zotero_type"]) if row["kind"] == "item"
                 else gamma_standalone(side, inverse))
        # (a standalone PDF's page is changed here by its file too, as its own unit reads it)
        refiled = (row["kind"] == "attachment" and bool(row["detail"].get("gmd5"))
                   and side.md5 != row["detail"]["gmd5"])
        if G == row["gbase"] and not refiled and row["state"] != "pending":
            doomed.append((row, side))
        else:
            kept.append((row, side, _goes_back(r, row, side)))
    declined = []  # the PDFs Zotero removed that their pages hold unchanged: their items send them no more
    for key in simple:
        row, side = rows[key], sides.get(rows[key]["page_id"])
        if (row["kind"] == "attachment" and row["parent"] and row["parent"] not in keys and side is not None
                and side.live and side.md5 and (row["detail"].get("gmd5") or "") == side.md5):
            declined.append((row["parent"], side.md5))
    with connect_pages_db(r.ws) as conn:
        write_lock(conn)
        for parent, md5 in declined:
            item = read_rows(conn, "key = ? AND kind = 'item'", (parent,)).get(parent)
            if item is not None:
                write_rows(conn, [{**item, "detail": {**item["detail"], "declined_md5": md5}}])
        drop_rows(conn, simple)
        for row, side, again in kept:
            drop_rows(conn, _rows_of(conn, row, side.page_id))
            fresh = _fresh_row(row, side.page_id, again)
            if fresh is not None:
                write_rows(conn, [fresh])
            zr.enqueue(conn, [zr.page_entry(side.page_id)])
        zr.enqueue(conn, _tops_of(conn, requeue))
        conn.commit()
    deleted = 0
    for row, side in doomed:
        def forget(conn, row=row, page_id=side.page_id):
            drop_rows(conn, _rows_of(conn, row, page_id))

        def check(conn, stamp=dict(side.stamp)):  # the page as it was decided on: an edit since keeps it
            return zotero_local.stamps(conn, list(stamp)) == stamp

        try:
            if zotero_local.trash_page(r.ws, side.page_id, check=check, rows=forget) is not None:
                deleted += 1
                page_note(r, side.page_id, "deleted here")
        except zotero_local.Changed:  # changed as it went: its unit finds the item gone and keeps it (above)
            with connect_pages_db(r.ws) as conn:
                write_lock(conn)
                zr.enqueue(conn, [zr.page_entry(side.page_id)])
                conn.commit()
        except OpError as e:
            log.warning(f"[zotero] {r.ws}: {side.page_id} stayed: {e}")
    r.report["pages_deleted"] = r.report.get("pages_deleted", 0) + deleted
    for row, side, again in kept:
        record_conflict(r.ws, side.page_id, side.page_id, "zotero_deleted_edited", mine="(changed here)",
                        result=f"Zotero deleted this {_WHAT.get(row['kind'], 'item')} for good after it was changed "
                               f"here: {_GOES_BACK[again]}")
    for page_id, rows_of in children.items():
        children_gone(r, page_id, rows_of)


def _goes_back(r: Round, row: dict, side: Side) -> str:
    """What a page changed here becomes once Zotero removed its object
    ``row`` for good: "item" (an item's page, made a new item under a fresh
    key; a standalone PDF's page that ``wants_item`` makes one once its rows
    went, as any page of Gamma's), "note" (a note page, while the key reads
    notes), "pdf" (a standalone PDF's page, while the link sends PDFs
    without a record and may upload them) or "" (it stays here, unlinked)."""
    if row["kind"] == "item":
        return "item"
    if row["kind"] == "note":
        return "note" if r.access.get("notes") else ""
    sends = r.link["send_unrecorded"]
    if sends and side.doc_id and r.link["upload"] and r.access.get("files"):
        return "pdf"
    want = zotero_merge.wants_item(side.props, now=page_now(), send_unrecorded=sends,
                                   created_at=side.block.get("created_at") or "")
    if want == zotero_merge.WANT_WAIT:  # (its lookup may still bring a record: a document unless it does)
        want = zotero_merge.WANT_DOCUMENT if sends else ""
    return "item" if want in (zotero_merge.WANT_PAPER, zotero_merge.WANT_DOCUMENT) else ""


def _rows_of(conn, row: dict, page_id: str) -> list[str]:
    """The keys of the rows that go with the page's object Zotero removed
    for good (``row``): the page's item and attachment rows, the object's
    own row (a note page's) and its note's pictures."""
    return list(read_rows(conn, "(page_id = ? AND kind IN ('item', 'attachment')) OR key = ? "
                                "OR (kind = 'image' AND parent = ?)", (page_id, row["key"], row["key"])))


def _fresh_row(row: dict, page_id: str, again: str) -> dict | None:
    """The pending row a page kept by ``items_gone`` goes back to Zotero
    under (a key Zotero never had, nothing sent yet): its new item's (sent
    by ``zotero_sync_pages._resend``), its new standalone note's or PDF's
    (``send_back``); None when nothing goes under a fresh key (a standalone
    PDF's page becoming an item does so as any page of Gamma's)."""
    common = {"page_id": page_id, "block_id": page_id, "version": -1, "state": "pending", "gsig": "",
              "detail": {"sent": {}}}
    if row["kind"] == "item" and again == "item":
        return new_row(random_key(), "item", zotero_type=row["zotero_type"], **common)
    if row["kind"] == "note" and again == "note":
        return new_row(random_key(), "note", zotero_type="note", **common)
    if row["kind"] == "attachment" and again == "pdf":
        return new_row(random_key(), "attachment", zotero_type="attachment", **common)
    return None


def send_back(r: Round, row: dict, side: Side, cmap: dict, entry: zr.Entry):
    """A note page or a standalone PDF's page that ``items_gone`` kept
    (``row``: pending, or refused, under a fresh key), sent to Zotero as a
    new standalone note, or a new standalone PDF attachment whose file the
    file queue uploads once it lands, made from the page as it is now (its
    note or title, its labels as tags, its folders' collections). The row
    holds what is sent before the create goes (``detail.sent``), so a
    create whose answer was lost is confirmed by the listing that finds it,
    and a create Zotero answers it holds under that key already is that
    one, confirmed by its unit. A page gone for good, or a PDF page without
    its PDF any more, takes the row with it; one in Recently deleted, or
    whose kind may not travel now (notes access, PDF uploads), is looked at
    again when it or the link changes; a PDF waits while Zotero's storage
    is full; a create Zotero refused goes again only once the page
    changed. The page's ``zotero_key`` (and a note page's ``zotero_note``)
    names the new object once Zotero holds it."""
    page_id, key, note = side.page_id, row["key"], row["kind"] == "note"
    if side.block is None or (not note and not side.doc_id):
        with connect_pages_db(r.ws) as conn:
            write_lock(conn)
            drop_rows(conn, [key])
            zr.done(conn, [entry.id])
            conn.commit()
        return None
    if not side.live or not (r.access.get("notes") if note else may_send_files(r)):
        entries_done(r, [entry.id])
        return None
    if not note and quota_active(r):  # no fileless attachment while Zotero's storage is full
        with connect_pages_db(r.ws) as conn:
            write_lock(conn)
            zr.enqueue(conn, [zr.page_entry(page_id, next_at=r.quota_until, reason="Zotero's storage is full")])
            conn.commit()
        r.excluded.add(entry.id)
        return HELD
    inverse = {fid: k for k, fid in cmap.items()}
    html = ""
    if note:
        with connect_pages_db(r.ws) as conn:
            side = page_side(conn, r.ws, page_id, note=True)
            html = note_page_html(conn, page_id)
    G = gamma_note(side, inverse) if note else gamma_standalone(side, inverse)
    sig = signature(G)
    if row["state"] == "rejected" and row["gsig"] == sig:  # refused as it is: it goes again once the page changes
        entries_done(r, [entry.id])
        return None
    tags = [{"tag": name.strip()} for name in side.labels.values() if name.strip()]
    filename = "" if note else pdf_filename(side)
    body = ({"itemType": "note", "note": html, "tags": tags, "collections": G["collections"]} if note else
            {"itemType": "attachment", "linkMode": "imported_file", "title": G["title"] or filename,
             "contentType": zotero_merge.PDF_TYPE, "filename": filename, "tags": tags,
             "collections": G["collections"]})
    pending = {**row, "state": "pending", "gsig": sig, "detail": {**without(row["detail"], "reason"), "sent": G}}
    outcome, error, refused, retry, stop, queue = None, None, "", False, None, []
    if note and len(html) > NOTE_MAX:
        refused = NOTE_TOO_LONG
    else:
        with connect_pages_db(r.ws) as conn:  # what goes, before it goes: a lost answer is confirmed by the listing
            write_lock(conn)
            write_rows(conn, [pending])
            conn.commit()
        writes = zr.Writes(r.client, r.library)
        writes.create("items" if note else "attachments", key, body)
        outcome = writes.flush()[key]
        error = writes.error
        stop = error if error is not None and zr.error_kind(error) in STOPS_ROUND else None
    data = ((outcome.data or {}).get("data") or {}) if outcome is not None else {}
    landed, ours, fresh = None, False, None
    if outcome is None:
        pass
    elif outcome.status in (zr.OK, zr.UNCHANGED):
        Z = (zotero_note(r, data, cmap.keys()) if note else zotero_standalone(r, data, cmap.keys())) if data else G
        detail = without(pending["detail"], "sent")
        if not note:
            detail.update(md5="", gmd5="", filename=filename, link_mode="imported_file")
        landed = {**pending, "state": "live", "version": int(outcome.version or 0), "zbase": Z, "gbase": G,
                  "detail": detail}
        if not note:  # its file goes by the file queue
            queue.append({"id": zr.FILE_PREFIX + key, "kind": "file", "version": landed["version"],
                          "priority": zr.PAGE_PRIORITY})
    elif outcome.status == zr.EXISTS and data.get("itemType") == body["itemType"] and not data.get("parentItem"):
        ours = True  # its create went before (an answer lost): its unit confirms it from the pending row
        queue.append({"id": key, "kind": "top"})
    elif outcome.status == zr.EXISTS:  # another object holds the key: a fresh one, sent at once
        fresh = {**pending, "key": random_key()}
        queue.append(zr.page_entry(page_id))
    elif outcome.status == zr.REJECTED:
        refused = outcome.message
    else:
        retry = True

    def write(conn):
        if landed is not None:
            write_rows(conn, [landed])
        elif refused:
            write_rows(conn, [{**pending, "state": "rejected", "detail": {**pending["detail"], "reason": refused}}])
        elif fresh is not None:
            drop_rows(conn, [key])
            write_rows(conn, [fresh])
        zr.done(conn, [entry.id])
        zr.enqueue(conn, queue)
        if retry:
            zr.backoff(conn, entry.id, str(error or "a write did not land"), now=page_now(), kind="page")

    uri = item_uri(r.library, key)
    ops = [zotero_local.props_op(page_id, {"zotero_key": uri, **({"zotero_note": uri} if note else {})})] \
        if landed is not None or ours else []
    if ops:
        try:
            zotero_local.apply(r.ws, page_id, ops, rows=write)
        except (zotero_local.Changed, zotero_local.Moved, OpError):
            ops = []
    if not ops:
        with connect_pages_db(r.ws) as conn:
            write_lock(conn)
            write(conn)
            conn.commit()
    if landed is not None:
        r.report["pages_pushed"] = r.report.get("pages_pushed", 0) + 1
        page_note(r, page_id, "created there")
    if refused:
        record_conflict(r.ws, page_id, page_id, "zotero_rejected", result=f"Zotero refused this: {refused}")
    if retry:
        r.excluded.add(entry.id)
    if stop is not None:
        raise stop
    return None


def _tops_of(conn, keys) -> list[dict]:
    """The queue entries of the top-level items ``keys`` (parents of rows
    that went) belong to: an attachment's or a note's own item, a standalone
    PDF itself; a key with no row is queued as a child, whose unit finds its
    item. Never a child as a top-level unit: one would read as a child made
    standalone."""
    out = {}
    for key in dict.fromkeys(keys):
        row = read_rows(conn, "key = ?", (key,)).get(key)
        if row is None:
            out[key] = {"id": key, "kind": "child"}
            continue
        top = row["parent"] or key if row["kind"] in ("attachment", "note") else key
        found = read_rows(conn, "key = ?", (top,)).get(top) if top != key else row
        out[top] = {"id": top, "kind": "top", "version": int(found["version"]) if found else 0}
    return list(out.values())


def absent(r: Round, key: str, entry: zr.Entry) -> None:
    """A key Zotero did not answer by key: removed for good (``/deleted``),
    or a read that missed it, tried again (and given up after a while)."""
    if zr.hard_deleted(r.client, r.library, [key], kind="items"):
        # first: what the removal queues (a page that stays, sent as a new item) stays queued
        entries_done(r, [entry.id])
        items_gone(r, [key])
        return
    raise ZoteroError(f"Zotero did not answer for {key}")
