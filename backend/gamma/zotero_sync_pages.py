"""Gamma's pages in a Zotero link's round (see
docs/dev/zotero_sync.md#items-and-pages): a page the local change feed
queued (``unit_page``). One the link keeps is reconciled through its item's
unit when it differs from what last synced; a paper page Zotero has no item
of becomes one, its create sent with those of the other pages due to become
items (``_create_items``: 50 a request) and its PDF sent as a new
attachment; a standalone PDF whose record a person made or confirmed gets a
parent item, the attachment moved under it (``_send_move``); a create whose
answer was lost, or that Zotero refused, is derived again from the page as
it is (``_resend``).

Of the engine's parts it imports gamma/zotero_sync_rows.py,
gamma/zotero_sync_reads.py, gamma/zotero_sync_blocks.py,
gamma/zotero_sync_pdfs.py, gamma/zotero_sync_items.py and
gamma/zotero_sync_gone.py.
"""

from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from . import zotero_fields, zotero_local, zotero_merge
from . import zotero_remote as zr
from .bibtex import pinned_key
from .blocks_store import FOLDERS, LABELS, write_lock
from .db import connect_pages_db, page_now, parse_stamp
from .ops import OpError
from .zotero_sync_blocks import blocks_dirty
from .zotero_sync_gone import absent, send_back
from .zotero_sync_items import reconcile
from .zotero_sync_pdfs import attachment_body, may_send_files, pdf_filename, quota_active
from .zotero_sync_reads import (HELD_ROWS, READ_AHEAD, Side, collection_map, entries_done, gamma_item, gamma_note,
                                gamma_standalone, moving_pdf, page_side, read_unit, zotero_item)
from .zotero_sync_rows import (HELD, SETTLED, STOPS_ROUND, Round, drop_rows, new_row, page_note, read_rows,
                               record_conflict, signature, without, write_rows)
from .zotero_web import ZoteroError, item_uri, object_key, random_key


def _page_dirty(r: Round, row: dict, side: Side, rows: dict, cmap: dict) -> bool:
    """Whether a page whose item the link keeps differs from what last synced:
    its record, tags or folders, its trash state, its PDF; a first link's pair
    not settled (or a restore's: ``detail.restore``); a standalone PDF whose
    record now earns a parent item."""
    if row["detail"].get("first") or row["detail"].get("restore"):
        return True
    if side.block is None or side.trashed:
        return row["state"] != "trashed"
    if row["state"] == "trashed":
        return True
    inverse = {fid: key for key, fid in cmap.items()}
    if row["kind"] == "attachment":
        if zotero_merge.standalone_gets_parent(side.props.get("meta")):
            return True
        G = gamma_standalone(side, inverse)
    elif row["kind"] == "note":
        if not r.access.get("notes"):
            return False
        with connect_pages_db(r.ws) as conn:
            side = page_side(conn, r.ws, side.page_id, note=True)
        return gamma_note(side, inverse) != row["gbase"]
    else:
        G = gamma_item(side, inverse, row["zotero_type"])
    if G != row["gbase"]:
        return True
    bound = next((b for b in rows.values() if b["kind"] == "attachment" and b["page_id"] == side.page_id
                  and b["state"] in HELD_ROWS), None)
    if bound is None:
        if side.doc_id:  # (a stored PDF; its MD5 is not needed to say so)
            return True
    elif side.md5 != (bound["detail"].get("gmd5") or "") and bool(side.md5 or bound["detail"].get("gmd5")):
        return True
    return blocks_dirty(r, side.page_id, side.md5, bound, notes=row["kind"] == "item")


def unit_page(r: Round, entry: zr.Entry):
    """A Gamma page to reconcile: a page the link keeps is reconciled through
    its item's unit when it differs from what last synced; a paper page Zotero
    has no item of becomes one (a page with a PDF and no record first waits
    for its lookup); a standalone PDF whose record a person made or confirmed
    gets a parent item. Receive-only rounds leave Gamma's side for later (a
    switch to two-way starts the local feed over). ``SETTLED`` when there is
    nothing to do."""
    page_id = entry.id[len(zr.PAGE_PREFIX):]
    if page_id in (FOLDERS, LABELS) or not r.pushes:
        entries_done(r, [entry.id])
        return SETTLED
    with connect_pages_db(r.ws) as conn:
        rows = read_rows(conn, "page_id = ? AND kind IN ('item', 'attachment')", (page_id,))
        side = page_side(conn, r.ws, page_id)
        cmap = collection_map(conn)
    item = next((row for row in rows.values() if row["kind"] == "item"), None)
    target = item or next((row for row in rows.values() if row["kind"] == "attachment" and not row["parent"]), None)
    if target is None:
        with connect_pages_db(r.ws) as conn:  # (by the page's rows: ``+parent`` keeps the planner off every
            # top-level row's parent index entry)
            target = next((row for row in read_rows(conn, "page_id = ? AND kind = 'note' AND +parent = '' "
                                                    "AND block_id = page_id", (page_id,)).values()), None)
    if target is not None:
        if target["state"] in ("pending", "rejected") and int(target["version"]) <= 0:
            if target["kind"] != "item":  # a note page or a standalone PDF going back under a fresh key
                return send_back(r, target, side, cmap, entry)
            return _resend(r, target, side, rows, cmap, entry)
        moving = moving_pdf(rows) if target["kind"] == "item" else None
        if moving is not None and target["state"] == "live" and side.live:
            return _send_move(r, moving, target, entry)
        if not _page_dirty(r, target, side, rows, cmap):
            entries_done(r, [entry.id])
            return SETTLED
        if target["kind"] == "attachment" and side.live and zotero_merge.standalone_gets_parent(side.props.get("meta")):
            # a standalone PDF whose record a person made or confirmed: a parent item made from it, the
            # attachment moved under it
            return _create_item(r, side, cmap, entry, document=False, parent_of=target)
        unit = read_unit(r, target["key"])
        if unit.item is None:
            return absent(r, target["key"], entry)
        return reconcile(r, unit, [entry.id])
    if not side.live:
        entries_done(r, [entry.id])
        return SETTLED
    created = side.block.get("created_at") or ""
    want = zotero_merge.wants_item(side.props, now=page_now(), send_unrecorded=r.link["send_unrecorded"],
                                   created_at=created)
    if want == zotero_merge.WANT_WAIT:  # its lookup may still bring a record: looked at again then
        at = parse_stamp(created) or datetime.now(timezone.utc)  # (a naive stamp is UTC: ``zr.when``)
        with connect_pages_db(r.ws) as conn:
            write_lock(conn)
            zr.enqueue(conn, [zr.page_entry(page_id, next_at=at + timedelta(seconds=zotero_merge.LOOKUP_WAIT_S + 1),
                                            reason="waits for its record")])
            conn.commit()
        r.excluded.add(entry.id)
        return HELD
    if want not in (zotero_merge.WANT_PAPER, zotero_merge.WANT_DOCUMENT):
        entries_done(r, [entry.id])
        return SETTLED
    # its create goes with those of the other pages due to become items now (50 a request)
    return _create_items(r, cmap, [_Create(side, entry, want == zotero_merge.WANT_DOCUMENT), *_more_creates(r, entry)])


# --- pages made items -------------------------------------------------------------------

def _item_body(side: Side, cmap: dict, *, document: bool) -> dict:
    """A new item holding a page (``zotero_fields.fields_from_meta``), with
    its labels as manual tags and its folders' collections."""
    props = side.props
    inverse = {fid: key for key, fid in cmap.items()}
    body = zotero_fields.fields_from_meta(props.get("meta") or {}, pinned_key(props),
                                          "document" if document else None, side.title)
    body["tags"] = [{"tag": name.strip()} for name in side.labels.values() if name.strip()]
    body["collections"] = sorted({inverse[f] for f in side.folders if f in inverse})
    return body


def _stops(writes: zr.Writes) -> ZoteroError | None:
    """The failure of a flush that stops the round, None for none."""
    return writes.error if writes.error is not None and zr.error_kind(writes.error) in STOPS_ROUND else None


def _created(row: dict, outcome) -> tuple[dict | None, str]:
    if outcome.status in (zr.OK, zr.UNCHANGED):
        return {**row, "state": "live", "version": int(outcome.version or 0),
                "saved": (outcome.data or {}).get("data") or {}}, ""
    if outcome.status == zr.REJECTED:
        return None, f"rejected: {outcome.message}"
    return None, "retry"


def _send_creates(r: Round, stage: str, creates) -> tuple[list, list, ZoteroError | None]:
    """Send the creates of ``creates`` (``[(row, body, resend)]``, each row
    written pending already) in one ``Writes`` (50 objects a request) and
    settle what Zotero answers for each: saved → the row live; a
    412 (Zotero holds the key) on the same kind of object under the same
    parent → ours (its create landed, or it is in Zotero's trash): it is
    brought back with Gamma's fields, unless the create went before under
    that key (``resend``): then it is ours as Zotero holds it now, with what
    the user did to it since, and its unit confirms it (``(None,
    "confirm")``); on another → a fresh key, its pending row written first,
    and the create sent again under it. → ``(results, rows, stop)``: per
    create ``(the row landed, "")``, ``(None, "retry")`` or ``(None,
    "rejected: <Zotero's sentence>")``; the rows under the keys last sent;
    the failure that stops the round (None for none), the creates it held
    back read as retries."""
    rows = [row for row, _, _ in creates]
    out: list = [(None, "retry")] * len(creates)
    todo, stop = list(range(len(creates))), None
    for _ in range(3):
        if not todo or stop is not None:
            break
        writes = zr.Writes(r.client, r.library)
        for i in todo:
            writes.create(stage, rows[i]["key"], creates[i][1])
        outcomes = writes.flush()
        stop = _stops(writes)
        again, ours, fresh = [], [], []
        patch = zr.Writes(r.client, r.library)
        for i in todo:
            row, body, resend = rows[i], creates[i][1], creates[i][2]
            outcome = outcomes[row["key"]]
            data = (outcome.data or {}).get("data") or {}
            if outcome.status == zr.EXISTS and outcome.data and data.get("itemType") == body.get("itemType") and (
                    (data.get("parentItem") or "") == (body.get("parentItem") or "")):
                if resend:
                    out[i] = (None, "confirm")
                    continue
                patch.update(stage, row["key"], outcome.version, {
                    **{k: v for k, v in body.items() if k not in ("itemType", "parentItem", "linkMode")},
                    "deleted": False})
                ours.append(i)
            elif outcome.status == zr.EXISTS and outcome.data:
                fresh.append((row["key"], i))
                rows[i] = {**row, "key": random_key()}
                again.append(i)
            else:
                out[i] = _created(row, outcome)
        if ours and stop is None:  # (held back by a round that stops: retries)
            outcomes = patch.flush()
            stop = _stops(patch)
            for i in ours:
                out[i] = _created(rows[i], outcomes[rows[i]["key"]])
        if fresh:
            with connect_pages_db(r.ws) as conn:
                write_lock(conn)
                drop_rows(conn, [old for old, _ in fresh])
                write_rows(conn, [rows[i] for _, i in fresh])
                conn.commit()
        todo = again
    return out, rows, stop


class _Create(NamedTuple):
    """A page Zotero has no item of, to make one of (``_create_items``)."""
    side: Side
    entry: zr.Entry
    document: bool
    key: str = ""                  # the key its create went under before (a resend); "" for a new one
    parent_of: dict | None = None  # a standalone PDF's row, moved under the item once it is made


def _create_item(r: Round, side: Side, cmap: dict, entry: zr.Entry, *, document: bool, key: str = "",
                 parent_of: dict | None = None):
    """One page made an item (``_create_items``)."""
    return _create_items(r, cmap, [_Create(side, entry, document, key, parent_of)])


def _more_creates(r: Round, entry: zr.Entry) -> list[_Create]:
    """The other due pages that become items now (a paper, or a PDF without a
    record the link sends, no row of the link's on it), so their creates go
    with this one's (``READ_AHEAD`` a round trip at most); a page whose
    lookup it still waits for, or any other, is its own unit's."""
    out = []
    with connect_pages_db(r.ws) as conn:
        due = zr.take(conn, now=page_now(), limit=READ_AHEAD, kinds="page", exclude=r.excluded | {entry.id})
        for other in due:
            page_id = other.id[len(zr.PAGE_PREFIX):]
            if page_id in (FOLDERS, LABELS) or conn.execute(
                    "SELECT 1 FROM zotero_objects WHERE page_id = ? LIMIT 1", (page_id,)).fetchone():
                continue
            side = page_side(conn, r.ws, page_id)
            if not side.live:
                continue
            want = zotero_merge.wants_item(side.props, now=page_now(), send_unrecorded=r.link["send_unrecorded"],
                                           created_at=side.block.get("created_at") or "")
            if want in (zotero_merge.WANT_PAPER, zotero_merge.WANT_DOCUMENT):
                out.append(_Create(side, other, want == zotero_merge.WANT_DOCUMENT))
    return out[:READ_AHEAD - 1]


def _create_items(r: Round, cmap: dict, creates: list[_Create]):
    """Pages Zotero has no item of, made items (see
    docs/dev/zotero_sync.md#keys-and-identity): each row written pending
    before the creates are sent (so a create whose answer was lost is
    confirmed by the next listing, never made twice), the items sent
    together (``_send_creates``: 50 a request), then, under the keys they
    landed with, each one's PDF as a new attachment when PDFs may go, sent
    together too (uploaded by the file queue), or, with ``parent_of`` (a
    standalone PDF's row), that attachment moved under it (``_send_move``).
    Each page's rows, its ``zotero_key`` and its entry are written in its
    own transaction."""
    inverse = {fid: k for k, fid in cmap.items()}
    plans, pending = [], []
    for c in creates:
        key = c.key or object_key(r.ws, r.library, "item", "", c.side.page_id, "")
        body = _item_body(c.side, cmap, document=c.document)
        G = gamma_item(c.side, inverse, body["itemType"])
        row = new_row(key, "item", page_id=c.side.page_id, block_id=c.side.page_id, zotero_type=body["itemType"],
                      version=-1, state="pending", gsig=signature(G), detail={"sent": G})
        parent_of = c.parent_of
        if parent_of is not None:  # what the item is made for, kept until the move lands, whatever fails meanwhile
            row["detail"]["adopts"] = parent_of["key"]
            parent_of = {**parent_of, "detail": {**parent_of["detail"], "parent_to": key}}
            pending.append(parent_of)
        pending.append(row)
        plans.append({"c": c, "row": row, "body": body, "G": G, "parent_of": parent_of, "rows": [], "ops": [],
                      "queue": [], "item": None})
    with connect_pages_db(r.ws) as conn:
        write_lock(conn)
        write_rows(conn, pending)
        conn.commit()
    results, sent, stop = _send_creates(r, "items", [(p["row"], p["body"], bool(p["c"].key)) for p in plans])
    atts = []  # (plan, its attachment's pending row, body)
    for p, (landed, why), last in zip(plans, results, sent):
        side, page_id = p["c"].side, p["c"].side.page_id
        p.update(landed=landed, retry=why == "retry")
        if why == "confirm":  # an earlier create landed: its unit confirms it, with what Zotero holds now
            p["queue"].append({"id": last["key"], "kind": "top"})
        if landed is None:
            if why.startswith("rejected"):
                refused = {**last, "state": "rejected", "detail": {**last["detail"], "reason": why[len("rejected: "):]}}
                p["rows"].append(refused)
                record_conflict(r.ws, page_id, page_id, "zotero_rejected",
                                result=f"Zotero refused this: {refused['detail']['reason']}")
            continue
        saved = landed.pop("saved")
        item = {**landed, "zbase": zotero_item(r, saved, cmap.keys()) if saved else p["G"], "gbase": p["G"],
                "detail": {**without(landed["detail"], "sent", "reason"),
                           "collections": list(saved.get("collections") or [])}}
        p["item"] = item
        p["rows"].append(item)
        p["ops"].append(zotero_local.props_op(page_id, {"zotero_key": item_uri(r.library, item["key"])}))
        if p["parent_of"] is not None or not side.doc_id or not may_send_files(r):
            continue
        filename = pdf_filename(side)
        att_key = object_key(r.ws, r.library, "attachment", item["key"], page_id, side.md5)
        if quota_active(r):  # Zotero's storage is full: it waits, and no fileless attachment goes
            p["rows"].append(new_row(att_key, "attachment", page_id=page_id, block_id=page_id, parent=item["key"],
                                     zotero_type="attachment", state="waiting", gsig=side.md5,
                                     detail={"made": True, "md5": "", "gmd5": "", "filename": filename,
                                             "file": "quota"}))
            continue
        att_body = attachment_body(item["key"], filename)
        atts.append((p, new_row(att_key, "attachment", page_id=page_id, block_id=page_id, parent=item["key"],
                                zotero_type="attachment", version=-1, state="pending", gsig=side.md5,
                                detail={"sent": att_body, "made": True, "md5": "", "gmd5": "",
                                        "filename": filename}), att_body))
    if stop is not None:  # a round that stops sends no attachment: each page is looked at again later
        for p, _, _ in atts:
            p["retry"] = True
    elif atts:
        with connect_pages_db(r.ws) as conn:
            write_lock(conn)
            write_rows(conn, [x for p, att, _ in atts for x in (p["item"], att)])
            conn.commit()
        made, _, stop = _send_creates(r, "attachments", [(att, body, False) for _, att, body in atts])
        for (p, _, _), (att, how) in zip(atts, made):
            if att is not None:
                att.pop("saved")
                p["rows"].append({**att, "detail": without(att["detail"], "sent")})
                p["queue"].append({"id": zr.FILE_PREFIX + att["key"], "kind": "file", "version": att["version"],
                                   "priority": zr.PAGE_PRIORITY})
            else:
                p["retry"] = p["retry"] or how == "retry"
    for p in plans:
        _create_written(r, p, stop)
    if stop is not None:
        raise stop
    for p in plans:
        if p["parent_of"] is not None and p["item"] is not None:
            _send_move(r, p["parent_of"], p["item"], p["c"].entry)
    return None


def _create_written(r: Round, p: dict, stop) -> None:
    """One page's part of ``_create_items``: its rows, its ``zotero_key`` and
    its entry (done; backed off when a write did not land) in one
    transaction; a create the stopped round held back leaves its entry due."""
    entry, page_id = p["c"].entry, p["c"].side.page_id
    if stop is not None and p["landed"] is None and p["retry"]:
        return

    def write(conn):
        write_rows(conn, p["rows"])
        zr.done(conn, [entry.id])
        zr.enqueue(conn, p["queue"])
        if p["retry"]:
            zr.backoff(conn, entry.id, "a write did not land", now=page_now(), kind="page")

    ops = p["ops"]
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
    if p["landed"] is not None:
        r.report["pages_pushed"] = r.report.get("pages_pushed", 0) + 1
        page_note(r, page_id, "created there")
    if p["retry"]:
        r.excluded.add(entry.id)


def _send_move(r: Round, att: dict, item: dict, entry: zr.Entry):
    """A standalone PDF moved under the item made for its page,
    as Zotero's Create Parent Item does: its collections go to the item, its
    tags stay. It goes at the attachment's version as Zotero holds it now,
    and only while that is the version the row last synced: a change made
    in Zotero since comes into the page first (its own unit, which queues
    the page again), so the move empties no collection the page has not
    taken. Until it lands the row keeps ``parent_to`` (``moving_pdf``): no
    unit of the item takes it for a PDF to unbind or send again, and a
    failure that does not stop the round is tried again later. A PDF that
    Zotero moved, trashed or removed meanwhile is its own unit's."""
    key = att["key"]
    obj = zr.fetch_items(r.client, r.library, [key]).objects.get(key)
    data = (obj or {}).get("data") or {}
    version = int((obj or {}).get("version") or 0)
    parent = str(data.get("parentItem") or "")
    rows, queue, retry, error, refused, writes = [], [], False, None, "", None
    moved = {**att, "parent": item["key"], "zbase": {}, "gbase": {}, "detail": without(att["detail"], "parent_to")}
    if parent == item["key"]:  # it went already (an answer lost)
        rows.append({**moved, "version": version})
    elif obj is None or data.get("deleted") or parent:
        rows.append({**att, "detail": without(att["detail"], "parent_to")})
        if obj is not None:
            queue.append({"id": key, "kind": "child" if parent else "top", "version": version})
    elif version != int(att["version"]):
        queue.append({"id": key, "kind": "top", "version": version})
    else:
        writes = zr.Writes(r.client, r.library)
        writes.update("attachments", key, version, {"parentItem": item["key"], "collections": []})
        outcome = writes.flush()[key]
        error = writes.error if writes.error is not None and zr.error_kind(writes.error) in STOPS_ROUND else None
        if outcome.status in (zr.OK, zr.UNCHANGED):
            rows.append({**moved, "version": int(outcome.version or version)})
        elif outcome.status == zr.CONFLICT:  # Zotero changed it meanwhile: its unit takes that in first
            queue.append({"id": key, "kind": "top", "version": version + 1})
        elif outcome.status == zr.REJECTED:
            refused = outcome.message
        else:
            retry = True
    with connect_pages_db(r.ws) as conn:
        write_lock(conn)
        write_rows(conn, rows)
        zr.done(conn, [entry.id])
        zr.enqueue(conn, queue)
        if retry:
            zr.backoff(conn, entry.id, str((writes and writes.error) or "the move did not land"), now=page_now(),
                       kind="page")
        conn.commit()
    if refused:
        record_conflict(r.ws, att["page_id"], att["page_id"], "zotero_rejected",
                        result=f"Zotero refused this: {refused}")
    if retry:
        r.excluded.add(entry.id)
    if error is not None:
        raise error
    return None


def _resend(r: Round, row: dict, side: Side, rows: dict, cmap: dict, entry: zr.Entry):
    """A page whose item's create is not confirmed (or Zotero refused it):
    derived again from the page as it is now: gone → the row goes; a
    refused create is sent again only once the page changed; otherwise the
    create is sent again under the same key, which a 412 confirms."""
    if side.block is None:
        with connect_pages_db(r.ws) as conn:
            write_lock(conn)
            drop_rows(conn, [k for k, x in rows.items() if x["state"] in ("pending", "rejected")
                             and int(x["version"]) <= 0])
            zr.done(conn, [entry.id])
            conn.commit()
        return None
    if not side.live:
        entries_done(r, [entry.id])
        return None
    if row["state"] == "pending":  # a create whose answer was lost: Zotero's item, as it holds it now
        unit = zr.fetch_unit(r.client, r.library, row["key"])
        data = (unit.item or {}).get("data") or {}
        if unit.item is not None and not data.get("parentItem") and data.get("itemType") not in (
                "note", "attachment", "annotation"):
            return reconcile(r, unit, [entry.id])  # confirmed (zbase = gbase = what was sent), then merged
    inverse = {fid: k for k, fid in cmap.items()}
    G = gamma_item(side, inverse, row["zotero_type"])
    if row["state"] == "rejected" and row["gsig"] == signature(G):
        entries_done(r, [entry.id])
        return None
    document = row["zotero_type"] == "document" and not zotero_merge.is_paper(side.props)
    adopts = rows.get(row["detail"].get("adopts") or "")
    parent_of = adopts if adopts is not None and adopts["state"] in HELD_ROWS and not adopts["parent"] else None
    return _create_item(r, side, cmap, entry, document=document, key=row["key"], parent_of=parent_of)
